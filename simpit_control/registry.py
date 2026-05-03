"""
simpit_control.registry
=======================
Standard script definitions for the SimPit fleet.

Script content lives in ``simpit_control/scripts/`` as real files
(``launch_xplane.bat``, ``launch_xplane.sh``, etc.) — edit them there
with full syntax highlighting. This module reads those files at import
time and wires them into the :class:`ScriptDef` / REGISTRY structures.

Seeding
-------
Call :func:`seed_registry` on a fresh Store to populate it with the
standard scripts. Idempotent — existing ``script_name`` entries are
left untouched so user customisations survive upgrades.

Adding a new standard script
-----------------------------
1. Drop ``myscript.bat`` and/or ``myscript.sh`` in ``simpit_control/scripts/``.
2. Add a ``ScriptDef`` entry to :data:`REGISTRY` below.
3. No other changes required.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import data as sp_data

log = logging.getLogger(__name__)

import sys

_SCRIPTS_DIR = Path(__file__).parent / "scripts"


def _scripts_dir() -> Path:
    """Return the scripts directory, handling PyInstaller onefile bundles.

    When frozen by PyInstaller (--onefile), __file__ points into a temp
    extraction folder that doesn't contain the scripts/ subdirectory.
    PyInstaller sets sys._MEIPASS to the extraction root, so we use that
    as the base instead. In normal (non-frozen) execution __file__ is
    reliable and we use it directly.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "simpit_control" / "scripts"
    return Path(__file__).parent / "scripts"


def _load(filename: str) -> str:
    """Read a script file from the scripts/ directory.

    Returns empty string if the file doesn't exist so that platform-specific
    files (e.g. no .sh on a Windows-only fleet) don't hard-error.
    """
    p = _scripts_dir() / filename
    if not p.exists():
        log.warning("registry: script file not found: %s", p)
        return ""
    return p.read_text(encoding="utf-8")


# ── ScriptDef ─────────────────────────────────────────────────────────────────

@dataclass
class ScriptDef:
    """Everything needed to register one standard script."""

    # ── Identity ────────────────────────────────────────────────────────────
    name:          str
    script_name:   str

    # ── Cascade / targeting ──────────────────────────────────────────────────
    cascade:       bool = True
    target_slaves: list[str] | None = None  # None = all slaves

    # ── Privilege ───────────────────────────────────────────────────────────
    needs_admin:   bool = False

    # ── Probe ───────────────────────────────────────────────────────────────
    state_probe:   dict | None = None

    # ── Pairing (optional) ──────────────────────────────────────────────────
    # Points at the inverse script's ``script_name``. Mirrored on the
    # paired half. The UI collapses both rows into one toggle.
    pair_with:     str | None = None

    # ── Script content (loaded from scripts/ at import time) ─────────────────
    content_bat:   str = ""
    content_sh:    str = ""


# ── Canonical registry ────────────────────────────────────────────────────────

REGISTRY: list[ScriptDef] = [
    ScriptDef(
        name        = "Enable Custom Scenery",
        script_name = "enable_custom_scenery",
        cascade     = True,
        needs_admin = False,
        # "Is enable's action available right now?" -> yes iff
        # 'Custom Scenery DISABLED' exists (a snapshot exists to
        # restore from). NOT inverted — present means "show this
        # button," consistent with the convention used by the
        # toggle-pair viewmodel.
        state_probe = {
            "type":   "folder_exists",
            "params": {"path": "${XPLANE_FOLDER}/Custom Scenery DISABLED"},
        },
        pair_with   = "disable_custom_scenery",
        content_bat = _load("enable_custom_scenery.bat"),
        content_sh  = _load("enable_custom_scenery.sh"),
    ),
    ScriptDef(
        name        = "Disable Custom Scenery",
        script_name = "disable_custom_scenery",
        cascade     = True,
        needs_admin = False,
        # "Is disable's action available right now?" -> yes iff
        # 'Custom Scenery DISABLED' is *absent* (no snapshot yet,
        # so this is the action that creates one). User invariant:
        # 'Custom Scenery' itself is always present, so we don't
        # need to check it separately. The disable script auto-
        # creates 'Custom Scenery DEFAULT' if it's missing.
        state_probe = {
            "type":   "folder_exists",
            "params": {"path": "${XPLANE_FOLDER}/Custom Scenery DISABLED",
                       "invert": True},
        },
        pair_with   = "enable_custom_scenery",
        content_bat = _load("disable_custom_scenery.bat"),
        content_sh  = _load("disable_custom_scenery.sh"),
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
        # .py handles both platforms — stored in content_bat by convention
        content_bat = _load("block_xplane_updates.py"),
        content_sh  = "",
    ),
    ScriptDef(
        name        = "Restore X-Plane Updates",
        script_name = "restore_xplane_updates",
        cascade     = True,
        needs_admin = True,
        state_probe = None,
        content_bat = _load("restore_xplane_updates.py"),
        content_sh  = "",
    ),
    ScriptDef(
<<<<<<< HEAD
        # Backs up XPLANE_FOLDER (everything except Custom Scenery)
        # to BACKUP_FOLDER. Filenames embed hostname so several
        # slaves can share one BACKUP_FOLDER without collisions.
        # After writing, prunes to the newest BACKUP_KEEP archives
        # (default 2) for THIS host only — never another slave's.
        # Cross-platform .py for the same reason as block/restore
        # update scripts: zipfile/tarfile in stdlib makes one source
        # cleaner than two shell dialects.
        name        = "Backup X-Plane",
        script_name = "backup_xplane",
        cascade     = True,
        needs_admin = False,
        state_probe = None,
        content_bat = _load("backup_xplane.py"),
        content_sh  = "",
    ),
    ScriptDef(
        # Symmetric inverse of backup_xplane: extracts the newest
        # archive for this host (or BACKUP_FILE if specified) and
        # overwrites in place. Custom Scenery is left alone. Refuses
        # to run if SIM_EXE_NAME is currently a running process.
        name        = "Restore X-Plane",
        script_name = "restore_xplane",
        cascade     = True,
        needs_admin = False,
        state_probe = None,
        content_bat = _load("restore_xplane.py"),
=======
        name        = "Quit X-Plane",
        script_name = "quit_xplane",
        cascade     = True,
        needs_admin = False,
        # No probe: quit is fire-and-forget UDP. Whether X-Plane is
        # actually still running after the packet is best surfaced by
        # the launch_xplane probe (process_running on SIM_EXE_NAME).
        state_probe = None,
        # .py handles both platforms — stored in content_bat by convention
        content_bat = _load("quit_xplane.py"),
>>>>>>> feat(scripts): add quit_xplane script
        content_sh  = "",
    ),
]

REGISTRY_BY_NAME: dict[str, ScriptDef] = {s.script_name: s for s in REGISTRY}


# ── Seeder ───────────────────────────────────────────────────────────────────

def seed_registry(store: "sp_data.Store") -> int:
    """Populate `store` with any standard scripts not yet registered.

    Idempotent: scripts whose ``script_name`` already exists are left
    untouched. Returns the number of entries actually added.
    """
    existing_names = {b.script_name for b in store.batfiles()}
    added = 0
    for defn in REGISTRY:
        if defn.script_name in existing_names:
            log.debug("registry: skipping existing %s", defn.script_name)
            continue
        store.add_batfile(
            name         = defn.name,
            script_name  = defn.script_name,
            cascade      = defn.cascade,
            content      = defn.content_bat,
            target_slaves= defn.target_slaves,
            needs_admin  = defn.needs_admin,
            state_probe  = defn.state_probe,
            pair_with    = defn.pair_with,
        )
        log.info("registry: seeded %s", defn.script_name)
        added += 1
    return added
