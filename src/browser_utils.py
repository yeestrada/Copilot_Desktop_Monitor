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


def find_firefox_executable() -> str | None:
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Mozilla Firefox\firefox.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return shutil.which("firefox")

    return shutil.which("firefox")


def open_url_in_firefox(url: str) -> bool:
    firefox = find_firefox_executable()
    if not firefox:
        return False
    target = url.strip()
    if not target:
        return False
    subprocess.Popen(
        [firefox, target],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return True


def open_openai_login_url() -> None:
    """Open OpenAI billing in Firefox when available for readable cookies on Windows."""
    if not open_url_in_firefox(OPENAI_BILLING_URL):
        open_url(OPENAI_BILLING_URL)


def open_siliconflow_login_url() -> None:
    """Open SiliconFlow billing in Firefox when available for readable cookies on Windows."""
    if not open_url_in_firefox(SILICONFLOW_BILLING_URL):
        open_url(SILICONFLOW_BILLING_URL)


def open_claude_login_url() -> None:
    """Open Claude.ai usage settings in Firefox when available for readable cookies."""
    if not open_url_in_firefox(CLAUDE_USAGE_URL):
        open_url(CLAUDE_USAGE_URL)
