from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

import requests

from browser_session import (
    BROWSER_COOKIE_LOADERS,
    CHROMIUM_DECRYPT_ERROR,
    assemble_chunked_cookie,
    extract_bearer_jwt,
    extract_sess_key,
)
from browser_utils import open_openai_login_url, preferred_auth_browser_name

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
OPENAI_CHROMIUM_HOSTS = (
    "https://platform.openai.com",
    "https://auth.openai.com",
    "https://chatgpt.com",
)
ONBOARD_KEYS = (
    "oai-auth:onboard",
    "@@auth0spajs@@",
)

_RAW_ACCESS_TOKEN = re.compile(
    rb'"accessToken"\s*:\s*"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"'
)
_RAW_ACCESS_TOKEN_ALT = re.compile(
    rb'"access_token"\s*:\s*"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"'
)
_RAW_SESSION_TOKEN = re.compile(
    rb'"(?:sessionToken|sess_key)"\s*:\s*"(sess-[A-Za-z0-9_-]{20,})"'
)
_RAW_SESS_INLINE = re.compile(rb"(sess-[A-Za-z0-9_-]{20,})")

# Avoid re-copying unchanged browser DBs and re-hitting OpenAI with the same JWT.
_ls_payload_cache: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}
_chromium_ls_cache: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}
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
    """Read OpenAI sess- from installed browsers (Edge first on Windows)."""
    notes: list[str] = []

    cookies, cookie_notes = _collect_all_openai_cookies()
    notes.extend(cookie_notes)
    sess_from_cookies = _sess_from_openai_cookies(cookies, notes)
    if sess_from_cookies:
        notes.append(f"{preferred_auth_browser_name()}: sess- ready from cookies.")
        return sess_from_cookies, notes

    for browser_label, user_data in _chromium_user_data_roots():
        if not user_data.is_dir():
            continue
        profiles = _chromium_profiles(user_data)
        if not profiles:
            continue
        notes.append(f"{browser_label}: checking {len(profiles)} profile(s).")
        for profile in profiles:
            auth_payload = _read_chromium_auth_payload(profile, notes, browser_label)
            if not auth_payload:
                continue
            sess = _sess_from_onboard_payload(auth_payload, notes, browser_label)
            if sess:
                return sess, notes
        notes.append(
            f"{browser_label}: Billing login not detected yet. "
            "Stay on the Billing page until it finishes loading."
        )

    firefox_profiles = _firefox_profiles()
    if sys.platform == "win32" and not firefox_profiles:
        notes.append(
            "Firefox not installed. OpenAI Sign in relies on Edge Billing on this PC."
        )

    if firefox_profiles:
        notes.append(f"Firefox: checking {len(firefox_profiles)} profile(s).")
    else:
        notes.append("Firefox not found.")

    for profile in firefox_profiles:
        sess_cookie = _read_firefox_openai_sess_cookie(profile)
        if sess_cookie:
            notes.append("Firefox: sess- found in cookies.")
            return sess_cookie, notes

        onboard = _read_firefox_onboard_payload(profile)
        if not onboard:
            continue
        sess = _sess_from_onboard_payload(onboard, notes, "Firefox")
        if sess:
            return sess, notes

    if cookies:
        browser = preferred_auth_browser_name()
        notes.append(
            f"Web session detected; waiting for Billing token in {browser}. "
            "Stay on the Billing page until the monitor connects."
        )
    else:
        browser = preferred_auth_browser_name()
        notes.append(
            f"Sign in with {browser} (Billing). "
            "The monitor will detect the session automatically."
        )
    return None, notes


def _collect_all_openai_cookies() -> tuple[dict[str, str], list[str]]:
    """Collect every OpenAI-related cookie (Edge first), including chunked next-auth."""
    notes: list[str] = []
    merged: dict[str, str] = {}
    try:
        import browser_cookie3
    except ImportError as exc:
        notes.append(f"browser-cookie3 unavailable: {exc}")
        return {}, notes

    for label, loader_name in BROWSER_COOKIE_LOADERS:
        loader = getattr(browser_cookie3, loader_name, None)
        if loader is None:
            continue

        browser_found = False
        for domain in OPENAI_COOKIE_DOMAINS:
            try:
                cookie_jar = loader(domain_name=domain)
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if CHROMIUM_DECRYPT_ERROR in message and loader_name in {"chrome", "edge", "brave", "chromium"}:
                    notes.append(f"{label}: encrypted cookies (Windows).")
                else:
                    notes.append(f"{label}@{domain}: {message}")
                continue

            for cookie in cookie_jar:
                name = str(getattr(cookie, "name", "")).strip()
                value = str(getattr(cookie, "value", "")).strip()
                if name and value:
                    merged[name] = value
                    browser_found = True

        if browser_found:
            notes.append(f"{label}: OpenAI cookies collected.")

    for prefix in SESSION_COOKIE_NAMES:
        assembled = assemble_chunked_cookie(merged, prefix)
        if assembled:
            merged[prefix] = assembled
            for name in list(merged):
                if name.startswith(f"{prefix}."):
                    del merged[name]

    return merged, notes


def _sess_from_openai_cookies(cookies: dict[str, str], notes: list[str]) -> str | None:
    sess = _extract_sess_from_cookie_map(cookies)
    if sess:
        notes.append("OpenAI session cookie contains sess-.")
        return sess

    for name, value in cookies.items():
        if not value:
            continue
        jwt = extract_bearer_jwt(value)
        if not jwt:
            continue
        try:
            sess_key = _exchange_sess_key_cached(jwt)
            notes.append(f"Cookie {name}: sess- obtained via JWT exchange.")
            return sess_key
        except OpenAIAuthError as exc:
            notes.append(f"Cookie {name}: {exc}")
    return None


def _sess_from_onboard_payload(
    payload: dict[str, Any],
    notes: list[str],
    source: str,
) -> str | None:
    session_token = str(payload.get("sessionToken") or "").strip()
    if session_token.startswith("sess-"):
        notes.append(f"{source}: sessionToken sess- in localStorage.")
        return session_token

    access_token = str(payload.get("accessToken") or payload.get("access_token") or "").strip()
    if not access_token and isinstance(payload.get("body"), dict):
        access_token = str(payload["body"].get("access_token") or "").strip()
    if not access_token:
        return None

    notes.append(f"{source}: accessToken found in localStorage.")
    if access_token in _exchange_failed_tokens:
        notes.append(f"{source}: same JWT already rejected; waiting for a new session.")
        return None

    try:
        sess_key = _exchange_sess_key_cached(access_token)
        notes.append(f"{source}: sess- obtained automatically.")
        return sess_key
    except OpenAIAuthError as exc:
        notes.append(f"{source} localStorage: {exc}")
        return None


def _extract_sess_from_cookie_map(cookies: dict[str, str]) -> str | None:
    for name in SESSION_COOKIE_NAMES:
        value = cookies.get(name, "")
        if not value:
            continue
        sess = extract_sess_key(value)
        if sess:
            return sess
    for value in cookies.values():
        sess = extract_sess_key(value)
        if sess:
            return sess
    return None


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
    sess_key = extract_sess_key(normalized)
    if sess_key:
        _probe_credit_grants(sess_key, timeout=timeout)
        return "OpenAI account"

    jwt = extract_bearer_jwt(normalized)
    if jwt:
        sess_key = _exchange_sess_key_cached(jwt)
        _probe_credit_grants(sess_key, timeout=timeout)
        return "OpenAI account"

    if normalized:
        errors: list[str] = []
        for cookie_name in SESSION_COOKIE_NAMES:
            try:
                _probe_credit_grants_with_cookie(normalized, cookie_name, timeout=timeout)
                return "OpenAI account"
            except OpenAIAuthError as exc:
                errors.append(str(exc))
        detail = errors[-1] if errors else "unknown error"
        raise OpenAIAuthError(f"OpenAI session invalid or expired. ({detail})")

    raise OpenAIAuthError("OpenAI session token must start with sess-.")


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


def _chromium_user_data_roots() -> list[tuple[str, Path]]:
    if sys.platform == "win32":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        return [
            ("Microsoft Edge", local_app_data / "Microsoft" / "Edge" / "User Data"),
            ("Google Chrome", local_app_data / "Google" / "Chrome" / "User Data"),
        ]

    home = Path.home()
    if sys.platform == "darwin":
        return [
            ("Microsoft Edge", home / "Library" / "Application Support" / "Microsoft Edge"),
            ("Google Chrome", home / "Library" / "Application Support" / "Google" / "Chrome"),
        ]

    return [
        ("Microsoft Edge", home / ".config" / "microsoft-edge"),
        ("Google Chrome", home / ".config" / "google-chrome"),
    ]


def _chromium_profiles(user_data: Path) -> list[Path]:
    ordered: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen or not path.is_dir():
            return
        seen.add(key)
        ordered.append(path)

    local_state = user_data / "Local State"
    preferred_names: list[str] = []
    if local_state.is_file():
        try:
            data = json.loads(local_state.read_text(encoding="utf-8"))
            profile = data.get("profile", {})
            if isinstance(profile, dict):
                last_used = str(profile.get("last_used") or "").strip()
                if last_used:
                    preferred_names.append(last_used)
                info_cache = profile.get("info_cache", {})
                if isinstance(info_cache, dict):
                    for name in info_cache:
                        if name not in preferred_names:
                            preferred_names.append(name)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass

    for name in preferred_names:
        add(user_data / name)

    default_profile = user_data / "Default"
    add(default_profile)

    if user_data.is_dir():
        for path in sorted(user_data.iterdir()):
            if path.name == "System Profile":
                continue
            if (path / "Preferences").is_file():
                add(path)
            elif path.name.startswith("Profile "):
                add(path)

    return ordered


def _leveldb_fingerprint(leveldb_dir: Path) -> tuple[int, int] | None:
    if not leveldb_dir.is_dir():
        return None
    try:
        latest_mtime = 0
        total_size = 0
        for path in leveldb_dir.iterdir():
            if not path.is_file():
                continue
            stat = path.stat()
            latest_mtime = max(latest_mtime, stat.st_mtime_ns)
            total_size += stat.st_size
    except OSError:
        return None
    if total_size <= 0:
        return None
    return (latest_mtime, total_size)


def _read_chromium_auth_payload(
    profile_dir: Path,
    notes: list[str],
    browser_label: str,
) -> dict[str, Any] | None:
    for label, relative in (
        ("localStorage", Path("Local Storage") / "leveldb"),
        ("sessionStorage", Path("Session Storage")),
    ):
        storage_dir = profile_dir / relative
        fingerprint = _leveldb_fingerprint(storage_dir)
        if fingerprint is None:
            continue

        cache_key = f"{storage_dir.resolve()}:{label}"
        cached = _chromium_ls_cache.get(cache_key)
        if cached and cached[0] == fingerprint:
            if cached[1]:
                notes.append(f"{browser_label}: {label} auth payload cached.")
            return cached[1]

        payload = _load_chromium_auth_payload(storage_dir, label, notes, browser_label)
        _chromium_ls_cache[cache_key] = (fingerprint, payload)
        if payload:
            notes.append(f"{browser_label}: auth payload found in {label}.")
            return payload

    payload = _load_chromium_indexeddb_payload(profile_dir, notes, browser_label)
    if payload:
        notes.append(f"{browser_label}: auth payload found in IndexedDB.")
    return payload


def _snapshot_leveldb_dir(source: Path) -> Path | None:
    if not source.is_dir():
        return None

    temp_root = Path(tempfile.mkdtemp(prefix="copilot-openai-ls-"))
    destination = temp_root / "leveldb"
    try:
        destination.mkdir(parents=True, exist_ok=True)
        copied_any = False
        for item in source.iterdir():
            if not item.is_file():
                continue
            target = destination / item.name
            try:
                _copy_file_shared(item, target)
                copied_any = True
            except OSError:
                continue
        if not copied_any:
            shutil.rmtree(temp_root, ignore_errors=True)
            return None
        return destination
    except OSError:
        shutil.rmtree(temp_root, ignore_errors=True)
        return None


def _load_chromium_auth_payload(
    storage_dir: Path,
    storage_label: str,
    notes: list[str],
    browser_label: str,
) -> dict[str, Any] | None:
    snapshot = _snapshot_leveldb_dir(storage_dir)
    scan_dirs: list[Path] = []
    if snapshot is not None:
        scan_dirs.append(snapshot)
    scan_dirs.append(storage_dir)

    temp_root = snapshot.parent if snapshot is not None else None
    try:
        for scan_dir in scan_dirs:
            if storage_label == "sessionStorage":
                payload = _scan_chromium_session_storage(scan_dir)
            else:
                payload = _scan_chromium_local_storage(scan_dir)
                if not payload:
                    payload = _scan_leveldb_raw_for_auth(scan_dir)
                    if payload:
                        notes.append(f"{browser_label}: auth token found via raw {storage_label} scan.")
            if payload:
                return payload
    except Exception as exc:  # noqa: BLE001
        notes.append(f"{browser_label}: {storage_label} scan failed: {exc}")
        return None
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)

    if snapshot is None:
        notes.append(
            f"{browser_label}: could not copy {storage_label} (Edge may be locking files). "
            "Try keeping the Billing tab open."
        )
    return None


def _scan_leveldb_raw_for_auth(leveldb_dir: Path) -> dict[str, Any] | None:
    """Fallback: grep Edge/Chrome leveldb files for auth JSON when structured parsing fails."""
    if not leveldb_dir.is_dir():
        return None

    for path in leveldb_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".ldb", ".log"}:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue

        for pattern, field in (
            (_RAW_SESSION_TOKEN, "sessionToken"),
            (_RAW_ACCESS_TOKEN, "accessToken"),
            (_RAW_ACCESS_TOKEN_ALT, "accessToken"),
        ):
            match = pattern.search(data)
            if not match:
                continue
            value = match.group(1).decode("ascii", errors="ignore")
            if field == "sessionToken" and value.startswith("sess-"):
                return {"sessionToken": value}
            if field == "accessToken" and value.startswith("eyJ"):
                return {"accessToken": value}

        match = _RAW_SESS_INLINE.search(data)
        if match:
            value = match.group(1).decode("ascii", errors="ignore")
            if value.startswith("sess-"):
                return {"sessionToken": value}

    return None


def _scan_chromium_local_storage(leveldb_dir: Path) -> dict[str, Any] | None:
    try:
        from chromium_reader.localstorage import LocalStorageReader
    except ImportError:
        return None

    with LocalStorageReader(str(leveldb_dir)) as reader:
        for host in OPENAI_CHROMIUM_HOSTS:
            for record in reader.records(host=host):
                payload = _parse_openai_storage_value(record.script_key, record.value)
                if payload:
                    return payload

        for record in reader.records():
            host = str(record.host or "")
            if "openai.com" not in host and "chatgpt.com" not in host:
                continue
            payload = _parse_openai_storage_value(record.script_key, record.value)
            if payload:
                return payload
    return None


def _scan_chromium_session_storage(storage_dir: Path) -> dict[str, Any] | None:
    try:
        from chromium_reader.sessionstorage import SessionStorageReader
    except ImportError:
        return None

    with SessionStorageReader(str(storage_dir)) as reader:
        for host in OPENAI_CHROMIUM_HOSTS:
            for record in reader.records(host=host):
                payload = _parse_openai_storage_value(record.script_key, record.value or "")
                if payload:
                    return payload
    return None


def _load_chromium_indexeddb_payload(
    profile_dir: Path,
    notes: list[str],
    browser_label: str,
) -> dict[str, Any] | None:
    idb_root = profile_dir / "IndexedDB"
    if not idb_root.is_dir():
        return None

    try:
        from chromium_reader.indexeddb import IndexedDbReader
    except ImportError:
        return None

    for origin_dir in sorted(idb_root.iterdir()):
        name = origin_dir.name.lower()
        if "openai.com" not in name and "chatgpt.com" not in name:
            continue
        if not origin_dir.is_dir():
            continue
        try:
            with IndexedDbReader(str(origin_dir)) as reader:
                for record in reader.records():
                    payload = _parse_openai_storage_value(
                        f"{record.database_name}:{record.object_store_name}",
                        _stringify_storage_value(record.value),
                    )
                    if payload:
                        return payload
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{browser_label}: IndexedDB@{origin_dir.name}: {exc}")
    return None


def _parse_openai_storage_value(key: str, text: str | None) -> dict[str, Any] | None:
    if not text:
        return None

    cleaned = str(text).strip()
    if not cleaned:
        return None

    key_text = str(key or "").strip()
    if key_text in ONBOARD_KEYS or key_text.startswith("@@auth0"):
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    sess = extract_sess_key(cleaned)
    if sess:
        return {"sessionToken": sess}

    jwt = extract_bearer_jwt(cleaned)
    if jwt:
        return {"accessToken": jwt}

    if not (cleaned.startswith("{") or cleaned.startswith("[")):
        return None

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    if any(
        parsed.get(field)
        for field in ("accessToken", "access_token", "sessionToken", "refreshToken", "idToken")
    ):
        return parsed

    body = parsed.get("body")
    if isinstance(body, dict) and body.get("access_token"):
        return parsed

    for value in parsed.values():
        if isinstance(value, str):
            nested = _parse_openai_storage_value("", value)
            if nested:
                return nested
    return None


def _stringify_storage_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def _read_chromium_onboard_payload(profile_dir: Path) -> dict[str, Any] | None:
    return _read_chromium_auth_payload(profile_dir, [], "Chromium")


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
        raise OpenAIAuthError("Token already rejected; waiting for a new session.")

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


def _probe_credit_grants_with_cookie(
    cookie_value: str,
    cookie_name: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    errors: list[str] = []
    for path in CREDIT_GRANTS_PATHS:
        try:
            return _get_credit_grants(
                path,
                cookies={cookie_name: cookie_value},
                timeout=timeout,
            )
        except OpenAIAuthError as exc:
            errors.append(str(exc))

    detail = errors[-1] if errors else "unknown error"
    raise OpenAIAuthError(f"OpenAI session invalid or expired. ({detail})")


def _get_credit_grants(
    path: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    timeout: float,
) -> dict[str, Any]:
    url = f"https://api.openai.com{path}"
    response = requests.get(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "CopilotDesktopMonitor/2.0",
            "Referer": "https://platform.openai.com/",
            "Origin": "https://platform.openai.com",
            **(headers or {}),
        },
        cookies=cookies,
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
