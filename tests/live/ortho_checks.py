"""Pytest-free live checks for the ortho scenery cache chain.

This module is deliberately stdlib-only (psutil is optional) so it can be
copied to a slave machine together with `verify_live.py` and run with any
Python 3.10+ - no repo checkout, no pytest, no CI involvement:

    copy ortho_checks.py verify_live.py  ->  slave
    python verify_live.py [ortho_agent.json]

The same check functions back the pytest wrappers in
`tests/live/test_ortho_chain.py` for use on a dev machine.

The tile/atlas helpers import the PRODUCTION implementations
(`simpit_common.tilemath` / `simpit_ortho_agent.atlas_index`) when the
repo is importable, so verification exercises the agent's real code. On
a bare slave (two copied files, no repo) the import fails and the
self-contained fallback copies below take over — same logic, verified
against the same NAS listings (2026-07-05).
"""
from __future__ import annotations

import json
import math
import os
import re
import socket
import stat
import struct
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

DEFAULTS: dict = {
    "mount_root": "X:/",
    "rc_addr": "127.0.0.1:5572",
    "cache_dir": "",
    "master_ip": "127.0.0.1",
    "xp_udp_port": 49000,
    "active_zoom": 18,
    "lookahead_seconds": 45,
    "cache_max_bytes": 160 * 10**9,
    "xplane_custom_scenery": "C:/X-Plane 12.1/Custom Scenery",
    "prime_timeout_seconds": 90,
}

# Verified on RandhawaNAS 2026-07-05: {y16}_{x16}_{provider}{zoom}.dds,
# y first, coords floored to multiples of 16 at that atlas's own zoom.
ATLAS_RE = re.compile(r"^(\d+)_(\d+)_([A-Z]+)(\d{1,2})\.dds$")

POSITION_DATAREFS = {
    1: "sim/flightmodel/position/latitude",
    2: "sim/flightmodel/position/longitude",
    3: "sim/flightmodel/position/groundspeed",
    4: "sim/flightmodel/position/hpath",
}


@dataclass
class CheckResult:
    """Outcome of one live check.

    Attributes:
        name: short check identifier.
        status: PASS, FAIL, or SKIP.
        message: one-line human-readable outcome.
        data: extra machine-readable detail (e.g. the position sample).
    """
    name: str
    status: str
    message: str
    data: dict = field(default_factory=dict)


def load_config(path: str | None = None) -> dict:
    """Load verifier configuration.

    Args:
        path: explicit config file path; falls back to the
            ORTHO_AGENT_CONFIG env var, then ./ortho_agent.json.

    Returns:
        DEFAULTS overlaid with the config file if one exists.
    """
    merged = dict(DEFAULTS)
    candidate = Path(path or os.environ.get("ORTHO_AGENT_CONFIG",
                                            "ortho_agent.json"))
    if candidate.is_file():
        merged.update(json.loads(candidate.read_text(encoding="utf-8")))
    # ortho_agent.json states the cap in GB (cache_max_gb, mirrored into
    # the mount command); the checks compare bytes. Per-machine caps
    # differ (e.g. 460G on CENTERLEFT), so the config must win.
    if "cache_max_gb" in merged:
        merged["cache_max_bytes"] = int(merged["cache_max_gb"]) * 10**9
    return merged


# ── low-level helpers ────────────────────────────────────────────────────

def rc_post(rc_addr: str, command: str, params: dict | None = None,
            timeout: float = 5.0) -> dict:
    """POST a command to the rclone rc API and return the JSON response.

    Args:
        rc_addr: host:port of the rc listener (e.g. "127.0.0.1:5572").
        command: rc command path (e.g. "vfs/stats").
        params: JSON-serializable request body, or None for empty.
        timeout: socket timeout in seconds.

    Returns:
        Decoded JSON response.

    Raises:
        OSError: if the rc endpoint is unreachable.
    """
    body = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{rc_addr}/{command}", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_in_cache(filename: str, stats: dict | None = None,
                  cache_dir: str = "") -> Path | None:
    """Locate a file inside rclone's on-disk VFS cache (read-only check).

    Args:
        filename: bare atlas filename, e.g. "53600_28416_BI17.dds".
        stats: optional vfs/stats response; if it carries a cache path,
            that is searched first.
        cache_dir: custom --cache-dir if the mount uses one (config
            key "cache_dir"); empty = rclone's default location.

    Returns:
        Path of the cached copy, or None if not cached. The cache dir is
        only ever *read* here - the never-write rule applies to the
        verifier too.
    """
    roots: list[Path] = []
    if stats:
        disk = stats.get("diskCache") or {}
        if disk.get("path"):
            roots.append(Path(disk["path"]))
    if cache_dir:
        roots.append(Path(cache_dir) / "vfs")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "rclone" / "vfs")
    for root in roots:
        if root.is_dir():
            hit = next(root.rglob(filename), None)
            if hit:
                return hit
    return None


def rref_snapshot(host: str, port: int, timeout: float = 5.0) -> dict[str, float]:
    """Subscribe to the position datarefs once and collect one full sample.

    Same wire format as simpit_common.probes._eval_xplane_dataref: RREF
    subscribe packets at 5 Hz, unsubscribe (freq 0) before returning.

    Args:
        host: X-Plane master IP.
        port: X-Plane UDP port.
        timeout: overall deadline in seconds for a complete sample.

    Returns:
        {"lat": .., "lon": .., "gs": .., "track": ..}

    Raises:
        TimeoutError: if X-Plane did not deliver all four values in time.
    """
    def pkt(freq: int, idx: int, dataref: str) -> bytes:
        return struct.pack("<4sxii400s", b"RREF", freq, idx,
                           dataref.encode("latin-1"))

    got: dict[int, float] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(0.5)
        for idx, ref in POSITION_DATAREFS.items():
            s.sendto(pkt(5, idx, ref), (host, port))
        deadline = time.monotonic() + timeout
        try:
            while len(got) < len(POSITION_DATAREFS):
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"no complete RREF sample from {host}:{port} in "
                        f"{timeout}s (got ids {sorted(got)})")
                try:
                    data, _ = s.recvfrom(4096)
                except socket.timeout:
                    continue
                if data[:4] != b"RREF":
                    continue
                payload = data[5:]
                for off in range(0, len(payload) - 7, 8):
                    i, value = struct.unpack_from("<if", payload, off)
                    if i in POSITION_DATAREFS:
                        got[i] = value
        finally:
            for idx, ref in POSITION_DATAREFS.items():
                s.sendto(pkt(0, idx, ref), (host, port))
    return {"lat": got[1], "lon": got[2], "gs": got[3], "track": got[4]}


def agent_running() -> bool:
    """Return True if a simpit ortho agent process is running locally.

    Uses psutil when available, otherwise falls back to `tasklist` so the
    check works on slaves without any packages installed.
    """
    try:
        import psutil
    except ImportError:
        out = subprocess.run(["tasklist", "/FO", "CSV"],
                             capture_output=True, text=True, check=False)
        return "ortho_agent" in out.stdout.lower() or \
               "ortho-agent" in out.stdout.lower()
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            blob = " ".join([proc.info["name"] or ""]
                            + (proc.info["cmdline"] or [])).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "ortho_agent" in blob or "ortho-agent" in blob:
            return True
    return False


# ── tile / atlas math ────────────────────────────────────────────────────
# Production imports when the repo is on the path; stdlib-only fallback
# copies otherwise (bare-slave deployment of just these two files).
try:
    from simpit_common.tilemath import dsf_folder_name, latlon_to_tile, project_position
    from simpit_ortho_agent.atlas_index import load_atlas_index, resolve_atlas
    USING_PRODUCTION_CODE = True
except ImportError:
    USING_PRODUCTION_CODE = False

    def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
        """Convert lat/lon to slippy tile x, y at a zoom level.

        Args:
            lat: latitude in degrees.
            lon: longitude in degrees.
            zoom: slippy zoom level.

        Returns:
            (x, y) tile indices.
        """
        n = 2 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi)
                / 2.0 * n)
        return x, y

    def dsf_folder_name(lat: float, lon: float, zoom_label: int) -> str:
        """Build the scenery folder name covering a position.

        Args:
            lat: latitude in degrees.
            lon: longitude in degrees.
            zoom_label: scenery-set label (18 or 16) - NOT the atlas zoom.

        Returns:
            e.g. "zOrtho4XP_Z18_+42-073" (floor, not truncation).
        """
        return (f"zOrtho4XP_Z{zoom_label}_"
                f"{math.floor(lat):+03d}{math.floor(lon):+04d}")

    def load_atlas_index(folder: Path) -> dict[tuple[int, int, int], str]:
        """Index a scenery folder's textures directory.

        Args:
            folder: a zOrtho4XP_* folder on the mount.

        Returns:
            {(x16, y16, zoom): filename} for every atlas .dds present.
        """
        index: dict[tuple[int, int, int], str] = {}
        for entry in (folder / "textures").iterdir():
            m = ATLAS_RE.match(entry.name)
            if m:
                index[(int(m.group(2)), int(m.group(1)),
                       int(m.group(4)))] = entry.name
        return index

    def resolve_atlas(lat: float, lon: float,
                      index: dict[tuple[int, int, int], str]) -> str | None:
        """Find the atlas covering a position, highest zoom first.

        Zooms are mixed within one folder (airport patches over a
        lower-zoom base), so every zoom present in the index is tried
        in descending order.

        Args:
            lat: latitude in degrees.
            lon: longitude in degrees.
            index: output of load_atlas_index().

        Returns:
            Atlas filename, or None (water / unbuilt area).
        """
        for zoom in sorted({k[2] for k in index}, reverse=True):
            x, y = latlon_to_tile(lat, lon, zoom)
            name = index.get((x // 16 * 16, y // 16 * 16, zoom))
            if name:
                return name
        return None

    def project_position(lat: float, lon: float, track_deg: float,
                         gs_ms: float, seconds: float) -> tuple[float, float]:
        """Advance a position along the ground track (flat-earth approx).

        Args:
            lat: latitude in degrees.
            lon: longitude in degrees.
            track_deg: ground track, degrees true.
            gs_ms: groundspeed in m/s.
            seconds: lookahead time.

        Returns:
            (lat, lon) of the projected position.
        """
        dist = gs_ms * seconds
        t = math.radians(track_deg)
        dlat = dist * math.cos(t) / 111_320.0
        dlon = dist * math.sin(t) / (111_320.0 * math.cos(math.radians(lat)))
        return lat + dlat, lon + dlon


def find_dsf_folder(mount_root: Path, lat: float, lon: float,
                    preferred_label: int) -> Path | None:
    """Locate the scenery folder for a position, trying both zoom labels.

    Args:
        mount_root: root of the rclone mount (Custom Scenery level).
        lat: latitude in degrees.
        lon: longitude in degrees.
        preferred_label: try this scenery-set label (18/16) first.

    Returns:
        Existing folder Path, or None if neither set covers the position.
    """
    for label in (preferred_label, 16 if preferred_label == 18 else 18):
        folder = mount_root / dsf_folder_name(lat, lon, label)
        if folder.is_dir():
            return folder
    return None


def read_fully(path: Path, chunk: int = 8 * 1024 * 1024) -> tuple[int, float]:
    """Sequentially read a file to EOF (same access pattern as the primer).

    Args:
        path: file to read.
        chunk: read size in bytes.

    Returns:
        (total_bytes, elapsed_seconds).
    """
    total = 0
    start = time.perf_counter()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            total += len(block)
    return total, time.perf_counter() - start


def _scenery_folders(mount_root: Path, limit: int = 5) -> list[Path]:
    """Return the first few zOrtho4XP_* folders on the mount."""
    found: list[Path] = []
    for entry in mount_root.iterdir():
        if entry.name.startswith("zOrtho4XP_") and entry.is_dir():
            found.append(entry)
            if len(found) >= limit:
                break
    return found


def _wait_cached(filename: str, full_size: int, rc_addr: str,
                 timeout: float, cache_dir: str = "") -> Path | None:
    """Poll the VFS cache until `filename` is fully cached or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            stats = rc_post(rc_addr, "vfs/stats")
        except OSError:
            stats = None
        hit = find_in_cache(filename, stats, cache_dir)
        if hit and hit.stat().st_size >= full_size:
            return hit
        time.sleep(2.0)
    return None


# ── checks (each returns a CheckResult; never raises for expected faults) ─

def check_mount(cfg: dict) -> CheckResult:
    """Mount is up and lists zOrtho4XP scenery folders."""
    root = Path(cfg["mount_root"])
    if not root.exists():
        return CheckResult("mount", FAIL,
                           f"rclone mount not present at {root}")
    folders = _scenery_folders(root)
    if not folders:
        return CheckResult(
            "mount", FAIL,
            f"{root} has no zOrtho4XP_* folders - mount target should be "
            "randhawanas:XPlane12/Custom Scenery")
    return CheckResult("mount", PASS,
                       f"{root} up, scenery folders visible "
                       f"(e.g. {folders[0].name})")


def check_atlas_naming(cfg: dict) -> CheckResult:
    """Sampled atlas filenames still match the verified naming scheme."""
    root = Path(cfg["mount_root"])
    folders = _scenery_folders(root, limit=1) if root.exists() else []
    if not folders:
        return CheckResult("atlas-naming", SKIP, "mount not available")
    names = [p.name for p in (folders[0] / "textures").iterdir()
             if p.suffix == ".dds"][:20]
    if not names:
        return CheckResult("atlas-naming", FAIL,
                           f"no .dds atlases in {folders[0].name}/textures")
    for name in names:
        m = ATLAS_RE.match(name)
        if not m or int(m.group(1)) % 16 or int(m.group(2)) % 16:
            return CheckResult(
                "atlas-naming", FAIL,
                f"unexpected atlas filename {name!r} - agent tilemath "
                "assumptions are stale")
    return CheckResult("atlas-naming", PASS,
                       f"{len(names)} sampled names match "
                       "{{y16}}_{{x16}}_{{provider}}{{zoom}}.dds")


def _is_reparse_point(path: Path) -> bool:
    """True for NTFS junctions and symlinks (cross-platform fallback)."""
    try:
        st = path.stat(follow_symlinks=False)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)   # Windows-only field
    if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    return path.is_symlink()


def check_scenery_link(cfg: dict) -> CheckResult:
    """Custom Scenery resolves onto the mount, wholesale or per-folder.

    Two supported layouts:

    * The whole ``Custom Scenery`` directory is one junction onto the
      mount (the current fleet setup: ``mklink /J`` → ``X:\\``).
    * ``Custom Scenery`` is a real folder holding per-folder junctions
      or symlinks into the mount.

    NTFS cannot hard-link directories; only reparse points (junctions,
    symlinks) are considered links — ordinary files that merely LIVE
    under a junctioned tree (scenery_packs.ini and friends) are not.
    """
    scenery = Path(cfg["xplane_custom_scenery"])
    mount_drive = Path(cfg["mount_root"]).drive.upper()
    if not scenery.is_dir():
        return CheckResult("scenery-link", SKIP,
                           f"Custom Scenery not found at {scenery} - set "
                           "xplane_custom_scenery in config")
    if _is_reparse_point(scenery):
        try:
            resolved = scenery.resolve()
        except OSError as exc:
            return CheckResult("scenery-link", FAIL,
                               f"{scenery} is a broken link: {exc}")
        if resolved.drive.upper() == mount_drive:
            return CheckResult("scenery-link", PASS,
                               f"{scenery} is a junction onto the mount "
                               f"({resolved})")
        return CheckResult("scenery-link", SKIP,
                           f"{scenery} links to {resolved}, not the "
                           f"{mount_drive} mount")
    links, broken = [], []
    for entry in scenery.iterdir():
        if not _is_reparse_point(entry):
            continue
        try:
            resolved = entry.resolve()
        except OSError:
            broken.append(entry.name)
            continue
        if resolved.drive.upper() != mount_drive:
            continue
        (links if resolved.is_dir() else broken).append(entry.name)
    if broken:
        return CheckResult("scenery-link", FAIL,
                           f"broken links into the mount: {broken}")
    if not links:
        return CheckResult("scenery-link", SKIP,
                           f"no junctions/symlinks into {mount_drive} "
                           f"found in {scenery}")
    return CheckResult("scenery-link", PASS,
                       f"{len(links)} link(s) resolve onto the mount "
                       f"(e.g. {links[0]})")


def check_rc_cache(cfg: dict) -> CheckResult:
    """rc API answers and the disk cache is within the configured cap."""
    try:
        stats = rc_post(cfg["rc_addr"], "vfs/stats")
    except OSError as exc:
        return CheckResult("rc-cache", FAIL,
                           f"rclone rc unreachable on {cfg['rc_addr']}: {exc}")
    used = (stats.get("diskCache") or {}).get("bytesUsed")
    if used is None:
        return CheckResult("rc-cache", FAIL,
                           f"vfs/stats missing diskCache.bytesUsed: {stats}")
    cap = int(cfg["cache_max_bytes"])
    if used > cap * 1.05:
        return CheckResult(
            "rc-cache", FAIL,
            f"VFS cache {used / 1e9:.1f} GB exceeds {cap / 1e9:.0f} GB cap "
            "- check --vfs-cache-max-size / --vfs-cache-max-age")
    return CheckResult("rc-cache", PASS,
                       f"rc up; cache {used / 1e9:.2f} GB of "
                       f"{cap / 1e9:.0f} GB cap")


def check_cache_write(cfg: dict) -> CheckResult:
    """Reading an uncached atlas through the mount lands it in the cache.

    This is the exact mechanism agent priming relies on, so a FAIL here
    means priming cannot work (cache mode not 'full', or the cache is
    being purged by a misconfigured --vfs-cache-max-age).
    """
    root = Path(cfg["mount_root"])
    if not root.exists():
        return CheckResult("cache-write", SKIP, "mount not available")
    candidate = None
    for folder in _scenery_folders(root):
        for atlas in sorted((folder / "textures").glob("*.dds"))[:10]:
            if find_in_cache(atlas.name,
                             cache_dir=str(cfg.get("cache_dir", ""))) is None:
                candidate = atlas
                break
        if candidate:
            break
    if candidate is None:
        return CheckResult("cache-write", SKIP,
                           "all sampled atlases already cached")
    size, cold_s = read_fully(candidate)
    cached = _wait_cached(candidate.name, size, cfg["rc_addr"], timeout=30.0,
                          cache_dir=str(cfg.get("cache_dir", "")))
    if not cached:
        return CheckResult(
            "cache-write", FAIL,
            f"{candidate.name} not in VFS cache 30 s after a full read - "
            "is --vfs-cache-mode full set and --vfs-cache-max-age large?")
    _, warm_s = read_fully(candidate)
    return CheckResult("cache-write", PASS,
                       f"{candidate.name} cached after read "
                       f"({size / 1e6:.1f} MB; cold {cold_s:.2f}s, "
                       f"warm {warm_s:.2f}s)")


def check_rref(cfg: dict) -> CheckResult:
    """X-Plane delivers a plausible position sample over RREF."""
    try:
        pos = rref_snapshot(cfg["master_ip"], int(cfg["xp_udp_port"]))
    except (TimeoutError, OSError) as exc:
        return CheckResult("rref", FAIL,
                           f"X-Plane RREF feed unavailable: {exc}")
    sane = (-90 <= pos["lat"] <= 90 and -180 <= pos["lon"] <= 180
            and 0 <= pos["gs"] < 400 and -360 <= pos["track"] <= 360
            and (pos["lat"] or pos["lon"]))
    if not sane:
        return CheckResult("rref", FAIL,
                           f"implausible position sample: {pos}", {"pos": pos})
    return CheckResult(
        "rref", PASS,
        f"aircraft at {pos['lat']:.4f},{pos['lon']:.4f}, "
        f"gs {pos['gs']:.1f} m/s, track {pos['track']:.0f}°", {"pos": pos})


def _check_position_primed(name: str, lat: float, lon: float,
                           cfg: dict) -> CheckResult:
    """Shared body for the agent priming checks.

    Asserts the atlas covering (lat, lon) is fully cached WITHOUT reading
    it through the mount (that would self-prime and mask agent failure).
    """
    root = Path(cfg["mount_root"])
    folder = find_dsf_folder(root, lat, lon, int(cfg["active_zoom"]))
    if folder is None:
        return CheckResult(name, SKIP,
                           f"no scenery folder covers {lat:.3f},{lon:.3f}")
    atlas = resolve_atlas(lat, lon, load_atlas_index(folder))
    if atlas is None:
        return CheckResult(name, SKIP,
                           f"{lat:.3f},{lon:.3f} is water/unbuilt in "
                           f"{folder.name}")
    full_size = (folder / "textures" / atlas).stat().st_size
    timeout = float(cfg["prime_timeout_seconds"])
    cached = _wait_cached(atlas, full_size, cfg["rc_addr"], timeout,
                          cache_dir=str(cfg.get("cache_dir", "")))
    if not cached:
        return CheckResult(name, FAIL,
                           f"agent has not primed {folder.name}/{atlas} "
                           f"within {timeout:.0f}s")
    return CheckResult(name, PASS,
                       f"{folder.name}/{atlas} primed "
                       f"({full_size / 1e6:.1f} MB)")


def check_agent_current(cfg: dict, pos: dict[str, float] | None) -> CheckResult:
    """Agent has primed the atlas under the aircraft."""
    if pos is None:
        return CheckResult("agent-current", SKIP, "no position sample")
    if not agent_running():
        return CheckResult("agent-current", SKIP,
                           "ortho agent process not running")
    return _check_position_primed("agent-current", pos["lat"], pos["lon"], cfg)


def check_agent_lookahead(cfg: dict, pos: dict[str, float] | None) -> CheckResult:
    """Agent has primed the atlas at the ground-track lookahead position."""
    if pos is None:
        return CheckResult("agent-lookahead", SKIP, "no position sample")
    if not agent_running():
        return CheckResult("agent-lookahead", SKIP,
                           "ortho agent process not running")
    if pos["gs"] < 2.0:
        return CheckResult("agent-lookahead", SKIP,
                           "aircraft stationary - lookahead equals current")
    lat, lon = project_position(pos["lat"], pos["lon"], pos["track"],
                                pos["gs"], float(cfg["lookahead_seconds"]))
    return _check_position_primed("agent-lookahead", lat, lon, cfg)
