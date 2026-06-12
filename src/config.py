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

DEFAULT_CONFIG: dict[str, Any] = {
    "github_username": "",
    "token": "",
    "account_type": "user",
    "organization": "",
    "enterprise": "",
    "plan": "pro",
    "billing_mode": "auto",
    "data_source": "auto",
    "monthly_limit": None,
    "refresh_interval_seconds": 300,
    "thresholds": {
        "warning_percent": 75,
        "critical_percent": 90,
    },
    "autostart": {
        "enabled": True,
    },
    "widget": {
        "always_on_top": True,
        "opacity": 0.92,
        "position": {"x": 50, "y": 50},
    },
}


@dataclass
class Thresholds:
    warning_percent: float = 75.0
    critical_percent: float = 90.0


@dataclass
class WidgetSettings:
    always_on_top: bool = True
    opacity: float = 0.92
    position_x: int = 50
    position_y: int = 50


@dataclass
class AutostartSettings:
    enabled: bool = True


@dataclass
class AppConfig:
    github_username: str
    token: str
    account_type: str = "user"
    organization: str = ""
    enterprise: str = ""
    plan: str = "pro"
    billing_mode: str = "auto"
    data_source: str = "auto"
    monthly_limit: float | None = None
    refresh_interval_seconds: int = 300
    thresholds: Thresholds = field(default_factory=Thresholds)
    autostart: AutostartSettings = field(default_factory=AutostartSettings)
    widget: WidgetSettings = field(default_factory=WidgetSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        config_path = path or CONFIG_PATH
        if not config_path.exists():
            if EXAMPLE_CONFIG_PATH.exists():
                data = _read_json(EXAMPLE_CONFIG_PATH)
            else:
                data = deepcopy(DEFAULT_CONFIG)
            _write_json(config_path, data)
            raise FileNotFoundError(
                f"Created {config_path.name}. Edit it with your username and token, then run again."
            )

        data = _read_json(config_path)
        merged = deepcopy(DEFAULT_CONFIG)
        _deep_merge(merged, data)

        widget = merged.get("widget", {})
        position = widget.get("position", {})
        autostart = merged.get("autostart", {})

        return cls(
            github_username=str(merged.get("github_username", "")).strip(),
            token=str(merged.get("token", "")).strip(),
            account_type=str(merged.get("account_type", "user")).strip().lower(),
            organization=str(merged.get("organization", "")).strip(),
            enterprise=str(merged.get("enterprise", "")).strip(),
            plan=str(merged.get("plan", "pro")).strip().lower(),
            billing_mode=str(merged.get("billing_mode", "auto")).strip().lower(),
            data_source=str(merged.get("data_source", "auto")).strip().lower(),
            monthly_limit=merged.get("monthly_limit"),
            refresh_interval_seconds=int(merged.get("refresh_interval_seconds", 300)),
            thresholds=Thresholds(
                warning_percent=float(merged["thresholds"]["warning_percent"]),
                critical_percent=float(merged["thresholds"]["critical_percent"]),
            ),
            autostart=AutostartSettings(
                enabled=bool(autostart.get("enabled", True)),
            ),
            widget=WidgetSettings(
                always_on_top=bool(widget.get("always_on_top", True)),
                opacity=float(widget.get("opacity", 0.92)),
                position_x=int(position.get("x", 50)),
                position_y=int(position.get("y", 50)),
            ),
        )

    def save_widget_position(self, x: int, y: int) -> None:
        data = _read_json(CONFIG_PATH)
        data.setdefault("widget", {})
        data["widget"].setdefault("position", {})
        data["widget"]["position"]["x"] = x
        data["widget"]["position"]["y"] = y
        _write_json(CONFIG_PATH, data)

    def save_autostart_enabled(self, enabled: bool) -> None:
        data = _read_json(CONFIG_PATH)
        data.setdefault("autostart", {})
        data["autostart"]["enabled"] = enabled
        _write_json(CONFIG_PATH, data)
        self.autostart.enabled = enabled

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.github_username:
            errors.append("Missing github_username in config.json")
        if not self.token:
            errors.append("Missing token in config.json")
        if self.account_type not in {"user", "organization", "enterprise"}:
            errors.append("account_type must be 'user', 'organization', or 'enterprise'")
        if self.account_type == "organization" and not self.organization:
            errors.append("organization is required when account_type is 'organization'")
        if self.account_type == "enterprise" and not self.enterprise:
            errors.append("enterprise is required when account_type is 'enterprise'")
        if self.billing_mode not in {"auto", "premium_requests", "ai_credits"}:
            errors.append("billing_mode must be auto, premium_requests, or ai_credits")
        if self.data_source not in {"auto", "copilot_internal", "billing_api"}:
            errors.append("data_source must be auto, copilot_internal, or billing_api")
        return errors

    def resolve_monthly_limit(self, billing_mode: str, api_limit: float | None) -> float | None:
        if self.monthly_limit is not None:
            return float(self.monthly_limit)
        if api_limit is not None and api_limit > 0:
            return float(api_limit)
        plan = self.plan.lower()
        if billing_mode == "ai_credits":
            return PLAN_LIMITS_CREDITS.get(plan)
        return float(PLAN_LIMITS_PREMIUM.get(plan, 0)) or None


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
