# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for simpit-control.

Build with:
    python -m PyInstaller --clean simpit-control.spec

Why this spec exists rather than ad-hoc CLI flags:
  * --collect-submodules is required because simpit_control's
    `from . import registry as sp_registry` plus the
    TYPE_CHECKING-guarded import inside registry.py confuses
    PyInstaller's static analyser, and it drops the registry
    submodule from the bundle.
  * The contents of simpit_control/scripts/ are read at runtime
    via Path.read_text(), not imported. They have to ride along
    as bundled data so registry._scripts_dir() can find them
    under sys._MEIPASS when the exe is frozen.
  * --noconsole (console=False below) builds against the
    pythonw.exe windowless subsystem so users don't get a stray
    console window when launching the GUI.

Side effect of console=False: stdout/stderr go to the void.
If something blows up at startup, run the un-frozen launcher
(`python launch_control.py`) to see the traceback.
"""

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("simpit_control")

a = Analysis(
    ["launch_control.py"],
    pathex=[],
    binaries=[],
    datas=[("simpit_control/scripts", "simpit_control/scripts")],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="simpit-control",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                       # equivalent to --noconsole
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="simpit_control/ui/Simpit Control.ico",
)
