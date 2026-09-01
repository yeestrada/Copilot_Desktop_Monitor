from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import UUID

import requests

from browser_session import BROWSER_COOKIE_LOADERS
from browser_utils import open_claude_login_url, preferred_auth_browser_name
from openai_auth import _file_fingerprint, _firefox_profiles, _sqlite_temp_connection

CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
ORGANIZATIONS_URL = "https://claude.ai/api/organizations"
WEB_USAGE_URL = "https://claude.ai/api/organizations/{org_id}/usage"
SESSION_COOKIE_NAME = "sessionKey"
ORG_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
PLATFORM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) "
    "Gecko/20100101 Firefox/154.0"
)

_cookie_header_cache: dict[str, tuple[tuple[int, int], str | None]] = {}
_validated_session_cache: dict[str, str] = {}


class ClaudeAuthError(Exception):
    pass


def open_claude_login() -> None:
    open_claude_login_url()


def read_session_from_browsers() -> tuple[str | None, str | None, list[str]]:
    """Return (cookie_header, org_uuid, notes)."""
    notes: list[str] = []

    cookie_header, cookie_notes = _read_cookies_via_browser_cookie3()
    notes.extend(cookie_notes)
    if not cookie_header:
        cookie_header = _read_firefox_cookie_header()
        if cookie_header:
            notes.append("Firefox: claude.ai cookies read.")

    if not cookie_header or f"{SESSION_COOKIE_NAME}=" not in cookie_header:
        browser = preferred_auth_browser_name()
        notes.append(
            f"Sign in with {browser} (claude.ai Usage). "
            "The monitor will detect the session automatically."
        )
        return None, None, notes

    cached_org = _validated_session_cache.get(cookie_header)
    if cached_org:
        notes.append("Claude: cached session.")
        return cookie_header, cached_org, notes

    try:
        org_id = _resolve_org_id(cookie_header)
    except ClaudeAuthError as exc:
        notes.append(str(exc))
        return None, None, notes

    notes.append("Claude: organization UUID selected from the signed-in session.")
    return cookie_header, org_id, notes


def validate_session(cookie_header: str, org_id: str, timeout: float = 20.0) -> str:
    cookie = _normalize_cookie_header(cookie_header)
    organization = org_id.strip()
    if not cookie or f"{SESSION_COOKIE_NAME}=" not in cookie:
        raise ClaudeAuthError("Missing claude.ai sessionKey cookie.")
    if not _is_org_uuid(organization):
        raise ClaudeAuthError("organization must be a claude.ai UUID.")

    response = requests.get(
        WEB_USAGE_URL.format(org_id=organization),
        headers=_api_headers(cookie),
        timeout=timeout,
    )
    if response.status_code in {401, 403}:
        raise ClaudeAuthError(
            f"claude.ai session invalid or expired ({response.status_code})."
        )
    if not response.ok:
        raise ClaudeAuthError(
            f"usage HTTP {response.status_code}: {response.text[:200]}"
        )

    payload = response.json()
    if not isinstance(payload, dict):
        raise ClaudeAuthError("Invalid usage response.")

    _validated_session_cache[cookie] = organization

    try:
        account = requests.get(
            "https://claude.ai/api/account",
            headers=_api_headers(cookie),
            timeout=timeout,
        )
        if account.ok:
            data = account.json()
            if isinstance(data, dict):
                name = str(data.get("display_name") or data.get("email_address") or "").strip()
                if name:
                    return name
    except requests.RequestException:
        pass
    return organization


def _resolve_org_id(cookie_header: str) -> str:
    response = requests.get(
        ORGANIZATIONS_URL,
        headers=_api_headers(cookie_header),
        timeout=20,
    )
    if response.status_code in {401, 403}:
        raise ClaudeAuthError(
            "Not signed in to claude.ai. Complete login in Firefox."
        )
    if not response.ok:
        raise ClaudeAuthError(
            f"Could not list organizations ({response.status_code})."
        )

    payload = response.json()
    orgs = _as_org_list(payload)
    if not orgs:
        raise ClaudeAuthError("Account returned no organizations.")

    preferred = _pick_preferred_org(orgs, cookie_header)
    if preferred:
        return preferred
    raise ClaudeAuthError("No valid organization UUID found.")


def _pick_preferred_org(orgs: list[dict[str, Any]], cookie_header: str) -> str | None:
    """Prefer the org the user is using on claude.ai, then paid plans over Free."""
    by_id: dict[str, dict[str, Any]] = {}
    for org in orgs:
        candidate = str(org.get("uuid") or org.get("id") or "").strip()
        if _is_org_uuid(candidate):
            by_id[candidate.lower()] = org

    if not by_id:
        return None

    active = _cookie_value(cookie_header, "lastActiveOrg")
    if active and active.lower() in by_id:
        return active

    ranked = sorted(
        by_id.items(),
        key=lambda item: (_org_plan_rank(item[1]), item[0]),
        reverse=True,
    )
    return ranked[0][0]


def _org_plan_rank(org: dict[str, Any]) -> int:
    caps = org.get("capabilities")
    if not isinstance(caps, list):
        caps = []
    normalized = [str(item).strip().lower() for item in caps if str(item).strip()]
    ranks = {
        "claude_max": 100,
        "max": 100,
        "claude_team": 85,
        "team": 85,
        "claude_enterprise": 90,
        "enterprise": 90,
        "claude_pro_plus": 70,
        "pro_plus": 70,
        "claude_pro": 60,
        "pro": 60,
        "api": 40,
        "chat": 1,
    }
    best = 0
    for cap in normalized:
        best = max(best, ranks.get(cap, 5 if cap.startswith("claude_") else 0))
    return best


def _cookie_value(cookie_header: str, name: str) -> str | None:
    target = name.lower()
    for part in cookie_header.split(";"):
        piece = part.strip()
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        if key.strip().lower() == target:
            text = value.strip()
            return text or None
    return None


def _as_org_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "organizations", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _api_headers(cookie_header: str) -> dict[str, str]:
    return {
        "Cookie": cookie_header,
        "Accept": "application/json",
        "User-Agent": PLATFORM_USER_AGENT,
        "Referer": CLAUDE_USAGE_URL,
        "Origin": "https://claude.ai",
    }


def _read_firefox_cookie_header() -> str | None:
    for profile in _firefox_profiles():
        db_path = profile / "cookies.sqlite"
        fingerprint = _file_fingerprint(db_path)
        if fingerprint is None:
            continue

        cache_key = str(db_path.resolve())
        cached = _cookie_header_cache.get(cache_key)
        if cached and cached[0] == fingerprint:
            if cached[1]:
                return cached[1]
            continue

        header = _load_cookie_header_from_db(db_path)
        _cookie_header_cache[cache_key] = (fingerprint, header)
        if header:
            return header
    return None


def _load_cookie_header_from_db(db_path: Path) -> str | None:
    with _sqlite_temp_connection(db_path) as connection:
        if connection is None:
            return None
        try:
            rows = connection.execute(
                "SELECT name, value FROM moz_cookies "
                "WHERE host LIKE '%claude.ai%' "
                "ORDER BY name"
            ).fetchall()
        except sqlite3.Error:
            return None

    pairs: list[str] = []
    has_session = False
    for name, value in rows:
        cookie_name = str(name).strip()
        cookie_value = str(value).strip()
        if not cookie_name or not cookie_value:
            continue
        if cookie_name == SESSION_COOKIE_NAME:
            has_session = True
        pairs.append(f"{cookie_name}={cookie_value}")

    if not has_session or not pairs:
        return None
    return "; ".join(pairs)


def _read_cookies_via_browser_cookie3() -> tuple[str | None, list[str]]:
    notes: list[str] = []
    try:
        import browser_cookie3
    except ImportError:
        notes.append("browser-cookie3 not installed.")
        return None, notes

    loaders = BROWSER_COOKIE_LOADERS
    for label, loader_name in loaders:
        loader = getattr(browser_cookie3, loader_name, None)
        if loader is None:
            continue
        try:
            jar = loader(domain_name="claude.ai")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{label}: {exc}")
            continue

        pairs: list[str] = []
        has_session = False
        for cookie in jar:
            name = str(getattr(cookie, "name", "")).strip()
            value = str(getattr(cookie, "value", "")).strip()
            if not name or not value:
                continue
            if name == SESSION_COOKIE_NAME:
                has_session = True
            pairs.append(f"{name}={value}")

        if has_session and pairs:
            notes.append(f"{label}: sessionKey cookie found.")
            return "; ".join(pairs), notes
        notes.append(f"{label}: no sessionKey cookie.")

    return None, notes


def _normalize_cookie_header(cookie: str) -> str:
    normalized = unquote(cookie).strip()
    if normalized.lower().startswith("cookie:"):
        normalized = normalized.split(":", 1)[1].strip()
    if normalized and f"{SESSION_COOKIE_NAME}=" not in normalized and ";" not in normalized:
        return f"{SESSION_COOKIE_NAME}={normalized}"
    return normalized


def _is_org_uuid(value: str) -> bool:
    text = value.strip()
    if not text or not ORG_UUID_RE.match(text):
        return False
    try:
        UUID(text)
        return True
    except ValueError:
        return False
