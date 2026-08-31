from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

import requests

from config import AccountConfig
from usage_types import AccountUsage, UsageStatus

OPENAI_API_BASE = "https://api.openai.com/v1"
COSTS_PATH = "/organization/costs"
CREDIT_GRANTS_PATHS = (
    "/dashboard/billing/credit_grants",
    "/v1/dashboard/billing/credit_grants",
)
SESSION_COOKIE_NAMES = (
    "__Secure-next-auth.session-token",
    "next-auth.session-token",
)


class OpenAIApiError(Exception):
    pass


class OpenAIClient:
    def __init__(self, account: AccountConfig) -> None:
        self.account = account
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": "CopilotDesktopMonitor/2.0",
                "Accept": "application/json",
            }
        )

    def fetch_usage(self) -> AccountUsage:
        now = datetime.now(timezone.utc)
        period_label = _next_month_reset_label(now)

        if not self.account.session_token.strip() and not self.account.api_key.strip():
            return self._error_usage(
                period_label,
                "Sign in to OpenAI to load your credit balance.",
                needs_auth=True,
            )

        if self.account.session_token.strip():
            try:
                return self._fetch_credit_grants_usage(period_label)
            except OpenAIApiError as exc:
                if not self.account.api_key.strip():
                    return self._error_usage(
                        period_label,
                        str(exc),
                        needs_auth=_openai_auth_needed(str(exc)),
                    )
                # Fall through to Admin costs when session fails but api_key exists.

        if self.account.api_key.strip():
            try:
                return self._fetch_admin_costs_usage(period_label, now)
            except OpenAIApiError as exc:
                return self._error_usage(period_label, str(exc))

        return self._error_usage(
            period_label,
            "Sign in to OpenAI to load your credit balance.",
            needs_auth=True,
        )

    def _fetch_credit_grants_usage(self, period_label: str) -> AccountUsage:
        payload = self._request_credit_grants()
        total_granted = _to_float(payload.get("total_granted")) or 0.0
        total_used = _to_float(payload.get("total_used")) or 0.0
        total_available = _to_float(payload.get("total_available"))
        if total_available is None:
            total_available = max(total_granted - total_used, 0.0)

        # Prefer paid available when present (closer to spendable balance).
        paid_available = _to_float(payload.get("total_paid_available"))
        if paid_available is not None and paid_available >= 0:
            remaining = paid_available
        else:
            remaining = max(total_available, 0.0)

        used = max(total_used, 0.0)
        limit = total_granted if total_granted > 0 else used + remaining

        # No spendable credit left → treat as fully used (even if grants are $0).
        if remaining <= 0:
            if limit <= 0:
                used = 0.0
                limit = 0.0
            percent = 100.0
            remaining = 0.0
            status = UsageStatus.EXCEEDED
        else:
            if limit <= 0:
                limit = remaining
            status, percent, remaining = _compute_status(used, limit, self.account, remaining)

        expiry = _earliest_active_grant_expiry(payload)
        username = self.account.display_username or self.account.organization or "openai"

        return AccountUsage(
            used=used,
            limit=limit,
            unit="USD",
            billing_mode="openai_credits",
            status=status,
            percent_used=percent,
            remaining=remaining,
            username=username,
            period_label=expiry or period_label,
            plan=self.account.plan or "credits",
            organization="Credit balance",
            provider=self.account.provider,
            label=self.account.label,
        )

    def _fetch_admin_costs_usage(self, period_label: str, now: datetime) -> AccountUsage:
        self.session.headers["Authorization"] = f"Bearer {self.account.api_key.strip()}"
        start_time = _month_start_unix(now)
        used = self._fetch_month_cost(start_time)
        top_line = self._fetch_top_line_item(start_time)

        limit = self.account.resolve_monthly_limit("openai_costs", None)
        status, percent, remaining = _compute_status(used, limit, self.account)
        username = self.account.display_username or self.account.organization or "openai"

        return AccountUsage(
            used=used,
            limit=limit,
            unit="USD",
            billing_mode="openai_costs",
            status=status,
            percent_used=percent,
            remaining=remaining,
            username=username,
            period_label=period_label,
            plan=self.account.plan or "api",
            organization=top_line,
            provider=self.account.provider,
            label=self.account.label,
        )

    def _request_credit_grants(self) -> dict[str, Any]:
        token = unquote(self.account.session_token).strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        errors: list[str] = []

        # Browser session JWT as Bearer (preferred).
        for path in CREDIT_GRANTS_PATHS:
            try:
                return self._get_credit_grants(
                    path,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except OpenAIApiError as exc:
                errors.append(str(exc))

        # Legacy next-auth cookie only when the value does not look like a JWT.
        if not token.startswith("eyJ"):
            for cookie_name in SESSION_COOKIE_NAMES:
                for path in CREDIT_GRANTS_PATHS:
                    try:
                        return self._get_credit_grants(
                            path,
                            cookies={cookie_name: token},
                        )
                    except OpenAIApiError as exc:
                        errors.append(str(exc))

        detail = errors[-1] if errors else "unknown error"
        raise OpenAIApiError(
            f"could not read credit balance with session_token. "
            f"Copy a fresh browser JWT from platform.openai.com "
            f"(Network → api.openai.com → Authorization Bearer). ({detail})"
        )

    def _get_credit_grants(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"https://api.openai.com{path}"
        response = self.session.get(
            url,
            headers=headers or {},
            cookies=cookies,
            timeout=15,
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
            if "session key" in message.lower() or "secret" in message.lower():
                raise OpenAIApiError(
                    "session_token is not a browser session key "
                    "(API keys cannot read credit balance)"
                )
            raise OpenAIApiError(f"invalid or expired session_token ({response.status_code})")
        if not response.ok:
            detail = _safe_json(response).get("error") or _safe_json(response).get("message")
            if isinstance(detail, dict):
                detail = detail.get("message", response.text[:200])
            detail = detail or response.text[:200]
            raise OpenAIApiError(f"HTTP {response.status_code}: {detail}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise OpenAIApiError("unexpected credit_grants response")
        if (
            _to_float(payload.get("total_granted")) is None
            and _to_float(payload.get("total_available")) is None
            and _to_float(payload.get("total_used")) is None
        ):
            raise OpenAIApiError("credit_grants did not return balance fields")
        return payload

    def _fetch_month_cost(self, start_time: int) -> float:
        payload = self._request_costs(start_time=start_time)
        return _sum_cost_amounts(payload)

    def _fetch_top_line_item(self, start_time: int) -> str:
        try:
            payload = self._request_costs(start_time=start_time, group_by=["line_item"])
        except OpenAIApiError:
            return ""

        totals: dict[str, float] = {}
        for bucket in payload.get("data") or []:
            if not isinstance(bucket, dict):
                continue
            for result in bucket.get("results") or []:
                if not isinstance(result, dict):
                    continue
                line_item = result.get("line_item")
                if not isinstance(line_item, str) or not line_item.strip():
                    continue
                amount = _amount_value(result.get("amount"))
                if amount is None:
                    continue
                totals[line_item] = totals.get(line_item, 0.0) + amount

        if not totals:
            return ""

        top_name, top_value = max(totals.items(), key=lambda item: item[1])
        return f"{top_name} ${top_value:.2f}"

    def _request_costs(
        self,
        *,
        start_time: int,
        group_by: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start_time": start_time,
            "bucket_width": "1d",
            "limit": 35,
        }
        if group_by:
            params["group_by"] = group_by

        response = self.session.get(
            f"{OPENAI_API_BASE}{COSTS_PATH}",
            params=params,
            timeout=30,
        )
        if response.status_code == 401:
            raise OpenAIApiError("invalid or expired Admin API key (401)")
        if response.status_code == 403:
            raise OpenAIApiError(
                "missing permission to read organization costs. Use an OpenAI Admin API key"
            )
        if not response.ok:
            detail = _safe_json(response).get("error") or _safe_json(response).get("message")
            if isinstance(detail, dict):
                detail = detail.get("message", response.text[:200])
            detail = detail or response.text[:200]
            raise OpenAIApiError(f"HTTP {response.status_code}: {detail}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise OpenAIApiError("unexpected costs response")
        return payload

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
            username=self.account.display_username or self.account.organization or "openai",
            period_label=period_label,
            message=message,
            plan=self.account.plan or "api",
            organization=self.account.organization,
            provider=self.account.provider,
            label=self.account.label,
            needs_auth=needs_auth,
        )


def _openai_auth_needed(message: str) -> bool:
    lowered = message.lower()
    return (
        "401" in lowered
        or "403" in lowered
        or "invalid or expired session_token" in lowered
        or "sign in to openai" in lowered
        or "missing session_token" in lowered
    )


def _month_start_unix(now: datetime) -> int:
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    return int(start.timestamp())


def _next_month_reset_label(now: datetime) -> str:
    if now.month == 12:
        return f"01/{now.year + 1}"
    return f"{now.month + 1:02d}/{now.year}"


def _earliest_active_grant_expiry(payload: dict[str, Any]) -> str:
    grants = payload.get("grants")
    items: list[Any] = []
    if isinstance(grants, dict):
        items = list(grants.get("data") or [])
    elif isinstance(grants, list):
        items = grants

    now_ts = datetime.now(timezone.utc).timestamp()
    expiries: list[float] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        expires_at = _to_float(item.get("expires_at"))
        if expires_at is None or expires_at <= now_ts:
            continue
        remaining = _to_float(item.get("grant_amount")) or 0.0
        used = _to_float(item.get("used_amount")) or 0.0
        if remaining - used <= 0:
            continue
        expiries.append(expires_at)

    if not expiries:
        return ""
    dt = datetime.fromtimestamp(min(expiries), tz=timezone.utc)
    return f"{dt.month:02d}/{dt.year}"


def _sum_cost_amounts(payload: dict[str, Any]) -> float:
    total = 0.0
    for bucket in payload.get("data") or []:
        if not isinstance(bucket, dict):
            continue
        for result in bucket.get("results") or []:
            if not isinstance(result, dict):
                continue
            amount = _amount_value(result.get("amount"))
            if amount is not None:
                total += amount
    return total


def _amount_value(amount: Any) -> float | None:
    if isinstance(amount, dict):
        return _to_float(amount.get("value"))
    return _to_float(amount)


def _compute_status(
    used: float,
    limit: float | None,
    account: AccountConfig,
    remaining: float | None = None,
) -> tuple[UsageStatus, float | None, float | None]:
    if limit is None or limit < 0:
        return UsageStatus.UNKNOWN, None, None

    if limit == 0:
        return UsageStatus.EXCEEDED, 100.0, 0.0

    percent = min((used / limit) * 100, 999.9)
    if remaining is None:
        remaining = max(limit - used, 0)

    if percent >= 100 or remaining <= 0:
        return UsageStatus.EXCEEDED, max(percent, 100.0), max(remaining, 0.0)
    if percent >= account.thresholds.critical_percent:
        return UsageStatus.CRITICAL, percent, remaining
    if percent >= account.thresholds.warning_percent:
        return UsageStatus.WARNING, percent, remaining
    return UsageStatus.OK, percent, remaining


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
