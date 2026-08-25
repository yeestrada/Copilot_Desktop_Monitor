from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from config import AccountConfig, MonitorConfig
from provider_icons import get_provider_icon
from usage_types import AccountUsage, UsageStatus
from platform_utils import apply_window_attributes, format_period_date, ui_font

STATUS_COLORS = {
    UsageStatus.OK: ("#0d1117", "#238636", "#3fb950"),
    UsageStatus.WARNING: ("#0d1117", "#9a6700", "#e3b341"),
    UsageStatus.CRITICAL: ("#0d1117", "#bc4c00", "#f0883e"),
    UsageStatus.EXCEEDED: ("#0d1117", "#8b1a1a", "#f85149"),
    UsageStatus.UNKNOWN: ("#0d1117", "#30363d", "#8b949e"),
    UsageStatus.ERROR: ("#0d1117", "#30363d", "#f85149"),
}

PROVIDER_LABELS = {
    "github_copilot": "GitHub Copilot",
    "cursor": "Cursor",
    "openai": "OpenAI",
}

BG_APP = "#0d1117"
BG_CARD = "#161b22"
BG_CARD_BORDER = "#30363d"
FG_PRIMARY = "#f0f6fc"
FG_MUTED = "#8b949e"


class AccountWidget(tk.Toplevel):
    def __init__(
        self,
        root: tk.Tk,
        monitor_config: MonitorConfig,
        account: AccountConfig,
        on_refresh: Callable[[], AccountUsage],
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(root)
        self.monitor_config = monitor_config
        self.account = account
        self.on_refresh = on_refresh
        self.on_close = on_close
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        provider_label = PROVIDER_LABELS.get(account.provider, account.provider)
        self.title(f"{account.display_title} · {provider_label}")
        self.overrideredirect(True)
        apply_window_attributes(self, account.widget.always_on_top, account.widget.opacity)
        self.geometry(f"+{account.widget.position_x}+{account.widget.position_y}")
        self.configure(bg=BG_APP)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui(provider_label)
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._end_drag)
        self.bind("<Escape>", lambda _event: self.on_close())

    def _card(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=BG_CARD,
            padx=10,
            pady=10,
            highlightthickness=1,
            highlightbackground=BG_CARD_BORDER,
            highlightcolor=BG_CARD_BORDER,
        )

    def _build_ui(self, provider_label: str) -> None:
        frame = tk.Frame(
            self,
            bg=BG_APP,
            padx=14,
            pady=12,
            highlightthickness=1,
            highlightbackground=BG_CARD_BORDER,
        )
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg=BG_APP)
        header.pack(fill="x", pady=(0, 8))

        header_left = tk.Frame(header, bg=BG_APP)
        header_left.pack(side="left", fill="both", expand=True)

        self._provider_icon = get_provider_icon(self, self.account.provider)
        if self._provider_icon is not None:
            self.icon_label = tk.Label(
                header_left,
                image=self._provider_icon,
                bg=BG_APP,
            )
            self.icon_label.grid(row=0, column=0, rowspan=2, padx=(0, 8), sticky="n", pady=1)

        self.title_label = tk.Label(
            header_left,
            text=self.account.display_title,
            font=ui_font(11, bold=True),
            fg=FG_PRIMARY,
            bg=BG_APP,
            anchor="w",
        )
        self.title_label.grid(row=0, column=1, sticky="w")

        self.user_label = tk.Label(
            header_left,
            text=f"{provider_label} · @{self.account.display_username or '...'}",
            font=ui_font(9),
            fg=FG_MUTED,
            bg=BG_APP,
            anchor="w",
        )
        self.user_label.grid(row=1, column=1, sticky="w", pady=(2, 0))
        header_left.grid_columnconfigure(1, weight=1)

        header_right = tk.Frame(header, bg=BG_APP)
        header_right.pack(side="right", anchor="ne")

        controls = tk.Frame(header_right, bg=BG_APP)
        controls.pack(anchor="ne")

        self.refresh_btn = tk.Label(
            controls,
            text="↻",
            font=ui_font(12),
            fg=FG_MUTED,
            bg=BG_APP,
            cursor="hand2",
        )
        self.refresh_btn.pack(side="right", padx=(8, 0))
        self.refresh_btn.bind("<Button-1>", lambda _e: self.refresh_now())

        self.close_btn = tk.Label(
            controls,
            text="×",
            font=ui_font(14),
            fg=FG_MUTED,
            bg=BG_APP,
            cursor="hand2",
        )
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda _e: self.on_close())

        status_card = tk.Frame(
            header_right,
            bg=BG_CARD,
            highlightthickness=1,
            highlightbackground=BG_CARD_BORDER,
            highlightcolor=BG_CARD_BORDER,
        )
        status_card.pack(anchor="e", pady=(4, 0))

        self.status_badge = tk.Label(
            status_card,
            text="Loading...",
            font=ui_font(9, bold=True),
            fg="#ffffff",
            bg="#30363d",
            padx=8,
            pady=3,
        )
        self.status_badge.pack()

        content = tk.Frame(frame, bg=BG_APP)
        content.pack(fill="x", pady=(10, 0))

        main_card = self._card(content)
        main_card.pack(side="left", fill="both", expand=True)

        self.usage_label = tk.Label(
            main_card,
            text="Usage: --",
            font=ui_font(16, bold=True),
            fg=FG_PRIMARY,
            bg=BG_CARD,
            anchor="w",
        )
        self.usage_label.pack(fill="x")

        self.used_label = tk.Label(
            main_card,
            text="Used: --",
            font=ui_font(10),
            fg=FG_MUTED,
            bg=BG_CARD,
            anchor="w",
        )
        self.used_label.pack(fill="x", pady=(4, 0))

        self.limit_label = tk.Label(
            main_card,
            text="Monthly Limit: --",
            font=ui_font(10),
            fg=FG_MUTED,
            bg=BG_CARD,
            anchor="w",
        )
        self.limit_label.pack(fill="x", pady=(2, 0))

        self._progress_style = f"Usage.{self.account.id}.Horizontal.TProgressbar"
        self.progress = ttk.Progressbar(
            main_card,
            mode="determinate",
            maximum=100,
            style=self._progress_style,
        )
        self.progress.pack(fill="x", pady=(10, 0))

        details_card = self._card(content)
        details_card.pack(side="right", fill="y", anchor="n", padx=(10, 0))

        detail_style = {
            "font": ui_font(8),
            "fg": FG_MUTED,
            "bg": BG_CARD,
            "anchor": "w",
            "justify": "left",
        }

        self.remaining_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.remaining_detail_label.pack(fill="x", pady=(2, 0))

        self.plan_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.plan_detail_label.pack(fill="x", pady=(2, 0))

        self.org_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.org_detail_label.pack(fill="x", pady=(2, 0))

        self.reset_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.reset_detail_label.pack(fill="x", pady=(2, 0))

        self._configure_progress_style("#238636")

        self.error_label = tk.Label(
            frame,
            text="",
            font=ui_font(8),
            fg="#f85149",
            bg=BG_APP,
            anchor="w",
            wraplength=320,
            justify="left",
        )
        self.error_label.pack(fill="x", pady=(8, 0))

    def _configure_progress_style(self, accent: str) -> None:
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure(
            self._progress_style,
            troughcolor="#21262d",
            background=accent,
            thickness=8,
            borderwidth=0,
        )

    def _set_detail_labels(
        self,
        remaining: str,
        plan: str,
        org: str,
        reset: str,
    ) -> None:
        self.remaining_detail_label.configure(text=remaining)
        self.plan_detail_label.configure(text=plan)
        self.org_detail_label.configure(text=org)
        self.reset_detail_label.configure(text=reset)

    def _clear_details(self) -> None:
        self._set_detail_labels("—", "—", "—", "—")

    def refresh_now(self) -> None:
        usage = self.on_refresh()
        self.update_usage(usage)

    def update_usage(self, usage: AccountUsage) -> None:
        _bg, badge_bg, accent = STATUS_COLORS.get(usage.status, STATUS_COLORS[UsageStatus.UNKNOWN])

        provider_label = PROVIDER_LABELS.get(usage.provider, usage.provider)
        handle = str(usage.username or "").strip() or "..."
        self.user_label.configure(text=f"{provider_label} · @{handle}")
        self.status_badge.configure(text=usage.status_label, bg=badge_bg)

        if usage.status == UsageStatus.ERROR:
            self.usage_label.configure(text="Usage: --", fg=FG_PRIMARY)
            self.used_label.configure(text="Used: --")
            self.limit_label.configure(text="Monthly Limit: --")
            self.progress["value"] = 0
            self._clear_details()
            self.error_label.configure(text=usage.message or "Failed to query the API")
            return

        self.error_label.configure(text="")

        if usage.unit == "USD":
            used_text = f"${usage.used:.2f}"
            limit_text = f"${usage.limit:.2f}" if usage.limit is not None else "N/A"
            remaining_detail = (
                f"${usage.remaining:.2f}" if usage.remaining is not None else "N/A"
            )
        elif usage.unit == "%":
            used_text = f"{usage.used:.1f}%"
            limit_text = "100%"
            remaining_detail = (
                f"{usage.remaining:.1f}%" if usage.remaining is not None else "N/A"
            )
        else:
            used_text = f"{usage.used:,.0f}"
            limit_text = f"{usage.limit:,.0f}" if usage.limit is not None else "N/A"
            remaining_detail = (
                f"{usage.remaining:,.0f}" if usage.remaining is not None else "N/A"
            )

        usage_fg = accent if usage.status != UsageStatus.OK else FG_PRIMARY
        if usage.percent_used is not None:
            self.usage_label.configure(text=f"Usage: {usage.percent_used:.1f}%", fg=usage_fg)
        else:
            self.usage_label.configure(text="Usage: --", fg=FG_PRIMARY)

        self.used_label.configure(text=f"Used: {used_text}")
        self.limit_label.configure(text=f"Monthly Limit: {limit_text}")

        if usage.percent_used is not None:
            self.progress["value"] = min(usage.percent_used, 100)
            self._configure_progress_style(accent)

            fourth_line = "—"
            if usage.organization:
                if usage.provider == "github_copilot":
                    fourth_line = f"Org: {usage.organization}"
                elif usage.provider == "openai":
                    fourth_line = f"Top: {usage.organization}"
                else:
                    fourth_line = f"Mix: {usage.organization}"

            self._set_detail_labels(
                remaining=f"Remaining: {remaining_detail}",
                plan=f"Plan: {usage.plan}" if usage.plan else "—",
                org=fourth_line,
                reset=(
                    f"Reset: {format_period_date(usage.period_label)}"
                    if usage.period_label
                    else "—"
                ),
            )
        else:
            self.progress["value"] = 0
            self._configure_progress_style("#30363d")
            self._set_detail_labels(
                remaining="—",
                plan="—",
                org="—",
                reset="Set monthly_limit in config.json",
            )

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_offset_x = event.x
        self._drag_offset_y = event.y

    def _on_drag(self, event: tk.Event) -> None:
        x = self.winfo_x() + event.x - self._drag_offset_x
        y = self.winfo_y() + event.y - self._drag_offset_y
        self.geometry(f"+{x}+{y}")

    def _end_drag(self, _event: tk.Event) -> None:
        self.monitor_config.save_widget_position(self.account.id, self.winfo_x(), self.winfo_y())


# Backward-compatible alias
CopilotWidget = AccountWidget
