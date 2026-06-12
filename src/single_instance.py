from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path

from app_paths import app_dir

_lock_handle = None
_lock_path: Path | None = None


class SingleInstanceError(Exception):
    pass


def ensure_single_instance() -> None:
    global _lock_handle, _lock_path

    _lock_path = app_dir() / ".copilot-monitor.lock"
    _lock_path.parent.mkdir(parents=True, exist_ok=True)

    handle = open(_lock_path, "a+", encoding="utf-8")

    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise SingleInstanceError("Usage Monitor is already running.") from exc

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()

    _lock_handle = handle
    atexit.register(release_single_instance)


def release_single_instance() -> None:
    global _lock_handle, _lock_path

    if _lock_handle is None:
        return

    try:
        if sys.platform == "win32":
            import msvcrt

            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        _lock_handle.close()
        _lock_handle = None

    if _lock_path is not None and _lock_path.exists():
        try:
            _lock_path.unlink()
        except OSError:
            pass


def notify_already_running() -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(
            "Usage Monitor",
            "Usage Monitor is already running.",
            parent=root,
        )
        root.destroy()
    except Exception:
        print("Usage Monitor is already running.", file=sys.stderr)
