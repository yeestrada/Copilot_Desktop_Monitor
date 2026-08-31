from __future__ import annotations

import subprocess
import sys
import webbrowser


def open_url(url: str) -> None:
    """Open a URL in the default browser (reliable on Windows frozen exe)."""
    target = url.strip()
    if not target:
        raise RuntimeError("URL is empty")

    try:
        if webbrowser.open(target, new=2):
            return
    except Exception:
        pass

    if sys.platform == "win32":
        # Fallback when webbrowser.register() fails inside PyInstaller.
        subprocess.Popen(
            ["cmd", "/c", "start", "", target],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return

    if not webbrowser.open(target, new=2):
        raise RuntimeError(f"Could not open browser for {target}")
