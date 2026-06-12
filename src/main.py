from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

import autostart
from config import AppConfig
from github_api import CopilotUsage, GitHubCopilotClient, UsageStatus
from single_instance import SingleInstanceError, ensure_single_instance, notify_already_running
from widget import CopilotWidget

try:
    import pystray
except ImportError:  # pragma: no cover
    pystray = None


class CopilotMonitorApp:
    def __init__(self) -> None:
        self.config = AppConfig.load()
        errors = self.config.validate()
        if errors:
            raise RuntimeError("\n".join(errors))

        if self.config.autostart.enabled:
            try:
                autostart.ensure_installed()
            except Exception as exc:
                print(f"Could not enable autostart: {exc}")

        self.client = GitHubCopilotClient(self.config)
        self.latest_usage = CopilotUsage(
            used=0,
            limit=None,
            unit="",
            billing_mode="",
            status=UsageStatus.UNKNOWN,
            percent_used=None,
            remaining=None,
            username=self.config.github_username,
            period_label="",
            message="Initializing...",
        )
        self.widget: CopilotWidget | None = None
        self.tray_icon: pystray.Icon | None = None
        self._stop = False

    def fetch_usage(self) -> CopilotUsage:
        self.latest_usage = self.client.fetch_usage()
        return self.latest_usage

    def _schedule_refresh(self) -> None:
        if self._stop or self.widget is None:
            return

        def worker() -> None:
            usage = self.fetch_usage()
            if self.widget is not None:
                self.widget.after(0, lambda: self._apply_usage(usage))

        threading.Thread(target=worker, daemon=True).start()
        interval_ms = max(self.config.refresh_interval_seconds, 30) * 1000
        self.widget.after(interval_ms, self._schedule_refresh)

    def _apply_usage(self, usage: CopilotUsage) -> None:
        if self.widget is not None:
            self.widget.update_usage(usage)
        if self.tray_icon is not None:
            title = "Copilot Monitor"
            if usage.percent_used is not None:
                subtitle = f"{usage.percent_used:.1f}%"
            elif usage.limit is not None:
                if usage.unit == "USD":
                    subtitle = f"{usage.used:.2f}/{usage.limit:.2f} USD"
                else:
                    subtitle = f"{usage.used:.0f}/{usage.limit:.0f} requests"
            else:
                subtitle = usage.status_label
            self.tray_icon.title = f"{title} · {subtitle}"

    def _toggle_autostart(self, _icon: pystray.Icon | None, _item: pystray.MenuItem) -> None:
        enabled = not self.config.autostart.enabled
        self.config.save_autostart_enabled(enabled)
        if enabled:
            message = autostart.install()
        else:
            message = autostart.uninstall()
        print(message)

    def _create_tray_icon(self) -> None:
        if pystray is None:
            return

        image = Image.new("RGB", (64, 64), color="#238636")
        draw = ImageDraw.Draw(image)
        draw.ellipse((8, 8, 56, 56), fill="#0d1117")
        draw.rectangle((24, 20, 40, 44), fill="#238636")

        menu = pystray.Menu(
            pystray.MenuItem("Refresh now", lambda _icon, _item: self._tray_refresh()),
            pystray.MenuItem(
                "Launch at startup",
                self._toggle_autostart,
                checked=lambda _item: self.config.autostart.enabled,
            ),
            pystray.MenuItem("Quit", lambda _icon, _item: self.quit()),
        )
        self.tray_icon = pystray.Icon("copilot_monitor", image, "Copilot Monitor", menu)

    def _tray_refresh(self) -> None:
        usage = self.fetch_usage()
        if self.widget is not None:
            self.widget.after(0, lambda: self._apply_usage(usage))

    def quit(self) -> None:
        self._stop = True
        if self.tray_icon is not None:
            self.tray_icon.stop()
        if self.widget is not None:
            self.widget.destroy()

    def run(self) -> None:
        self.widget = CopilotWidget(
            config=self.config,
            on_refresh=self.fetch_usage,
            on_quit=self.quit,
        )

        self._create_tray_icon()
        if self.tray_icon is not None:
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

        self.widget.after(200, self.widget.refresh_now)
        self.widget.after(1000, self._schedule_refresh)
        self.widget.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GitHub Copilot usage monitor")
    parser.add_argument("--install-autostart", action="store_true", help="Enable launch at startup")
    parser.add_argument("--uninstall-autostart", action="store_true", help="Disable launch at startup")
    parser.add_argument("--autostart-status", action="store_true", help="Show launch-at-startup status")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.install_autostart:
        print(autostart.install())
        try:
            config = AppConfig.load()
            config.save_autostart_enabled(True)
        except FileNotFoundError:
            pass
        return

    if args.uninstall_autostart:
        print(autostart.uninstall())
        try:
            config = AppConfig.load()
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
        app = CopilotMonitorApp()
    except FileNotFoundError as exc:
        print(str(exc))
        sys.exit(1)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)

    app.run()


if __name__ == "__main__":
    main()
