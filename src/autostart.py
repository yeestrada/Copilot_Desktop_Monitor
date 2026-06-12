from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from config import ROOT_DIR
from platform_utils import is_linux, is_macos, is_windows, launch_command


APP_ID = "com.github.copilot.monitor"
APP_NAME = "Copilot Monitor"
REGISTRY_VALUE_NAME = "CopilotMonitor"


def autostart_target_path() -> Path | None:
    if is_windows():
        return None
    if is_macos():
        return Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"
    if is_linux():
        return Path.home() / ".config" / "autostart" / f"{APP_ID}.desktop"
    return None


def is_installed() -> bool:
    if is_windows():
        return _windows_is_installed()
    target = autostart_target_path()
    return target is not None and target.exists()


def install() -> str:
    command = launch_command()
    if is_windows():
        _windows_install(command)
        return "Autostart enabled on Windows (user registry)."
    if is_macos():
        _macos_install(command)
        return f"Autostart enabled on macOS ({autostart_target_path()})."
    if is_linux():
        _linux_install(command)
        return f"Autostart enabled on Linux ({autostart_target_path()})."
    raise RuntimeError("Unsupported operating system for autostart.")


def uninstall() -> str:
    if is_windows():
        _windows_uninstall()
        return "Autostart disabled on Windows."
    target = autostart_target_path()
    if target is not None and target.exists():
        target.unlink()
    if is_macos():
        _macos_unload()
        return "Autostart disabled on macOS."
    if is_linux():
        return "Autostart disabled on Linux."
    raise RuntimeError("Unsupported operating system for autostart.")


def ensure_installed() -> None:
    if not is_installed():
        install()


def status_message() -> str:
    installed = is_installed()
    platform_label = "Windows" if is_windows() else "macOS" if is_macos() else "Linux" if is_linux() else "Unknown"
    state = "enabled" if installed else "disabled"
    command = " ".join(f'"{part}"' if " " in part else part for part in launch_command())
    return (
        f"Platform: {platform_label}\n"
        f"Status: {state}\n"
        f"Command: {command}"
    )


def _windows_install(command: list[str]) -> None:
    import winreg

    launch = subprocess.list2cmdline(command)
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, REGISTRY_VALUE_NAME, 0, winreg.REG_SZ, launch)


def _windows_uninstall() -> None:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, REGISTRY_VALUE_NAME)
    except FileNotFoundError:
        pass


def _windows_is_installed() -> bool:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, REGISTRY_VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def _macos_install(command: list[str]) -> None:
    target = autostart_target_path()
    assert target is not None
    target.parent.mkdir(parents=True, exist_ok=True)

    plist_data = {
        "Label": APP_ID,
        "ProgramArguments": command,
        "RunAtLoad": True,
        "KeepAlive": False,
        "WorkingDirectory": str(ROOT_DIR.resolve()),
        "StandardOutPath": str((ROOT_DIR / "logs" / "autostart.out.log").resolve()),
        "StandardErrorPath": str((ROOT_DIR / "logs" / "autostart.err.log").resolve()),
    }
    (ROOT_DIR / "logs").mkdir(exist_ok=True)

    with target.open("wb") as handle:
        plistlib.dump(plist_data, handle)

    _macos_load()


def _macos_load() -> None:
    target = autostart_target_path()
    if target is None or not target.exists():
        return
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)],
        check=False,
        capture_output=True,
        text=True,
    )


def _macos_unload() -> None:
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", APP_ID],
        check=False,
        capture_output=True,
        text=True,
    )


def _linux_install(command: list[str]) -> None:
    target = autostart_target_path()
    assert target is not None
    target.parent.mkdir(parents=True, exist_ok=True)

    exec_line = " ".join(_shell_quote(part) for part in command)
    content = "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={APP_NAME}",
            f"Exec={exec_line}",
            f"Path={ROOT_DIR.resolve()}",
            "Terminal=false",
            "Hidden=false",
            "NoDisplay=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        ]
    )
    target.write_text(content, encoding="utf-8")
    target.chmod(0o644)


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    if any(char in value for char in " \t\n\"'$\\"):
        return "'" + value.replace("'", "'\\''") + "'"
    return value
