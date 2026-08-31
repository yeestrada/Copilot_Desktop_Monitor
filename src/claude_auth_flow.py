from __future__ import annotations

import threading
from typing import Callable

from claude_auth import (
    ClaudeAuthError,
    open_claude_login,
    read_session_from_browsers,
    validate_session,
)

POLL_INTERVAL_SECONDS = 2.5
AUTH_TIMEOUT_SECONDS = 300.0


class ClaudeBrowserAuth:
    """Browser Claude.ai sign-in with background Firefox cookie polling."""

    def __init__(
        self,
        schedule_ui: Callable[[Callable[[], None]], None],
        on_success: Callable[[str, str, str], None],
        on_failure: Callable[[str], None],
        on_complete: Callable[[], None],
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._schedule_ui = schedule_ui
        self._on_success = on_success
        self._on_failure = on_failure
        self._on_complete = on_complete
        self._on_progress = on_progress
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            open_claude_login()
        except Exception as exc:  # noqa: BLE001
            self._schedule_ui(lambda: self._finish_failure(f"Could not open browser: {exc}"))
            return

        self._schedule_ui(
            lambda: self._notify_progress(
                "Firefox opened claude.ai Usage. Sign in and wait; "
                "the monitor will detect the session automatically."
            )
        )
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()

    def _poll_loop(self) -> None:
        elapsed = 0.0
        last_message = ""
        while elapsed < AUTH_TIMEOUT_SECONDS and not self._stop.is_set():
            try:
                cookie_header, org_id, notes = read_session_from_browsers()
                if cookie_header and org_id:
                    account_label = validate_session(cookie_header, org_id)
                    self._schedule_ui(
                        lambda c=cookie_header, o=org_id, label=account_label: (
                            self._finish_success(c, o, label)
                        )
                    )
                    return

                if notes:
                    message = notes[-1]
                    if message != last_message:
                        last_message = message
                        self._schedule_ui(lambda msg=message: self._notify_progress(msg))
            except ClaudeAuthError as exc:
                self._schedule_ui(lambda msg=str(exc): self._notify_progress(msg))
            except Exception as exc:  # noqa: BLE001
                self._schedule_ui(lambda msg=str(exc): self._notify_progress(msg))

            if self._stop.wait(POLL_INTERVAL_SECONDS):
                return
            elapsed += POLL_INTERVAL_SECONDS

        if not self._stop.is_set():
            self._schedule_ui(
                lambda: self._finish_failure(
                    "Timed out. Use Firefox, sign in to claude.ai "
                    "(Settings → Usage), and try again."
                )
            )

    def _notify_progress(self, message: str) -> None:
        if self._on_progress is not None:
            self._on_progress(message)

    def _finish_success(self, cookie_header: str, org_id: str, account_label: str) -> None:
        try:
            self._on_success(cookie_header, org_id, account_label)
        finally:
            self._on_complete()

    def _finish_failure(self, message: str) -> None:
        try:
            self._on_failure(message)
        finally:
            self._on_complete()
