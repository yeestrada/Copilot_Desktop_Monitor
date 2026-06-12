from __future__ import annotations

import platform
import sys
import tkinter as tk
from pathlib import Path

from app_paths import is_frozen
from config import ROOT_DIR


def system_name() -> str:
    return platform.system().lower()


def is_windows() -> bool:
    return system_name() == "windows"


def is_macos() -> bool:
    return system_name() == "darwin"


def is_linux() -> bool:
    return system_name() == "linux"


def format_period_date(value: str) -> str:
    text = value.strip()
    if not text:
        return ""

    if "T" in text:
        return text.split("T", 1)[0]

    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]

    if "/" in text:
        month, year = (part.strip() for part in text.split("/", 1))
        if len(year) == 4 and len(month) <= 2:
            return f"{year}-{month.zfill(2)}-01"

    return text


def ui_font(size: int = 10, bold: bool = False) -> tuple[str, int] | tuple[str, int, str]:
    if is_windows():
        family = "Segoe UI"
    elif is_macos():
        family = "SF Pro Text"
    else:
        family = "DejaVu Sans"

    if bold:
        return (family, size, "bold")
    return (family, size)


def apply_window_attributes(window: tk.Tk, always_on_top: bool, opacity: float) -> None:
    if always_on_top:
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass

    if opacity < 1.0:
        try:
            window.attributes("-alpha", opacity)
        except tk.TclError:
            pass


def resolve_python_executable() -> Path:
    if is_frozen():
        return Path(sys.executable)
    venv_windows = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    venv_unix = ROOT_DIR / ".venv" / "bin" / "python"

    if venv_windows.exists():
        return venv_windows
    if venv_unix.exists():
        return venv_unix
    return Path(sys.executable)


def launch_command() -> list[str]:
    if is_frozen():
        return [str(Path(sys.executable).resolve())]
    python = resolve_python_executable()
    main_script = ROOT_DIR / "src" / "main.py"
    return [str(python.resolve()), str(main_script.resolve())]
