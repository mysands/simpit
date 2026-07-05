#!/usr/bin/env python3
"""
set_scenery_profile.py — SimPit standard script

Rule-based generator for X-Plane's scenery_packs.ini that switches the
active Ortho4XP zoom level per tile (Z16 low-detail vs Z18 high-detail)
according to a named profile. Regenerates the ortho lines of the ini
from the folders actually present on disk, so newly added tiles are
picked up automatically — no static ini copies to keep in sync.

Profiles
--------
JSON files in ``<Custom Scenery>/scenery_profiles/``:

    {
      "name": "hybrid",
      "default_zoom": "Z16",
      "overrides": [
        { "airports": ["KLAX", "KSAN"], "radius_tiles": 1, "zoom": "Z18" },
        { "tiles": [[34, -118]], "zoom": "Z18" }
      ]
    }

``default_zoom`` applies to every tile; overrides claim tiles either by
explicit [lat, lon] pairs or by airports (ICAO resolved to a tile via
X-Plane's Global Airports apt.dat, then grown by ``radius_tiles``).
Later overrides win over earlier ones. A tile whose desired zoom folder
doesn't exist falls back to the other zoom if that one does.

Ini handling
------------
Only lines referencing ``zOrtho4XP_Z16_*`` / ``zOrtho4XP_Z18_*`` are
touched (flipped between SCENERY_PACK and SCENERY_PACK_DISABLED in
place, order preserved). Everything else — airports, landmarks, meshes,
overlays — passes through byte-identical. Tiles on disk but missing
from the ini are appended at the bottom. A timestamped backup is
written first, the replacement is atomic, and the active profile is
recorded in ``scenery_profiles/active_profile.json``.

Usage
-----
    set_scenery_profile.py <profile> [--dry-run]
    set_scenery_profile.py --status | --list

Required env: XPLANE_FOLDER  (or CUSTOM_SCENERY_FOLDER, see below)
Optional env: CUSTOM_SCENERY_FOLDER  scenery root if not
                  ``XPLANE_FOLDER/Custom Scenery`` (e.g. a NAS share)
              SCENERY_PROFILE        profile name if no argv (per-slave
                  env dict pattern: one registry script, per-slave value)
              APT_DAT                override path to apt.dat
              SIM_EXE_NAME           refuse to run while this process runs
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from math import floor
from pathlib import Path

ORTHO_RE = re.compile(
    r"^(SCENERY_PACK|SCENERY_PACK_DISABLED)\s+"
    r"Custom Scenery/zOrtho4XP_(Z\d{2})_([+-]\d{2})([+-]\d{3})/\s*$"
)
FOLDER_RE = re.compile(r"^zOrtho4XP_(Z\d{2})_([+-]\d{2})([+-]\d{3})$")
ZOOMS = ("Z16", "Z18")


def fail(msg: str) -> "None":
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def scenery_root() -> Path:
    override = os.environ.get("CUSTOM_SCENERY_FOLDER", "").strip()
    if override:
        root = Path(override)
    else:
        xp = os.environ.get("XPLANE_FOLDER", "").strip()
        if not xp:
            fail("neither CUSTOM_SCENERY_FOLDER nor XPLANE_FOLDER is set")
        root = Path(xp) / "Custom Scenery"
    if not root.is_dir():
        fail(f"Custom Scenery folder not found: {root}")
    return root


def sim_running() -> bool:
    exe = os.environ.get("SIM_EXE_NAME", "").strip()
    if not exe:
        return False
    if sys.platform.startswith("win"):
        out = subprocess.run(
            ["tasklist", "/fi", f"imagename eq {exe}"],
            capture_output=True, text=True,
        ).stdout
        return exe.lower() in out.lower()
    return subprocess.run(["pgrep", "-x", exe], capture_output=True).returncode == 0


def tile_key(lat_s: str, lon_s: str) -> tuple[int, int]:
    return int(lat_s), int(lon_s)


def scan_tiles(root: Path) -> dict[tuple[int, int], set[str]]:
    """Map (lat, lon) -> set of zooms present on disk."""
    tiles: dict[tuple[int, int], set[str]] = {}
    for entry in root.iterdir():
        m = FOLDER_RE.match(entry.name)
        if m and entry.is_dir():
            tiles.setdefault(tile_key(m.group(2), m.group(3)), set()).add(m.group(1))
    return tiles


def apt_dat_path() -> Path:
    override = os.environ.get("APT_DAT", "").strip()
    if override:
        return Path(override)
    xp = os.environ.get("XPLANE_FOLDER", "").strip()
    if not xp:
        fail("XPLANE_FOLDER (or APT_DAT) must be set to resolve airports")
    return Path(xp) / "Global Scenery" / "Global Airports" / "Earth nav data" / "apt.dat"


def resolve_airports(icaos: set[str], profiles_dir: Path) -> dict[str, tuple[float, float]]:
    """ICAO -> (lat, lon), via a JSON cache next to the profiles, else apt.dat scan."""
    cache_file = profiles_dir / "airport_coords_cache.json"
    cache: dict[str, list[float]] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    missing = {i for i in icaos if i not in cache}
    if missing:
        apt = apt_dat_path()
        if not apt.exists():
            fail(f"apt.dat not found: {apt}")
        current = None
        lat = lon = None
        with apt.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] in ("1", "16", "17") and len(parts) >= 5:
                    current = parts[4] if parts[4] in missing else None
                    lat = lon = None
                elif current and parts[0] == "1302" and len(parts) >= 3:
                    if parts[1] == "datum_lat":
                        lat = float(parts[2])
                    elif parts[1] == "datum_lon":
                        lon = float(parts[2])
                    if lat is not None and lon is not None:
                        cache[current] = [lat, lon]
                        missing.discard(current)
                        current = None
                        if not missing:
                            break
        if missing:
            fail(f"airport(s) not found in apt.dat: {', '.join(sorted(missing))}")
        cache_file.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    return {i: (cache[i][0], cache[i][1]) for i in icaos}


def plan_tiles(profile: dict, tiles: dict[tuple[int, int], set[str]],
               profiles_dir: Path) -> dict[tuple[int, int], str]:
    """Decide the zoom for every tile on disk. Returns (lat, lon) -> zoom."""
    default = profile.get("default_zoom")
    if default not in ZOOMS:
        fail(f"profile default_zoom must be one of {ZOOMS}, got {default!r}")
    desired = {key: default for key in tiles}

    for ov in profile.get("overrides", []):
        zoom = ov.get("zoom")
        if zoom not in ZOOMS:
            fail(f"override zoom must be one of {ZOOMS}, got {zoom!r}")
        claimed: set[tuple[int, int]] = set()
        for lat, lon in ov.get("tiles", []):
            claimed.add((int(lat), int(lon)))
        icaos = {i.upper() for i in ov.get("airports", [])}
        if icaos:
            radius = int(ov.get("radius_tiles", 1))
            for lat, lon in resolve_airports(icaos, profiles_dir).values():
                base = (floor(lat), floor(lon))
                for dlat in range(-radius, radius + 1):
                    for dlon in range(-radius, radius + 1):
                        claimed.add((base[0] + dlat, base[1] + dlon))
        for key in claimed:
            if key in desired:
                desired[key] = zoom

    # Fall back to whichever zoom actually exists for the tile.
    plan: dict[tuple[int, int], str] = {}
    for key, zoom in desired.items():
        have = tiles[key]
        if zoom in have:
            plan[key] = zoom
        else:
            other = next(iter(have - {zoom}), None)
            if other:
                plan[key] = other
    return plan


def format_tile(key: tuple[int, int], zoom: str) -> str:
    lat, lon = key
    return f"zOrtho4XP_{zoom}_{lat:+03d}{lon:+04d}"


def rewrite_ini(ini: Path, tiles: dict[tuple[int, int], set[str]],
                plan: dict[tuple[int, int], str], dry_run: bool) -> dict[str, int]:
    lines = ini.read_text(encoding="utf-8").splitlines()
    stats = {"enabled": 0, "disabled": 0, "changed": 0, "appended": 0}
    seen: set[tuple[tuple[int, int], str]] = set()
    out: list[str] = []

    for line in lines:
        m = ORTHO_RE.match(line)
        if not m:
            out.append(line)
            continue
        zoom, key = m.group(2), tile_key(m.group(3), m.group(4))
        if key not in tiles or zoom not in tiles.get(key, set()):
            out.append(line)          # unknown/missing folder: leave untouched
            continue
        seen.add((key, zoom))
        want_enabled = plan.get(key) == zoom
        prefix = "SCENERY_PACK" if want_enabled else "SCENERY_PACK_DISABLED"
        new_line = f"{prefix} Custom Scenery/{format_tile(key, zoom)}/"
        stats["enabled" if want_enabled else "disabled"] += 1
        if new_line != line:
            stats["changed"] += 1
        out.append(new_line)

    for key, zooms in sorted(tiles.items()):
        for zoom in sorted(zooms):
            if (key, zoom) in seen:
                continue
            want_enabled = plan.get(key) == zoom
            prefix = "SCENERY_PACK" if want_enabled else "SCENERY_PACK_DISABLED"
            out.append(f"{prefix} Custom Scenery/{format_tile(key, zoom)}/")
            stats["enabled" if want_enabled else "disabled"] += 1
            stats["appended"] += 1
            stats["changed"] += 1

    if not dry_run and stats["changed"]:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(ini, ini.with_name(f"scenery_packs.ini.{stamp}.bak"))
        fd, tmp = tempfile.mkstemp(dir=str(ini.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, ini)
    return stats


def cmd_status(root: Path) -> int:
    state_file = root / "scenery_profiles" / "active_profile.json"
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        print(f"Active profile: {state.get('name')} (applied {state.get('applied')})")
    else:
        print("Active profile: unknown (never applied)")
    counts = {z: 0 for z in ZOOMS}
    ini = root / "scenery_packs.ini"
    for line in ini.read_text(encoding="utf-8").splitlines():
        m = ORTHO_RE.match(line)
        if m and m.group(1) == "SCENERY_PACK":
            counts[m.group(2)] = counts.get(m.group(2), 0) + 1
    print("Enabled ortho tiles: " + ", ".join(f"{z}={n}" for z, n in sorted(counts.items())))
    return 0


def cmd_list(profiles_dir: Path) -> int:
    for p in sorted(profiles_dir.glob("*.json")):
        if p.name in ("active_profile.json", "airport_coords_cache.json"):
            continue
        try:
            prof = json.loads(p.read_text(encoding="utf-8"))
            print(f"{p.stem:15s} {prof.get('description', '')}")
        except (OSError, ValueError) as exc:
            print(f"{p.stem:15s} (unreadable: {exc})")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    root = scenery_root()
    profiles_dir = root / "scenery_profiles"

    if "--status" in args:
        return cmd_status(root)
    if "--list" in args:
        return cmd_list(profiles_dir)

    name = args[0] if args else os.environ.get("SCENERY_PROFILE", "").strip()
    if not name:
        fail("no profile given (argv or SCENERY_PROFILE env)")
    profile_file = profiles_dir / f"{name}.json"
    if not profile_file.exists():
        fail(f"profile not found: {profile_file}")
    profile = json.loads(profile_file.read_text(encoding="utf-8"))

    if not dry_run and sim_running():
        fail(f"{os.environ['SIM_EXE_NAME']} is running; quit X-Plane first")

    tiles = scan_tiles(root)
    if not tiles:
        fail("no zOrtho4XP_Z*_ tile folders found — nothing to manage")
    plan = plan_tiles(profile, tiles, profiles_dir)
    stats = rewrite_ini(root / "scenery_packs.ini", tiles, plan, dry_run)

    mode = "DRY RUN — no files written" if dry_run else "applied"
    print(f"[set_scenery_profile] profile '{name}' {mode}")
    print(f"  tiles on disk: {len(tiles)}  |  enabled: {stats['enabled']}  "
          f"disabled: {stats['disabled']}  changed lines: {stats['changed']}  "
          f"appended: {stats['appended']}")
    by_zoom = {z: sum(1 for v in plan.values() if v == z) for z in ZOOMS}
    print("  plan: " + ", ".join(f"{z}={n} active" for z, n in sorted(by_zoom.items())))

    if not dry_run:
        state = {"name": name, "applied": datetime.now().isoformat(timespec="seconds")}
        (profiles_dir / "active_profile.json").write_text(
            json.dumps(state, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
