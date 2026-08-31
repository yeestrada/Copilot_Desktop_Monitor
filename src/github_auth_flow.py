from __future__ import annotations

import threading
from typing import Any, Callable

from github_auth import (
    GitHubAuthError,
    GitHubAuthPending,
    normalize_user_code,
    poll_access_token,
    request_device_code,
    validate_github_token,
)


class GitHubBrowserAuth:
    """GitHub OAuth device flow with browser approval and background polling."""

    def __init__(
        self,
        schedule_ui: Callable[[Callable[[], None]], None],
        on_waiting: Callable[[str, str], None],
        on_success: Callable[[str, str], None],
        on_failure: Callable[[str], None],
        on_complete: Callable[[], None],
    ) -> None:
        self._schedule_ui = schedule_ui
        self._on_waiting = on_waiting
        self._on_success = on_success
        self._on_failure = on_failure
        self._on_complete = on_complete
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._device: dict[str, Any] | None = None

    def start(self) -> None:
        try:
            device = request_device_code()
            user_code = normalize_user_code(str(device["user_code"]))
            verification_uri = str(device["verification_uri"])
            self._device = device
        except GitHubAuthError as exc:
            self._schedule_ui(lambda: self._finish_failure(str(exc)))
            return
        except Exception as exc:  # noqa: BLE001
            self._schedule_ui(lambda: self._finish_failure(str(exc)))
            return

        self._schedule_ui(lambda: self._on_waiting(user_code, verification_uri))
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()

    def _poll_loop(self) -> None:
        if self._device is None:
            return

        device_code = str(self._device["device_code"])
        interval = max(float(self._device.get("interval") or 5), 2.0)
        expires_in = float(self._device.get("expires_in") or 900)
        elapsed = 0.0

        while elapsed < expires_in and not self._stop.is_set():
            try:
                token = poll_access_token(device_code)
                username = validate_github_token(token)
                self._schedule_ui(
                    lambda t=token, user=username: self._finish_success(t, user)
                )
                return
            except GitHubAuthPending as exc:
                if str(exc) == "slow_down":
                    interval = min(interval + 2.0, 15.0)
            except GitHubAuthError as exc:
                self._schedule_ui(lambda message=str(exc): self._finish_failure(message))
                return
            except Exception as exc:  # noqa: BLE001
                self._schedule_ui(lambda message=str(exc): self._finish_failure(message))
                return

            if self._stop.wait(interval):
                return
            elapsed += interval

        if not self._stop.is_set():
            self._schedule_ui(
                lambda: self._finish_failure(
                    "Sign-in timed out. Approve access in your browser, then click Sign in again."
                )
            )

    def _finish_success(self, token: str, username: str) -> None:
        try:
            self._on_success(token, username)
        finally:
            self._on_complete()

    def _finish_failure(self, message: str) -> None:
        try:
            self._on_failure(message)
        finally:
            self._on_complete()
