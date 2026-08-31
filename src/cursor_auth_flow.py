from __future__ import annotations

import threading
from typing import Callable

from cursor_auth import (
    CursorAuthError,
    open_cursor_login,
    read_session_token_from_browsers,
    validate_session_token,
)

POLL_INTERVAL_SECONDS = 2.5
AUTH_TIMEOUT_SECONDS = 300.0


class CursorBrowserAuth:
    """Browser-only Cursor sign-in with background cookie polling."""

    def __init__(
        self,
        schedule_ui: Callable[[Callable[[], None]], None],
        on_success: Callable[[str, str], None],
        on_failure: Callable[[str], None],
        on_complete: Callable[[], None],
    ) -> None:
        self._schedule_ui = schedule_ui
        self._on_success = on_success
        self._on_failure = on_failure
        self._on_complete = on_complete
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            open_cursor_login()
        except Exception as exc:  # noqa: BLE001
            self._schedule_ui(lambda: self._finish_failure(f"Could not open browser: {exc}"))
            return

        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()

    def _poll_loop(self) -> None:
        elapsed = 0.0
        while elapsed < AUTH_TIMEOUT_SECONDS and not self._stop.is_set():
            try:
                token, _notes = read_session_token_from_browsers()
                if token:
                    account_label = validate_session_token(token)
                    self._schedule_ui(
                        lambda t=token, label=account_label: self._finish_success(t, label)
                    )
                    return
            except CursorAuthError:
                pass
            except Exception:
                pass

            if self._stop.wait(POLL_INTERVAL_SECONDS):
                return
            elapsed += POLL_INTERVAL_SECONDS

        if not self._stop.is_set():
            self._schedule_ui(
                lambda: self._finish_failure(
                    "Sign-in timed out. Complete login in your browser, then click Sign in again."
                )
            )

    def _finish_success(self, token: str, account_label: str) -> None:
        try:
            self._on_success(token, account_label)
        finally:
            self._on_complete()

    def _finish_failure(self, message: str) -> None:
        try:
            self._on_failure(message)
        finally:
            self._on_complete()
