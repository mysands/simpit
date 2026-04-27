"""
simpit_control.registry
=======================
Standard script definitions for the SimPit fleet.

This module declares the canonical set of scripts that ship with Control.
Each entry is a :class:`ScriptDef` that carries both the Windows (.bat)
and POSIX (.sh) content plus the metadata that belongs in ``batfiles.json``
(cascade flag, admin requirement, probe definition).

Seeding
-------
Call :func:`seed_registry` on a fresh Store to populate it with the
standard scripts. The function is idempotent — if a script with the
same ``script_name`` already exists, it is left untouched so user
customisations survive upgrades.

Adding a new standard script
-----------------------------
1. Define a ``ScriptDef`` below.
2. Add it to :data:`REGISTRY`.
3. No other changes required — Control picks it up automatically.

Script content conventions
---------------------------
* Use ``%XPLANE_FOLDER%`` in .bat, ``${XPLANE_FOLDER}`` in .sh.
* ``SIM_EXE_NAME`` holds the executable filename (``X-Plane.exe`` on
  Windows, ``X-Plane`` on Linux/macOS).
* Both .bat and .sh must be present for cross-platform fleets, but the
  agent silently skips files whose OS doesn't match, so a Windows-only
  fleet can ignore .sh content and vice versa.
* Scripts must be idempotent and exit 0 on success, non-zero on error.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import data as sp_data

log = logging.getLogger(__name__)


@dataclass
class ScriptDef:
    """Everything needed to register one standard script."""

    # ── Identity ────────────────────────────────────────────────────────────
    name:          str   # human label in the UI
    script_name:   str   # base name without extension

    # ── Cascade / targeting ──────────────────────────────────────────────────
    cascade:       bool  = True
    target_slaves: list[str] | None = None  # None = all slaves

    # ── Privilege ───────────────────────────────────────────────────────────
    needs_admin:   bool  = False

    # ── Probe ───────────────────────────────────────────────────────────────
    state_probe:   dict | None = None

    # ── Script content (one per OS) ──────────────────────────────────────────
    content_bat:   str = ""   # Windows .bat  (deployed as <script_name>.bat)
    content_sh:    str = ""   # POSIX   .sh   (deployed as <script_name>.sh)

    def cascaded_scripts(self) -> list[dict]:
        """Wire-format list for SYNC_PUSH (one entry per OS file present)."""
        out = []
        if self.content_bat:
            out.append({"name": self.script_name,
                        "content": self.content_bat,
                        "os": "windows"})
        if self.content_sh:
            out.append({"name": self.script_name,
                        "content": self.content_sh,
                        "os": "posix"})
        return out


# ── Script definitions ────────────────────────────────────────────────────────

_LAUNCH_XPLANE_BAT = """\
@echo off
REM launch_xplane.bat  —  Start X-Plane (SimPit standard script)
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME
if "%XPLANE_FOLDER%"=="" (
    echo ERROR: XPLANE_FOLDER not set >&2
    exit /b 1
)
if "%SIM_EXE_NAME%"=="" (
    echo ERROR: SIM_EXE_NAME not set >&2
    exit /b 1
)
set XP_EXE=%XPLANE_FOLDER%\\%SIM_EXE_NAME%
if not exist "%XP_EXE%" (
    echo ERROR: not found: %XP_EXE% >&2
    exit /b 1
)
REM Check if already running
tasklist /fi "imagename eq %SIM_EXE_NAME%" 2>nul | find /i "%SIM_EXE_NAME%" >nul
if %errorlevel%==0 (
    echo X-Plane already running.
    exit /b 0
)
start "" "%XP_EXE%"
echo Launched: %XP_EXE%
exit /b 0
"""

_LAUNCH_XPLANE_SH = """\
#!/usr/bin/env bash
# launch_xplane.sh  —  Start X-Plane (SimPit standard script)
# Required env: XPLANE_FOLDER  SIM_EXE_NAME
set -euo pipefail

: "${XPLANE_FOLDER:?XPLANE_FOLDER not set}"
: "${SIM_EXE_NAME:?SIM_EXE_NAME not set}"

XP_EXE="${XPLANE_FOLDER%/}/${SIM_EXE_NAME}"

if [ ! -f "$XP_EXE" ]; then
    echo "ERROR: not found: $XP_EXE" >&2
    exit 1
fi

if pgrep -x "$SIM_EXE_NAME" >/dev/null 2>&1; then
    echo "X-Plane already running."
    exit 0
fi

nohup "$XP_EXE" >/dev/null 2>&1 &
echo "Launched: $XP_EXE"
"""

_ENABLE_CUSTOM_SCENERY_BAT = """\
@echo off
REM enable_custom_scenery.bat  —  Rename 'Custom Scenery DISABLED' back to 'Custom Scenery'
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME
if "%XPLANE_FOLDER%"=="" (
    echo ERROR: XPLANE_FOLDER not set >&2
    exit /b 1
)
set DISABLED_DIR=%XPLANE_FOLDER%\\Custom Scenery DISABLED
set ENABLED_DIR=%XPLANE_FOLDER%\\Custom Scenery

REM Guard: don't touch folders if X-Plane is running
tasklist /fi "imagename eq %SIM_EXE_NAME%" 2>nul | find /i "%SIM_EXE_NAME%" >nul
if %errorlevel%==0 (
    echo ERROR: X-Plane is running — quit before toggling scenery >&2
    exit /b 1
)

if not exist "%DISABLED_DIR%" (
    echo Custom Scenery is already enabled (or folder missing).
    exit /b 0
)

if exist "%ENABLED_DIR%" (
    echo ERROR: Both 'Custom Scenery' and 'Custom Scenery DISABLED' exist >&2
    exit /b 1
)

ren "%DISABLED_DIR%" "Custom Scenery"
echo Custom Scenery enabled.
exit /b 0
"""

_ENABLE_CUSTOM_SCENERY_SH = """\
#!/usr/bin/env bash
# enable_custom_scenery.sh
set -euo pipefail
: "${XPLANE_FOLDER:?XPLANE_FOLDER not set}"
: "${SIM_EXE_NAME:?SIM_EXE_NAME not set}"

DISABLED="${XPLANE_FOLDER%/}/Custom Scenery DISABLED"
ENABLED="${XPLANE_FOLDER%/}/Custom Scenery"

if pgrep -x "$SIM_EXE_NAME" >/dev/null 2>&1; then
    echo "ERROR: X-Plane is running — quit before toggling scenery" >&2
    exit 1
fi

if [ ! -d "$DISABLED" ]; then
    echo "Custom Scenery already enabled (or folder missing)."
    exit 0
fi

if [ -d "$ENABLED" ]; then
    echo "ERROR: Both 'Custom Scenery' and 'Custom Scenery DISABLED' exist" >&2
    exit 1
fi

mv "$DISABLED" "$ENABLED"
echo "Custom Scenery enabled."
"""

_DISABLE_CUSTOM_SCENERY_BAT = """\
@echo off
REM disable_custom_scenery.bat  —  Rename 'Custom Scenery' to 'Custom Scenery DISABLED'
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME
if "%XPLANE_FOLDER%"=="" (
    echo ERROR: XPLANE_FOLDER not set >&2
    exit /b 1
)
set DISABLED_DIR=%XPLANE_FOLDER%\\Custom Scenery DISABLED
set ENABLED_DIR=%XPLANE_FOLDER%\\Custom Scenery
set DEFAULT_DIR=%XPLANE_FOLDER%\\Custom Scenery\\DEFAULT

REM Guard: don't touch folders if X-Plane is running
tasklist /fi "imagename eq %SIM_EXE_NAME%" 2>nul | find /i "%SIM_EXE_NAME%" >nul
if %errorlevel%==0 (
    echo ERROR: X-Plane is running — quit before toggling scenery >&2
    exit /b 1
)

if exist "%DISABLED_DIR%" (
    echo Custom Scenery is already disabled.
    exit /b 0
)

if not exist "%ENABLED_DIR%" (
    REM Fresh install — create empty DEFAULT so XP12 doesn't complain
    mkdir "%ENABLED_DIR%"
    mkdir "%DEFAULT_DIR%"
    echo Created empty Custom Scenery\\DEFAULT for fresh install.
)

ren "%ENABLED_DIR%" "Custom Scenery DISABLED"
echo Custom Scenery disabled.
exit /b 0
"""

_DISABLE_CUSTOM_SCENERY_SH = """\
#!/usr/bin/env bash
# disable_custom_scenery.sh
set -euo pipefail
: "${XPLANE_FOLDER:?XPLANE_FOLDER not set}"
: "${SIM_EXE_NAME:?SIM_EXE_NAME not set}"

DISABLED="${XPLANE_FOLDER%/}/Custom Scenery DISABLED"
ENABLED="${XPLANE_FOLDER%/}/Custom Scenery"
DEFAULT="${ENABLED}/DEFAULT"

if pgrep -x "$SIM_EXE_NAME" >/dev/null 2>&1; then
    echo "ERROR: X-Plane is running — quit before toggling scenery" >&2
    exit 1
fi

if [ -d "$DISABLED" ]; then
    echo "Custom Scenery already disabled."
    exit 0
fi

if [ ! -d "$ENABLED" ]; then
    # Fresh install — create skeleton so XP12 doesn't complain
    mkdir -p "$DEFAULT"
    echo "Created empty Custom Scenery/DEFAULT for fresh install."
fi

mv "$ENABLED" "$DISABLED"
echo "Custom Scenery disabled."
"""

# block/restore_xplane_updates ship as .py so they work cross-platform
# without needing bash or cmd. The slave executor runs .py via the system
# Python that ships with the simpit_slave package.
_BLOCK_XPLANE_UPDATES_PY = """\
#!/usr/bin/env python3
\"\"\"block_xplane_updates.py — Add xplane.com to hosts to block update checks.
Requires admin/root. Called by SimPit Control via EXEC_SCRIPT (needs_admin=True).
\"\"\"
import sys
import platform

BLOCK_MARKER = "# simpit: block xplane updates"
BLOCK_ENTRIES = [
    f"0.0.0.0 updater.x-plane.com  {BLOCK_MARKER}",
    f"0.0.0.0 store.x-plane.com    {BLOCK_MARKER}",
]

def hosts_path():
    if platform.system() == "Windows":
        import os
        return os.path.join(os.environ.get("SystemRoot", r"C:\\Windows"),
                            "System32", "drivers", "etc", "hosts")
    return "/etc/hosts"

def main():
    path = hosts_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        print(f"ERROR: cannot read {path}: {e}", file=sys.stderr)
        return 1

    existing = {l.strip() for l in lines}
    to_add = [e for e in BLOCK_ENTRIES if e.strip() not in existing]
    if not to_add:
        print("X-Plane update hosts entries already present.")
        return 0

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\\n" + "\\n".join(to_add) + "\\n")
    except PermissionError:
        print(f"ERROR: permission denied writing {path} — run as admin/root",
              file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Added {len(to_add)} block entries to {path}.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
"""

_RESTORE_XPLANE_UPDATES_PY = """\
#!/usr/bin/env python3
\"\"\"restore_xplane_updates.py — Remove simpit block entries from hosts file.
Requires admin/root.
\"\"\"
import sys
import platform

BLOCK_MARKER = "# simpit: block xplane updates"

def hosts_path():
    if platform.system() == "Windows":
        import os
        return os.path.join(os.environ.get("SystemRoot", r"C:\\Windows"),
                            "System32", "drivers", "etc", "hosts")
    return "/etc/hosts"

def main():
    path = hosts_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        print(f"ERROR: cannot read {path}: {e}", file=sys.stderr)
        return 1

    filtered = [l for l in lines if BLOCK_MARKER not in l]
    # Strip trailing blank lines the removal may leave.
    while filtered and not filtered[-1].strip():
        filtered.pop()

    if len(filtered) == len(lines):
        print("No simpit block entries found — nothing to remove.")
        return 0

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\\n".join(filtered) + "\\n")
    except PermissionError:
        print(f"ERROR: permission denied writing {path} — run as admin/root",
              file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    removed = len(lines) - len(filtered)
    print(f"Removed {removed} block entries from {path}.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
"""

# ── Canonical registry ────────────────────────────────────────────────────────

REGISTRY: list[ScriptDef] = [
    ScriptDef(
        name        = "Launch X-Plane",
        script_name = "launch_xplane",
        cascade     = True,
        needs_admin = False,
        state_probe = {
            "type":   "process_running",
            "params": {"name": "${SIM_EXE_NAME}"},
        },
        content_bat = _LAUNCH_XPLANE_BAT,
        content_sh  = _LAUNCH_XPLANE_SH,
    ),
    ScriptDef(
        name        = "Enable Custom Scenery",
        script_name = "enable_custom_scenery",
        cascade     = True,
        needs_admin = False,
        state_probe = {
            # Probe true when the DISABLED folder doesn't exist
            # (i.e. scenery IS enabled)
            "type":   "folder_exists",
            "params": {"path": "${XPLANE_FOLDER}/Custom Scenery DISABLED",
                       "invert": True},
        },
        content_bat = _ENABLE_CUSTOM_SCENERY_BAT,
        content_sh  = _ENABLE_CUSTOM_SCENERY_SH,
    ),
    ScriptDef(
        name        = "Disable Custom Scenery",
        script_name = "disable_custom_scenery",
        cascade     = True,
        needs_admin = False,
        state_probe = {
            # Probe true when the DISABLED folder exists (scenery IS disabled)
            "type":   "folder_exists",
            "params": {"path": "${XPLANE_FOLDER}/Custom Scenery DISABLED"},
        },
        content_bat = _DISABLE_CUSTOM_SCENERY_BAT,
        content_sh  = _DISABLE_CUSTOM_SCENERY_SH,
    ),
    ScriptDef(
        name        = "Block X-Plane Updates",
        script_name = "block_xplane_updates",
        cascade     = True,
        needs_admin = True,
        state_probe = {
            "type":   "file_contains",
            "params": {"path": "${HOSTS_FILE}",
                       "text": "# simpit: block xplane updates"},
        },
        # .py ships for both platforms via the 'py' os tag understood by
        # apply_sync_push. No .bat/.sh — Python is the portable vehicle here.
        content_bat = _BLOCK_XPLANE_UPDATES_PY,   # deployed as .py, not .bat
        content_sh  = "",                           # .py handles posix too
    ),
    ScriptDef(
        name        = "Restore X-Plane Updates",
        script_name = "restore_xplane_updates",
        cascade     = True,
        needs_admin = True,
        state_probe = None,
        content_bat = _RESTORE_XPLANE_UPDATES_PY,
        content_sh  = "",
    ),
]

# Convenient lookup by script_name
REGISTRY_BY_NAME: dict[str, ScriptDef] = {s.script_name: s for s in REGISTRY}


# ── Seeder ───────────────────────────────────────────────────────────────────

def seed_registry(store: "sp_data.Store") -> int:
    """Populate `store` with any standard scripts not yet registered.

    Idempotent: scripts whose ``script_name`` already exists are left
    untouched. Returns the number of entries actually added.

    Call this at first-run (batfiles list is empty) or after an upgrade
    that introduces new standard scripts.
    """
    existing_names = {b.script_name for b in store.batfiles()}
    added = 0
    for defn in REGISTRY:
        if defn.script_name in existing_names:
            log.debug("registry: skipping existing %s", defn.script_name)
            continue

        # block/restore_xplane_updates ship .py content but we store it
        # with a special script_name so the executor can find them as .py.
        # The content is the same regardless of OS — we tag os=None so the
        # agent writes the same file on Windows and Linux.
        store.add_batfile(
            name         = defn.name,
            script_name  = defn.script_name,
            cascade      = defn.cascade,
            content      = defn.content_bat,  # primary content (or .py)
            target_slaves= defn.target_slaves,
            needs_admin  = defn.needs_admin,
            state_probe  = defn.state_probe,
        )
        log.info("registry: seeded %s", defn.script_name)
        added += 1

    return added
