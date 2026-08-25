from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

import requests

from config import AccountConfig
from usage_types import AccountUsage, UsageStatus

# Wallet amounts are returned as integer strings in 1e-12 currency units.
AMOUNT_SCALE = 1_000_000_000_000.0
WALLET_PEEK_URL = "https://cloud.siliconflow.com/walletd-server/api/v1/subject/profile/peek"


class SiliconFlowApiError(Exception):
    pass


class SiliconFlowClient:
    def __init__(self, account: AccountConfig) -> None:
        self.account = account
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "*/*",
                "Content-Type": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) "
                    "Gecko/20100101 Firefox/154.0"
                ),
                "Referer": "https://cloud.siliconflow.com/me/expensebill",
                "Origin": "https://cloud.siliconflow.com",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )

    def fetch_usage(self) -> AccountUsage:
        period_label = datetime.now(timezone.utc).strftime("%m/%Y")
        try:
            payload = self._request_wallet_peek()
        except SiliconFlowApiError as exc:
            return self._error_usage(period_label, str(exc))

        data = payload.get("data")
        if not isinstance(data, dict):
            return self._error_usage(period_label, "SiliconFlow wallet peek missing data")

        financial = data.get("financialInfo")
        if not isinstance(financial, dict):
            return self._error_usage(period_label, "SiliconFlow wallet peek missing financialInfo")

        available = _scaled_amount(financial.get("available"))
        balance = _scaled_amount(financial.get("balance"))
        recharged = _scaled_amount(financial.get("recharged"))
        used = _scaled_amount(financial.get("used"))

        if available is None:
            available = balance if balance is not None else 0.0
        if used is None:
            used = 0.0
        if recharged is None or recharged <= 0:
            recharged = max(used + available, available, 0.0)

        remaining = max(available, 0.0)
        limit = max(recharged, 0.0)

        if remaining <= 0 and limit <= 0:
            status = UsageStatus.EXCEEDED
            percent = 100.0
            remaining = 0.0
        else:
            status, percent, remaining = _compute_status(used, limit, self.account, remaining)

        username = self.account.display_username or self.account.organization or "siliconflow"
        detail = f"Recharged ${recharged:.2f}"
        if balance is not None and abs(balance - available) > 0.0001:
            detail = f"Balance ${balance:.2f} · {detail}"

        return AccountUsage(
            used=max(used, 0.0),
            limit=limit,
            unit="USD",
            billing_mode="siliconflow_balance",
            status=status,
            percent_used=percent,
            remaining=remaining,
            username=username,
            period_label=period_label,
            plan=self.account.plan or "wallet",
            organization=detail,
            provider=self.account.provider,
            label=self.account.label,
        )

    def _request_wallet_peek(self) -> dict[str, Any]:
        cookie = unquote(self.account.session_token).strip()
        if cookie.lower().startswith("cookie:"):
            cookie = cookie.split(":", 1)[1].strip()
        subject_id = self.account.organization.strip()
        if not cookie:
            raise SiliconFlowApiError(
                "missing session_token (browser Cookie header from cloud.siliconflow.com)"
            )
        if not subject_id:
            raise SiliconFlowApiError(
                "missing organization (x-subject-id from wallet peek request headers)"
            )
        if "\u2026" in cookie or "…" in cookie:
            raise SiliconFlowApiError(
                "session_token looks truncated (contains …). "
                "Copy the FULL Cookie header from Network → profile/peek "
                "(not the shortened Firefox preview)."
            )
        try:
            cookie.encode("latin-1")
            subject_id.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise SiliconFlowApiError(
                "Cookie/x-subject-id has invalid characters for HTTP headers. "
                "Copy the raw Cookie header from Network (no ellipsis …)."
            ) from exc

        try:
            response = self.session.get(
                WALLET_PEEK_URL,
                headers={
                    "Cookie": cookie,
                    "x-subject-id": subject_id,
                },
                timeout=20,
            )
        except UnicodeEncodeError as exc:
            raise SiliconFlowApiError(
                "Cookie contains non-ASCII characters (often a truncated …). "
                "Copy the full Cookie header from Network → profile/peek."
            ) from exc
        if response.status_code in {401, 403}:
            raise SiliconFlowApiError(
                f"invalid or expired SiliconFlow session ({response.status_code}). "
                "Refresh Cookie + x-subject-id from Network → profile/peek."
            )
        if not response.ok:
            detail = _safe_json(response).get("message") or response.text[:200]
            raise SiliconFlowApiError(f"HTTP {response.status_code}: {detail}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise SiliconFlowApiError("unexpected wallet peek response")

        code = payload.get("code")
        if code is not None and int(code) != 20000:
            message = str(payload.get("message") or "SiliconFlow wallet peek failed")
            raise SiliconFlowApiError(message)

        return payload

    def _error_usage(self, period_label: str, message: str) -> AccountUsage:
        return AccountUsage(
            used=0.0,
            limit=None,
            unit="",
            billing_mode="",
            status=UsageStatus.ERROR,
            percent_used=None,
            remaining=None,
            username=self.account.display_username or self.account.organization or "siliconflow",
            period_label=period_label,
            message=message,
            plan=self.account.plan or "wallet",
            organization=self.account.organization,
            provider=self.account.provider,
            label=self.account.label,
        )


def _scaled_amount(value: Any) -> float | None:
    raw = _to_float(value)
    if raw is None:
        return None
    return raw / AMOUNT_SCALE


def _compute_status(
    used: float,
    limit: float | None,
    account: AccountConfig,
    remaining: float | None = None,
) -> tuple[UsageStatus, float | None, float | None]:
    if limit is None or limit < 0:
        return UsageStatus.UNKNOWN, None, None
    if limit == 0:
        if remaining is not None and remaining > 0:
            return UsageStatus.OK, 0.0, remaining
        return UsageStatus.EXCEEDED, 100.0, 0.0

    percent = min((used / limit) * 100, 999.9)
    if remaining is None:
        remaining = max(limit - used, 0.0)

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
