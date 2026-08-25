from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app_paths import ROOT_DIR, bundle_dir, is_frozen

CONFIG_PATH = ROOT_DIR / "config.json"
EXAMPLE_CONFIG_PATH = ROOT_DIR / "config.example.json"
if is_frozen() and not EXAMPLE_CONFIG_PATH.exists():
    EXAMPLE_CONFIG_PATH = bundle_dir() / "config.example.json"

PLAN_LIMITS_PREMIUM = {
    "free": 50,
    "pro": 300,
    "pro+": 1500,
    "max": 5000,
    "student": 300,
    "business": 500,
    "enterprise": 1000,
}

PLAN_LIMITS_CREDITS = {
    "free": 0.0,
    "pro": 10.0,
    "pro+": 39.0,
    "max": 100.0,
    "student": 10.0,
    "business": 19.0,
    "enterprise": 39.0,
}

PROVIDERS = {"github_copilot", "cursor", "openai"}

DEFAULT_ACCOUNT: dict[str, Any] = {
    "id": "",
    "label": "",
    "provider": "github_copilot",
    "enabled": True,
    "github_username": "",
    "token": "",
    "account_type": "user",
    "organization": "",
    "enterprise": "",
    "plan": "pro",
    "billing_mode": "auto",
    "data_source": "auto",
    "monthly_limit": None,
    "session_token": "",
    "api_key": "",
    "cursor_auth_mode": "auto",
    "api_base_url": "https://api2.cursor.sh",
    "admin_api_base_url": "https://api.cursor.com",
    "cursor_email": "",
    "included_model_key": "gpt-4",
    "widget": {
        "enabled": True,
        "always_on_top": True,
        "opacity": 0.92,
        "position": {"x": 50, "y": 50},
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "refresh_interval_seconds": 300,
    "thresholds": {
        "warning_percent": 75,
        "critical_percent": 90,
    },
    "widget": {
        "always_on_top": True,
        "opacity": 0.92,
    },
    "autostart": {
        "enabled": True,
    },
    "accounts": [],
}


@dataclass
class Thresholds:
    warning_percent: float = 75.0
    critical_percent: float = 90.0


@dataclass
class WidgetSettings:
    enabled: bool = True
    always_on_top: bool = True
    opacity: float = 0.92
    position_x: int = 50
    position_y: int = 50


@dataclass
class AutostartSettings:
    enabled: bool = True


@dataclass
class AccountConfig:
    id: str
    label: str
    provider: str
    enabled: bool = True
    github_username: str = ""
    token: str = ""
    account_type: str = "user"
    organization: str = ""
    enterprise: str = ""
    plan: str = "pro"
    billing_mode: str = "auto"
    data_source: str = "auto"
    monthly_limit: float | None = None
    session_token: str = ""
    api_key: str = ""
    cursor_auth_mode: str = "auto"
    api_base_url: str = "https://api2.cursor.sh"
    admin_api_base_url: str = "https://api.cursor.com"
    cursor_email: str = ""
    included_model_key: str = "gpt-4"
    thresholds: Thresholds = field(default_factory=Thresholds)
    widget: WidgetSettings = field(default_factory=WidgetSettings)

    @property
    def display_title(self) -> str:
        if self.label:
            return self.label
        if self.provider == "cursor":
            return "Cursor"
        if self.provider == "openai":
            return "OpenAI"
        return "GitHub Copilot"

    @property
    def display_username(self) -> str:
        if self.provider == "cursor":
            email = self.cursor_email.strip()
            if email and "@" in email:
                return email.split("@", 1)[0]
            return email
        if self.provider == "openai":
            return self.organization.strip()
        return self.github_username

    def resolve_monthly_limit(self, billing_mode: str, api_limit: float | None) -> float | None:
        if self.monthly_limit is not None:
            return float(self.monthly_limit)
        if api_limit is not None and api_limit > 0:
            return float(api_limit)
        plan = self.plan.lower()
        if billing_mode == "ai_credits":
            return PLAN_LIMITS_CREDITS.get(plan)
        return float(PLAN_LIMITS_PREMIUM.get(plan, 0)) or None

    def validate(self) -> list[str]:
        errors: list[str] = []
        prefix = f"Account '{self.id or self.label or '?'}'"

        if not self.id:
            errors.append(f"{prefix}: missing id")
        if not self.label:
            errors.append(f"{prefix}: missing label")
        if self.provider not in PROVIDERS:
            errors.append(f"{prefix}: provider must be one of {', '.join(sorted(PROVIDERS))}")

        if self.provider == "github_copilot":
            if not self.github_username:
                errors.append(f"{prefix}: missing github_username")
            if not self.token:
                errors.append(f"{prefix}: missing token")
            if self.account_type not in {"user", "organization", "enterprise"}:
                errors.append(f"{prefix}: account_type must be user, organization, or enterprise")
            if self.account_type == "organization" and not self.organization:
                errors.append(f"{prefix}: organization is required for organization accounts")
            if self.account_type == "enterprise" and not self.enterprise:
                errors.append(f"{prefix}: enterprise is required for enterprise accounts")
            if self.billing_mode not in {"auto", "premium_requests", "ai_credits"}:
                errors.append(f"{prefix}: billing_mode must be auto, premium_requests, or ai_credits")
            if self.data_source not in {"auto", "copilot_internal", "billing_api"}:
                errors.append(f"{prefix}: data_source must be auto, copilot_internal, or billing_api")

        if self.provider == "cursor":
            if self.cursor_auth_mode not in {"auto", "session", "admin_api"}:
                errors.append(f"{prefix}: cursor_auth_mode must be auto, session, or admin_api")
            if self.cursor_auth_mode == "admin_api":
                if not self.api_key:
                    errors.append(f"{prefix}: missing api_key for Cursor admin API")
                if not self.cursor_email:
                    errors.append(f"{prefix}: missing cursor_email for Cursor admin API")
            elif not self.api_key and not self.session_token:
                errors.append(f"{prefix}: provide api_key and/or session_token for Cursor")

        if self.provider == "openai":
            if not self.api_key:
                errors.append(f"{prefix}: missing api_key (OpenAI Admin API key)")
            try:
                if self.monthly_limit is None or float(self.monthly_limit) <= 0:
                    errors.append(f"{prefix}: monthly_limit (USD budget) must be greater than 0")
            except (TypeError, ValueError):
                errors.append(f"{prefix}: monthly_limit (USD budget) must be a number greater than 0")

        return errors


@dataclass
class MonitorConfig:
    accounts: list[AccountConfig]
    refresh_interval_seconds: int = 300
    thresholds: Thresholds = field(default_factory=Thresholds)
    widget_defaults: WidgetSettings = field(default_factory=WidgetSettings)
    autostart: AutostartSettings = field(default_factory=AutostartSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> "MonitorConfig":
        config_path = path or CONFIG_PATH
        if not config_path.exists():
            if EXAMPLE_CONFIG_PATH.exists():
                data = _read_json(EXAMPLE_CONFIG_PATH)
            else:
                data = deepcopy(DEFAULT_CONFIG)
            _write_json(config_path, data)
            raise FileNotFoundError(
                f"Created {config_path.name}. Edit it with your accounts, then run again."
            )

        raw = _read_json(config_path)
        data = _normalize_config(raw)
        if data != raw:
            _write_json(config_path, data)

        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "MonitorConfig":
        global_thresholds = data.get("thresholds", {})
        global_widget = data.get("widget", {})
        autostart = data.get("autostart", {})

        accounts: list[AccountConfig] = []
        for index, raw_account in enumerate(data.get("accounts", [])):
            merged = deepcopy(DEFAULT_ACCOUNT)
            _deep_merge(merged, raw_account)

            raw_thresholds = raw_account.get("thresholds")
            account_thresholds = raw_thresholds if isinstance(raw_thresholds, dict) else {}
            widget = merged.get("widget", {})
            position = widget.get("position", {})

            account_id = str(merged.get("id", "")).strip() or f"account-{index + 1}"
            label = str(merged.get("label", "")).strip() or account_id

            accounts.append(
                AccountConfig(
                    id=account_id,
                    label=label,
                    provider=str(merged.get("provider", "github_copilot")).strip().lower(),
                    enabled=bool(merged.get("enabled", True)),
                    github_username=str(merged.get("github_username", "")).strip(),
                    token=str(merged.get("token", "")).strip(),
                    account_type=str(merged.get("account_type", "user")).strip().lower(),
                    organization=str(merged.get("organization", "")).strip(),
                    enterprise=str(merged.get("enterprise", "")).strip(),
                    plan=str(merged.get("plan", "pro")).strip().lower(),
                    billing_mode=str(merged.get("billing_mode", "auto")).strip().lower(),
                    data_source=str(merged.get("data_source", "auto")).strip().lower(),
                    monthly_limit=merged.get("monthly_limit"),
                    session_token=str(merged.get("session_token", "")).strip(),
                    api_key=str(merged.get("api_key", "")).strip(),
                    cursor_auth_mode=str(merged.get("cursor_auth_mode", "auto")).strip().lower(),
                    api_base_url=str(merged.get("api_base_url", "https://api2.cursor.sh")).strip().rstrip("/"),
                    admin_api_base_url=str(
                        merged.get("admin_api_base_url", "https://api.cursor.com")
                    ).strip().rstrip("/"),
                    cursor_email=str(merged.get("cursor_email", "")).strip(),
                    included_model_key=str(merged.get("included_model_key", "gpt-4")).strip(),
                    thresholds=Thresholds(
                        warning_percent=float(
                            account_thresholds.get(
                                "warning_percent", global_thresholds.get("warning_percent", 75)
                            )
                        ),
                        critical_percent=float(
                            account_thresholds.get(
                                "critical_percent", global_thresholds.get("critical_percent", 90)
                            )
                        ),
                    ),
                    widget=WidgetSettings(
                        enabled=bool(widget.get("enabled", True)),
                        always_on_top=bool(
                            widget.get("always_on_top", global_widget.get("always_on_top", True))
                        ),
                        opacity=float(widget.get("opacity", global_widget.get("opacity", 0.92))),
                        position_x=int(position.get("x", 50 + index * 30)),
                        position_y=int(position.get("y", 50 + index * 130)),
                    ),
                )
            )

        return cls(
            accounts=accounts,
            refresh_interval_seconds=int(data.get("refresh_interval_seconds", 300)),
            thresholds=Thresholds(
                warning_percent=float(global_thresholds.get("warning_percent", 75)),
                critical_percent=float(global_thresholds.get("critical_percent", 90)),
            ),
            widget_defaults=WidgetSettings(
                always_on_top=bool(global_widget.get("always_on_top", True)),
                opacity=float(global_widget.get("opacity", 0.92)),
            ),
            autostart=AutostartSettings(enabled=bool(autostart.get("enabled", True))),
        )

    def enabled_accounts(self) -> list[AccountConfig]:
        return [account for account in self.accounts if account.enabled and account.widget.enabled]

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.accounts:
            errors.append("No accounts configured. Add at least one entry to accounts[] in config.json")
        for account in self.accounts:
            if account.enabled:
                errors.extend(account.validate())
        return errors

    def save_widget_position(self, account_id: str, x: int, y: int) -> None:
        data = _read_json(CONFIG_PATH)
        for account in data.get("accounts", []):
            if str(account.get("id")) == account_id:
                account.setdefault("widget", {})
                account["widget"].setdefault("position", {})
                account["widget"]["position"]["x"] = x
                account["widget"]["position"]["y"] = y
                break
        _write_json(CONFIG_PATH, data)

    def save_autostart_enabled(self, enabled: bool) -> None:
        data = _read_json(CONFIG_PATH)
        data.setdefault("autostart", {})
        data["autostart"]["enabled"] = enabled
        _write_json(CONFIG_PATH, data)
        self.autostart.enabled = enabled


# Backward-compatible alias
AppConfig = MonitorConfig


def _normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    if "accounts" in data:
        merged = deepcopy(DEFAULT_CONFIG)
        _deep_merge(merged, data)
        return merged

    if not any(data.get(key) for key in ("github_username", "token", "session_token", "api_key")):
        merged = deepcopy(DEFAULT_CONFIG)
        _deep_merge(merged, data)
        return merged

    legacy_widget = data.get("widget", {})
    position = legacy_widget.get("position", {})

    account = deepcopy(DEFAULT_ACCOUNT)
    account.update(
        {
            "id": "default",
            "label": "GitHub Copilot",
            "provider": "github_copilot",
            "enabled": True,
            "github_username": data.get("github_username", ""),
            "token": data.get("token", ""),
            "account_type": data.get("account_type", "user"),
            "organization": data.get("organization", ""),
            "enterprise": data.get("enterprise", ""),
            "plan": data.get("plan", "pro"),
            "billing_mode": data.get("billing_mode", "auto"),
            "data_source": data.get("data_source", "auto"),
            "monthly_limit": data.get("monthly_limit"),
            "widget": {
                "enabled": True,
                "always_on_top": legacy_widget.get("always_on_top", True),
                "opacity": legacy_widget.get("opacity", 0.92),
                "position": {
                    "x": position.get("x", 50),
                    "y": position.get("y", 50),
                },
            },
        }
    )

    normalized = deepcopy(DEFAULT_CONFIG)
    normalized["refresh_interval_seconds"] = data.get("refresh_interval_seconds", 300)
    normalized["thresholds"] = data.get("thresholds", normalized["thresholds"])
    normalized["widget"] = {
        "always_on_top": legacy_widget.get("always_on_top", True),
        "opacity": legacy_widget.get("opacity", 0.92),
    }
    normalized["autostart"] = data.get("autostart", normalized["autostart"])
    normalized["accounts"] = [account]
    return normalized


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
