"""Tests for simpit_common.tilemath (pure math, no filesystem).

Anchor values were verified against real RandhawaNAS listings
(2026-07-05): KEEN (42.898, -72.271) lives in zOrtho4XP_Z18_+42-073,
and atlas filenames are {y16}_{x16}_{provider}{zoom}.dds — y first,
floored to multiples of 16 at the atlas's own zoom.
"""
from __future__ import annotations

import pytest

from simpit_common import tilemath as tm

KEEN = (42.898, -72.271)


# ── slippy tiles ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("zoom, tile", [
    (16, (19611, 24106)),
    (17, (39222, 48213)),
    (18, (78445, 96426)),
    (19, (156891, 192852)),
])
def test_latlon_to_tile_keen_fixtures(zoom, tile):
    """KEEN maps to the expected tile at every zoom the NAS uses."""
    assert tm.latlon_to_tile(*KEEN, zoom) == tile


def test_tile_to_latlon_round_trips():
    """NW-corner lat/lon of a tile maps back into the same tile."""
    for zoom in (16, 18):
        x, y = tm.latlon_to_tile(*KEEN, zoom)
        lat, lon = tm.tile_to_latlon(x + 0.5, y + 0.5, zoom)  # tile center
        assert tm.latlon_to_tile(lat, lon, zoom) == (x, y)


# ── atlases ──────────────────────────────────────────────────────────────
def test_atlas_origin_floors_to_16():
    assert tm.atlas_origin(19611, 24106) == (19600, 24096)
    assert tm.atlas_origin(19600, 24096) == (19600, 24096)   # already aligned
    assert tm.atlas_origin(15, 16) == (0, 16)


def test_atlas_filename_is_y_first():
    """The on-disk name puts y16 before x16 — the verified NAS scheme."""
    x16, y16 = tm.latlon_to_atlas(*KEEN, 16)
    assert (x16, y16) == (19600, 24096)
    assert tm.atlas_filename(x16, y16, 16) == "24096_19600_BI16.dds"


def test_parse_atlas_filename_real_example():
    """The captured real filename parses to x-first coords + zoom."""
    assert tm.parse_atlas_filename("53600_28416_BI17.dds") == (28416, 53600, 17)


def test_parse_atlas_filename_round_trip():
    for name in ("24096_19600_BI16.dds", "192848_156880_BI19.dds",
                 "53600_28416_BI17.dds"):
        x16, y16, zoom = tm.parse_atlas_filename(name)
        assert x16 % 16 == 0 and y16 % 16 == 0
        assert tm.atlas_filename(x16, y16, zoom) == name


def test_parse_atlas_filename_mixed_case_provider():
    """ArcGIS atlases (found live in Z18_+34-119) have a mixed-case
    provider — the parse must not assume all-uppercase 'BI'."""
    assert tm.parse_atlas_filename("103824_44416_Arc18.dds") == \
        (44416, 103824, 18)


@pytest.mark.parametrize("name", [
    "terrain_1234.ter",               # per-atlas terrain files
    "24096_19600_BI16.png",           # wrong extension
    "24096_19600.dds",                # no provider+zoom
    "not_an_atlas.dds",
])
def test_parse_atlas_filename_rejects_non_atlases(name):
    assert tm.parse_atlas_filename(name) is None


def test_atlas_bounds_contain_the_position():
    """An atlas's bbox contains every position that resolves into it."""
    for zoom in (16, 19):
        x16, y16 = tm.latlon_to_atlas(*KEEN, zoom)
        lat_min, lat_max, lon_min, lon_max = tm.atlas_bounds(x16, y16, zoom)
        assert lat_min < KEEN[0] < lat_max
        assert lon_min < KEEN[1] < lon_max
        assert lat_min < lat_max and lon_min < lon_max


# ── rings ────────────────────────────────────────────────────────────────
def test_ring_bounds_zero_rings_equals_atlas_bounds():
    x16, y16 = tm.latlon_to_atlas(*KEEN, 16)
    assert tm.ring_bounds(x16, y16, 16, 0) == tm.atlas_bounds(x16, y16, 16)


def test_atlases_in_bounds_ring4_is_9x9():
    """N_RINGS=4 produces exactly the 9×9 base-atlas block."""
    x16, y16 = tm.latlon_to_atlas(*KEEN, 16)
    cells = tm.atlases_in_bounds(tm.ring_bounds(x16, y16, 16, 4), 16)
    assert len(cells) == 81
    assert (x16, y16) in cells
    assert all(cx % 16 == 0 and cy % 16 == 0 for cx, cy in cells)
    xs = {cx for cx, _ in cells}
    ys = {cy for _, cy in cells}
    assert xs == {x16 + 16 * i for i in range(-4, 5)}
    assert ys == {y16 + 16 * i for i in range(-4, 5)}


def test_atlases_in_bounds_higher_zoom_covers_same_area():
    """Intersecting a base-zoom ring bbox at a higher zoom finds the
    higher-zoom atlases inside it (airport-patch lookup path)."""
    x16, y16 = tm.latlon_to_atlas(*KEEN, 16)
    bounds = tm.ring_bounds(x16, y16, 16, 0)          # one BI16 atlas
    cells19 = tm.atlases_in_bounds(bounds, 19)
    # One zoom-16 atlas spans 8×8 zoom-19 atlases (2³ per axis).
    assert len(cells19) == 64
    assert tm.latlon_to_atlas(*KEEN, 19) in cells19


# ── DSF folders ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("lat, lon, label, expected", [
    (42.898, -72.271, 18, "zOrtho4XP_Z18_+42-073"),   # KEEN: floor, not int()
    (42.898, -72.271, 16, "zOrtho4XP_Z16_+42-073"),
    (42.0, -73.0, 18, "zOrtho4XP_Z18_+42-073"),       # exactly on the corner
    (-33.9, 151.2, 18, "zOrtho4XP_Z18_-34+151"),      # southern hemisphere
    (-0.5, -0.5, 18, "zOrtho4XP_Z18_-01-001"),        # both floors go down
    (47.6, -122.3, 16, "zOrtho4XP_Z16_+47-123"),      # WUS Z16 set
])
def test_dsf_folder_name(lat, lon, label, expected):
    assert tm.dsf_folder_name(lat, lon, label) == expected


def test_dsf_squares_in_bounds_spans_degree_boundaries():
    """A bbox straddling degree lines lists every overlapped square."""
    squares = tm.dsf_squares_in_bounds((42.9, 43.1, -72.05, -71.95))
    assert squares == [(42, -73), (42, -72), (43, -73), (43, -72)]
    assert tm.dsf_squares_in_bounds((42.1, 42.2, -72.9, -72.8)) == [(42, -73)]


# ── projection ───────────────────────────────────────────────────────────
def test_project_position_follows_track_not_anything_else():
    """Due-east track moves only longitude; due-north only latitude."""
    lat, lon = tm.project_position(*KEEN, 90.0, 100.0, 45.0)
    assert lat == pytest.approx(KEEN[0])
    assert lon > KEEN[1]
    lat, lon = tm.project_position(*KEEN, 0.0, 100.0, 45.0)
    assert lat > KEEN[0]
    assert lon == pytest.approx(KEEN[1])


def test_project_position_lon_step_scaled_by_cos_lat():
    """The same eastward run covers more degrees of lon at high latitude."""
    _, lon_mid = tm.project_position(45.0, 0.0, 90.0, 100.0, 45.0)
    _, lon_high = tm.project_position(60.0, 0.0, 90.0, 100.0, 45.0)
    assert lon_high > lon_mid > 0


def test_project_position_zero_speed_is_identity():
    assert tm.project_position(*KEEN, 123.0, 0.0, 45.0) == KEEN
