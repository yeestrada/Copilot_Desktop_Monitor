# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

project_root = Path(SPECPATH)
src = project_root / "src"

hiddenimports = [
    "pystray",
    "PIL",
    "PIL._tkinter_finder",
    "requests",
    "app_paths",
    "single_instance",
    "autostart",
    "platform_utils",
    "config",
    "github_api",
    "widget",
]

if sys.platform == "win32":
    hiddenimports += ["pystray._win32"]
elif sys.platform == "darwin":
    hiddenimports += ["pystray._darwin"]
else:
    hiddenimports += [
        "pystray._appindicator",
        "pystray._gtk",
        "pystray._xorg",
        "pystray._util.gtk",
        "pystray._util.notify_dbus",
    ]

a = Analysis(
    [str(src / "main.py")],
    pathex=[str(src)],
    binaries=[],
    datas=[(str(project_root / "config.example.json"), ".")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CopilotMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
