from __future__ import annotations

import configparser
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

import requests

from browser_session import extract_bearer_jwt, extract_sess_key, read_provider_cookies
from browser_utils import open_openai_login_url

OPENAI_BILLING_URL = "https://platform.openai.com/settings/organization/billing/overview"
SESSION_COOKIE_PREFIX = "__Secure-next-auth.session-token"
SESSION_COOKIE_NAMES = (
    SESSION_COOKIE_PREFIX,
    "next-auth.session-token",
)
OPENAI_COOKIE_DOMAINS = (
    "platform.openai.com",
    "openai.com",
    "auth.openai.com",
    "chatgpt.com",
)
ONBOARDING_LOGIN_URL = "https://api.openai.com/dashboard/onboarding/login"
CREDIT_GRANTS_PATHS = (
    "/dashboard/billing/credit_grants",
    "/v1/dashboard/billing/credit_grants",
)
PLATFORM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FIREFOX_LS_ORIGINS = (
    "https+++platform.openai.com",
    "https+++auth.openai.com",
    "https+++chatgpt.com",
)
ONBOARD_KEYS = (
    "oai-auth:onboard",
    "@@auth0spajs@@",
)

# Avoid re-copying unchanged Firefox DBs and re-hitting OpenAI with the same JWT.
_ls_payload_cache: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}
_cookie_sess_cache: dict[str, tuple[tuple[int, int], str | None]] = {}
_exchange_success_cache: dict[str, str] = {}
_exchange_failed_tokens: set[str] = set()


class OpenAIAuthError(Exception):
    pass


def normalize_session_token(token: str) -> str:
    return unquote(token).strip()


def open_openai_login() -> None:
    open_openai_login_url()


def read_session_token_from_browsers() -> tuple[str | None, list[str]]:
    """Read OpenAI sess- from Firefox localStorage / cookies automatically."""
    notes: list[str] = []
    profiles = _firefox_profiles()
    if not profiles:
        notes.append("Firefox not found. Install Firefox and sign in to Billing.")
    else:
        notes.append(f"Firefox: checking {len(profiles)} profile(s).")

    for profile in profiles:
        sess_cookie = _read_firefox_openai_sess_cookie(profile)
        if sess_cookie:
            notes.append("Firefox: sess- found in cookies.")
            return sess_cookie, notes

        onboard = _read_firefox_onboard_payload(profile)
        if not onboard:
            continue

        session_token = str(onboard.get("sessionToken") or "").strip()
        if session_token.startswith("sess-"):
            notes.append("Firefox: sessionToken sess- in localStorage.")
            return session_token, notes

        access_token = str(onboard.get("accessToken") or onboard.get("access_token") or "").strip()
        if not access_token and isinstance(onboard.get("body"), dict):
            access_token = str(onboard["body"].get("access_token") or "").strip()
        if not access_token:
            continue

        notes.append("Firefox: accessToken found in localStorage.")
        if access_token in _exchange_failed_tokens:
            notes.append("Firefox: same JWT already rejected; waiting for a new session.")
            continue

        try:
            sess_key = _exchange_sess_key_cached(access_token)
            notes.append("Firefox: sess- obtained automatically.")
            return sess_key, notes
        except OpenAIAuthError as exc:
            notes.append(f"Firefox localStorage: {exc}")

    cookies, cookie_notes = read_provider_cookies(
        domains=OPENAI_COOKIE_DOMAINS,
        cookie_prefixes=SESSION_COOKIE_NAMES,
    )
    notes.extend(cookie_notes)

    if cookies:
        notes.append(
            "Web session detected; waiting for Billing token in Firefox. "
            "Stay on the Billing page until the monitor connects."
        )
    else:
        notes.append(
            "Sign in with Firefox (Billing). The monitor will detect the session automatically."
        )
    return None, notes


def parse_pasted_openai_token(text: str) -> str | None:
    sess_key = extract_sess_key(text)
    if sess_key:
        return sess_key

    access_token = extract_bearer_jwt(text)
    if not access_token:
        return None

    try:
        return _exchange_sess_key_cached(access_token)
    except OpenAIAuthError:
        return None


def parse_clipboard_sess_key(text: str) -> str | None:
    return parse_pasted_openai_token(text)


def validate_session_token(token: str, timeout: float = 20.0) -> str:
    normalized = normalize_session_token(token)
    sess_key = extract_sess_key(normalized) or normalized
    if not sess_key.startswith("sess-"):
        raise OpenAIAuthError("OpenAI session token must start with sess-.")

    _probe_credit_grants(sess_key, timeout=timeout)
    return "OpenAI account"


def _firefox_root() -> Path:
    return Path(os.path.expandvars(r"%APPDATA%\Mozilla\Firefox"))


def _firefox_profiles() -> list[Path]:
    root = _firefox_root()
    profiles_dir = root / "Profiles"
    ordered: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        resolved = str(path.resolve()) if path.exists() else str(path)
        if resolved in seen or not path.is_dir():
            return
        seen.add(resolved)
        ordered.append(path)

    ini_path = root / "profiles.ini"
    if ini_path.is_file():
        parser = configparser.ConfigParser()
        parser.read(ini_path, encoding="utf-8")
        install_default: str | None = None
        marked_default: str | None = None
        for section in parser.sections():
            path_value = parser.get(section, "Path", fallback="").strip()
            if section.startswith("Install") and parser.get(section, "Default", fallback=""):
                install_default = parser.get(section, "Default", fallback="").strip()
            if path_value and parser.get(section, "Default", fallback="") == "1":
                marked_default = path_value
            if path_value:
                candidate = (
                    root / path_value
                    if parser.get(section, "IsRelative", fallback="1") == "1"
                    else Path(path_value)
                )
                add(candidate)
        preferred: list[Path] = []
        for relative in (install_default, marked_default):
            if not relative:
                continue
            candidate = root / relative if not os.path.isabs(relative) else Path(relative)
            if candidate.is_dir():
                preferred.append(candidate)
        rest = [path for path in ordered if path not in preferred]
        ordered = preferred + rest

    if profiles_dir.is_dir():
        for path in sorted(profiles_dir.iterdir()):
            add(path)

    return ordered


def _file_fingerprint(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _read_firefox_openai_sess_cookie(profile: Path) -> str | None:
    db_path = profile / "cookies.sqlite"
    fingerprint = _file_fingerprint(db_path)
    if fingerprint is None:
        return None

    cache_key = str(db_path.resolve())
    cached = _cookie_sess_cache.get(cache_key)
    if cached and cached[0] == fingerprint:
        return cached[1]

    token: str | None = None
    with _sqlite_temp_connection(db_path) as connection:
        if connection is None:
            _cookie_sess_cache[cache_key] = (fingerprint, None)
            return None
        try:
            rows = connection.execute(
                "SELECT value FROM moz_cookies "
                "WHERE host LIKE '%openai%' AND value LIKE 'sess-%' "
                "ORDER BY lastAccessed DESC LIMIT 1"
            ).fetchall()
        except sqlite3.Error:
            rows = []

    if rows:
        candidate = str(rows[0][0]).strip()
        if candidate.startswith("sess-"):
            token = candidate

    _cookie_sess_cache[cache_key] = (fingerprint, token)
    return token


def _read_firefox_onboard_payload(profile: Path) -> dict[str, Any] | None:
    storage_root = profile / "storage" / "default"
    if not storage_root.is_dir():
        return None

    origin_dirs: list[Path] = []
    for origin in FIREFOX_LS_ORIGINS:
        exact = storage_root / origin
        if exact.is_dir():
            origin_dirs.append(exact)
        origin_dirs.extend(sorted(storage_root.glob(f"{origin}*")))

    seen: set[str] = set()
    for origin_dir in origin_dirs:
        key = str(origin_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        db_path = origin_dir / "ls" / "data.sqlite"
        payload = _read_onboard_from_ls_db(db_path)
        if payload:
            return payload
    return None


def _read_onboard_from_ls_db(db_path: Path) -> dict[str, Any] | None:
    fingerprint = _file_fingerprint(db_path)
    if fingerprint is None:
        return None

    cache_key = str(db_path.resolve())
    cached = _ls_payload_cache.get(cache_key)
    if cached and cached[0] == fingerprint:
        return cached[1]

    payload: dict[str, Any] | None = None
    with _sqlite_temp_connection(db_path) as connection:
        if connection is None:
            _ls_payload_cache[cache_key] = (fingerprint, None)
            return None
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(data)").fetchall()
            }
            has_compression = "compression_type" in columns
            for key in ONBOARD_KEYS:
                if has_compression:
                    row = connection.execute(
                        "SELECT compression_type, value FROM data WHERE key = ?",
                        (key,),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT 0, value FROM data WHERE key = ?",
                        (key,),
                    ).fetchone()
                if not row and key.startswith("@@auth0"):
                    row = connection.execute(
                        "SELECT compression_type, value FROM data WHERE key LIKE ? LIMIT 1"
                        if has_compression
                        else "SELECT 0, value FROM data WHERE key LIKE ? LIMIT 1",
                        (f"{key}%",),
                    ).fetchone()
                if not row:
                    continue
                text = _decode_ls_value(row[1], compression_type=int(row[0] or 0))
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    payload = parsed
                    break
        except sqlite3.Error:
            payload = None

    _ls_payload_cache[cache_key] = (fingerprint, payload)
    return payload


def _decode_ls_value(raw: Any, *, compression_type: int) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        blob: bytes | str = raw.encode("utf-8", errors="ignore")
    else:
        blob = bytes(raw)

    if compression_type == 1:
        try:
            import snappy

            blob = snappy.decompress(blob)
        except Exception:  # noqa: BLE001
            return None

    if isinstance(blob, str):
        return blob
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        start = blob.find(b"{")
        if start < 0:
            return None
        try:
            return blob[start:].decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            return None


@contextmanager
def _sqlite_temp_connection(db_path: Path) -> Iterator[sqlite3.Connection | None]:
    """Copy SQLite (+ WAL/SHM) to temp, yield a read-only connection, then delete temp."""
    if not db_path.is_file():
        yield None
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="copilot-openai-"))
    connection: sqlite3.Connection | None = None
    try:
        copied = temp_dir / db_path.name
        _copy_file_shared(db_path, copied)
        for suffix in ("-wal", "-shm"):
            side = Path(str(db_path) + suffix)
            if side.is_file():
                _copy_file_shared(side, Path(str(copied) + suffix))
        connection = sqlite3.connect(f"file:{copied.as_posix()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
    except (OSError, sqlite3.Error):
        shutil.rmtree(temp_dir, ignore_errors=True)
        yield None
        return

    try:
        yield connection
    finally:
        try:
            connection.close()
        except sqlite3.Error:
            pass
        shutil.rmtree(temp_dir, ignore_errors=True)


def _copy_file_shared(source: Path, destination: Path) -> None:
    # Firefox keeps LS DBs open; shared read still works on Windows.
    with open(source, "rb") as src, open(destination, "wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)


def _exchange_sess_key_cached(access_token: str) -> str:
    cached = _exchange_success_cache.get(access_token)
    if cached:
        return cached
    if access_token in _exchange_failed_tokens:
        raise OpenAIAuthError("Token already rejected; waiting for a new session in Firefox.")

    try:
        sess_key = _exchange_sess_key(access_token)
    except OpenAIAuthError:
        _exchange_failed_tokens.add(access_token)
        raise

    _exchange_success_cache[access_token] = sess_key
    return sess_key


def _exchange_sess_key(access_token: str) -> str:
    response = requests.post(
        ONBOARDING_LOGIN_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": PLATFORM_USER_AGENT,
            "Origin": "https://platform.openai.com",
            "Referer": "https://platform.openai.com/",
        },
        json={},
        timeout=20,
    )
    if not response.ok:
        detail = _safe_json(response)
        message = ""
        if isinstance(detail.get("error"), dict):
            message = str(detail["error"].get("message") or "")
        message = message or response.text[:200]
        raise OpenAIAuthError(message or f"HTTP {response.status_code}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise OpenAIAuthError("OpenAI returned an invalid response while signing in.")

    sess_key = _extract_sess_key(payload)
    if not sess_key:
        raise OpenAIAuthError("OpenAI did not return a sess- token from onboarding/login.")
    return sess_key


def _extract_sess_key(payload: dict[str, Any]) -> str | None:
    direct = str(payload.get("sess_key") or "").strip()
    if direct.startswith("sess-"):
        return direct

    session = payload.get("user", {}).get("session", {})
    if isinstance(session, dict):
        for key in ("sensitive_id", "sess_key"):
            value = str(session.get(key) or "").strip()
            if value.startswith("sess-"):
                return value
    return None


def _probe_credit_grants(token: str, *, timeout: float) -> dict[str, Any]:
    errors: list[str] = []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": PLATFORM_USER_AGENT,
        "Referer": "https://platform.openai.com/",
        "Origin": "https://platform.openai.com",
    }

    for path in CREDIT_GRANTS_PATHS:
        try:
            return _get_credit_grants(path, headers=headers, timeout=timeout)
        except OpenAIAuthError as exc:
            errors.append(str(exc))

    detail = errors[-1] if errors else "unknown error"
    raise OpenAIAuthError(f"OpenAI session invalid or expired. ({detail})")


def _get_credit_grants(
    path: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> dict[str, Any]:
    url = f"https://api.openai.com{path}"
    response = requests.get(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "CopilotDesktopMonitor/2.0",
            **(headers or {}),
        },
        timeout=timeout,
    )
    if response.status_code in {401, 403}:
        body = _safe_json(response)
        err = body.get("error")
        message = ""
        if isinstance(err, dict):
            message = str(err.get("message") or "")
        elif isinstance(err, str):
            message = err
        message = message or response.text[:200]
        raise OpenAIAuthError(f"invalid or expired session_token ({response.status_code}): {message}")
    if not response.ok:
        detail = _safe_json(response).get("error") or _safe_json(response).get("message")
        if isinstance(detail, dict):
            detail = detail.get("message", response.text[:200])
        detail = detail or response.text[:200]
        raise OpenAIAuthError(f"HTTP {response.status_code}: {detail}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise OpenAIAuthError("unexpected credit_grants response")
    if (
        _to_float(payload.get("total_granted")) is None
        and _to_float(payload.get("total_available")) is None
        and _to_float(payload.get("total_used")) is None
    ):
        raise OpenAIAuthError("credit_grants did not return balance fields")
    return payload


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
