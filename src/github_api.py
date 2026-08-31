from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from config import AccountConfig
from usage_types import AccountUsage, UsageStatus

GITHUB_API = "https://api.github.com"
API_VERSION = "2026-03-10"
COPILOT_INTERNAL_API_VERSION = "2025-05-01"
COPILOT_INTERNAL_PATH = "/copilot_internal/user"


@dataclass(frozen=True)
class UsageRequest:
    mode: str
    path: str
    params: dict[str, Any]
    label: str


class GitHubCopilotClient:
    def __init__(self, account: AccountConfig) -> None:
        self.account = account
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {account.token}",
                "Accept": "application/json",
                "User-Agent": "CopilotDesktopMonitor/2.0",
            }
        )

    def fetch_usage(self) -> AccountUsage:
        now = datetime.now(timezone.utc)
        period_label = f"{now.month:02d}/{now.year}"

        if not self.account.token.strip():
            return self._error_usage(
                period_label,
                "Sign in to GitHub to load your Copilot usage.",
                needs_auth=True,
            )

        errors: list[str] = []

        if self.account.data_source in {"auto", "copilot_internal"}:
            try:
                return self._fetch_internal_usage(period_label)
            except GitHubApiError as exc:
                errors.append(f"copilot_internal: {exc}")
                if self.account.data_source == "copilot_internal":
                    return self._error_usage(
                        period_label,
                        str(exc),
                        needs_auth=_github_auth_needed(str(exc)),
                    )

        if self.account.data_source in {"auto", "billing_api"}:
            attempts = self._usage_requests(now.year, now.month)
            for attempt in attempts:
                try:
                    payload = self._request_billing_usage(attempt)
                    return self._parse_billing_usage(payload, attempt.mode, period_label)
                except GitHubApiError as exc:
                    errors.append(f"{attempt.label}: {exc}")

        message = self._compose_error_message(errors)
        return self._error_usage(
            period_label,
            message,
            needs_auth=_github_auth_needed(message),
        )

    def _fetch_internal_usage(self, period_label: str) -> AccountUsage:
        response = self.session.get(
            f"{GITHUB_API}{COPILOT_INTERNAL_PATH}",
            headers={"X-GitHub-Api-Version": COPILOT_INTERNAL_API_VERSION},
            timeout=30,
        )
        if response.status_code == 401:
            raise GitHubApiError("invalid or expired token (401)")
        if response.status_code == 403:
            raise GitHubApiError(
                "missing permission to read your personal quota. Token needs copilot scope"
            )
        if not response.ok:
            detail = _safe_json(response).get("message", response.text[:200])
            raise GitHubApiError(f"HTTP {response.status_code}: {detail}")

        payload = response.json()
        premium = (payload.get("quota_snapshots") or {}).get("premium_interactions") or {}

        entitlement = _to_float(premium.get("entitlement"))
        remaining = _to_float(premium.get("remaining", premium.get("quota_remaining")))
        unlimited = bool(premium.get("unlimited"))

        if unlimited:
            limit = None
            used = 0.0
            percent = None
        elif entitlement is not None and remaining is not None:
            limit = entitlement
            used = max(entitlement - remaining, 0)
            percent = min((used / entitlement) * 100, 999.9) if entitlement > 0 else None
        else:
            raise GitHubApiError("API did not return premium interactions quota")

        username = self.account.github_username or str(payload.get("login") or "")
        plan = self.account.plan or str(payload.get("copilot_plan") or "")
        reset_date = str(payload.get("quota_reset_date") or period_label)

        status, percent, remaining_value = _compute_status(used, limit, self.account, percent)

        org_label = self.account.organization.strip()
        if not org_label:
            orgs = payload.get("organization_login_list") or []
            org_label = ", ".join(str(org) for org in orgs if org)

        return AccountUsage(
            used=used,
            limit=limit,
            unit="premium requests",
            billing_mode="copilot_internal",
            status=status,
            percent_used=percent,
            remaining=remaining_value if remaining_value is not None else remaining,
            username=username,
            period_label=reset_date,
            raw_items_count=1,
            plan=plan,
            organization=org_label,
            provider="github_copilot",
            label=self.account.label,
        )

    def _usage_requests(self, year: int, month: int) -> list[UsageRequest]:
        base_params: dict[str, Any] = {"year": year, "month": month}
        user = self.account.github_username
        org = self.account.organization
        enterprise = self.account.enterprise
        account_type = self.account.account_type
        modes = self._billing_modes_to_try()
        requests_list: list[UsageRequest] = []

        def add(scope: str, mode: str, path: str, params: dict[str, Any]) -> None:
            requests_list.append(
                UsageRequest(
                    mode=mode,
                    path=path,
                    params={**base_params, **params},
                    label=f"{scope}/{mode}",
                )
            )

        if account_type == "enterprise" and enterprise:
            for mode in modes:
                segment = "ai_credit" if mode == "ai_credits" else "premium_request"
                params: dict[str, Any] = {"user": user, "product": "Copilot"}
                if org:
                    params["organization"] = org
                add(
                    f"enterprise:{enterprise}",
                    mode,
                    f"/enterprises/{enterprise}/settings/billing/{segment}/usage",
                    params,
                )
            return requests_list

        if account_type == "organization" and org:
            for mode in modes:
                segment = "ai_credit" if mode == "ai_credits" else "premium_request"
                add(
                    f"organization:{org}",
                    mode,
                    f"/organizations/{org}/settings/billing/{segment}/usage",
                    {"user": user, "product": "Copilot"},
                )
            return requests_list

        for mode in modes:
            segment = "ai_credit" if mode == "ai_credits" else "premium_request"
            add(
                f"user:{user}",
                mode,
                f"/users/{user}/settings/billing/{segment}/usage",
                {},
            )

        return requests_list

    def _billing_modes_to_try(self) -> list[str]:
        mode = self.account.billing_mode
        if mode == "premium_requests":
            return ["premium_requests"]
        if mode == "ai_credits":
            return ["ai_credits"]
        return ["premium_requests", "ai_credits"]

    def _request_billing_usage(self, attempt: UsageRequest) -> dict[str, Any]:
        response = self.session.get(
            f"{GITHUB_API}{attempt.path}",
            headers={"X-GitHub-Api-Version": API_VERSION, "Accept": "application/vnd.github+json"},
            params=attempt.params,
            timeout=30,
        )
        if response.status_code == 401:
            raise GitHubApiError("invalid or expired token (401)")
        if response.status_code == 403:
            raise GitHubApiError("missing billing permissions (403)")
        if response.status_code == 404:
            raise GitHubApiError("endpoint not available (404)")
        if not response.ok:
            detail = _safe_json(response).get("message", response.text[:200])
            raise GitHubApiError(f"HTTP {response.status_code}: {detail}")

        return response.json()

    def _compose_error_message(self, errors: list[str]) -> str:
        header = "Could not fetch your Copilot usage."
        hints = (
            "Use data_source 'copilot_internal' (recommended for org members) "
            "or a token with copilot scope."
        )
        detail = " | ".join(errors[-2:]) if errors else ""
        return f"{header} {hints} ({detail})"

    def _parse_billing_usage(
        self, payload: dict[str, Any], mode: str, period_label: str
    ) -> AccountUsage:
        items = payload.get("usageItems") or []
        if not items:
            raise GitHubApiError(f"API returned 0 items for {mode} in {period_label}")

        if mode == "ai_credits":
            used, unit, api_limit = _parse_ai_credits(items)
        else:
            used, unit, api_limit = _parse_premium_requests(items)

        limit = self.account.resolve_monthly_limit(mode, api_limit)
        status, percent, remaining = _compute_status(used, limit, self.account)

        return AccountUsage(
            used=used,
            limit=limit,
            unit=unit,
            billing_mode=mode,
            status=status,
            percent_used=percent,
            remaining=remaining,
            username=self.account.github_username,
            period_label=period_label,
            raw_items_count=len(items),
            plan=self.account.plan,
            organization=self.account.organization,
            provider="github_copilot",
            label=self.account.label,
        )

    def _error_usage(
        self,
        period_label: str,
        message: str,
        *,
        needs_auth: bool = False,
    ) -> AccountUsage:
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
            provider="github_copilot",
            label=self.account.label,
            needs_auth=needs_auth,
        )


def _github_auth_needed(message: str) -> bool:
    lowered = message.lower()
    return (
        "401" in lowered
        or "invalid or expired token" in lowered
        or "sign in to github" in lowered
        or "missing token" in lowered
    )


class GitHubApiError(Exception):
    pass


def _parse_premium_requests(items: list[dict[str, Any]]) -> tuple[float, str, float | None]:
    premium_items = [
        item
        for item in items
        if "premium" in str(item.get("sku", "")).lower()
        or "copilot" in str(item.get("product", "")).lower()
    ]
    target_items = premium_items or items

    used = 0.0
    for item in target_items:
        qty = item.get("grossQuantity", item.get("gross_quantity", 0))
        used += float(qty or 0)

    limits = [
        float(item["limit"])
        for item in target_items
        if isinstance(item.get("limit"), (int, float)) and float(item["limit"]) > 0
    ]
    api_limit = max(limits) if limits else None
    return used, "requests", api_limit


def _parse_ai_credits(items: list[dict[str, Any]]) -> tuple[float, str, float | None]:
    used = 0.0
    for item in items:
        amount = item.get("netAmount", item.get("grossAmount", 0))
        used += float(amount or 0)

    limits = [
        float(item["limit"])
        for item in items
        if isinstance(item.get("limit"), (int, float)) and float(item["limit"]) > 0
    ]
    api_limit = max(limits) if limits else None
    return used, "USD", api_limit


def _compute_status(
    used: float,
    limit: float | None,
    account: AccountConfig,
    percent_override: float | None = None,
) -> tuple[UsageStatus, float | None, float | None]:
    if limit is None or limit <= 0:
        return UsageStatus.UNKNOWN, None, None

    percent = percent_override if percent_override is not None else min((used / limit) * 100, 999.9)
    remaining = max(limit - used, 0)

    if percent >= 100:
        return UsageStatus.EXCEEDED, percent, remaining
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
