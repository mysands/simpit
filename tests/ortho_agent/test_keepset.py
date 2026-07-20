"""Tests for simpit_ortho_agent.keepset (geometry over tmp_path scenery).

Covers the handoff's keep-set cases: stationary / cruise 090 / turning /
crosswind (projection follows ground track, not heading), atlas-level
dedupe, mixed-zoom patches inside the ring, and degree-boundary spans.

Positions are atlas-aligned (the center of a BI16 atlas well inside the
+42-073 square) so ring spans and projection steps are exact and the
fixtures never spill into folders the test didn't create.
"""
from __future__ import annotations

import math

from simpit_common import tilemath as tm
from simpit_ortho_agent.atlas_index import SceneryIndex
from simpit_ortho_agent.keepset import compute_keep_set
from tests.ortho_agent.conftest import grid_names, make_folder

FOLDER = "zOrtho4XP_Z18_+42-073"
N_RINGS = 2          # 5×5 block keeps fixtures small
LOOKAHEAD = 45.0
RING_STEP = 16 * N_RINGS

# Center of the BI16 atlas containing (42.5, -72.5): ring math around it
# is exact, and 250 m/s × 90 s (~22.5 km ≈ 3 atlas steps) stays inside
# the degree square.
X16, Y16 = tm.latlon_to_atlas(42.5, -72.5, 16)
CENTER = tm.tile_to_latlon(X16 + 8, Y16 + 8, 16)


def _full_square(tmp_path, extra: list[str] = ()) -> SceneryIndex:
    """One folder holding every BI16 atlas of the whole +42-073 square."""
    names = grid_names(42.5, -72.5, 16, 40, 42, -73)  # generous cover
    make_folder(tmp_path, FOLDER, list(names) + list(extra))
    return SceneryIndex(tmp_path, active_zoom=18)


def test_stationary_keep_set_is_one_ring(tmp_path):
    """Parked: lookahead equals current, union collapses to one ring."""
    scenery = _full_square(tmp_path)
    keep = compute_keep_set(*CENTER, track_deg=270.0, gs_ms=0.0,
                            n_rings=N_RINGS, lookahead_seconds=LOOKAHEAD,
                            scenery=scenery)
    assert len(keep) == (2 * N_RINGS + 1) ** 2
    assert len(set(keep)) == len(keep)          # deduped


def test_nearest_first_starts_under_the_aircraft(tmp_path):
    """The first queued atlas is the one the aircraft is sitting on."""
    scenery = _full_square(tmp_path)
    keep = compute_keep_set(*CENTER, track_deg=90.0, gs_ms=250.0,
                            n_rings=N_RINGS, lookahead_seconds=LOOKAHEAD,
                            scenery=scenery)
    assert (keep[0].x16, keep[0].y16, keep[0].zoom) == (X16, Y16, 16)
    # And distances are non-decreasing from the current position.
    def dist(a):
        alat, alon = tm.tile_to_latlon(a.x16 + 8, a.y16 + 8, a.zoom)
        return math.hypot(alat - CENTER[0],
                          (alon - CENTER[1]) * math.cos(math.radians(CENTER[0])))
    assert all(dist(a) <= dist(b) + 1e-12
               for a, b in zip(keep, keep[1:], strict=False))


def test_cruise_extends_along_ground_track(tmp_path):
    """Moving east: the lookahead ring adds atlases east of the aircraft."""
    scenery = _full_square(tmp_path)
    parked = compute_keep_set(*CENTER, 90.0, 0.0, N_RINGS, LOOKAHEAD, scenery)
    cruise = compute_keep_set(*CENTER, 90.0, 250.0, N_RINGS, LOOKAHEAD, scenery)
    assert len(cruise) > len(parked)
    added = set(cruise) - set(parked)
    assert added and all(a.x16 > X16 for a in added)     # strictly east
    assert all(a in cruise for a in parked)              # union, not shift


def test_crosswind_projection_follows_track_not_heading(tmp_path):
    """Keep set depends only on the ground track passed in — a crabbed
    heading (not an input) cannot skew it. Track east vs track north
    reach past the ring in exactly the track direction."""
    scenery = _full_square(tmp_path)
    east = compute_keep_set(*CENTER, 90.0, 250.0, N_RINGS, 90.0, scenery)
    north = compute_keep_set(*CENTER, 0.0, 250.0, N_RINGS, 90.0, scenery)
    assert any(a.x16 > X16 + RING_STEP for a in east)
    assert not any(a.x16 > X16 + RING_STEP for a in north)
    assert any(a.y16 < Y16 - RING_STEP for a in north)   # slippy y grows south
    assert not any(a.y16 < Y16 - RING_STEP for a in east)


def test_turning_changes_the_keep_set(tmp_path):
    """A track change drops the old lookahead atlases from the keep set."""
    scenery = _full_square(tmp_path)
    east = compute_keep_set(*CENTER, 90.0, 250.0, N_RINGS, 90.0, scenery)
    west = compute_keep_set(*CENTER, 270.0, 250.0, N_RINGS, 90.0, scenery)
    assert set(east) != set(west)


def test_higher_zoom_patch_inside_ring_is_kept(tmp_path):
    """A BI19 airport patch inside the ring rides along with the base."""
    patch19 = tm.atlas_filename(*tm.latlon_to_atlas(*CENTER, 19), 19)
    scenery = _full_square(tmp_path, extra=[patch19])
    keep = compute_keep_set(*CENTER, 90.0, 0.0, N_RINGS, LOOKAHEAD, scenery)
    zooms = {a.zoom for a in keep}
    assert zooms == {16, 19}
    assert any(a.filename == patch19 for a in keep)


def test_ring_spans_degree_boundary_into_neighbor_folder(tmp_path):
    """Near -72.0 the ring pulls atlases from BOTH DSF folders."""
    near_edge = (42.5, -72.01)
    make_folder(tmp_path, "zOrtho4XP_Z18_+42-073",
                grid_names(*near_edge, 16, 6, 42, -73))
    make_folder(tmp_path, "zOrtho4XP_Z18_+42-072",
                grid_names(*near_edge, 16, 6, 42, -72))
    scenery = SceneryIndex(tmp_path, active_zoom=18)
    keep = compute_keep_set(*near_edge, 90.0, 0.0, N_RINGS, LOOKAHEAD,
                            scenery)
    folders = {a.folder for a in keep}
    assert folders == {"zOrtho4XP_Z18_+42-073", "zOrtho4XP_Z18_+42-072"}
    # The ring itself is still one full block of atlas cells...
    cells = {(a.x16, a.y16, a.zoom) for a in keep}
    assert len(cells) == (2 * N_RINGS + 1) ** 2
    # ...but atlases straddling the -72.0 meridian exist in BOTH folders
    # (each DSF ships its own copy), and both copies must stay warm.
    straddlers = [a for a in keep
                  if len([b for b in keep
                          if (b.x16, b.y16, b.zoom) == (a.x16, a.y16, a.zoom)]) == 2]
    assert straddlers and len(keep) == len(cells) + len(straddlers) // 2


def test_open_water_center_contributes_nothing(tmp_path):
    """No folder covers the center → empty keep set, no crash."""
    scenery = SceneryIndex(tmp_path, active_zoom=18)
    assert compute_keep_set(0.0, 0.0, 90.0, 250.0, N_RINGS, LOOKAHEAD,
                            scenery) == []


def test_rel_path_shape(tmp_path):
    """rel_path() is scenery-root-relative with the textures level."""
    scenery = _full_square(tmp_path)
    keep = compute_keep_set(*CENTER, 90.0, 0.0, 1, LOOKAHEAD, scenery)
    assert keep[0].rel_path() == f"{FOLDER}/textures/{keep[0].filename}"
