"""Fixtures for the ortho agent suite: tiny on-disk scenery trees.

The agent's folder/index/keep-set logic is filesystem-driven, so the
easiest faithful fake is a real (tmp_path) directory tree with
zero-byte atlas files named exactly like the NAS ones. Names are
generated through simpit_common.tilemath so fixtures can cover
arbitrary positions without hand-computing tile numbers.
"""
from __future__ import annotations

from pathlib import Path

from simpit_common import tilemath as tm

KEEN = (42.898, -72.271)


def make_folder(root: Path, folder: str, names: list[str]) -> Path:
    """Create one scenery folder with the given texture filenames."""
    tex = root / folder / "textures"
    tex.mkdir(parents=True, exist_ok=True)
    for name in names:
        (tex / name).write_bytes(b"\x00" * 256)
    return root / folder


def grid_names(lat: float, lon: float, zoom: int, n_rings: int,
               lat_floor: int, lon_floor: int) -> list[str]:
    """Atlas filenames fully covering a ring, clipped to one DSF square.

    Args:
        lat: ring center latitude.
        lon: ring center longitude.
        zoom: atlas zoom for the grid.
        n_rings: ring radius in atlas units.
        lat_floor: only emit atlases overlapping this 1°×1° square...
        lon_floor: ...so multi-folder fixtures stay realistic.

    Returns:
        List of atlas .dds filenames.
    """
    cx16, cy16 = tm.latlon_to_atlas(lat, lon, zoom)
    bounds = tm.ring_bounds(cx16, cy16, zoom, n_rings)
    clipped = (max(bounds[0], float(lat_floor)),
               min(bounds[1], lat_floor + 1.0),
               max(bounds[2], float(lon_floor)),
               min(bounds[3], lon_floor + 1.0))
    return [tm.atlas_filename(x16, y16, zoom)
            for x16, y16 in tm.atlases_in_bounds(clipped, zoom)]
