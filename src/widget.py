from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from config import AppConfig
from github_api import CopilotUsage, UsageStatus
from platform_utils import apply_window_attributes, ui_font

STATUS_COLORS = {
    UsageStatus.OK: ("#0d1117", "#238636", "#3fb950"),
    UsageStatus.WARNING: ("#0d1117", "#9a6700", "#d29922"),
    UsageStatus.CRITICAL: ("#0d1117", "#bc4c00", "#f0883e"),
    UsageStatus.EXCEEDED: ("#0d1117", "#8b1a1a", "#f85149"),
    UsageStatus.UNKNOWN: ("#0d1117", "#30363d", "#8b949e"),
    UsageStatus.ERROR: ("#0d1117", "#30363d", "#f85149"),
}

BG_APP = "#0d1117"
BG_CARD = "#161b22"
BG_CARD_BORDER = "#30363d"
FG_PRIMARY = "#f0f6fc"
FG_MUTED = "#8b949e"
FG_SUBTLE = "#6e7681"


class CopilotWidget(tk.Tk):
    def __init__(
        self,
        config: AppConfig,
        on_refresh: Callable[[], CopilotUsage],
        on_quit: Callable[[], None],
    ) -> None:
        super().__init__()
        self.config_data = config
        self.on_refresh = on_refresh
        self.on_quit = on_quit
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        self.title("Copilot Monitor")
        self.overrideredirect(True)
        apply_window_attributes(self, config.widget.always_on_top, config.widget.opacity)
        self.geometry(f"+{config.widget.position_x}+{config.widget.position_y}")
        self.configure(bg=BG_APP)

        self._build_ui()
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._end_drag)
        self.bind("<Escape>", lambda _event: on_quit())

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

    def _build_ui(self) -> None:
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
        header.pack(fill="x")

        self.title_label = tk.Label(
            header,
            text="GitHub Copilot",
            font=ui_font(11, bold=True),
            fg=FG_PRIMARY,
            bg=BG_APP,
        )
        self.title_label.pack(side="left")

        self.refresh_btn = tk.Label(
            header,
            text="↻",
            font=ui_font(12),
            fg=FG_MUTED,
            bg=BG_APP,
            cursor="hand2",
        )
        self.refresh_btn.pack(side="right", padx=(8, 0))
        self.refresh_btn.bind("<Button-1>", lambda _e: self.refresh_now())

        self.close_btn = tk.Label(
            header,
            text="×",
            font=ui_font(14),
            fg=FG_MUTED,
            bg=BG_APP,
            cursor="hand2",
        )
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda _e: self.on_quit())

        user_row = tk.Frame(frame, bg=BG_APP)
        user_row.pack(fill="x", pady=(4, 8))

        self.user_label = tk.Label(
            user_row,
            text="@user",
            font=ui_font(9),
            fg=FG_MUTED,
            bg=BG_APP,
            anchor="w",
        )
        self.user_label.pack(side="left", anchor="w")

        status_card = tk.Frame(
            user_row,
            bg=BG_CARD,
            highlightthickness=1,
            highlightbackground=BG_CARD_BORDER,
            highlightcolor=BG_CARD_BORDER,
        )
        status_card.pack(side="right", anchor="e")

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

        self.limit_label = tk.Label(
            main_card,
            text="Monthly Limit: --",
            font=ui_font(10),
            fg=FG_MUTED,
            bg=BG_CARD,
            anchor="w",
        )
        self.limit_label.pack(fill="x", pady=(4, 0))

        self.progress = ttk.Progressbar(main_card, mode="determinate", maximum=100)
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

        self.used_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.used_detail_label.pack(fill="x", pady=(2, 0))

        self.remaining_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.remaining_detail_label.pack(fill="x", pady=(2, 0))

        self.plan_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.plan_detail_label.pack(fill="x", pady=(2, 0))

        self.org_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.org_detail_label.pack(fill="x", pady=(2, 0))

        self.reset_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.reset_detail_label.pack(fill="x", pady=(2, 0))

        self.progress.configure(style="Copilot.Horizontal.TProgressbar")
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
            "Copilot.Horizontal.TProgressbar",
            troughcolor="#21262d",
            background=accent,
            thickness=8,
            borderwidth=0,
        )

    def _set_detail_labels(
        self,
        used: str,
        remaining: str,
        plan: str,
        org: str,
        reset: str,
    ) -> None:
        self.used_detail_label.configure(text=used)
        self.remaining_detail_label.configure(text=remaining)
        self.plan_detail_label.configure(text=plan)
        self.org_detail_label.configure(text=org)
        self.reset_detail_label.configure(text=reset)

    def _clear_details(self) -> None:
        self._set_detail_labels("—", "—", "—", "—", "—")

    def refresh_now(self) -> None:
        usage = self.on_refresh()
        self.update_usage(usage)

    def update_usage(self, usage: CopilotUsage) -> None:
        bg, badge_bg, accent = STATUS_COLORS.get(usage.status, STATUS_COLORS[UsageStatus.UNKNOWN])

        self.user_label.configure(text=f"@{usage.username}")
        self.status_badge.configure(text=usage.status_label, bg=badge_bg)

        if usage.status == UsageStatus.ERROR:
            self.usage_label.configure(text="Usage: --")
            self.limit_label.configure(text="Monthly Limit: --")
            self.progress["value"] = 0
            self._clear_details()
            self.error_label.configure(text=usage.message or "Failed to query the API")
            return

        self.error_label.configure(text="")

        if usage.unit == "USD":
            used_amount = f"${usage.used:.2f}"
            limit_text = f"${usage.limit:.2f}" if usage.limit is not None else "N/A"
            remaining_detail = f"${usage.remaining:.2f}" if usage.remaining is not None else "N/A"
            unit_label = "USD"
        else:
            used_amount = f"{usage.used:,.0f}"
            limit_text = f"{usage.limit:,.0f}" if usage.limit is not None else "N/A"
            remaining_detail = f"{usage.remaining:,.0f}" if usage.remaining is not None else "N/A"
            unit_label = "tokens"

        if usage.percent_used is not None:
            self.usage_label.configure(text=f"Usage: {usage.percent_used:.1f}%")
        else:
            self.usage_label.configure(text=f"Usage: {used_amount}")

        self.limit_label.configure(text=f"Monthly Limit: {limit_text}")

        if usage.percent_used is not None:
            self.progress["value"] = min(usage.percent_used, 100)
            self._configure_progress_style(accent)

            self._set_detail_labels(
                used=f"{used_amount} {unit_label} used",
                remaining=f"Remaining: {remaining_detail}",
                plan=f"Plan: {usage.plan}" if usage.plan else "—",
                org=f"Org: {usage.organization}" if usage.organization else "—",
                reset=f"Reset: {usage.period_label}" if usage.period_label else "—",
            )
        else:
            self.progress["value"] = 0
            self._configure_progress_style("#30363d")
            self._set_detail_labels(
                used="—",
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
        self.config_data.save_widget_position(self.winfo_x(), self.winfo_y())
