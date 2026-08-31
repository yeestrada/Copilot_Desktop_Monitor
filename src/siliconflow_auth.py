from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests

from browser_utils import open_siliconflow_login_url
from openai_auth import _file_fingerprint, _firefox_profiles, _sqlite_temp_connection

SILICONFLOW_BILLING_URL = "https://cloud.siliconflow.com/me/expensebill"
WALLET_PEEK_URL = "https://cloud.siliconflow.com/walletd-server/api/v1/subject/profile/peek"
SESSION_COOKIE_PREFIX = "__SF_auth.session-token"
SUBJECT_ID_RE = re.compile(
    r"window\.SF_SUBJECT_ID\s*=\s*['\"]([A-Za-z0-9_-]+)['\"]",
    re.IGNORECASE,
)
PLATFORM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) "
    "Gecko/20100101 Firefox/154.0"
)

_cookie_header_cache: dict[str, tuple[tuple[int, int], str | None]] = {}
_validated_session_cache: dict[str, str] = {}


class SiliconFlowAuthError(Exception):
    pass


def open_siliconflow_login() -> None:
    open_siliconflow_login_url()


def read_session_from_browsers() -> tuple[str | None, str | None, list[str]]:
    """Return (cookie_header, subject_id, notes)."""
    notes: list[str] = []

    cookie_header = _read_firefox_cookie_header()
    if not cookie_header:
        cookie_header, cookie_notes = _read_cookies_via_browser_cookie3()
        notes.extend(cookie_notes)
    else:
        notes.append("Firefox: SiliconFlow cookies read.")

    if not cookie_header or SESSION_COOKIE_PREFIX not in cookie_header:
        notes.append(
            "Sign in with Firefox (SiliconFlow Billing). "
            "The monitor will detect the session automatically."
        )
        return None, None, notes

    cache_key = cookie_header
    cached_subject = _validated_session_cache.get(cache_key)
    if cached_subject:
        notes.append("SiliconFlow: cached session.")
        return cookie_header, cached_subject, notes

    try:
        subject_id = _resolve_subject_id(cookie_header)
    except SiliconFlowAuthError as exc:
        notes.append(str(exc))
        return None, None, notes

    notes.append("SiliconFlow: subject-id obtained from Billing.")
    return cookie_header, subject_id, notes


def validate_session(cookie_header: str, subject_id: str, timeout: float = 20.0) -> str:
    cookie = _normalize_cookie_header(cookie_header)
    subject = subject_id.strip()
    if not cookie:
        raise SiliconFlowAuthError("Session cookie is empty.")
    if not subject:
        raise SiliconFlowAuthError("Missing x-subject-id.")
    if SESSION_COOKIE_PREFIX not in cookie:
        raise SiliconFlowAuthError("Cookie __SF_auth.session-token not found.")

    response = requests.get(
        WALLET_PEEK_URL,
        headers={
            "Cookie": cookie,
            "x-subject-id": subject,
            "Accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": PLATFORM_USER_AGENT,
            "Referer": SILICONFLOW_BILLING_URL,
            "Origin": "https://cloud.siliconflow.com",
        },
        timeout=timeout,
    )
    if response.status_code in {401, 403}:
        raise SiliconFlowAuthError(
            f"SiliconFlow session invalid or expired ({response.status_code})."
        )
    if not response.ok:
        detail = _safe_json(response).get("message") or response.text[:200]
        raise SiliconFlowAuthError(f"HTTP {response.status_code}: {detail}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise SiliconFlowAuthError("Invalid wallet peek response.")
    code = payload.get("code")
    if code is not None and int(code) != 20000:
        message = str(payload.get("message") or "SiliconFlow wallet peek failed")
        raise SiliconFlowAuthError(message)

    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("financialInfo"), dict):
        raise SiliconFlowAuthError("Wallet peek missing financialInfo.")

    _validated_session_cache[cookie] = subject
    return subject


def _resolve_subject_id(cookie_header: str) -> str:
    response = requests.get(
        SILICONFLOW_BILLING_URL,
        headers={
            "Cookie": cookie_header,
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": PLATFORM_USER_AGENT,
        },
        timeout=20,
    )
    if response.status_code in {401, 403}:
        raise SiliconFlowAuthError("Not signed in to SiliconFlow Billing.")
    if not response.ok:
        raise SiliconFlowAuthError(
            f"Could not open Billing ({response.status_code})."
        )

    match = SUBJECT_ID_RE.search(response.text)
    if not match:
        raise SiliconFlowAuthError(
            "SF_SUBJECT_ID not found. Open Billing while signed in with Firefox."
        )
    return match.group(1)


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
                "WHERE host LIKE '%siliconflow.com%' "
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
        if cookie_name.startswith(SESSION_COOKIE_PREFIX):
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

    loaders = (
        ("Mozilla Firefox", "firefox"),
        ("Microsoft Edge", "edge"),
        ("Google Chrome", "chrome"),
        ("Brave", "brave"),
    )
    for label, loader_name in loaders:
        loader = getattr(browser_cookie3, loader_name, None)
        if loader is None:
            continue
        try:
            jar = loader(domain_name="siliconflow.com")
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
            if name.startswith(SESSION_COOKIE_PREFIX):
                has_session = True
            pairs.append(f"{name}={value}")

        if has_session and pairs:
            notes.append(f"{label}: SiliconFlow session cookie found.")
            return "; ".join(pairs), notes
        notes.append(f"{label}: no __SF_auth.session-token cookie.")

    return None, notes


def _normalize_cookie_header(cookie: str) -> str:
    normalized = unquote(cookie).strip()
    if normalized.lower().startswith("cookie:"):
        normalized = normalized.split(":", 1)[1].strip()
    return normalized


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
