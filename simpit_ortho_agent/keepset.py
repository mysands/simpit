"""
simpit_ortho_agent.keepset
==========================
Keep-set computation: which atlases must stay warm right now.

The keep set is the union of two rings in ATLAS units (ground-tile
rings would be useless — a ZL18 tile is ~150 m, so a 4-tile ring
dedupes to about one atlas):

    keep = atlases(ring(current_atlas, N)) ∪ atlases(ring(projected_atlas, N))

where the projected position is the current one advanced along the
GROUND TRACK (hpath, not heading — heading diverges from track in
crosswind) by ``groundspeed * lookahead_seconds``.

Each ring is drawn on the atlas grid at its center folder's *base* zoom
(the most common zoom in the folder's textures index). The ring's
geographic bbox is then intersected with every overlapped folder's
index at EVERY zoom present, so higher-zoom airport patches inside the
box are included. The result is deduped at atlas level (neighbors share
atlases) and ordered nearest-first from the aircraft.

Defaults (config-overridable): N_RINGS=4 → a 9×9 base-atlas block
(≈44 km across at BI17, ≈88 km at BI16, ~0.9 GB either way at
10.7 MB/atlas); LOOKAHEAD_SECONDS=45.

``heading_offset_deg`` (per-machine view bias for the side-view
machines) is defined in config but deliberately NOT applied in v1 —
identical keep sets everywhere is the simplest correct behavior.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from simpit_common import tilemath

from .atlas_index import SceneryIndex


@dataclass(frozen=True, order=True)
class KeepAtlas:
    """One keep-set member: an atlas file within a scenery folder.

    Attributes:
        folder: scenery folder name (e.g. ``"zOrtho4XP_Z18_+42-073"``).
        filename: atlas filename inside ``<folder>/textures``.
        x16: atlas origin tile x at `zoom`.
        y16: atlas origin tile y at `zoom`.
        zoom: the atlas's own zoom level.
    """
    folder: str
    filename: str
    x16: int
    y16: int
    zoom: int

    def rel_path(self) -> str:
        """Path of this atlas relative to the scenery root."""
        return f"{self.folder}/textures/{self.filename}"


def _ring_atlases(lat: float, lon: float, n_rings: int,
                  scenery: SceneryIndex) -> set[KeepAtlas]:
    """Collect the atlases of one ring centered on a position.

    The ring's grid zoom comes from the center position's folder (its
    base zoom). If no scenery folder covers the center (open water),
    the ring contributes nothing — there is nothing to prime there.
    """
    center = scenery.folder_for_square(math.floor(lat), math.floor(lon))
    if center is None or center[1].base_zoom == 0:
        return set()
    base_zoom = center[1].base_zoom
    cx16, cy16 = tilemath.latlon_to_atlas(lat, lon, base_zoom)
    bounds = tilemath.ring_bounds(cx16, cy16, base_zoom, n_rings)

    keep: set[KeepAtlas] = set()
    # A ring near a degree boundary spans multiple DSF folders, so each
    # atlas's folder is derived from the ring's area — never from the
    # aircraft position alone.
    for lat_floor, lon_floor in tilemath.dsf_squares_in_bounds(bounds):
        found = scenery.folder_for_square(lat_floor, lon_floor)
        if found is None:
            continue
        folder_name, index = found
        # Clip the ring bbox to this 1°×1° square so we only pick up
        # the folder's own atlases, at every zoom the folder holds.
        clipped = (max(bounds[0], float(lat_floor)),
                   min(bounds[1], lat_floor + 1.0),
                   max(bounds[2], float(lon_floor)),
                   min(bounds[3], lon_floor + 1.0))
        for zoom in index.zooms:
            for x16, y16 in tilemath.atlases_in_bounds(clipped, zoom):
                name = index.atlases.get((x16, y16, zoom))
                if name:
                    keep.add(KeepAtlas(folder=folder_name, filename=name,
                                       x16=x16, y16=y16, zoom=zoom))
    return keep


def _distance_m(lat: float, lon: float, atlas: KeepAtlas) -> float:
    """Flat-earth distance from a position to an atlas's center, meters."""
    alat, alon = tilemath.tile_to_latlon(atlas.x16 + tilemath.ATLAS_GRID / 2,
                                         atlas.y16 + tilemath.ATLAS_GRID / 2,
                                         atlas.zoom)
    dy = (alat - lat) * 111_320.0
    dx = (alon - lon) * 111_320.0 * math.cos(math.radians(lat))
    return math.hypot(dx, dy)


def compute_keep_set(lat: float, lon: float, track_deg: float, gs_ms: float,
                     n_rings: int, lookahead_seconds: float,
                     scenery: SceneryIndex) -> list[KeepAtlas]:
    """Compute the ordered keep set for one position sample.

    Args:
        lat: aircraft latitude in degrees.
        lon: aircraft longitude in degrees.
        track_deg: ground track (hpath), degrees true.
        gs_ms: groundspeed in m/s.
        n_rings: ring radius in atlas units.
        lookahead_seconds: how far ahead along track to project.
        scenery: folder/index resolver for the mounted scenery root.

    Returns:
        Deduped atlases, nearest-first from the *current* position — so
        the atlases under the aircraft are primed before the lookahead
        ones.
    """
    keep = _ring_atlases(lat, lon, n_rings, scenery)
    plat, plon = tilemath.project_position(lat, lon, track_deg, gs_ms,
                                           lookahead_seconds)
    keep |= _ring_atlases(plat, plon, n_rings, scenery)
    return sorted(keep, key=lambda a: _distance_m(lat, lon, a))
