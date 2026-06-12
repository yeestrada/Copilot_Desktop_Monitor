from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

import requests

from config import AccountConfig
from usage_types import AccountUsage, UsageStatus

USAGE_META_KEYS = frozenset(
    {
        "startOfMonth",
        "monthlyInvoice",
        "billingCycle",
        "periodStart",
        "cycleStart",
        "billingCycleStart",
        "subscription",
        "plan",
        "user",
        "team",
        "organization",
        "metadata",
        "error",
        "message",
    }
)


class CursorApiError(Exception):
    pass


@dataclass
class IncludedBucket:
    model_key: str
    used: float
    limit: float
    value_kind: str = "requests"


class CursorClient:
    def __init__(self, account: AccountConfig) -> None:
        self.account = account
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "CopilotDesktopMonitor/2.0",
            }
        )

    def fetch_usage(self) -> AccountUsage:
        now = datetime.now(timezone.utc)
        period_label = f"{now.month:02d}/{now.year}"
        auth_mode = self.account.cursor_auth_mode

        if auth_mode == "admin_api":
            try:
                return self._fetch_admin_api_usage(period_label)
            except CursorApiError as exc:
                return self._error_usage(period_label, str(exc))

        if self.account.session_token:
            try:
                return self._fetch_cookie_usage(period_label)
            except CursorApiError as exc:
                if auth_mode == "session":
                    return self._error_usage(period_label, str(exc))

        if self.account.api_key and self.account.cursor_email:
            try:
                return self._fetch_admin_api_usage(period_label)
            except CursorApiError as exc:
                return self._error_usage(period_label, str(exc))

        if self.account.api_key:
            return self._error_usage(
                period_label,
                "Cursor User API keys do not expose usage quota. "
                "Add session_token (WorkosCursorSessionToken cookie) for personal accounts.",
            )

        return self._error_usage(period_label, "missing session_token for Cursor")

    def _normalize_session_token(self, token: str) -> str:
        return unquote(token).strip()

    def _fetch_cookie_usage(self, period_label: str) -> AccountUsage:
        token = self._normalize_session_token(self.account.session_token)
        web_summary = self._request_cursor_com("GET", "/api/usage-summary", token)
        merged = _merge_general_quota(_parse_general_plan_summary(web_summary))

        if not merged.get("included"):
            raise CursorApiError("Cursor usage-summary did not return plan quota data")

        if not self.account.cursor_email.strip():
            session_user = self._fetch_session_user(token)
            if session_user:
                merged["username"] = session_user

        return self._build_account_usage(merged, period_label, "cursor_session")

    def _fetch_session_user(self, token: str) -> str:
        try:
            data = self._request_cursor_com("GET", "/api/auth/me", token)
        except CursorApiError:
            return ""

        email = str(data.get("email", "")).strip()
        if email:
            return _email_local_part(email)

        return str(data.get("name", "")).strip()

    def _request_cursor_com(
        self,
        method: str,
        path: str,
        session_token: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"https://cursor.com{path}"
        cookies = {"WorkosCursorSessionToken": session_token}
        headers = {"Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        response = self.session.request(
            method,
            url,
            cookies=cookies,
            headers=headers,
            json=json_body,
            timeout=30,
        )
        if response.status_code == 401:
            raise CursorApiError(
                "invalid or expired session_token (401). "
                "Copy a fresh WorkosCursorSessionToken from cursor.com cookies."
            )
        if not response.ok:
            detail = _safe_json(response).get("message", response.text[:200])
            raise CursorApiError(f"HTTP {response.status_code}: {detail}")

        data = response.json()
        return data if isinstance(data, dict) else {}

    def _fetch_admin_api_usage(self, period_label: str) -> AccountUsage:
        response = self.session.post(
            f"{self.account.admin_api_base_url}/teams/spend",
            auth=(self.account.api_key, ""),
            json={"page": 1, "pageSize": 100},
            timeout=30,
        )
        if response.status_code == 401:
            raise CursorApiError("invalid Cursor admin API key (401)")
        if not response.ok:
            detail = _safe_json(response).get("message", response.text[:200])
            raise CursorApiError(f"HTTP {response.status_code}: {detail}")

        payload = response.json()
        members = payload.get("teamMemberSpend") or payload.get("members") or []
        email = self.account.cursor_email.lower()
        match = None
        for member in members:
            member_email = str(member.get("email", "")).lower()
            if member_email == email:
                match = member
                break

        if match is None:
            raise CursorApiError(f"no spend data found for {self.account.cursor_email}")

        used_cents = _to_float(match.get("spendCents") or match.get("overallSpendCents")) or 0.0
        limit_cents = _to_float(match.get("spendLimitCents") or match.get("hardLimitCents"))
        used = used_cents / 100.0
        limit = (limit_cents / 100.0) if limit_cents is not None else None

        status, percent, remaining = _compute_status(used, limit, self.account)
        return AccountUsage(
            used=used,
            limit=limit,
            unit="USD",
            billing_mode="admin_api",
            status=status,
            percent_used=percent,
            remaining=remaining,
            username=_resolve_cursor_username(None, self.account.cursor_email),
            period_label=period_label,
            plan="team",
            provider="cursor",
            label=self.account.label,
        )

    def _build_account_usage(
        self,
        normalized: dict[str, Any],
        period_label: str,
        billing_mode: str = "cursor_session",
    ) -> AccountUsage:
        included: IncludedBucket | None = normalized.get("included")
        if included is None:
            raise CursorApiError("Cursor API did not return usage quota data")

        period = normalized.get("period_start") or period_label
        plan_name = str(normalized.get("plan_name") or included.model_key)
        total_percent = _to_float(normalized.get("total_percent_used"))
        auto_percent = _to_float(normalized.get("auto_percent_used"))
        api_percent = _to_float(normalized.get("api_percent_used"))
        request_used = _to_float(normalized.get("request_used"))
        request_limit = _to_float(normalized.get("request_limit"))
        username = _resolve_cursor_username(
            normalized.get("username"),
            self.account.cursor_email,
        )

        if included.value_kind == "cents":
            used = included.used / 100.0
            limit = included.limit / 100.0
            unit = "USD"
            remaining_override = normalized.get("remaining_override")
            status, percent, remaining = _compute_status(used, limit, self.account)
            if remaining_override is not None:
                remaining = remaining_override / 100.0
            return AccountUsage(
                used=used,
                limit=limit,
                unit=unit,
                billing_mode=billing_mode,
                status=status,
                percent_used=percent,
                remaining=remaining,
                username=username,
                period_label=str(period),
                plan=plan_name,
                provider="cursor",
                label=self.account.label,
            )

        if total_percent is not None:
            status, _, _ = _compute_status(total_percent, 100.0, self.account)
            breakdown = ""
            if auto_percent is not None and api_percent is not None:
                breakdown = f"Auto {auto_percent:.0f}% / API {api_percent:.0f}%"

            # plan.used/limit are event counters; totalPercentUsed is the dashboard metric.
            # Derive display amounts from the percentage so Used, Limit and Usage stay aligned.
            used, limit, remaining, unit = _cursor_display_quota(
                total_percent, request_limit
            )

            return AccountUsage(
                used=used,
                limit=limit,
                unit=unit,
                billing_mode=billing_mode,
                status=status,
                percent_used=total_percent,
                remaining=remaining,
                username=username,
                period_label=str(period),
                plan=plan_name,
                organization=breakdown,
                provider="cursor",
                label=self.account.label,
            )

        used = included.used
        limit = included.limit
        remaining_override = normalized.get("remaining_override")
        status, percent, remaining = _compute_status(used, limit, self.account)
        if remaining_override is not None:
            remaining = remaining_override

        return AccountUsage(
            used=used,
            limit=limit,
            unit="requests",
            billing_mode=billing_mode,
            status=status,
            percent_used=percent,
            remaining=remaining,
            username=username,
            period_label=str(period),
            plan=plan_name,
            provider="cursor",
            label=self.account.label,
        )

    def _error_usage(self, period_label: str, message: str) -> AccountUsage:
        return AccountUsage(
            used=0,
            limit=None,
            unit="",
            billing_mode="",
            status=UsageStatus.ERROR,
            percent_used=None,
            remaining=None,
            username=self.account.display_username,
            period_label=period_label,
            message=message,
            provider="cursor",
            label=self.account.label,
        )


def _parse_dashboard_period_usage(
    plan_usage_payload: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = payload or plan_usage_payload
    plan_usage = plan_usage_payload.get("planUsage")
    if not isinstance(plan_usage, dict):
        return {}

    limit = _to_float(plan_usage.get("limit"))
    remaining = _to_float(plan_usage.get("remaining"))
    included_spend = _to_float(plan_usage.get("includedSpend"))
    total_spend = _to_float(plan_usage.get("totalSpend"))

    used: float | None
    if included_spend is not None:
        used = included_spend
    elif limit is not None and remaining is not None:
        used = max(0.0, limit - remaining)
    elif total_spend is not None:
        used = total_spend
    else:
        used = None

    period_start = root.get("billingCycleStart")
    if limit is None or limit <= 0 or used is None:
        return {"period_start": period_start}

    return {
        "period_start": period_start,
        "plan_name": str(root.get("membershipType") or "plan"),
        "remaining_override": remaining,
        "included": IncludedBucket(
            model_key="plan",
            used=used,
            limit=limit,
            value_kind="cents",
        ),
    }


def _parse_general_plan_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}

    plan = (payload.get("individualUsage") or {}).get("plan") or {}
    if not isinstance(plan, dict) or plan.get("enabled") is False:
        return {}

    used = _to_float(plan.get("used"))
    limit = _to_float(plan.get("limit"))
    remaining = _to_float(plan.get("remaining"))

    if limit is None or limit <= 0:
        return {}

    if used is None:
        if remaining is not None:
            used = max(limit - remaining, 0)
        else:
            return {}

    return {
        "period_start": payload.get("billingCycleStart"),
        "plan_name": str(payload.get("membershipType") or "plan"),
        "remaining_override": remaining,
        "total_percent_used": _to_float(plan.get("totalPercentUsed")),
        "auto_percent_used": _to_float(plan.get("autoPercentUsed")),
        "api_percent_used": _to_float(plan.get("apiPercentUsed")),
        "request_used": used,
        "request_limit": limit,
        "included": IncludedBucket(
            model_key="plan",
            used=used,
            limit=limit,
            value_kind="requests",
        ),
    }


def _merge_general_quota(*sources: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in sources:
        if not source:
            continue
        if source.get("period_start") and not merged.get("period_start"):
            merged["period_start"] = source["period_start"]
        if source.get("plan_name") and not merged.get("plan_name"):
            merged["plan_name"] = source["plan_name"]
        if source.get("remaining_override") is not None and merged.get("remaining_override") is None:
            merged["remaining_override"] = source["remaining_override"]
        for key in (
            "total_percent_used",
            "auto_percent_used",
            "api_percent_used",
            "request_used",
            "request_limit",
        ):
            if source.get(key) is not None and merged.get(key) is None:
                merged[key] = source[key]
        if source.get("included") and not merged.get("included"):
            merged["included"] = source["included"]
    return merged


def _parse_auth_usage(payload: dict[str, Any], preferred_model_key: str) -> dict[str, Any]:
    buckets = _collect_included_buckets(payload)
    included = _pick_bucket(buckets, preferred_model_key)
    return {
        "period_start": _first_str(
            payload,
            "startOfMonth",
            "periodStart",
            "cycleStart",
            "billingCycleStart",
        ),
        "included": included,
    }


def _collect_included_buckets(root: dict[str, Any], max_depth: int = 3) -> list[IncludedBucket]:
    buckets: list[IncludedBucket] = []
    seen: set[str] = set()

    def walk(obj: dict[str, Any], prefix: str, depth: int) -> None:
        for key, value in obj.items():
            if key in USAGE_META_KEYS or not isinstance(value, dict):
                continue
            path = f"{prefix}.{key}" if prefix else key
            pair = _extract_used_limit(value)
            if pair is not None:
                if path not in seen:
                    seen.add(path)
                    buckets.append(
                        IncludedBucket(
                            model_key=path,
                            used=pair[0],
                            limit=pair[1],
                            value_kind="requests",
                        )
                    )
            elif depth < max_depth:
                walk(value, path, depth + 1)

    walk(root, "", 0)
    return buckets


def _pick_bucket(buckets: list[IncludedBucket], preferred_key: str) -> IncludedBucket | None:
    if not buckets:
        return None
    for bucket in buckets:
        if bucket.model_key == preferred_key:
            return bucket
    for bucket in buckets:
        if bucket.model_key.startswith(f"{preferred_key}."):
            return bucket
    for bucket in buckets:
        if bucket.model_key.endswith(f".{preferred_key}"):
            return bucket
    return buckets[0]


def _extract_used_limit(value: dict[str, Any]) -> tuple[float, float] | None:
    num_requests = _to_float(value.get("numRequests") or value.get("used") or value.get("requests"))
    max_requests = _to_float(
        value.get("maxRequestUsage")
        or value.get("limit")
        or value.get("maxRequests")
        or value.get("requestLimit")
    )
    if num_requests is not None and max_requests is not None:
        return num_requests, max_requests

    remaining_requests = _to_float(
        value.get("remainingRequests") or value.get("requestsRemaining") or value.get("requestsLeft")
    )
    if num_requests is not None and remaining_requests is not None:
        return num_requests, num_requests + remaining_requests

    num_tokens = _to_float(
        value.get("numTokens") or value.get("totalTokens") or value.get("tokens") or value.get("inputTokens")
    )
    max_tokens = _to_float(
        value.get("maxTokenUsage")
        or value.get("maxTokens")
        or value.get("tokenLimit")
        or value.get("includedTokens")
        or value.get("tokenQuota")
        or value.get("includedTokenLimit")
    )
    if num_tokens is not None and max_tokens is not None:
        return num_tokens, max_tokens

    remaining_tokens = _to_float(
        value.get("remainingTokens") or value.get("tokensRemaining") or value.get("tokensLeft")
    )
    if num_tokens is not None and remaining_tokens is not None:
        return num_tokens, num_tokens + remaining_tokens

    return None


def _email_local_part(email: str) -> str:
    return email.split("@", 1)[0].strip()


def _resolve_cursor_username(
    api_username: str | None,
    cursor_email: str,
) -> str:
    email = cursor_email.strip()
    if email:
        return _email_local_part(email) if "@" in email else email

    return str(api_username or "").strip()


def _cursor_display_quota(
    total_percent: float,
    request_limit: float | None,
) -> tuple[float, float, float, str]:
    """Map Cursor's dashboard percent onto a consistent used/limit pair."""
    if request_limit is not None and request_limit > 0:
        limit = request_limit
        used = limit * total_percent / 100.0
        remaining = max(limit - used, 0.0)
        return used, limit, remaining, "tokens"

    limit = 100.0
    used = total_percent
    remaining = max(100.0 - total_percent, 0.0)
    return used, limit, remaining, "%"


def _compute_status(
    used: float,
    limit: float | None,
    account: AccountConfig,
) -> tuple[UsageStatus, float | None, float | None]:
    if limit is None or limit <= 0:
        return UsageStatus.UNKNOWN, None, None

    percent = min((used / limit) * 100, 999.9)
    remaining = max(limit - used, 0)

    if percent >= 100:
        return UsageStatus.EXCEEDED, percent, remaining
    if percent >= account.thresholds.critical_percent:
        return UsageStatus.CRITICAL, percent, remaining
    if percent >= account.thresholds.warning_percent:
        return UsageStatus.WARNING, percent, remaining
    return UsageStatus.OK, percent, remaining


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


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
