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
        has_web = bool(self.account.session_token.strip() and self.account.organization.strip())
        has_configured_oauth = _has_configured_oauth_token(self.account)
        # Do not silently use ~/.claude/.credentials.json or env tokens — users must Sign in
        # (or paste credentials into config) so Free vs Pro/Max is the account they chose.
        if not has_web and not has_configured_oauth:
            return self._error_usage(
                period_label,
                "Sign in to Claude to load your plan quota.",
                needs_auth=True,
            )
        try:
            payload, source = self._request_live_usage()
        except ClaudeCodeApiError as exc:
            return self._error_usage(
                period_label,
                str(exc),
                needs_auth=_claude_auth_needed(str(exc)),
            )

        session = _pick_bucket(payload, "five_hour", kinds=("session",))
        weekly = _pick_bucket(payload, "seven_day", kinds=("weekly_all",))
        weekly_sonnet = _pick_bucket(
            payload, "seven_day_sonnet", kinds=("weekly_scoped",), model="sonnet"
        )
        weekly_opus = _pick_bucket(
            payload, "seven_day_opus", kinds=("weekly_scoped",), model="opus"
        )

        # Re-normalize with payload-wide scale detection (0–1 vs 0–100).
        percent_scale = _payload_uses_percent_scale(payload, source=source)
        session = _renormalize_bucket(session, percent_scale=percent_scale)
        weekly = _renormalize_bucket(weekly, percent_scale=percent_scale)
        weekly_sonnet = _renormalize_bucket(weekly_sonnet, percent_scale=percent_scale)
        weekly_opus = _renormalize_bucket(weekly_opus, percent_scale=percent_scale)

        profile = _fetch_account_profile(self.account)
        display_name = (
            (profile or {}).get("display_name")
            or (profile or {}).get("email_address")
            or ""
        ).strip()

        if session is None and weekly is None:
            if _is_empty_quota_payload(payload):
                detected_plan = _plan_from_capabilities((profile or {}).get("capabilities"))
                plan_label = detected_plan or "free"
                username = display_name
                if "@" in username:
                    username = username.split("@", 1)[0]
                if not username or _is_org_uuid(username):
                    username = "claude"
                return AccountUsage(
                    used=0.0,
                    limit=100.0,
                    unit="%",
                    billing_mode="claude_code_quota",
                    status=UsageStatus.OK,
                    percent_used=0.0,
                    remaining=100.0,
                    username=username,
                    period_label=period_label,
                    plan=plan_label,
                    organization=f"Free plan · no quota windows · via {source}",
                    provider=self.account.provider,
                    label=self.account.label,
                )
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
            display_name
            or self.account.display_username
            or self.account.organization
            or "claude"
        )
        if "@" in username:
            username = username.split("@", 1)[0]
        if _is_org_uuid(username):
            username = "claude"

        detected_plan = _plan_from_capabilities((profile or {}).get("capabilities"))
        plan_label = detected_plan or self.account.plan or "claude"

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
            plan=plan_label,
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
                "missing Claude OAuth accessToken in config. "
                "Use Sign in (claude.ai), or paste an OAuth accessToken into session_token."
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

    def _error_usage(
        self,
        period_label: str,
        message: str,
        *,
        needs_auth: bool = False,
    ) -> AccountUsage:
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
            needs_auth=needs_auth,
        )


def _claude_auth_needed(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "missing claude",
            "invalid or expired",
            "sign in",
            "401",
            "403",
            "sessionkey",
            "credentials",
        )
    )


def _is_empty_quota_payload(payload: dict[str, Any]) -> bool:
    """Free claude.ai accounts often return null bars and an empty limits list."""
    has_legacy = any(
        isinstance(payload.get(key), dict)
        for key in ("five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus")
    )
    limits = payload.get("limits")
    has_limits = isinstance(limits, list) and any(isinstance(item, dict) for item in limits)
    return not has_legacy and not has_limits


def _plan_from_capabilities(capabilities: Any) -> str:
    if not isinstance(capabilities, list):
        return ""
    caps = [str(item).strip().lower() for item in capabilities if str(item).strip()]
    for cap in caps:
        if cap.startswith("claude_"):
            return cap.removeprefix("claude_").replace("_", " ") or "free"
    for name in ("max", "team", "enterprise", "pro+", "pro", "api"):
        if name in caps:
            return name
    if caps == ["chat"] or caps == []:
        return "free"
    return ""


def _collect_utilizations(payload: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for key in (
        "five_hour",
        "seven_day",
        "seven_day_sonnet",
        "seven_day_opus",
        "seven_day_oauth_apps",
        "seven_day_omelette",
        "seven_day_cowork",
    ):
        bucket = payload.get(key)
        if isinstance(bucket, dict):
            raw = _to_float(bucket.get("utilization"))
            if raw is None:
                raw = _to_float(bucket.get("percent"))
            if raw is not None:
                values.append(raw)
    limits = payload.get("limits")
    if isinstance(limits, list):
        for item in limits:
            if not isinstance(item, dict):
                continue
            raw = _to_float(item.get("utilization"))
            if raw is None:
                raw = _to_float(item.get("percent"))
            if raw is not None:
                values.append(raw)
    return values


def _payload_uses_percent_scale(payload: dict[str, Any], *, source: str) -> bool:
    """Detect whether utilization values are already 0–100 (vs 0–1 fractions).

    OAuth `/api/oauth/usage` returns percentages (e.g. 1.0 == 1%).
    Web `/organizations/.../usage` often returns fractions (0.01 == 1%), but some
    Team payloads use percentages. Multiplying a percent `1` by 100 caused 100%.
    """
    values = _collect_utilizations(payload)
    if any(value > 1.0 for value in values):
        return True
    if source == "oauth":
        return True
    if any(0.0 < value < 1.0 for value in values):
        return False
    # Only 0 and/or 1 — treat as percent so 1 stays 1%, not 100%.
    return True


def _fetch_account_profile(account: AccountConfig) -> dict[str, Any] | None:
    cookie = _normalize_cookie(account.session_token)
    if not cookie or not _looks_like_web_session(cookie):
        return None
    try:
        response = requests.get(
            "https://claude.ai/api/account",
            headers={
                "Accept": "application/json",
                "Cookie": cookie,
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": "https://claude.ai/settings/usage",
                "Origin": "https://claude.ai",
            },
            timeout=15,
        )
    except requests.RequestException:
        return None
    if not response.ok:
        return None
    payload = _safe_json(response)
    if not payload:
        return None

    capabilities: list[str] = []
    memberships = payload.get("memberships")
    if isinstance(memberships, list):
        for membership in memberships:
            if not isinstance(membership, dict):
                continue
            org = membership.get("organization")
            if not isinstance(org, dict):
                continue
            org_uuid = str(org.get("uuid") or "").strip()
            if account.organization and org_uuid and org_uuid != account.organization:
                continue
            caps = org.get("capabilities")
            if isinstance(caps, list):
                capabilities = [str(item) for item in caps]
                break

    return {
        "display_name": str(payload.get("display_name") or "").strip(),
        "email_address": str(payload.get("email_address") or "").strip(),
        "capabilities": capabilities,
    }


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


def _has_configured_oauth_token(account: AccountConfig) -> bool:
    """True only when config itself contains an OAuth token (not browser Cookie)."""
    token = unquote(account.session_token).strip()
    if not token:
        return False
    if _looks_like_web_session(token):
        return False
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token.startswith("sk-ant-") or token.startswith("eyJ")


def _resolve_oauth_tokens(account: AccountConfig) -> tuple[str, str, Path | None]:
    """Resolve OAuth tokens from config only — never auto-login via local Claude Code files."""
    configured = unquote(account.session_token).strip()
    if configured.lower().startswith("bearer "):
        configured = configured[7:].strip()
    if (
        not configured
        or configured.lower().startswith("cookie:")
        or "sessionKey=" in configured
        or _looks_like_web_session(account.session_token)
        or (not configured.startswith("sk-ant-") and not configured.startswith("eyJ"))
    ):
        configured = ""

    refresh = unquote(account.api_key).strip()
    return configured, refresh, None


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


def _renormalize_bucket(
    bucket: dict[str, Any] | None,
    *,
    percent_scale: bool,
) -> dict[str, Any] | None:
    if bucket is None:
        return None
    utilization = float(bucket["utilization"])
    if not percent_scale and 0.0 <= utilization <= 1.0:
        utilization *= 100.0
    return {
        "utilization": utilization,
        "resets_at": bucket.get("resets_at"),
    }


def _normalize_bucket(value: Any) -> dict[str, Any] | None:
    """Extract raw utilization; scaling to percent happens in _renormalize_bucket."""
    if not isinstance(value, dict):
        return None
    utilization = _to_float(value.get("utilization"))
    if utilization is None:
        utilization = _to_float(value.get("percent"))
    if utilization is None:
        utilization = _to_float(value.get("used_percentage"))
    if utilization is None:
        return None
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
