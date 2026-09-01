from __future__ import annotations

from browser_session import BROWSER_COOKIE_LOADERS
from browser_utils import open_cursor_login_url
from urllib.parse import unquote

import requests

CURSOR_LOGIN_URL = "https://cursor.com/login"
CURSOR_COOKIE_NAME = "WorkosCursorSessionToken"
CURSOR_COOKIE_DOMAINS = (".cursor.com", "cursor.com", "www.cursor.com")


class CursorAuthError(Exception):
    pass


def normalize_session_token(token: str) -> str:
    return unquote(token).strip()


def open_cursor_login() -> None:
    open_cursor_login_url()


def read_session_token_from_browsers() -> tuple[str | None, list[str]]:
    """Return (token, notes) where notes describe what was tried."""
    try:
        import browser_cookie3
    except ImportError as exc:
        raise CursorAuthError(
            "browser-cookie3 is not installed. Run: py -m pip install browser-cookie3 pycryptodomex"
        ) from exc

    notes: list[str] = []
    for label, loader_name in BROWSER_COOKIE_LOADERS:
        loader = getattr(browser_cookie3, loader_name, None)
        if loader is None:
            continue
        try:
            cookie_jar = loader(domain_name="cursor.com")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{label}: {exc}")
            continue

        token = _pick_cookie_value(cookie_jar)
        if token:
            notes.append(f"{label}: found WorkosCursorSessionToken")
            return token, notes
        notes.append(f"{label}: no session cookie")

    return None, notes


def _pick_cookie_value(cookie_jar: object) -> str | None:
    matches: list[tuple[int, str]] = []
    for cookie in cookie_jar:  # type: ignore[attr-defined]
        name = str(getattr(cookie, "name", ""))
        if name != CURSOR_COOKIE_NAME:
            continue
        value = normalize_session_token(str(getattr(cookie, "value", "")))
        if not value:
            continue
        domain = str(getattr(cookie, "domain", "")).lower()
        priority = 0
        if domain == ".cursor.com":
            priority = 3
        elif domain.endswith("cursor.com"):
            priority = 2
        else:
            priority = 1
        matches.append((priority, value))

    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def validate_session_token(token: str, timeout: float = 20.0) -> str:
    """Validate token against Cursor and return the account email if available."""
    normalized = normalize_session_token(token)
    if not normalized:
        raise CursorAuthError("Session token is empty.")

    response = requests.get(
        "https://cursor.com/api/auth/me",
        cookies={CURSOR_COOKIE_NAME: normalized},
        headers={"Accept": "application/json", "User-Agent": "CopilotDesktopMonitor/2.0"},
        timeout=timeout,
    )
    if response.status_code == 401:
        raise CursorAuthError("Invalid or expired session. Sign in again at cursor.com.")
    if not response.ok:
        detail = response.text[:200]
        raise CursorAuthError(f"Cursor rejected the session (HTTP {response.status_code}): {detail}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise CursorAuthError("Cursor returned an invalid auth response.") from exc

    if not isinstance(payload, dict):
        raise CursorAuthError("Cursor returned an invalid auth response.")

    email = str(payload.get("email", "")).strip()
    if email:
        return email
    name = str(payload.get("name", "")).strip()
    if name:
        return name
    return "Cursor account"
