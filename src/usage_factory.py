from __future__ import annotations

from typing import Protocol

from config import AccountConfig
from cursor_api import CursorClient
from github_api import GitHubCopilotClient
from usage_types import AccountUsage


class UsageClient(Protocol):
    def fetch_usage(self) -> AccountUsage: ...


def create_usage_client(account: AccountConfig) -> UsageClient:
    if account.provider == "github_copilot":
        return GitHubCopilotClient(account)
    if account.provider == "cursor":
        return CursorClient(account)
    raise ValueError(f"Unsupported provider: {account.provider}")
