from __future__ import annotations

import argparse
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

import autostart
from config import AccountConfig, MonitorConfig
from single_instance import SingleInstanceError, ensure_single_instance, notify_already_running
from usage_factory import UsageClient, create_usage_client
from usage_types import AccountUsage, UsageStatus
from widget import AccountWidget

try:
    import pystray
except ImportError:  # pragma: no cover
    pystray = None


@dataclass
class AccountInstance:
    account: AccountConfig
    client: UsageClient
    widget: AccountWidget | None = None
    latest_usage: AccountUsage = field(default_factory=lambda: AccountUsage(
        used=0,
        limit=None,
        unit="",
        billing_mode="",
        status=UsageStatus.UNKNOWN,
        percent_used=None,
        remaining=None,
        username="",
        period_label="",
        message="Initializing...",
    ))


class UsageMonitorApp:
    def __init__(self) -> None:
        self.config = MonitorConfig.load()
        errors = self.config.validate()
        if errors:
            raise RuntimeError("\n".join(errors))

        if self.config.autostart.enabled:
            try:
                autostart.ensure_installed()
            except Exception as exc:
                print(f"Could not enable autostart: {exc}")

        self.root = tk.Tk()
        self.root.withdraw()

        self.instances: list[AccountInstance] = [
            AccountInstance(account=account, client=create_usage_client(account))
            for account in self.config.enabled_accounts()
        ]
        if not self.instances:
            raise RuntimeError("No enabled accounts with widgets. Check accounts[] in config.json")

        self.tray_icon: pystray.Icon | None = None
        self._stop = False

    def fetch_usage(self, instance: AccountInstance) -> AccountUsage:
        usage = instance.client.fetch_usage()
        instance.latest_usage = usage
        return usage

    def _schedule_refresh(self) -> None:
        if self._stop:
            return

        for instance in self.instances:
            if instance.widget is None:
                continue

            def worker(target: AccountInstance = instance) -> None:
                usage = self.fetch_usage(target)
                if target.widget is not None:
                    target.widget.after(0, lambda: self._apply_usage(target, usage))

            threading.Thread(target=worker, daemon=True).start()

        interval_ms = max(self.config.refresh_interval_seconds, 30) * 1000
        self.root.after(interval_ms, self._schedule_refresh)

    def _apply_usage(self, instance: AccountInstance, usage: AccountUsage) -> None:
        if instance.widget is not None:
            instance.widget.update_usage(usage)
        self._update_tray_title()

    def _usage_subtitle(self, usage: AccountUsage) -> str:
        if usage.percent_used is not None:
            return f"{usage.percent_used:.1f}%"
        if usage.limit is not None:
            if usage.unit == "USD":
                return f"{usage.used:.2f}/{usage.limit:.2f} USD"
            return f"{usage.used:.0f}/{usage.limit:.0f} {usage.unit or 'requests'}"
        return usage.status_label

    def _update_tray_title(self) -> None:
        if self.tray_icon is None:
            return

        parts: list[str] = []
        for instance in self.instances:
            usage = instance.latest_usage
            parts.append(f"{instance.account.label}: {self._usage_subtitle(usage)}")

        title = "Usage Monitor"
        if parts:
            self.tray_icon.title = f"{title} · {' | '.join(parts)}"
        else:
            self.tray_icon.title = title

    def _toggle_autostart(self, _icon: pystray.Icon | None, _item: pystray.MenuItem) -> None:
        enabled = not self.config.autostart.enabled
        self.config.save_autostart_enabled(enabled)
        if enabled:
            message = autostart.install()
        else:
            message = autostart.uninstall()
        print(message)

    def _refresh_account(self, instance: AccountInstance) -> None:
        if instance.widget is not None:
            instance.widget.refresh_now()
            return

        def worker() -> None:
            self.fetch_usage(instance)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_all(self) -> None:
        for instance in self.instances:
            self._refresh_account(instance)

    def _tray_refresh_handler(self, instance: AccountInstance):
        def handler(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            self._refresh_account(instance)

        return handler

    def _create_tray_icon(self) -> None:
        if pystray is None:
            return

        image = Image.new("RGB", (64, 64), color="#238636")
        draw = ImageDraw.Draw(image)
        draw.ellipse((8, 8, 56, 56), fill="#0d1117")
        draw.rectangle((24, 20, 40, 44), fill="#238636")

        refresh_items = [
            pystray.MenuItem(
                f"Refresh {instance.account.label}",
                self._tray_refresh_handler(instance),
            )
            for instance in self.instances
        ]

        menu = pystray.Menu(
            pystray.MenuItem("Refresh all", lambda _icon, _item: self._refresh_all()),
            pystray.Menu.SEPARATOR,
            *refresh_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Launch at startup",
                self._toggle_autostart,
                checked=lambda _item: self.config.autostart.enabled,
            ),
            pystray.MenuItem("Quit", lambda _icon, _item: self.quit()),
        )
        self.tray_icon = pystray.Icon("usage_monitor", image, "Usage Monitor", menu)

    def _close_account_widget(self, instance: AccountInstance) -> None:
        if instance.widget is not None:
            instance.widget.destroy()
            instance.widget = None

        active_widgets = [item for item in self.instances if item.widget is not None]
        if not active_widgets:
            self.quit()

    def quit(self) -> None:
        self._stop = True
        if self.tray_icon is not None:
            self.tray_icon.stop()
        for instance in self.instances:
            if instance.widget is not None:
                instance.widget.destroy()
        self.root.destroy()

    def run(self) -> None:
        for instance in self.instances:
            instance.widget = AccountWidget(
                root=self.root,
                monitor_config=self.config,
                account=instance.account,
                on_refresh=lambda target=instance: self.fetch_usage(target),
                on_close=lambda target=instance: self._close_account_widget(target),
            )
            usage = instance.latest_usage
            if not str(usage.username or "").strip():
                usage.username = instance.account.display_username
            usage.label = instance.account.label
            usage.provider = instance.account.provider

        self._create_tray_icon()
        if self.tray_icon is not None:
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

        for instance in self.instances:
            if instance.widget is not None:
                instance.widget.after(200, instance.widget.refresh_now)

        self.root.after(1000, self._schedule_refresh)
        self.root.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GitHub Copilot and Cursor usage monitor")
    parser.add_argument("--install-autostart", action="store_true", help="Enable launch at startup")
    parser.add_argument("--uninstall-autostart", action="store_true", help="Disable launch at startup")
    parser.add_argument("--autostart-status", action="store_true", help="Show launch-at-startup status")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.install_autostart:
        print(autostart.install())
        try:
            config = MonitorConfig.load()
            config.save_autostart_enabled(True)
        except FileNotFoundError:
            pass
        return

    if args.uninstall_autostart:
        print(autostart.uninstall())
        try:
            config = MonitorConfig.load()
            config.save_autostart_enabled(False)
        except FileNotFoundError:
            pass
        return

    if args.autostart_status:
        print(autostart.status_message())
        return

    try:
        ensure_single_instance()
    except SingleInstanceError:
        notify_already_running()
        sys.exit(0)

    try:
        app = UsageMonitorApp()
    except FileNotFoundError as exc:
        print(str(exc))
        sys.exit(1)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)

    app.run()


if __name__ == "__main__":
    main()
