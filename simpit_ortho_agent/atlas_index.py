"""
simpit_ortho_agent.atlas_index
==============================
Per-scenery-folder atlas index.

Atlas zooms are MIXED within one folder (verified on RandhawaNAS
2026-07-05): a ``Z18_+42-073`` folder holds a BI16 base plus BI17/18/19
airport patches, so ``tile_to_atlas_path(x, y, zoom)`` alone cannot
predict the covering atlas file. On first entering a DSF folder the
agent lists its ``textures/`` dir once (a few hundred names — a cheap
metadata read), parses it into an index, and resolves position → atlas
by trying every zoom present, highest first (patches win over the base
where they overlap).

Indexes are cached per folder for the life of the process; the NAS
scenery content is static, so there is no invalidation beyond
:meth:`SceneryIndex.clear` (called when the agent reloads its config).
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from simpit_common import tilemath

log = logging.getLogger("simpit.ortho.index")


@dataclass(frozen=True)
class AtlasIndex:
    """Parsed contents of one scenery folder's textures directory.

    Attributes:
        atlases: ``{(x16, y16, zoom): filename}`` for every atlas .dds.
        zooms: zoom levels present, descending (resolution order).
        base_zoom: the most common zoom — the folder's actual base layer
            (BI16 or BI17), NOT the Z16/Z18 folder label. 0 when the
            folder holds no atlases at all.
    """
    atlases: dict[tuple[int, int, int], str]
    zooms: tuple[int, ...]
    base_zoom: int


def load_atlas_index(folder: Path) -> AtlasIndex:
    """Index a scenery folder's textures directory.

    Args:
        folder: a ``zOrtho4XP_*`` folder on the mount.

    Returns:
        AtlasIndex over every atlas .dds present (non-atlas files —
        masks, .ter — are ignored).

    Raises:
        OSError: if the textures directory cannot be listed (mount
            down, folder vanished). Callers that can tolerate this use
            :class:`SceneryIndex`, which catches and retries later.
    """
    atlases: dict[tuple[int, int, int], str] = {}
    counts: Counter[int] = Counter()
    for entry in (folder / "textures").iterdir():
        parsed = tilemath.parse_atlas_filename(entry.name)
        if parsed:
            atlases[parsed] = entry.name
            counts[parsed[2]] += 1
    zooms = tuple(sorted({z for _, _, z in atlases}, reverse=True))
    base_zoom = counts.most_common(1)[0][0] if counts else 0
    return AtlasIndex(atlases=atlases, zooms=zooms, base_zoom=base_zoom)


def resolve_atlas(lat: float, lon: float, index: AtlasIndex) -> str | None:
    """Find the atlas covering a position, highest zoom first.

    Args:
        lat: latitude in degrees.
        lon: longitude in degrees.
        index: output of :func:`load_atlas_index`.

    Returns:
        Atlas filename, or None (water / unbuilt area).
    """
    for zoom in index.zooms:
        x16, y16 = tilemath.latlon_to_atlas(lat, lon, zoom)
        name = index.atlases.get((x16, y16, zoom))
        if name:
            return name
    return None


@dataclass
class SceneryIndex:
    """Cached folder lookup over the mounted Custom Scenery root.

    ``active_zoom`` selects which scenery-set label (``Z18``/``Z16``) is
    tried first; the other label is the fallback because the hybrid
    scenery profile mixes both sets, and a square covered only by the
    other set must still be primed (the live verifier resolves folders
    the same way).

    Attributes:
        scenery_root: the mount's Custom Scenery level
            (``mount_root / remote_rel_root``).
        active_zoom: preferred folder label, 18 or 16.
    """
    scenery_root: Path
    active_zoom: int
    _cache: dict[str, AtlasIndex | None] = field(default_factory=dict)

    def folder_for_square(self, lat_floor: int,
                          lon_floor: int) -> tuple[str, AtlasIndex] | None:
        """Resolve the scenery folder + index covering a 1°×1° square.

        Args:
            lat_floor: floor of the square's latitude.
            lon_floor: floor of the square's longitude.

        Returns:
            (folder_name, index), or None when neither scenery set
            covers the square (open water, unbuilt regions).
        """
        other = 16 if self.active_zoom == 18 else 18
        for label in (self.active_zoom, other):
            name = tilemath.dsf_folder_name(lat_floor, lon_floor, label)
            index = self._index_for(name)
            # A folder with no parseable atlases counts as absent, so a
            # stripped/odd folder under one label can't shadow a real
            # one under the other.
            if index is not None and index.atlases:
                return name, index
        return None

    def _index_for(self, folder_name: str) -> AtlasIndex | None:
        """Load-or-recall one folder's index; None if the folder is absent.

        A missing folder is cached as None (static NAS content), but a
        listing *error* (mount down mid-read) is not cached, so the
        folder is retried once the mount recovers.
        """
        if folder_name in self._cache:
            return self._cache[folder_name]
        folder = self.scenery_root / folder_name
        try:
            if not folder.is_dir():
                # Only cache "no such folder" while the mount itself is
                # up — otherwise a mount outage would permanently mark
                # real folders as water.
                if self.scenery_root.is_dir():
                    self._cache[folder_name] = None
                return None
            index = load_atlas_index(folder)
        except OSError as exc:
            log.debug("cannot index %s (mount hiccup?): %s", folder_name, exc)
            return None
        log.info("indexed %s: %d atlases, zooms %s (base %d)",
                 folder_name, len(index.atlases), list(index.zooms),
                 index.base_zoom)
        self._cache[folder_name] = index
        return index

    def clear(self) -> None:
        """Drop all cached indexes (config reload / mount restart)."""
        self._cache.clear()
