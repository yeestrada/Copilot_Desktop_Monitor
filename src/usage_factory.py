from __future__ import annotations

from typing import Protocol

from config import AccountConfig
from cursor_api import CursorClient
from github_api import GitHubCopilotClient
from openai_api import OpenAIClient
from siliconflow_api import SiliconFlowClient
from claude_code_api import ClaudeCodeClient
from usage_types import AccountUsage


class UsageClient(Protocol):
    def fetch_usage(self) -> AccountUsage: ...


def create_usage_client(account: AccountConfig) -> UsageClient:
    if account.provider == "github_copilot":
        return GitHubCopilotClient(account)
    if account.provider == "cursor":
        return CursorClient(account)
    if account.provider == "openai":
        return OpenAIClient(account)
    if account.provider == "siliconflow":
        return SiliconFlowClient(account)
    if account.provider == "claude_code":
        return ClaudeCodeClient(account)
    raise ValueError(f"Unsupported provider: {account.provider}")
