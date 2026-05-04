#!/usr/bin/env python3
"""restore_xplane.py — restore an X-Plane install from a backup archive.

Symmetric inverse of ``backup_xplane.py``.

Required env:
    XPLANE_FOLDER  — destination root (overwritten in place)
    BACKUP_FOLDER  — directory holding the archives

Optional env:
    BACKUP_FILE    — exact archive filename to restore (just the name,
                     not a full path). If unset, the newest archive
                     belonging to this host is used.
    SIM_EXE_NAME   — process-name guard. If set and the process is
                     running, refuse to restore (would clobber files
                     X-Plane has open).

Behavior:
    - Picks the archive: BACKUP_FILE if given, otherwise newest
      ``xplane-{host}-*.{zip|tar.gz}`` in BACKUP_FOLDER.
    - Extracts each member into XPLANE_FOLDER, **overwriting**
      existing files. The "Custom Scenery" subtree is skipped on
      extract — backup_xplane never archives it, but if a hand-rolled
      archive contains it we still leave the live install alone.
    - Files in XPLANE_FOLDER that are NOT in the archive are left
      alone. This script is "restore over the top," not "wipe and
      replace" — wiping risks losing addons the user installed
      between backup and restore, and is rarely what you want.
    - Refuses if X-Plane is running (when SIM_EXE_NAME is provided).
    - Path-traversal guard: rejects archive members containing ".."
      or absolute paths so a malicious or corrupt archive can't
      escape XPLANE_FOLDER.
"""
from __future__ import annotations

import os
import socket
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

# Top-level subtrees we refuse to extract into, even if the archive
# contains them. Mirrors backup_xplane.EXCLUDED_DIRS.
SKIP_TOP_LEVEL = {
    "Custom Scenery",
    "Custom Scenery DISABLED",
    "Custom Scenery DEFAULT",
}

FNAME_PREFIX = "xplane"


def _log(msg: str) -> None:
    print(f"[restore_xplane] {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)


def _safe_hostname() -> str:
    raw = socket.gethostname() or "unknown"
    bad = '<>:"/\\|?*\x00'
    return "".join("_" if c in bad else c for c in raw).strip(". ") or "unknown"


def _pick_archive(backup_dir: Path, explicit: str, host: str) -> Path | None:
    """Resolve which archive to restore.

    Explicit BACKUP_FILE wins. Otherwise newest archive for this host
    by mtime. Returns None and logs the reason if nothing found."""
    if explicit:
        # Reject anything that smells like a path — caller should give
        # the basename only. Prevents accidental traversal and forces
        # the file to be inside BACKUP_FOLDER.
        if "/" in explicit or "\\" in explicit or explicit.startswith(".."):
            _err(f"BACKUP_FILE must be a bare filename, got: {explicit!r}")
            return None
        path = backup_dir / explicit
        if not path.is_file():
            _err(f"BACKUP_FILE not found in BACKUP_FOLDER: {path}")
            return None
        return path

    # Case-sensitive scan: Path.glob inherits the filesystem's case
    # rules (NTFS / default APFS = insensitive), so on Windows
    # 'xplane-CenterLeft-*' would also match 'xplane-CENTERLEFT-*'
    # archives produced by a different slave — and we'd silently
    # restore over the wrong machine's install. str.startswith /
    # str.endswith are case-sensitive on every platform.
    own_prefix = f"{FNAME_PREFIX}-{host}-"
    candidates = []
    for p in backup_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if not name.startswith(own_prefix):
            continue
        if not (name.endswith(".zip") or name.endswith(".tar.gz")):
            continue
        candidates.append(p)
    if not candidates:
        _err(f"no archives found for host '{host}' in {backup_dir}")
        _err("set BACKUP_FILE to restore from a different host's archive")
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _is_safe_member(name: str) -> bool:
    """Reject members that would escape the destination root.

    We use PurePosixPath because both zip and tar normalize separators
    to forward slashes internally. ``is_absolute()`` catches names
    starting with ``/``; the part walk catches ``..`` anywhere.
    """
    if not name:
        return False
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute():
        return False
    if any(part == ".." for part in p.parts):
        return False
    # Windows-style drive letters (``C:foo``) — uncommon but possible
    # if an archive was created by a quirky tool.
    if len(name) >= 2 and name[1] == ":":
        return False
    return True


def _top_level(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).parts[0] if name else ""


def _extract_zip(archive: Path, dest: Path) -> tuple[int, int, int]:
    written = skipped_unsafe = skipped_excluded = 0
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not _is_safe_member(info.filename):
                _log(f"  WARN: skipping unsafe path: {info.filename}")
                skipped_unsafe += 1
                continue
            if _top_level(info.filename) in SKIP_TOP_LEVEL:
                skipped_excluded += 1
                continue
            target = dest / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, open(target, "wb") as out:
                    # Stream-copy in chunks so very large members
                    # don't load entirely into memory.
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                written += 1
            except OSError as e:
                _log(f"  WARN: could not write {info.filename}: {e}")
    return written, skipped_unsafe, skipped_excluded


def _extract_tar(archive: Path, dest: Path) -> tuple[int, int, int]:
    written = skipped_unsafe = skipped_excluded = 0
    with tarfile.open(archive, "r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            if not _is_safe_member(member.name):
                _log(f"  WARN: skipping unsafe path: {member.name}")
                skipped_unsafe += 1
                continue
            if _top_level(member.name) in SKIP_TOP_LEVEL:
                skipped_excluded += 1
                continue
            target = dest / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                written += 1
            except OSError as e:
                _log(f"  WARN: could not write {member.name}: {e}")
    return written, skipped_unsafe, skipped_excluded


def _is_xplane_running(sim_name: str) -> bool:
    """Best-effort process check. Returns False (allow restore) if we
    can't determine the answer — we don't want a missing psutil dep
    to permanently break restore."""
    try:
        import psutil  # type: ignore
    except ImportError:
        _log("note: psutil not available; skipping running-process check")
        return False
    target = sim_name.lower()
    for p in psutil.process_iter(["name"]):
        try:
            n = (p.info.get("name") or "").lower()
            if n == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def main() -> int:
    xplane = os.environ.get("XPLANE_FOLDER", "").strip()
    backup = os.environ.get("BACKUP_FOLDER", "").strip()
    explicit = os.environ.get("BACKUP_FILE", "").strip()
    sim_exe = os.environ.get("SIM_EXE_NAME", "").strip()

    if not xplane:
        _err("XPLANE_FOLDER not set"); return 1
    if not backup:
        _err("BACKUP_FOLDER not set"); return 1

    dest = Path(xplane)
    backup_dir = Path(backup)

    if not dest.is_dir():
        _err(f"XPLANE_FOLDER does not exist: {dest}"); return 1
    if not backup_dir.is_dir():
        _err(f"BACKUP_FOLDER does not exist: {backup_dir}"); return 1

    if sim_exe and _is_xplane_running(sim_exe):
        _err(f"{sim_exe} is running; quit X-Plane before restoring")
        return 1

    host = _safe_hostname()
    archive = _pick_archive(backup_dir, explicit, host)
    if archive is None:
        return 1

    _log(f"host={host}")
    _log(f"archive={archive}")
    _log(f"dest={dest}")
    _log("extracting (overwriting in place; Custom Scenery left alone)…")

    try:
        if archive.suffix == ".zip":
            written, unsafe, excluded = _extract_zip(archive, dest)
        else:
            # .tar.gz, .tar.xz, .tgz — all handled by tarfile mode "r:*"
            written, unsafe, excluded = _extract_tar(archive, dest)
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        _err(f"archive corrupt or unreadable: {type(e).__name__}: {e}")
        return 1

    _log(f"wrote {written} files; "
         f"skipped {excluded} excluded, {unsafe} unsafe")
    _log("OK: restore complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
