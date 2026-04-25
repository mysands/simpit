"""
simpit_slave.data
=================
On-disk state owned by the slave.

The slave's data footprint is intentionally minimal:

    <data_dir>/
        simpit.key           - shared HMAC secret (mode 0600 on POSIX)
        cascaded/            - scripts pushed by Control via SYNC_PUSH
            launch_xplane.bat
            ...
        local/               - operator-managed scripts (Control never touches)
            calibrate.bat
            ...
        agent.log            - rotating log (when run as a service)

Why a ``cascaded/`` vs ``local/`` split? Two reasons:

1. SYNC_PUSH does a *full replace* of cascaded/ — anything that wasn't
   in the latest push gets deleted. If an operator dropped a custom
   script directly in there, it would silently disappear after the next
   sync. ``local/`` is never touched by sync, so it's safe for that.

2. Audit clarity. When debugging a slave, "where did this script come
   from?" is answered by which folder it lives in.

Cascaded scripts are written atomically (tmp file + rename) so a
SYNC_PUSH that gets interrupted mid-write can't leave a half-written
script that would then execute with garbage content.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from simpit_common import platform as sp_platform
from simpit_common import security as sp_security


# ── Layout helpers ───────────────────────────────────────────────────────────
def default_data_dir() -> Path:
    """Where the slave stores its files by default.

    Mirrors :func:`simpit_common.platform.app_data_dir` so the slave and
    Control look in the same conceptual place when run by the same user.
    A different path can always be passed via CLI in __main__.
    """
    return sp_platform.app_data_dir("simpit-slave")


@dataclass(frozen=True)
class SlavePaths:
    """Resolved on-disk layout for a slave instance.

    Constructed once at startup and passed around. Avoids each module
    re-deriving paths from the same config and keeps mocking trivial in
    tests (just build a SlavePaths pointing at tmp_path).
    """
    root:        Path
    key_file:    Path
    cascaded:    Path
    local:       Path
    log_file:    Path

    @classmethod
    def under(cls, root: Path) -> "SlavePaths":
        root = Path(root)
        return cls(
            root      = root,
            key_file  = root / sp_security.KEY_FILENAME,
            cascaded  = root / "cascaded",
            local     = root / "local",
            log_file  = root / "agent.log",
        )

    def ensure(self) -> None:
        """Create any missing directories. Safe to call repeatedly."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.cascaded.mkdir(exist_ok=True)
        self.local.mkdir(exist_ok=True)


# ── Script lookup ────────────────────────────────────────────────────────────
def find_script(paths: SlavePaths, name: str) -> Path | None:
    """Locate a script by base name across cascaded/ and local/.

    Resolution order: cascaded first, then local. This lets Control's
    pushed version take precedence over a stale local copy with the same
    name. (Operators who want their local script to win should give it a
    different name.)

    Returns ``None`` rather than raising on miss because callers (the
    executor) want to format a specific 'script not found' response and
    don't need an exception bubbling up through the agent loop.

    Critically, this function REJECTS any name containing path
    separators — that closes the path-traversal hole that would
    otherwise let a forged EXEC_SCRIPT command run an arbitrary file by
    sending ``../../../../etc/passwd`` as the script name.
    """
    if not isinstance(name, str) or not name:
        return None
    # Block path traversal aggressively. We accept ONLY a base name.
    if any(c in name for c in ("/", "\\", "\x00")) or name in ("", ".", ".."):
        return None
    filename = sp_platform.script_filename(name)
    for folder in (paths.cascaded, paths.local):
        candidate = folder / filename
        if candidate.is_file():
            return candidate
    return None


def list_scripts(paths: SlavePaths) -> dict[str, list[str]]:
    """Return ``{"cascaded": [...], "local": [...]}`` of script base names.

    Used by the inspector for status responses so Control can show which
    scripts a slave has available without making a separate request.
    """
    def _names(folder: Path) -> list[str]:
        if not folder.is_dir():
            return []
        out = []
        for p in sorted(folder.iterdir()):
            if p.is_file():
                # Strip extension — Control thinks in script base names.
                out.append(p.stem)
        return out
    return {"cascaded": _names(paths.cascaded),
            "local":    _names(paths.local)}


# ── SYNC_PUSH application ────────────────────────────────────────────────────
@dataclass
class CascadedScript:
    """One entry in a SYNC_PUSH payload.

    `name` is the base name without extension; the slave appends the
    correct extension for its OS so a single Control machine can serve
    Windows and Linux slaves from the same JSON.
    """
    name:    str
    content: str
    # Optional per-script flag; lets Control mark something as Windows-only
    # or POSIX-only. None = no restriction.
    os:      str | None = None


def apply_sync_push(paths: SlavePaths,
                    scripts: Iterable[CascadedScript]) -> dict:
    """Replace ``cascaded/`` with the given scripts, atomically.

    Strategy: write all new scripts to a sibling temp directory, then
    swap directories. This guarantees we never see a half-applied state
    where some old scripts are gone but new ones aren't yet written.

    The swap itself isn't fully atomic on Windows (no ``rename(2)``
    semantics for a populated directory), but in practice the failure
    window is microseconds and the worst case is one stale script
    surviving until the next sync. The alternative — per-file replace —
    leaves more inconsistent intermediate states.

    Returns a summary dict suitable for inclusion in a SYNC_ACK body.
    """
    paths.ensure()
    new_dir = paths.root / "cascaded.new"
    if new_dir.exists():
        shutil.rmtree(new_dir)
    new_dir.mkdir()

    written = []
    skipped = []
    for entry in scripts:
        # Per-script OS gate: skip silently rather than error so a single
        # batfiles.json can serve mixed-OS fleets.
        if entry.os and entry.os != sp_platform.current_os():
            skipped.append({"name": entry.name, "reason": "os_mismatch"})
            continue
        if any(c in entry.name for c in ("/", "\\", "\x00")):
            skipped.append({"name": entry.name, "reason": "bad_name"})
            continue
        filename = sp_platform.script_filename(entry.name)
        out = new_dir / filename
        out.write_text(entry.content, encoding="utf-8")
        # Make POSIX shells executable so build_script_invocation can run
        # them directly via shebang.
        if os.name == "posix":
            out.chmod(0o755)
        written.append(entry.name)

    # Swap in the new directory.
    if paths.cascaded.exists():
        backup = paths.root / "cascaded.old"
        if backup.exists():
            shutil.rmtree(backup)
        paths.cascaded.rename(backup)
    new_dir.rename(paths.cascaded)
    backup = paths.root / "cascaded.old"
    if backup.exists():
        shutil.rmtree(backup)

    return {"written": written, "skipped": skipped,
            "count": len(written)}
