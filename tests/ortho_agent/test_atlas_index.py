"""Tests for simpit_ortho_agent.atlas_index (tmp_path scenery trees).

The mixed-zoom fixture mirrors the verified Z18_+42-073 (KEEN) layout:
a BI16 base with a higher-zoom airport patch on top — the case that
proves position→atlas resolution cannot come from a formula alone.
"""
from __future__ import annotations

from simpit_common import tilemath as tm
from simpit_ortho_agent import atlas_index as ai
from tests.ortho_agent.conftest import KEEN, make_folder

FOLDER18 = "zOrtho4XP_Z18_+42-073"


def _keen_mixed_folder(root):
    """Z18_+42-073-style fixture: BI16 base + BI19 patch, both over KEEN."""
    base16 = tm.atlas_filename(*tm.latlon_to_atlas(*KEEN, 16), 16)
    patch19 = tm.atlas_filename(*tm.latlon_to_atlas(*KEEN, 19), 19)
    # A second base atlas one step east, NOT covered by the patch.
    x16, y16 = tm.latlon_to_atlas(*KEEN, 16)
    east16 = tm.atlas_filename(x16 + 16, y16, 16)
    make_folder(root, FOLDER18,
                [base16, east16, patch19, "terrain_042.ter", "mask_1.png"])
    return base16, east16, patch19


def test_load_atlas_index_parses_and_ignores_non_atlases(tmp_path):
    """Only atlas .dds names land in the index; base zoom is the mode."""
    base16, east16, patch19 = _keen_mixed_folder(tmp_path)
    index = ai.load_atlas_index(tmp_path / FOLDER18)
    assert len(index.atlases) == 3
    assert index.zooms == (19, 16)          # descending
    assert index.base_zoom == 16            # 2×BI16 vs 1×BI19
    x16, y16 = tm.latlon_to_atlas(*KEEN, 16)
    assert index.atlases[(x16, y16, 16)] == base16


def test_resolve_atlas_highest_zoom_wins(tmp_path):
    """Where an airport patch overlaps the base, the patch is returned."""
    base16, east16, patch19 = _keen_mixed_folder(tmp_path)
    index = ai.load_atlas_index(tmp_path / FOLDER18)
    assert ai.resolve_atlas(*KEEN, index) == patch19


def test_resolve_atlas_falls_back_to_base(tmp_path):
    """Outside the patch but inside the base → the BI16 atlas."""
    base16, east16, patch19 = _keen_mixed_folder(tmp_path)
    index = ai.load_atlas_index(tmp_path / FOLDER18)
    # Center of the east base atlas: covered by BI16 only.
    x16, y16 = tm.latlon_to_atlas(*KEEN, 16)
    lat, lon = tm.tile_to_latlon(x16 + 16 + 8, y16 + 8, 16)
    assert ai.resolve_atlas(lat, lon, index) == east16


def test_resolve_atlas_water_is_none(tmp_path):
    """A position no atlas covers (water/unbuilt) resolves to None."""
    _keen_mixed_folder(tmp_path)
    index = ai.load_atlas_index(tmp_path / FOLDER18)
    assert ai.resolve_atlas(42.01, -72.99, index) is None


def test_scenery_index_prefers_active_zoom_label(tmp_path):
    """With both scenery sets present, the active_zoom label wins."""
    make_folder(tmp_path, "zOrtho4XP_Z18_+42-073", ["24096_19600_BI16.dds"])
    make_folder(tmp_path, "zOrtho4XP_Z16_+42-073", ["24096_19600_BI16.dds"])
    scenery = ai.SceneryIndex(tmp_path, active_zoom=18)
    found = scenery.folder_for_square(42, -73)
    assert found is not None and found[0] == "zOrtho4XP_Z18_+42-073"


def test_scenery_index_falls_back_to_other_label(tmp_path):
    """Hybrid profile: a square with only a Z16 folder still resolves."""
    make_folder(tmp_path, "zOrtho4XP_Z16_+47-123", ["11360_10496_BI16.dds"])
    scenery = ai.SceneryIndex(tmp_path, active_zoom=18)
    found = scenery.folder_for_square(47, -123)
    assert found is not None and found[0] == "zOrtho4XP_Z16_+47-123"


def test_scenery_index_caches_missing_squares_only_when_root_up(tmp_path):
    """Water squares cache as None; a downed mount caches nothing."""
    root = tmp_path / "mnt"
    scenery = ai.SceneryIndex(root, active_zoom=18)
    # Mount down: no cache entries may be created.
    assert scenery.folder_for_square(42, -73) is None
    root.mkdir()
    make_folder(root, "zOrtho4XP_Z18_+42-073", ["24096_19600_BI16.dds"])
    # Same square now resolves — nothing was poisoned while down.
    assert scenery.folder_for_square(42, -73) is not None
    # True water square: cached miss, and stays a miss.
    assert scenery.folder_for_square(0, 0) is None
    assert scenery.folder_for_square(0, 0) is None


def test_scenery_index_clear_forgets_cache(tmp_path):
    """clear() lets a config reload re-see new folders."""
    scenery = ai.SceneryIndex(tmp_path, active_zoom=18)
    assert scenery.folder_for_square(42, -73) is None      # cached miss
    make_folder(tmp_path, "zOrtho4XP_Z18_+42-073", ["24096_19600_BI16.dds"])
    assert scenery.folder_for_square(42, -73) is None      # still the cache
    scenery.clear()
    assert scenery.folder_for_square(42, -73) is not None
