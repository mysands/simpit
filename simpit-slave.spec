# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for simpit-slave.

Build with:
    python -m PyInstaller --clean simpit-slave.spec

The slave is a UDP listener with no GUI. console=False keeps it
silent when auto-started from shell:startup. Because there's no
console, log to a file from inside the agent if you want
post-mortem visibility — stderr goes nowhere once frozen.

The slave does not load anything from simpit_control/scripts/;
that's a Control concern. So no --add-data is needed here. The
collect_submodules call still applies because the slave imports
from simpit_common which has the same TYPE_CHECKING patterns.
"""

from PyInstaller.utils.hooks import collect_submodules

hidden = (
    collect_submodules("simpit_slave")
    + collect_submodules("simpit_common")
)

a = Analysis(
    ["launch_slave.py"],
    pathex=[],
    binaries=[],
    datas=[],
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
    name="simpit-slave",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                       # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
