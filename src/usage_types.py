from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UsageStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    EXCEEDED = "exceeded"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass
class AccountUsage:
    used: float
    limit: float | None
    unit: str
    billing_mode: str
    status: UsageStatus
    percent_used: float | None
    remaining: float | None
    username: str
    period_label: str
    message: str = ""
    raw_items_count: int = 0
    plan: str = ""
    organization: str = ""
    provider: str = ""
    label: str = ""
    needs_auth: bool = False

    @property
    def status_label(self) -> str:
        labels = {
            UsageStatus.OK: "Normal",
            UsageStatus.WARNING: "Warning",
            UsageStatus.CRITICAL: "Critical",
            UsageStatus.EXCEEDED: "Limit reached",
            UsageStatus.UNKNOWN: "Unknown",
            UsageStatus.ERROR: "Error",
        }
        return labels.get(self.status, "Unknown")


# Backward-compatible alias
CopilotUsage = AccountUsage
