"""
simpit_common.tilemath
======================
Pure slippy-tile / Ortho4XP-atlas math for the ortho cache agent.

Everything here is arithmetic on numbers and strings — no filesystem,
no network — so it is unit-testable offline and importable from the
stdlib-only live verifier (``tests/live/ortho_checks.py``).

Atlas scheme (verified on RandhawaNAS 2026-07-05)
-------------------------------------------------
* Scenery folders: ``zOrtho4XP_Z{16|18}_{lat}{lon}``, one per 1°×1° DSF
  square, lat/lon **floored** (KEEN at 42.898,-72.271 is in ``+42-073``;
  ``int()`` truncation would put western-hemisphere tiles one degree off).
* Atlas textures: ``{y16}_{x16}_{provider}{zoom}.dds`` — **y first**,
  slippy coords floored to multiples of 16 at the atlas's *own* zoom.
  Each atlas is a 16×16 grid of 256 px tiles (4096², DXT1+mips,
  uniformly ~10.7 MB).
* Zooms are MIXED within one folder: the folder's Z16/Z18 label is a
  scenery-set label, not the atlas zoom (``Z18_+42-073`` holds a BI16
  base plus BI17/18/19 airport patches). Position→atlas resolution
  therefore needs the folder's textures index and tries the highest
  zoom first; this module only supplies the per-zoom arithmetic.
"""
from __future__ import annotations

import math
import re

# Atlases are 16×16 blocks of slippy tiles at their own zoom.
ATLAS_GRID = 16

# {y16}_{x16}_{provider}{zoom}.dds — providers observed on the NAS:
# BI (Bing) and Arc (ArcGIS, e.g. 103824_44416_Arc18.dds in Z18_+34-119,
# found live 2026-07-19), so the provider match must be case-mixed.
ATLAS_RE = re.compile(r"^(\d+)_(\d+)_([A-Za-z]+?)(\d{1,2})\.dds$")

# Meters per degree of latitude (and of longitude at the equator).
_M_PER_DEG = 111_320.0


# ── Slippy tiles ─────────────────────────────────────────────────────────
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
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def tile_to_latlon(x: float, y: float, zoom: int) -> tuple[float, float]:
    """Convert slippy tile coords to the lat/lon of the tile's NW corner.

    Accepts fractional tile coords, so ``tile_to_latlon(x + 0.5, y + 0.5,
    z)`` is the tile center and ``tile_to_latlon(x + 1, y + 1, z)`` its
    SE corner.

    Args:
        x: tile x (may be fractional).
        y: tile y (may be fractional).
        zoom: slippy zoom level.

    Returns:
        (lat, lon) in degrees.
    """
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


# ── Atlases ──────────────────────────────────────────────────────────────
def atlas_origin(x: int, y: int) -> tuple[int, int]:
    """Floor tile coords to the origin of the 16×16 atlas containing them."""
    return x // ATLAS_GRID * ATLAS_GRID, y // ATLAS_GRID * ATLAS_GRID


def latlon_to_atlas(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Return the (x16, y16) origin of the atlas covering a position.

    Args:
        lat: latitude in degrees.
        lon: longitude in degrees.
        zoom: the atlas's own zoom level.

    Returns:
        (x16, y16) — tile coords of the atlas's NW corner, multiples of 16.
    """
    return atlas_origin(*latlon_to_tile(lat, lon, zoom))


def atlas_filename(x16: int, y16: int, zoom: int, provider: str = "BI") -> str:
    """Build an atlas .dds filename. Note the verified y-first order."""
    return f"{y16}_{x16}_{provider}{zoom}.dds"


def parse_atlas_filename(name: str) -> tuple[int, int, int] | None:
    """Parse an atlas filename into ``(x16, y16, zoom)``.

    Args:
        name: bare filename, e.g. ``"53600_28416_BI17.dds"``.

    Returns:
        (x16, y16, zoom) with x/y in tile coords (the on-disk name is
        y-first; this returns the conventional x-first order), or None
        if the name is not an atlas (masks, .ter files, etc.).
    """
    m = ATLAS_RE.match(name)
    if not m:
        return None
    return int(m.group(2)), int(m.group(1)), int(m.group(4))


def atlas_bounds(x16: int, y16: int,
                 zoom: int) -> tuple[float, float, float, float]:
    """Geographic bounding box of one atlas.

    Args:
        x16: atlas origin tile x (multiple of 16).
        y16: atlas origin tile y (multiple of 16).
        zoom: the atlas's zoom level.

    Returns:
        (lat_min, lat_max, lon_min, lon_max). Remember slippy y grows
        southward: y16 is the *north* edge.
    """
    lat_max, lon_min = tile_to_latlon(x16, y16, zoom)
    lat_min, lon_max = tile_to_latlon(x16 + ATLAS_GRID, y16 + ATLAS_GRID, zoom)
    return lat_min, lat_max, lon_min, lon_max


def ring_bounds(x16: int, y16: int, zoom: int,
                n_rings: int) -> tuple[float, float, float, float]:
    """Geographic bbox of the (2n+1)×(2n+1) atlas block centered on an atlas.

    The keep set is defined in ATLAS units (a ground-tile ring would
    dedupe to about one atlas), so the ring is n_rings atlas steps in
    each direction on the atlas grid at the given zoom.

    Args:
        x16: center atlas origin tile x.
        y16: center atlas origin tile y.
        zoom: the atlas grid's zoom level.
        n_rings: ring count; 4 → a 9×9 atlas block.

    Returns:
        (lat_min, lat_max, lon_min, lon_max) of the whole block.
    """
    step = n_rings * ATLAS_GRID
    lat_max, lon_min = tile_to_latlon(x16 - step, y16 - step, zoom)
    lat_min, lon_max = tile_to_latlon(x16 + step + ATLAS_GRID,
                                      y16 + step + ATLAS_GRID, zoom)
    return lat_min, lat_max, lon_min, lon_max


def atlases_in_bounds(bounds: tuple[float, float, float, float],
                      zoom: int) -> list[tuple[int, int]]:
    """Enumerate every atlas origin at `zoom` overlapping a geographic bbox.

    Used to intersect a keep-set ring's bbox with a folder's textures
    index at each zoom present in that index (higher-zoom airport
    patches inside the ring must be kept warm too).

    Args:
        bounds: (lat_min, lat_max, lon_min, lon_max).
        zoom: atlas zoom to enumerate at.

    Returns:
        List of (x16, y16) atlas origins whose area intersects `bounds`.
    """
    lat_min, lat_max, lon_min, lon_max = bounds
    n = 2 ** zoom
    # Shrink the corners inward by a hair so an edge that lands exactly
    # on an atlas boundary doesn't drag in the neighboring row/column.
    eps = 1e-9
    x_min, y_min = atlas_origin(*latlon_to_tile(lat_max - eps, lon_min + eps, zoom))
    x_max, y_max = atlas_origin(*latlon_to_tile(lat_min + eps, lon_max - eps, zoom))
    out: list[tuple[int, int]] = []
    for y16 in range(max(0, y_min), min(n - 1, y_max) + 1, ATLAS_GRID):
        for x16 in range(max(0, x_min), min(n - 1, x_max) + 1, ATLAS_GRID):
            out.append((x16, y16))
    return out


# ── DSF folders ──────────────────────────────────────────────────────────
def dsf_folder_name(lat: float, lon: float, zoom_label: int) -> str:
    """Build the scenery folder name covering a position.

    Args:
        lat: latitude in degrees.
        lon: longitude in degrees.
        zoom_label: scenery-set label (18 or 16) — NOT the atlas zoom.

    Returns:
        e.g. ``"zOrtho4XP_Z18_+42-073"``. Uses math.floor, not int():
        truncation would misplace every southern/western hemisphere tile.
    """
    return (f"zOrtho4XP_Z{zoom_label}_"
            f"{math.floor(lat):+03d}{math.floor(lon):+04d}")


def dsf_squares_in_bounds(
        bounds: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    """Enumerate every 1°×1° DSF square a geographic bbox overlaps.

    A keep set near a degree boundary spans multiple DSF folders, so the
    agent must derive each atlas's folder from the *atlas's* area — never
    from the aircraft position alone.

    Args:
        bounds: (lat_min, lat_max, lon_min, lon_max).

    Returns:
        List of (lat_floor, lon_floor) squares, row-major.
    """
    lat_min, lat_max, lon_min, lon_max = bounds
    return [(la, lo)
            for la in range(math.floor(lat_min), math.floor(lat_max) + 1)
            for lo in range(math.floor(lon_min), math.floor(lon_max) + 1)]


# ── Track projection ─────────────────────────────────────────────────────
def project_position(lat: float, lon: float, track_deg: float,
                     gs_ms: float, seconds: float) -> tuple[float, float]:
    """Advance a position along the ground track (flat-earth approximation).

    Uses hpath (ground track), not psi (heading): heading diverges from
    track in crosswind, which would skew the lookahead by up to an atlas
    width over 45 s.

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
    dlat = dist * math.cos(t) / _M_PER_DEG
    dlon = dist * math.sin(t) / (_M_PER_DEG * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon
