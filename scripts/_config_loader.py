from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


def load_raw_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or CONFIG_PATH
    with config_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def first_account_by_provider(raw: dict[str, Any], provider: str) -> dict[str, Any]:
    accounts = raw.get("accounts")
    if isinstance(accounts, list):
        for account in accounts:
            if not isinstance(account, dict):
                continue
            if account.get("provider", "github_copilot") == provider:
                return dict(account)

    if provider == "github_copilot":
        return {
            "github_username": raw.get("github_username", ""),
            "token": raw.get("token", ""),
            "account_type": raw.get("account_type", "user"),
            "organization": raw.get("organization", ""),
            "enterprise": raw.get("enterprise", ""),
            "plan": raw.get("plan", "pro"),
        }

    return {}


def first_github_account(raw: dict[str, Any]) -> dict[str, Any]:
    return first_account_by_provider(raw, "github_copilot")


def first_cursor_account(raw: dict[str, Any]) -> dict[str, Any]:
    return first_account_by_provider(raw, "cursor")


def first_openai_account(raw: dict[str, Any]) -> dict[str, Any]:
    return first_account_by_provider(raw, "openai")


def first_siliconflow_account(raw: dict[str, Any]) -> dict[str, Any]:
    return first_account_by_provider(raw, "siliconflow")


def load_github_account(path: Path | None = None) -> dict[str, Any]:
    raw = load_raw_config(path)
    account = first_github_account(raw)
    missing = [key for key in ("github_username", "token") if not str(account.get(key, "")).strip()]
    if missing:
        raise ValueError(f"Missing in config.json: {', '.join(missing)}")
    return account


def load_cursor_account(path: Path | None = None) -> dict[str, Any]:
    raw = load_raw_config(path)
    account = first_cursor_account(raw)
    if not account:
        raise ValueError("No Cursor account found in config.json accounts[]")
    if not str(account.get("session_token", "")).strip() and not str(account.get("api_key", "")).strip():
        raise ValueError("Missing session_token or api_key for Cursor in config.json")
    return account


def load_openai_account(path: Path | None = None) -> dict[str, Any]:
    raw = load_raw_config(path)
    account = first_openai_account(raw)
    if not account:
        raise ValueError("No OpenAI account found in config.json accounts[]")
    if not str(account.get("session_token", "")).strip():
        raise ValueError("Missing session_token for OpenAI in config.json")
    return account


def load_siliconflow_account(path: Path | None = None) -> dict[str, Any]:
    raw = load_raw_config(path)
    account = first_siliconflow_account(raw)
    if not account:
        raise ValueError("No SiliconFlow account found in config.json accounts[]")
    if not str(account.get("session_token", "")).strip():
        raise ValueError("Missing session_token (Cookie header) for SiliconFlow in config.json")
    if not str(account.get("organization", "")).strip():
        raise ValueError("Missing organization (x-subject-id) for SiliconFlow in config.json")
    return account
