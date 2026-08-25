from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from _config_loader import load_openai_account  # noqa: E402
from openai_api import OpenAIClient  # noqa: E402
from config import AccountConfig, Thresholds, WidgetSettings  # noqa: E402


def main() -> None:
    raw = load_openai_account()
    account = AccountConfig(
        id=str(raw.get("id", "openai-test")),
        label=str(raw.get("label", "OpenAI API")),
        provider="openai",
        api_key=str(raw.get("api_key", "")).strip(),
        monthly_limit=float(raw["monthly_limit"]),
        organization=str(raw.get("organization", "")).strip(),
        plan=str(raw.get("plan", "api")).strip() or "api",
        thresholds=Thresholds(),
        widget=WidgetSettings(),
    )

    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    start_time = int(start.timestamp())

    response = requests.get(
        "https://api.openai.com/v1/organization/costs",
        headers={
            "Authorization": f"Bearer {account.api_key}",
            "Content-Type": "application/json",
        },
        params={"start_time": start_time, "bucket_width": "1d", "limit": 35},
        timeout=30,
    )
    print(f"costs HTTP {response.status_code}")
    if not response.ok:
        print(response.text[:400])
        raise SystemExit(1)

    usage = OpenAIClient(account).fetch_usage()
    print(f"status: {usage.status_label}")
    print(f"used: ${usage.used:.4f}")
    print(f"limit: ${usage.limit:.2f}" if usage.limit is not None else "limit: N/A")
    print(f"percent: {usage.percent_used:.1f}%" if usage.percent_used is not None else "percent: N/A")
    print(f"remaining: ${usage.remaining:.2f}" if usage.remaining is not None else "remaining: N/A")
    print(f"top: {usage.organization or '(none)'}")
    print(f"reset: {usage.period_label}")
    if usage.message:
        print(f"message: {usage.message}")


if __name__ == "__main__":
    main()
