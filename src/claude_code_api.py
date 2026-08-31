from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import UUID

import requests

from config import AccountConfig
from usage_types import AccountUsage, UsageStatus

OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
WEB_USAGE_URL = "https://claude.ai/api/organizations/{org_id}/usage"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
ANTHROPIC_BETA = "oauth-2025-04-20"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
CLAUDE_CODE_USER_AGENT = "claude-code/2.1.72"
ORG_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class ClaudeCodeApiError(Exception):
    pass


class ClaudeCodeClient:
    """Live Claude plan quota (same numbers as claude.ai Settings → Usage / Claude Code /usage)."""

    def __init__(self, account: AccountConfig) -> None:
        self.account = account
        self.session = requests.Session()

    def fetch_usage(self) -> AccountUsage:
        period_label = datetime.now(timezone.utc).strftime("%m/%Y")
        try:
            payload, source = self._request_live_usage()
        except ClaudeCodeApiError as exc:
            return self._error_usage(period_label, str(exc))

        session = _pick_bucket(payload, "five_hour", kinds=("session",))
        weekly = _pick_bucket(payload, "seven_day", kinds=("weekly_all",))
        weekly_sonnet = _pick_bucket(
            payload, "seven_day_sonnet", kinds=("weekly_scoped",), model="sonnet"
        )
        weekly_opus = _pick_bucket(
            payload, "seven_day_opus", kinds=("weekly_scoped",), model="opus"
        )

        if session is None and weekly is None:
            return self._error_usage(
                period_label, "Claude usage response missing five_hour/seven_day quota"
            )

        primary = session or weekly
        assert primary is not None
        percent = max(min(float(primary["utilization"]), 999.9), 0.0)
        remaining = max(100.0 - percent, 0.0)
        status = _status_from_percent(percent, self.account)
        reset_label = _format_reset(primary.get("resets_at")) or period_label

        detail_parts: list[str] = []
        if weekly is not None:
            detail_parts.append(f"Week {weekly['utilization']:.0f}%")
        if weekly_sonnet is not None:
            detail_parts.append(f"Sonnet {weekly_sonnet['utilization']:.0f}%")
        if weekly_opus is not None:
            detail_parts.append(f"Opus {weekly_opus['utilization']:.0f}%")
        detail_parts.append(f"via {source}")
        detail = " · ".join(detail_parts)

        username = (
            self.account.display_username
            or self.account.organization
            or "claude"
        )
        if _is_org_uuid(username):
            username = "claude"

        return AccountUsage(
            used=percent,
            limit=100.0,
            unit="%",
            billing_mode="claude_code_quota",
            status=status,
            percent_used=percent,
            remaining=remaining,
            username=username,
            period_label=reset_label,
            plan=self.account.plan or "claude",
            organization=detail,
            provider=self.account.provider,
            label=self.account.label,
        )

    def _request_live_usage(self) -> tuple[dict[str, Any], str]:
        errors: list[str] = []

        # 1) Claude.ai web usage (same JSON as Settings → Usage) — preferred when configured.
        org_id = self.account.organization.strip()
        cookie = _normalize_cookie(self.account.session_token)
        if _is_org_uuid(org_id) and cookie and _looks_like_web_session(cookie):
            try:
                return self._request_web_usage(org_id, cookie), "claude.ai"
            except ClaudeCodeApiError as exc:
                errors.append(str(exc))

        # 2) Claude Code / Anthropic OAuth usage (same as /usage).
        try:
            return self._request_oauth_usage(), "oauth"
        except ClaudeCodeApiError as exc:
            errors.append(str(exc))

        # 3) Web again if org is set but cookie looked like a bare token earlier.
        if _is_org_uuid(org_id) and cookie:
            try:
                return self._request_web_usage(org_id, cookie), "claude.ai"
            except ClaudeCodeApiError as exc:
                errors.append(str(exc))

        if errors:
            raise ClaudeCodeApiError(" | ".join(dict.fromkeys(errors)))
        raise ClaudeCodeApiError(
            "missing Claude credentials for live quota. "
            "Paste claude.ai Cookie + organization UUID, or Claude Code OAuth accessToken."
        )

    def _request_web_usage(self, org_id: str, cookie: str) -> dict[str, Any]:
        if "…" in cookie or "\u2026" in cookie:
            raise ClaudeCodeApiError(
                "claude.ai Cookie looks truncated (contains …). Copy the FULL Cookie header."
            )
        try:
            cookie.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ClaudeCodeApiError(
                "claude.ai Cookie has invalid characters for HTTP headers"
            ) from exc

        url = WEB_USAGE_URL.format(org_id=org_id)
        response = self.session.get(
            url,
            headers={
                "Accept": "application/json",
                "Cookie": cookie,
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": "https://claude.ai/settings/usage",
                "Origin": "https://claude.ai",
            },
            timeout=20,
        )
        if response.status_code in {401, 403}:
            raise ClaudeCodeApiError(
                f"invalid or expired claude.ai session ({response.status_code}). "
                "Refresh Cookie from Network → organizations/.../usage."
            )
        if not response.ok:
            raise ClaudeCodeApiError(
                f"claude.ai usage HTTP {response.status_code}: {response.text[:200]}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ClaudeCodeApiError("unexpected claude.ai usage response")
        return payload

    def _request_oauth_usage(self) -> dict[str, Any]:
        access_token, refresh_token, credentials_path = _resolve_oauth_tokens(self.account)
        if not access_token:
            raise ClaudeCodeApiError(
                "missing Claude OAuth accessToken "
                "(session_token, CLAUDE_CODE_OAUTH_TOKEN, or ~/.claude/.credentials.json)"
            )

        response = self._get_oauth_usage(access_token)
        if response.status_code in {401, 403} and refresh_token:
            access_token = _refresh_access_token(refresh_token, credentials_path)
            response = self._get_oauth_usage(access_token)

        if response.status_code in {401, 403}:
            raise ClaudeCodeApiError(
                f"invalid or expired Claude OAuth token ({response.status_code})"
            )
        if response.status_code == 429:
            raise ClaudeCodeApiError(
                "Claude OAuth usage rate-limited (429). "
                "Prefer claude.ai Cookie + organization UUID, or retry later."
            )
        if not response.ok:
            detail = _safe_json(response).get("error")
            if isinstance(detail, dict):
                detail = detail.get("message") or detail
            raise ClaudeCodeApiError(f"OAuth usage HTTP {response.status_code}: {detail or response.text[:200]}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise ClaudeCodeApiError("unexpected OAuth usage response")
        return payload

    def _get_oauth_usage(self, access_token: str) -> requests.Response:
        return self.session.get(
            OAUTH_USAGE_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "anthropic-beta": ANTHROPIC_BETA,
                "User-Agent": CLAUDE_CODE_USER_AGENT,
            },
            timeout=20,
        )

    def _error_usage(self, period_label: str, message: str) -> AccountUsage:
        return AccountUsage(
            used=0.0,
            limit=None,
            unit="",
            billing_mode="",
            status=UsageStatus.ERROR,
            percent_used=None,
            remaining=None,
            username=self.account.display_username or "claude",
            period_label=period_label,
            message=message,
            plan=self.account.plan or "claude",
            organization=self.account.organization,
            provider=self.account.provider,
            label=self.account.label,
        )


def _normalize_cookie(raw: str) -> str:
    cookie = unquote(raw).strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if cookie and "sessionKey=" not in cookie and " " not in cookie and ";" not in cookie:
        # Bare sessionKey value from DevTools.
        if cookie.startswith("sk-ant-") or cookie.startswith("eyJ"):
            return cookie
        return f"sessionKey={cookie}"
    return cookie


def _looks_like_web_session(cookie: str) -> bool:
    lower = cookie.lower()
    if "sessionkey=" in lower:
        return True
    if cookie.startswith("sk-ant-oat") or cookie.startswith("sk-ant-api"):
        return False
    # Full browser Cookie headers are long and include several pairs.
    return ";" in cookie or "cf_" in lower


def _is_org_uuid(value: str) -> bool:
    text = value.strip()
    if not text or not ORG_UUID_RE.match(text):
        return False
    try:
        UUID(text)
        return True
    except ValueError:
        return False


def _resolve_oauth_tokens(account: AccountConfig) -> tuple[str, str, Path | None]:
    configured = unquote(account.session_token).strip()
    if configured.lower().startswith("bearer "):
        configured = configured[7:].strip()
    if configured.lower().startswith("cookie:") or "sessionKey=" in configured:
        configured = ""
    if _is_org_uuid(account.organization) and _looks_like_web_session(account.session_token):
        configured = ""

    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()

    credentials_path = _credentials_path()
    file_access = ""
    file_refresh = ""
    expires_at: float | None = None
    if credentials_path is not None and credentials_path.is_file():
        try:
            data = json.loads(credentials_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if isinstance(oauth, dict):
            file_access = str(oauth.get("accessToken") or oauth.get("access_token") or "").strip()
            file_refresh = str(oauth.get("refreshToken") or oauth.get("refresh_token") or "").strip()
            expires_at = _to_float(oauth.get("expiresAt") or oauth.get("expires_at"))

    access = configured or env_token or file_access
    refresh = file_refresh or unquote(account.api_key).strip()

    # Proactively refresh when local credentials are expired.
    if refresh and expires_at is not None:
        # expiresAt may be seconds or milliseconds.
        exp = expires_at / 1000.0 if expires_at > 10_000_000_000 else expires_at
        if datetime.now(timezone.utc).timestamp() >= exp - 60:
            try:
                access = _refresh_access_token(refresh, credentials_path)
            except ClaudeCodeApiError:
                pass

    return access, refresh, credentials_path


def _credentials_path() -> Path | None:
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser() / ".credentials.json"
    home = Path.home()
    candidates = [
        home / ".claude" / ".credentials.json",
        home / ".config" / "claude" / ".credentials.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def _refresh_access_token(refresh_token: str, credentials_path: Path | None) -> str:
    response = requests.post(
        OAUTH_TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        },
        headers={"Content-Type": "application/json", "User-Agent": CLAUDE_CODE_USER_AGENT},
        timeout=20,
    )
    if not response.ok:
        raise ClaudeCodeApiError(
            f"could not refresh Claude OAuth token ({response.status_code})"
        )
    payload = _safe_json(response)
    access = str(payload.get("access_token") or "").strip()
    new_refresh = str(payload.get("refresh_token") or refresh_token).strip()
    if not access:
        raise ClaudeCodeApiError("Claude token refresh returned no access_token")

    if credentials_path is not None:
        try:
            existing: dict[str, Any] = {}
            if credentials_path.is_file():
                loaded = json.loads(credentials_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            oauth = existing.get("claudeAiOauth")
            if not isinstance(oauth, dict):
                oauth = {}
            oauth["accessToken"] = access
            oauth["refreshToken"] = new_refresh
            expires_in = payload.get("expires_in")
            if expires_in is not None:
                try:
                    oauth["expiresAt"] = int(
                        datetime.now(timezone.utc).timestamp() + float(expires_in)
                    )
                except (TypeError, ValueError):
                    pass
            existing["claudeAiOauth"] = oauth
            credentials_path.parent.mkdir(parents=True, exist_ok=True)
            credentials_path.write_text(
                json.dumps(existing, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    return access


def _pick_bucket(
    payload: dict[str, Any],
    legacy_key: str,
    *,
    kinds: tuple[str, ...],
    model: str | None = None,
) -> dict[str, Any] | None:
    parsed = _normalize_bucket(payload.get(legacy_key))
    if parsed is not None:
        return parsed

    limits = payload.get("limits")
    if not isinstance(limits, list):
        return None
    for item in limits:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in kinds:
            continue
        if model:
            scope = item.get("scope")
            display = ""
            if isinstance(scope, dict):
                model_obj = scope.get("model")
                if isinstance(model_obj, dict):
                    display = str(model_obj.get("display_name") or model_obj.get("name") or "")
                else:
                    display = str(scope.get("display_name") or "")
            if model.lower() not in display.lower():
                continue
        parsed = _normalize_bucket(item)
        if parsed is not None:
            return parsed
    return None


def _normalize_bucket(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    utilization = _to_float(value.get("utilization"))
    if utilization is None:
        utilization = _to_float(value.get("percent"))
    if utilization is None:
        utilization = _to_float(value.get("used_percentage"))
    if utilization is None:
        return None
    # Some payloads return 0–1 instead of 0–100.
    if 0.0 <= utilization <= 1.0:
        utilization *= 100.0
    return {
        "utilization": utilization,
        "resets_at": value.get("resets_at") or value.get("resetsAt"),
    }


def _format_reset(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return str(value)
    text = str(value).strip()
    if not text:
        return ""
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value)[:16]


def _status_from_percent(percent: float, account: AccountConfig) -> UsageStatus:
    if percent >= 100:
        return UsageStatus.EXCEEDED
    if percent >= account.thresholds.critical_percent:
        return UsageStatus.CRITICAL
    if percent >= account.thresholds.warning_percent:
        return UsageStatus.WARNING
    return UsageStatus.OK


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
