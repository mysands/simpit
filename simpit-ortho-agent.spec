# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for simpit-ortho-agent.

Build with:
    python -m PyInstaller --clean simpit-ortho-agent.spec

The ortho cache agent is a console tool by design (handoff: no GUI,
console/log output only) — like the "SimPit Ortho Mount" window, a
visible console makes the priming activity inspectable on a slave.
It deploys as an at-logon Task Scheduler task in the INTERACTIVE
session, not a service (session-isolation lesson from the slave: it
only needs file reads on the mount plus localhost/LAN UDP).

Only simpit_common is bundled beside the agent package; the agent
never imports simpit_control or simpit_slave.
"""

from PyInstaller.utils.hooks import collect_submodules

hidden = (
    collect_submodules("simpit_ortho_agent")
    + collect_submodules("simpit_common")
)

a = Analysis(
    ["launch_ortho_agent.py"],
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
    name="simpit-ortho-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,                        # visible priming/log output
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
