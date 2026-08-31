from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
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
    "siliconflow": "SiliconFlow",
    "claude_code": "Claude Code",
}

BG_APP = "#0d1117"
BG_CARD = "#161b22"
BG_CARD_BORDER = "#30363d"
FG_PRIMARY = "#f0f6fc"
FG_MUTED = "#8b949e"

# Same outer size for every account widget (expanded / collapsed).
WIDGET_WIDTH = 400
WIDGET_HEIGHT = 190
DETAILS_PANEL_WIDTH = 128
HEADER_TEXT_WIDTH = 210
MAIN_TEXT_WIDTH = 210
DETAIL_TEXT_WIDTH = DETAILS_PANEL_WIDTH - 24
ERROR_TEXT_WIDTH = WIDGET_WIDTH - 40


class HoverTooltip:
    """Show full text on hover when a label is truncated."""

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self.text = ""
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text.strip()

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if not self.text:
            return
        self._after_id = self.widget.after(350, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _show(self) -> None:
        self._after_id = None
        if not self.text or self._tip is not None:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            tip,
            text=self.text,
            justify="left",
            background="#1c2128",
            foreground=FG_PRIMARY,
            relief="solid",
            borderwidth=1,
            font=ui_font(8),
            padx=8,
            pady=4,
            wraplength=320,
        )
        label.pack()
        tip.update_idletasks()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip.geometry(f"+{x}+{y}")
        self._tip = tip


def _truncate_to_width(text: str, font: tkfont.Font, max_width: int) -> str:
    value = text if text is not None else ""
    if not value or font.measure(value) <= max_width:
        return value
    ellipsis = "…"
    ellipsis_width = font.measure(ellipsis)
    if ellipsis_width >= max_width:
        return ellipsis
    available = max_width - ellipsis_width
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if font.measure(value[:mid]) <= available:
            low = mid
        else:
            high = mid - 1
    return value[:low].rstrip() + ellipsis


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
        self._collapsed = bool(account.widget.collapsed)

        provider_label = PROVIDER_LABELS.get(account.provider, account.provider)
        self.title(f"{account.display_title} · {provider_label}")
        self.overrideredirect(True)
        apply_window_attributes(self, account.widget.always_on_top, account.widget.opacity)
        self.geometry(f"{WIDGET_WIDTH}x{WIDGET_HEIGHT}+{account.widget.position_x}+{account.widget.position_y}")
        self.configure(bg=BG_APP)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui(provider_label)
        self._apply_collapsed()
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
        self._root_frame = tk.Frame(
            self,
            bg=BG_APP,
            padx=14,
            pady=12,
            highlightthickness=1,
            highlightbackground=BG_CARD_BORDER,
        )
        self._root_frame.pack(fill="both", expand=True)

        self._header = tk.Frame(self._root_frame, bg=BG_APP)
        self._header.pack(fill="x", pady=(0, 8))

        header_left = tk.Frame(self._header, bg=BG_APP)
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
            text="",
            font=ui_font(11, bold=True),
            fg=FG_PRIMARY,
            bg=BG_APP,
            anchor="w",
        )
        self.title_label.grid(row=0, column=1, sticky="w")
        self._title_tip = HoverTooltip(self.title_label)

        self.user_label = tk.Label(
            header_left,
            text="",
            font=ui_font(9),
            fg=FG_MUTED,
            bg=BG_APP,
            anchor="w",
        )
        self.user_label.grid(row=1, column=1, sticky="w", pady=(2, 0))
        self._user_tip = HoverTooltip(self.user_label)
        header_left.grid_columnconfigure(1, weight=1)

        self._set_truncated_label(
            self.title_label,
            self._title_tip,
            self.account.display_title,
            HEADER_TEXT_WIDTH,
        )
        self._set_truncated_label(
            self.user_label,
            self._user_tip,
            f"{provider_label} · @{self.account.display_username or '...'}",
            HEADER_TEXT_WIDTH,
        )

        header_right = tk.Frame(self._header, bg=BG_APP)
        header_right.pack(side="right", anchor="ne")

        controls = tk.Frame(header_right, bg=BG_APP)
        controls.pack(anchor="ne")

        # Rightmost → leftmost: close, refresh, collapse/expand
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

        self.refresh_btn = tk.Label(
            controls,
            text="↻",
            font=ui_font(12),
            fg=FG_MUTED,
            bg=BG_APP,
            cursor="hand2",
        )
        self.refresh_btn.pack(side="right", padx=(0, 8))
        self.refresh_btn.bind("<Button-1>", lambda _e: self.refresh_now())

        self.collapse_btn = tk.Label(
            controls,
            text="▾",
            font=ui_font(11),
            fg=FG_MUTED,
            bg=BG_APP,
            cursor="hand2",
        )
        self.collapse_btn.pack(side="right", padx=(0, 8))
        self.collapse_btn.bind("<Button-1>", lambda _e: self.toggle_collapsed())

        self._status_card = tk.Frame(
            header_right,
            bg=BG_CARD,
            highlightthickness=1,
            highlightbackground=BG_CARD_BORDER,
            highlightcolor=BG_CARD_BORDER,
        )
        self._status_card.pack(anchor="e", pady=(4, 0))

        self.status_badge = tk.Label(
            self._status_card,
            text="Loading...",
            font=ui_font(9, bold=True),
            fg="#ffffff",
            bg="#30363d",
            padx=8,
            pady=3,
            width=10,
        )
        self.status_badge.pack()

        self._body = tk.Frame(self._root_frame, bg=BG_APP)
        self._body.pack(fill="both", expand=True, pady=(10, 0))

        content = tk.Frame(self._body, bg=BG_APP)
        content.pack(fill="both", expand=True)

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
        self._usage_tip = HoverTooltip(self.usage_label)

        self.used_label = tk.Label(
            main_card,
            text="Used: --",
            font=ui_font(10),
            fg=FG_MUTED,
            bg=BG_CARD,
            anchor="w",
        )
        self.used_label.pack(fill="x", pady=(4, 0))
        self._used_tip = HoverTooltip(self.used_label)

        self.limit_label = tk.Label(
            main_card,
            text="Monthly Limit: --",
            font=ui_font(10),
            fg=FG_MUTED,
            bg=BG_CARD,
            anchor="w",
        )
        self.limit_label.pack(fill="x", pady=(2, 0))
        self._limit_tip = HoverTooltip(self.limit_label)

        self._progress_style = f"Usage.{self.account.id}.Horizontal.TProgressbar"
        self.progress = ttk.Progressbar(
            main_card,
            mode="determinate",
            maximum=100,
            style=self._progress_style,
        )
        self.progress.pack(fill="x", pady=(10, 0))

        details_wrap = tk.Frame(content, bg=BG_APP, width=DETAILS_PANEL_WIDTH)
        details_wrap.pack(side="right", fill="y", padx=(10, 0))
        details_wrap.pack_propagate(False)

        details_card = self._card(details_wrap)
        details_card.pack(fill="both", expand=True)

        detail_style = {
            "font": ui_font(8),
            "fg": FG_MUTED,
            "bg": BG_CARD,
            "anchor": "w",
            "justify": "left",
        }

        self.remaining_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.remaining_detail_label.pack(fill="x", pady=(2, 0))
        self._remaining_tip = HoverTooltip(self.remaining_detail_label)

        self.plan_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.plan_detail_label.pack(fill="x", pady=(2, 0))
        self._plan_tip = HoverTooltip(self.plan_detail_label)

        self.org_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.org_detail_label.pack(fill="x", pady=(2, 0))
        self._org_tip = HoverTooltip(self.org_detail_label)

        self.reset_detail_label = tk.Label(details_card, text="—", **detail_style)
        self.reset_detail_label.pack(fill="x", pady=(2, 0))
        self._reset_tip = HoverTooltip(self.reset_detail_label)

        self._configure_progress_style("#238636")

        self.error_label = tk.Label(
            self._body,
            text="",
            font=ui_font(8),
            fg="#f85149",
            bg=BG_APP,
            anchor="w",
            justify="left",
        )
        self.error_label.pack(fill="x", pady=(8, 0))
        self._error_tip = HoverTooltip(self.error_label)

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

    def _set_truncated_label(
        self,
        label: tk.Label,
        tip: HoverTooltip,
        text: str,
        max_width: int,
        **configure_kwargs: object,
    ) -> None:
        full = text if text is not None else ""
        font = tkfont.Font(font=label.cget("font"))
        display = _truncate_to_width(full, font, max_width)
        label.configure(text=display, **configure_kwargs)
        tip.set_text(full if display != full else "")

    def _set_detail_labels(
        self,
        remaining: str,
        plan: str,
        org: str,
        reset: str,
    ) -> None:
        self._set_truncated_label(
            self.remaining_detail_label, self._remaining_tip, remaining, DETAIL_TEXT_WIDTH
        )
        self._set_truncated_label(self.plan_detail_label, self._plan_tip, plan, DETAIL_TEXT_WIDTH)
        self._set_truncated_label(self.org_detail_label, self._org_tip, org, DETAIL_TEXT_WIDTH)
        self._set_truncated_label(self.reset_detail_label, self._reset_tip, reset, DETAIL_TEXT_WIDTH)

    def _clear_details(self) -> None:
        self._set_detail_labels("—", "—", "—", "—")

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self._apply_collapsed()
        self.monitor_config.save_widget_collapsed(self.account.id, self._collapsed)

    def _apply_collapsed(self) -> None:
        if self._collapsed:
            self._body.pack_forget()
            self._header.pack_configure(pady=(0, 0))
            self.collapse_btn.configure(text="▸")
        else:
            self._header.pack_configure(pady=(0, 8))
            if not self._body.winfo_ismapped():
                self._body.pack(fill="both", expand=True, pady=(10, 0))
            self.collapse_btn.configure(text="▾")
        # Keep badge metrics identical in both states.
        self.status_badge.configure(width=10, padx=8, pady=3, font=ui_font(9, bold=True))
        self._sync_geometry()

    def _sync_geometry(self) -> None:
        self.update_idletasks()
        if self._collapsed:
            # Size to the full header so the status card is never clipped/shrunk.
            height = max(self.winfo_reqheight(), self._root_frame.winfo_reqheight(), 1)
        else:
            height = WIDGET_HEIGHT
        if self.winfo_ismapped():
            x, y = self.winfo_x(), self.winfo_y()
        else:
            x, y = self.account.widget.position_x, self.account.widget.position_y
        self.geometry(f"{WIDGET_WIDTH}x{height}+{x}+{y}")
        self.minsize(WIDGET_WIDTH, height)
        self.maxsize(WIDGET_WIDTH, height)

    def refresh_now(self) -> None:
        _, badge_bg, _ = STATUS_COLORS[UsageStatus.UNKNOWN]
        self.status_badge.configure(text="Loading...", bg=badge_bg)
        self._set_truncated_label(self.error_label, self._error_tip, "", ERROR_TEXT_WIDTH)

        def worker() -> None:
            try:
                usage = self.on_refresh()
            except Exception as exc:  # noqa: BLE001
                usage = AccountUsage(
                    used=0.0,
                    limit=None,
                    unit="",
                    billing_mode="",
                    status=UsageStatus.ERROR,
                    percent_used=None,
                    remaining=None,
                    username=self.account.display_username or "...",
                    period_label="",
                    message=str(exc),
                    plan=self.account.plan,
                    organization=self.account.organization,
                    provider=self.account.provider,
                    label=self.account.label,
                )
            self.after(0, lambda: self.update_usage(usage))

        threading.Thread(target=worker, daemon=True).start()

    def update_usage(self, usage: AccountUsage) -> None:
        _bg, badge_bg, accent = STATUS_COLORS.get(usage.status, STATUS_COLORS[UsageStatus.UNKNOWN])

        provider_label = PROVIDER_LABELS.get(usage.provider, usage.provider)
        handle = str(usage.username or "").strip() or "..."
        self._set_truncated_label(
            self.user_label,
            self._user_tip,
            f"{provider_label} · @{handle}",
            HEADER_TEXT_WIDTH,
        )
        self.status_badge.configure(text=usage.status_label, bg=badge_bg)

        if usage.status == UsageStatus.ERROR:
            self._set_truncated_label(
                self.usage_label, self._usage_tip, "Usage: --", MAIN_TEXT_WIDTH, fg=FG_PRIMARY
            )
            self._set_truncated_label(self.used_label, self._used_tip, "Used: --", MAIN_TEXT_WIDTH)
            self._set_truncated_label(
                self.limit_label, self._limit_tip, "Monthly Limit: --", MAIN_TEXT_WIDTH
            )
            self.progress["value"] = 0
            self._clear_details()
            self._set_truncated_label(
                self.error_label,
                self._error_tip,
                usage.message or "Failed to query the API",
                ERROR_TEXT_WIDTH,
            )
            self._sync_geometry()
            return

        self._set_truncated_label(self.error_label, self._error_tip, "", ERROR_TEXT_WIDTH)

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
            self._set_truncated_label(
                self.usage_label,
                self._usage_tip,
                f"Usage: {usage.percent_used:.1f}%",
                MAIN_TEXT_WIDTH,
                fg=usage_fg,
            )
        else:
            self._set_truncated_label(
                self.usage_label, self._usage_tip, "Usage: --", MAIN_TEXT_WIDTH, fg=FG_PRIMARY
            )

        limit_title = (
            "Credit Limit"
            if usage.billing_mode in {"openai_credits", "siliconflow_balance"}
            else "Session Limit"
            if usage.billing_mode == "claude_code_quota"
            else "Monthly Limit"
        )
        used_title = (
            "Session"
            if usage.billing_mode == "claude_code_quota"
            else "Used"
        )
        self._set_truncated_label(
            self.used_label, self._used_tip, f"{used_title}: {used_text}", MAIN_TEXT_WIDTH
        )
        self._set_truncated_label(
            self.limit_label,
            self._limit_tip,
            f"{limit_title}: {limit_text}",
            MAIN_TEXT_WIDTH,
        )

        if usage.percent_used is not None:
            self.progress["value"] = min(usage.percent_used, 100)
            self._configure_progress_style(accent)

            fourth_line = "—"
            if usage.organization:
                if usage.provider == "github_copilot":
                    fourth_line = f"Org: {usage.organization}"
                elif usage.provider == "openai":
                    if usage.billing_mode == "openai_credits":
                        fourth_line = usage.organization
                    else:
                        fourth_line = f"Top: {usage.organization}"
                elif usage.provider == "siliconflow":
                    fourth_line = usage.organization
                elif usage.provider == "claude_code":
                    fourth_line = usage.organization or "—"
                else:
                    fourth_line = f"Mix: {usage.organization}"

            reset_prefix = (
                "Expires"
                if usage.billing_mode == "openai_credits"
                else "Period"
                if usage.billing_mode == "siliconflow_balance"
                else "Resets"
                if usage.billing_mode == "claude_code_quota"
                else "Reset"
            )
            self._set_detail_labels(
                remaining=f"Remaining: {remaining_detail}",
                plan=f"Plan: {usage.plan}" if usage.plan else "—",
                org=fourth_line,
                reset=(
                    f"{reset_prefix}: {format_period_date(usage.period_label)}"
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
                reset="Set monthly_limit or session_token in config.json",
            )
        self._sync_geometry()

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
