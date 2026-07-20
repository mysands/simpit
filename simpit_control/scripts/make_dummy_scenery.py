#!/usr/bin/env python3
"""
make_dummy_scenery.py — SimPit standard script

Builds a dummy-texture mirror of the shared Ortho4XP Custom Scenery for
the X-Plane MASTER machine. The master runs the flight model and cockpit
only — it never renders the ortho ground in useful detail — but X-Plane
still refuses to load a scenery pack whose .ter files point at missing
textures. This script emits a parallel scenery tree with identical
folder names, real DSFs and .ter files, and every multi-megabyte
``textures/*.dds`` atlas replaced by a ~300-byte uniform-color DXT1
stand-in, so the master loads the same scenery structure as the visual
slaves without pulling hundreds of GB through the NAS.

What is (and is not) mirrored per ``zOrtho4XP_Z*`` folder
---------------------------------------------------------
* ``Earth nav data/**/*.dsf`` — copied verbatim (X-Plane's mesh).
* ``terrain/*.ter``           — copied verbatim (they name the textures).
* ``textures/*.dds``          — replaced by one tiny valid DXT1 DDS
  (16×16, full mip chain) in a neutral terrain color.
* ``textures/*`` (non-.dds)   — copied verbatim (water/alpha masks).
* Everything else — Ortho4XP build intermediates at the folder root
  (``.alt``/``.mesh``/``.node``/``.poly``/``.apt``/``.cfg``) and any
  ``*.bak`` — is skipped: X-Plane never reads it, and it is most of the
  non-texture bytes on disk.

Incremental builds
------------------
Each completed dummy folder gets a ``.simpit_dummy.json`` marker
recording what it was built from. Re-runs skip marked folders, so after
adding new ortho tiles to the real Custom Scenery just run the script
again and only the new folders are built. ``--verify`` re-lists the
source and rebuilds folders whose file counts changed; ``--prune``
removes dummy folders (marker required) whose source tile is gone.
A folder that crashed mid-build has no marker and is rebuilt in full.

The source tree is opened strictly read-only; the script refuses to run
when the destination is, or is inside, the source scenery folder.

Usage
-----
    make_dummy_scenery.py [dest] [--dry-run] [--verify] [--prune]
                          [--only GLOB] [--workers N] [--color RRGGBB]

Required env: XPLANE_FOLDER  (or CUSTOM_SCENERY_FOLDER, see below)
Optional env: CUSTOM_SCENERY_FOLDER  source scenery root if not
                  ``XPLANE_FOLDER/Custom Scenery`` (e.g. the NAS share)
              DUMMY_SCENERY_FOLDER   destination root if not given as
                  argv (per-machine env dict pattern)
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import struct
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FOLDER_RE = re.compile(r"^zOrtho4XP_Z\d{2}_[+-]\d{2}[+-]\d{3}$")
MARKER_NAME = ".simpit_dummy.json"
MARKER_SCHEMA = 1

# Neutral olive/tan — reads as "ground" if the master ever shows scenery.
DEFAULT_COLOR = (110, 108, 86)


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ── Dummy DDS ────────────────────────────────────────────────────────────
def make_dummy_dds(color: tuple[int, int, int] = DEFAULT_COLOR,
                   size: int = 16) -> bytes:
    """Build a minimal valid DXT1 DDS of one uniform color.

    X-Plane accepts any resolution as a .ter texture as long as the file
    is a well-formed DXT1 DDS, so a 16×16 with a full mip chain (312
    bytes) stands in for the real 4096² atlases (~10.7 MB).

    Args:
        color: (r, g, b), 0-255 each.
        size: edge length in pixels; power of two.

    Returns:
        Complete DDS file contents (128-byte header + DXT1 mip chain).
    """
    r, g, b = color
    c565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    # color0 == color1 and all indices 0: every texel decodes to color0
    # in both DXT1 modes, so the equal-color ambiguity is harmless.
    block = struct.pack("<HH", c565, c565) + b"\x00" * 4

    mips = []
    w = size
    while True:
        blocks = max(1, w // 4) * max(1, w // 4)
        mips.append(block * blocks)
        if w == 1:
            break
        w //= 2

    ddsd = (0x1 | 0x2 | 0x4 | 0x1000      # CAPS|HEIGHT|WIDTH|PIXELFORMAT
            | 0x20000 | 0x80000)          # MIPMAPCOUNT|LINEARSIZE
    caps = 0x8 | 0x1000 | 0x400000        # COMPLEX|TEXTURE|MIPMAP
    header = struct.pack(
        "<4s7I44x2I4s5I I16x",
        b"DDS ", 124, ddsd, size, size, len(mips[0]), 0, len(mips),
        32, 0x4, b"DXT1", 0, 0, 0, 0, 0,
        caps)
    return header + b"".join(mips)


# ── Planning ─────────────────────────────────────────────────────────────
@dataclass
class Sources:
    """One tile folder's relevant source files (paths relative to it)."""
    dsfs:   list[Path] = field(default_factory=list)
    ters:   list[Path] = field(default_factory=list)
    ddses:  list[Path] = field(default_factory=list)
    extras: list[Path] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {"dsf": len(self.dsfs), "ter": len(self.ters),
                "dds": len(self.ddses), "extra": len(self.extras)}


def list_sources(folder: Path) -> Sources:
    """List the files of one source tile folder that the dummy mirrors.

    Args:
        folder: a real ``zOrtho4XP_*`` folder.

    Returns:
        Relative paths grouped by role. ``*.bak`` anywhere and root-level
        Ortho4XP intermediates are excluded by construction (only the
        three known subtrees are walked).
    """
    src = Sources()
    nav = folder / "Earth nav data"
    if nav.is_dir():
        for p in sorted(nav.rglob("*.dsf")):
            src.dsfs.append(p.relative_to(folder))
    terrain = folder / "terrain"
    if terrain.is_dir():
        for p in sorted(terrain.glob("*.ter")):
            src.ters.append(p.relative_to(folder))
    textures = folder / "textures"
    if textures.is_dir():
        for p in sorted(textures.iterdir()):
            if not p.is_file() or p.suffix.lower() == ".bak":
                continue
            rel = p.relative_to(folder)
            if p.suffix.lower() == ".dds":
                src.ddses.append(rel)
            else:
                src.extras.append(rel)
    return src


def read_marker(dummy_folder: Path) -> dict | None:
    """Return the completion marker's dict, or None if absent/corrupt."""
    try:
        data = json.loads((dummy_folder / MARKER_NAME).read_text(
            encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def needs_build(src_folder: Path, dummy_folder: Path,
                verify: bool) -> bool:
    """Decide whether a tile folder must be (re)built.

    Without ``verify`` a marked folder is trusted (NAS scenery content
    is static); with it, the source is re-listed and compared to the
    marker's file counts.
    """
    marker = read_marker(dummy_folder)
    if marker is None or marker.get("schema") != MARKER_SCHEMA:
        return True
    if not verify:
        return False
    return marker.get("counts") != list_sources(src_folder).counts()


# ── Building ─────────────────────────────────────────────────────────────
def _copy(src: Path, dst: Path) -> bool:
    """Copy ``src`` to ``dst`` unless already there with the same size."""
    try:
        if dst.stat().st_size == src.stat().st_size:
            return False
    except OSError:
        pass
    shutil.copyfile(src, dst)
    return True


def build_folder(src_folder: Path, dummy_folder: Path,
                 dds_bytes: bytes) -> dict[str, int]:
    """Build (or finish) one dummy tile folder.

    Args:
        src_folder: real scenery folder (read-only).
        dummy_folder: destination folder; created if needed.
        dds_bytes: the shared dummy DDS contents.

    Returns:
        The marker's ``counts`` dict for the folder.
    """
    src = list_sources(src_folder)
    # Explicitly, so degenerate source folders (no DSF/terrain/textures
    # at all — aborted Ortho4XP builds exist on the NAS) still get an
    # empty dummy folder + marker instead of failing the marker write.
    dummy_folder.mkdir(parents=True, exist_ok=True)
    made_dirs: set[Path] = set()
    for rel in src.dsfs + src.ters + src.extras + src.ddses:
        parent = dummy_folder / rel.parent
        if parent not in made_dirs:
            parent.mkdir(parents=True, exist_ok=True)
            made_dirs.add(parent)
    for rel in src.dsfs + src.ters + src.extras:
        _copy(src_folder / rel, dummy_folder / rel)
    for rel in src.ddses:
        out = dummy_folder / rel
        try:
            if out.stat().st_size == len(dds_bytes):
                continue
        except OSError:
            pass
        out.write_bytes(dds_bytes)
    counts = src.counts()
    marker = {"schema": MARKER_SCHEMA, "counts": counts,
              "built": datetime.now().isoformat(timespec="seconds")}
    (dummy_folder / MARKER_NAME).write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return counts


# ── Roots and safety ─────────────────────────────────────────────────────
def source_root() -> Path:
    """Resolve the real Custom Scenery root from the environment."""
    override = os.environ.get("CUSTOM_SCENERY_FOLDER", "").strip()
    if override:
        root = Path(override)
    else:
        xp = os.environ.get("XPLANE_FOLDER", "").strip()
        if not xp:
            fail("neither CUSTOM_SCENERY_FOLDER nor XPLANE_FOLDER is set")
        root = Path(xp) / "Custom Scenery"
    if not root.is_dir():
        fail(f"source Custom Scenery folder not found: {root}")
    return root


def check_dest(dest: Path, source: Path) -> None:
    """Refuse destinations that would touch the real scenery tree."""
    d, s = dest.resolve(), source.resolve()
    same = (os.path.normcase(str(d)) == os.path.normcase(str(s)))
    if same or d in s.parents or s in d.parents:
        fail(f"destination {dest} overlaps the source scenery tree "
             f"{source} — refusing to write")


def scan_tile_folders(root: Path) -> list[str]:
    """Names of every ``zOrtho4XP_Z*`` tile folder directly under root."""
    return sorted(e.name for e in root.iterdir()
                  if e.is_dir() and FOLDER_RE.match(e.name))


def prune(dest: Path, source_names: set[str], dry_run: bool) -> int:
    """Remove marked dummy folders whose source tile no longer exists.

    Only folders carrying our marker are eligible: anything else under
    the destination was not written by this script and is left alone
    (with a warning).

    Returns:
        Number of folders removed (or that would be, when dry-run).
    """
    removed = 0
    for name in scan_tile_folders(dest):
        if name in source_names:
            continue
        folder = dest / name
        if read_marker(folder) is None:
            print(f"  prune: skipping {name} (no marker — not ours)")
            continue
        print(f"  prune: {'would remove' if dry_run else 'removing'} {name}")
        if not dry_run:
            shutil.rmtree(folder)
        removed += 1
    return removed


# ── CLI ──────────────────────────────────────────────────────────────────
def parse_color(text: str) -> tuple[int, int, int]:
    """Parse ``RRGGBB`` hex into an (r, g, b) tuple."""
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        fail(f"--color wants RRGGBB hex, got {text!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mirror the ortho Custom Scenery with dummy textures "
                    "for the X-Plane master.")
    ap.add_argument("dest", nargs="?",
                    default=os.environ.get("DUMMY_SCENERY_FOLDER", "").strip(),
                    help="destination root (or DUMMY_SCENERY_FOLDER env)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the plan without writing anything")
    ap.add_argument("--verify", action="store_true",
                    help="re-list sources of already-built folders and "
                         "rebuild on count mismatch")
    ap.add_argument("--prune", action="store_true",
                    help="remove marked dummy folders whose source is gone")
    ap.add_argument("--only", metavar="GLOB",
                    help="only process tile folders matching this glob, "
                         "e.g. 'zOrtho4XP_Z18_+34*'")
    ap.add_argument("--workers", type=int, default=4,
                    help="parallel folder builds (default 4; SMB is "
                         "latency-bound, so a few help a lot)")
    ap.add_argument("--color", default=None, metavar="RRGGBB",
                    help="dummy texture color (default %02x%02x%02x)"
                         % DEFAULT_COLOR)
    args = ap.parse_args(argv)

    if not args.dest:
        fail("no destination given (argv or DUMMY_SCENERY_FOLDER env)")
    source = source_root()
    dest = Path(args.dest)
    check_dest(dest, source)

    names = scan_tile_folders(source)
    if not names:
        fail(f"no zOrtho4XP_Z* folders under {source}")
    if args.only:
        names = [n for n in names if fnmatch.fnmatch(n, args.only)]

    color = parse_color(args.color) if args.color else DEFAULT_COLOR
    dds_bytes = make_dummy_dds(color)
    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    todo = [n for n in names
            if needs_build(source / n, dest / n, args.verify)]
    print(f"[make_dummy_scenery] {source} -> {dest}")
    print(f"  tile folders: {len(names)}  up to date: "
          f"{len(names) - len(todo)}  to build: {len(todo)}")

    built = 0
    failed: list[str] = []
    totals = {"dsf": 0, "ter": 0, "dds": 0, "extra": 0}
    if args.dry_run:
        for n in todo:
            print(f"  would build {n}")
    elif todo:
        # One sick folder (vanished mid-build, SMB hiccup) must not
        # abort an hours-long run: record it, keep going, report at the
        # end. Failed folders carry no marker, so a re-run retries them.
        def one(name: str) -> dict[str, int] | Exception:
            try:
                return build_folder(source / name, dest / name, dds_bytes)
            except OSError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            done = 0
            for name, counts in zip(todo, pool.map(one, todo), strict=True):
                done += 1
                if isinstance(counts, Exception):
                    failed.append(name)
                    print(f"  [{done}/{len(todo)}] {name}: FAILED — {counts}",
                          flush=True)
                    continue
                built += 1
                for k in totals:
                    totals[k] += counts[k]
                print(f"  [{done}/{len(todo)}] {name}: "
                      f"dsf={counts['dsf']} ter={counts['ter']} "
                      f"dds={counts['dds']} extra={counts['extra']}",
                      flush=True)

    pruned = 0
    if args.prune:
        pruned = prune(dest, set(names) if not args.only else
                       set(scan_tile_folders(source)), args.dry_run)

    mode = "DRY RUN — nothing written" if args.dry_run else "done"
    print(f"[make_dummy_scenery] {mode}: built {built} folder(s) "
          f"(dsf={totals['dsf']} ter={totals['ter']} dds={totals['dds']} "
          f"extra={totals['extra']}), pruned {pruned}")
    if failed:
        print(f"  FAILED ({len(failed)}): {', '.join(failed)}\n"
              "  (no markers written — re-run to retry these)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
