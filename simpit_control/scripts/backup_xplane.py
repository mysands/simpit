#!/usr/bin/env python3
"""backup_xplane.py — back up XPLANE_FOLDER to BACKUP_FOLDER, then prune.

Required env:
    XPLANE_FOLDER  — root of the X-Plane install on this slave
    BACKUP_FOLDER  — destination directory for archives. Must be
                     writable. Often a NAS share or an external drive.

Optional env:
    BACKUP_KEEP    — number of prior archives to retain *for this
                     hostname*. Default 2.

Behavior:
    1. Archive everything under XPLANE_FOLDER **except** the
       ``Custom Scenery`` subdirectory (~tens of GB, intentionally
       excluded). On Windows the archive is .zip, on POSIX it is
       .tar.gz — i.e. the OS-default tool. Symmetric restore is
       provided by ``restore_xplane.py``.

    2. The output filename embeds the hostname and the wall-clock
       date+time at archive creation::

           xplane-{hostname}-YYYY-MM-DD_HHMMSS.{zip|tar.gz}

       The hostname comes from ``socket.gethostname()``, so each
       slave's backups are namespaced even when several slaves
       share a single BACKUP_FOLDER (e.g. one NAS share).

    3. After writing, prune older archives belonging to **this
       host only** so we never touch another slave's backups.
       Keeps the newest BACKUP_KEEP (default 2); deletes the rest.

    4. The archive is written to a ``.tmp`` sibling first and
       atomically renamed into place once complete. A killed agent
       or full disk leaves a stray .tmp instead of a half-zip
       indistinguishable from a real backup.
"""
from __future__ import annotations

import os
import socket
import sys
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

# Subdirectories under XPLANE_FOLDER to skip. ``Custom Scenery`` is
# what dominates install size; the toggled-aside variants are skipped
# too so a backup taken right after disable_custom_scenery doesn't
# unexpectedly include them.
EXCLUDED_DIRS = {
    "Custom Scenery",
    "Custom Scenery DISABLED",
    "Custom Scenery DEFAULT",
}

# Filename anchor. Anything matching xplane-{host}-*.<ext> is "this
# host's backup" for prune purposes; anything else is left alone.
FNAME_PREFIX = "xplane"


def _log(msg: str) -> None:
    print(f"[backup_xplane] {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)


def _archive_extension() -> str:
    """zip on Windows, tar.gz everywhere else — 'OS default'."""
    return ".zip" if os.name == "nt" else ".tar.gz"


def _safe_hostname() -> str:
    """``socket.gethostname()`` sanitized for use in a filename.

    Hostnames are usually filename-safe already, but we strip just in
    case (some macOS hostnames carry a trailing ``.local`` and dots
    are fine; what we filter out are slashes, colons, etc. that would
    confuse ``Path``)."""
    raw = socket.gethostname() or "unknown"
    bad = '<>:"/\\|?*\x00'
    return "".join("_" if c in bad else c for c in raw).strip(". ") or "unknown"


def _iter_files(root: Path):
    """Yield (full_path, arcname) for every file under root, skipping
    EXCLUDED_DIRS at the top level only.

    We prune at the top level, not recursively: a user file named
    ``Custom Scenery`` deep inside Aircraft/ is harmless and worth
    backing up. Only the canonical ``XPLANE_FOLDER/Custom Scenery``
    chunk is the size problem."""
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name in EXCLUDED_DIRS:
            _log(f"  skipping {entry.name}/")
            continue
        if entry.is_file():
            yield entry, entry.name
            continue
        if entry.is_dir():
            for path in entry.rglob("*"):
                # Skip symlinks-to-dirs to avoid traversing into
                # network mounts or recursive links. Symlinked files
                # are followed (rare in an X-Plane tree).
                if path.is_dir():
                    continue
                if path.is_symlink() and path.resolve().is_dir():
                    continue
                if path.is_file():
                    yield path, str(path.relative_to(root))


def _write_zip(src_root: Path, dst: Path) -> int:
    count = 0
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED,
                         allowZip64=True) as zf:
        for full, arc in _iter_files(src_root):
            try:
                zf.write(full, arcname=arc)
                count += 1
            except (OSError, PermissionError) as e:
                # Don't abort the whole backup over one locked file —
                # just record it and keep going. X-Plane caches and
                # update-staging files commonly get held open.
                _log(f"  WARN: skipping {arc}: {e}")
    return count


def _write_tar_gz(src_root: Path, dst: Path) -> int:
    count = 0
    # Mode "w:gz" gives the standard tar.gz compatible with `tar xzf`.
    with tarfile.open(dst, "w:gz") as tf:
        for full, arc in _iter_files(src_root):
            try:
                tf.add(full, arcname=arc, recursive=False)
                count += 1
            except (OSError, PermissionError) as e:
                _log(f"  WARN: skipping {arc}: {e}")
    return count


def _prune(backup_dir: Path, host: str, ext: str, keep: int) -> None:
    """Keep the newest ``keep`` archives for this host; delete the rest.

    Uses mtime, not name, for ordering — robust to clock changes mid-
    sequence and to manual file copies that preserve the original
    timestamp.

    Host matching is **case-sensitive** even on case-insensitive
    filesystems (NTFS on Windows, default APFS/HFS+ on macOS).
    ``Path.glob`` follows the underlying filesystem's case rules,
    so on Windows a glob of ``xplane-CenterLeft-*`` would also
    match ``xplane-CENTERLEFT-*`` files written by a different
    slave whose hostname differs only in case — and prune would
    silently delete them. We instead enumerate the directory and
    filter via ``str.startswith`` / ``str.endswith``, both of which
    are case-sensitive in Python regardless of platform.
    """
    own_prefix = f"{FNAME_PREFIX}-{host}-"
    candidates = []
    for p in backup_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        # Case-sensitive match on both ends; `own_prefix` ends with
        # '-' so we don't accidentally match a longer hostname that
        # starts with this one (e.g. host='Left' wouldn't pull in
        # 'LeftSlave' archives).
        if not name.startswith(own_prefix):
            continue
        if not name.endswith(ext):
            continue
        candidates.append(p)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    _log(f"prune: found {len(candidates)} archive(s) for host '{host}', keep={keep}")
    for stale in candidates[keep:]:
        try:
            stale.unlink()
            _log(f"  deleted {stale.name}")
        except OSError as e:
            _err(f"could not delete {stale.name}: {e}")
            # Non-fatal: we already wrote a fresh backup.


def main() -> int:
    xplane = os.environ.get("XPLANE_FOLDER", "").strip()
    backup = os.environ.get("BACKUP_FOLDER", "").strip()
    keep_s = os.environ.get("BACKUP_KEEP", "").strip()

    if not xplane:
        _err("XPLANE_FOLDER not set"); return 1
    if not backup:
        _err("BACKUP_FOLDER not set"); return 1

    src = Path(xplane)
    dst_dir = Path(backup)

    if not src.is_dir():
        _err(f"XPLANE_FOLDER does not exist or is not a directory: {src}"); return 1

    # Create the destination if missing — common case for a brand-new
    # NAS share. parents=True so we don't fail if the user picks a
    # nested path that doesn't exist yet.
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _err(f"cannot create BACKUP_FOLDER {dst_dir}: {e}"); return 1

    if not os.access(dst_dir, os.W_OK):
        _err(f"BACKUP_FOLDER is not writable: {dst_dir}"); return 1

    try:
        keep = int(keep_s) if keep_s else 2
    except ValueError:
        _err(f"BACKUP_KEEP not an integer: {keep_s!r}"); return 1
    if keep < 1:
        _err(f"BACKUP_KEEP must be >= 1, got {keep}"); return 1

    host = _safe_hostname()
    ext = _archive_extension()
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    final_name = f"{FNAME_PREFIX}-{host}-{stamp}{ext}"
    final_path = dst_dir / final_name
    tmp_path = dst_dir / (final_name + ".tmp")

    _log(f"host={host}")
    _log(f"src={src}")
    _log(f"dst={final_path}")
    _log(f"excluding top-level: {sorted(EXCLUDED_DIRS)}")

    # If a stale .tmp from a previous crash is sitting there with
    # exactly our target name, get rid of it so we can write fresh.
    if tmp_path.exists():
        try:
            tmp_path.unlink()
            _log(f"removed stale tmp: {tmp_path.name}")
        except OSError as e:
            _err(f"cannot remove stale {tmp_path.name}: {e}"); return 1

    _log("archiving…")
    try:
        if ext == ".zip":
            n = _write_zip(src, tmp_path)
        else:
            n = _write_tar_gz(src, tmp_path)
    except Exception as e:
        # Catch broadly — we want to clean up the .tmp on any failure
        # so the next run starts fresh. The exception is re-raised in
        # the form of a non-zero exit code with the original message.
        _err(f"archive failed: {type(e).__name__}: {e}")
        if tmp_path.exists():
            try: tmp_path.unlink()
            except OSError: pass
        return 1

    # Atomic rename — on Windows this can fail if anti-virus has the
    # tmp file open. Fall back to non-atomic copy+delete in that case
    # since the backup itself is already complete.
    try:
        os.replace(tmp_path, final_path)
    except OSError as e:
        _err(f"atomic rename failed ({e}); leaving {tmp_path.name} for manual recovery")
        return 1

    size = final_path.stat().st_size
    _log(f"wrote {final_path.name}: {n} files, {size:,} bytes")

    _prune(dst_dir, host, ext, keep)

    _log("OK: backup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
