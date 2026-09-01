from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

OPENAI_BILLING_URL = "https://platform.openai.com/settings/organization/billing/overview"
SILICONFLOW_BILLING_URL = "https://cloud.siliconflow.com/me/expensebill"
CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
CURSOR_LOGIN_URL = "https://cursor.com/login"


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


def find_edge_executable() -> str | None:
    if sys.platform != "win32":
        return shutil.which("microsoft-edge") or shutil.which("msedge")

    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return shutil.which("msedge")


def open_url_in_edge(url: str) -> bool:
    edge = find_edge_executable()
    if not edge:
        return False
    target = url.strip()
    if not target:
        return False
    subprocess.Popen(
        [edge, target],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return True


def preferred_auth_browser_name() -> str:
    """Human-readable browser used for Sign in (Edge on Windows when installed)."""
    if sys.platform == "win32" and find_edge_executable():
        return "Microsoft Edge"
    return "your browser"


def auth_browser_open_message(context: str) -> str:
    browser = preferred_auth_browser_name()
    return f"{browser} will open {context}. Sign in; the monitor will connect automatically."


def open_url_for_auth(url: str) -> None:
    """Open sign-in URL in Edge on Windows when available; else default browser."""
    if sys.platform == "win32" and open_url_in_edge(url):
        return
    open_url(url)


def open_openai_login_url() -> None:
    open_url_for_auth(OPENAI_BILLING_URL)


def open_siliconflow_login_url() -> None:
    open_url_for_auth(SILICONFLOW_BILLING_URL)


def open_claude_login_url() -> None:
    open_url_for_auth(CLAUDE_USAGE_URL)


def open_cursor_login_url() -> None:
    open_url_for_auth(CURSOR_LOGIN_URL)
