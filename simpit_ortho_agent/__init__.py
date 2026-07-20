"""
simpit_ortho_agent
==================
Per-machine ortho scenery cache agent.

A small autonomous helper that runs on EVERY X-Plane machine (master,
CENTERLEFT, RIGHT). Each instance keeps a moving "bubble" of Ortho4XP
texture atlases warm in that machine's own local rclone VFS cache,
priming ahead of the aircraft along its ground track so scenery is
already on local disk before X-Plane asks for it.

Why per-machine (architecture rationale)
----------------------------------------
* The rclone VFS cache is strictly local: priming REQUIRES a local file
  read on the machine that owns the cache. Control cannot warm a
  slave's cache remotely.
* Position is identical everywhere: the X-Plane master serves RREF to
  multiple subscribers, so each agent gets the same lat/lon feed with
  zero coordination.
* This does NOT violate the "slaves are pure executors" principle: the
  agent is machine-local infrastructure (like the OS page cache), not
  command/script logic. No new Control→slave protocol messages, no
  Control runtime involvement.

Eviction model (why there is no evictor)
----------------------------------------
rclone's rc API cannot evict a single file from the VFS disk cache
(``vfs/forget`` clears directory/metadata cache only), so the agent
never deletes anything. It re-touches keep-set atlases on a rolling
interval so their access times stay fresh; atlases that leave the keep
set simply stop being touched, and rclone's own LRU eviction at the
``--vfs-cache-max-size`` cap removes the coldest files. The cache dir
is never written to directly, and bulk deletion while the sim runs is
forbidden outright — an eviction burst once deleted .dds textures
X-Plane had memory-mapped, crashing it with EXCEPTION_IN_PAGE_ERROR
(2026-07-06).

Modules:
    atlas_index - per-scenery-folder textures index (mixed atlas zooms)
    keepset     - keep-set geometry: rings in atlas units + lookahead
    rref        - continuous RREF position feed from the X-Plane master
    primer      - worker thread doing full primes and keep-warm touches
    mount       - rclone mount supervision (drive gate, rc health)
    engine      - SIM_OFFLINE / IDLE / ACTIVE state machine + main loop

Shares :mod:`simpit_common` (tilemath, xp_rref, ortho_config) with the
rest of SimPit; config is ``ortho_agent.json`` loaded fleet-first via
:func:`simpit_common.ortho_config.load_effective`.
"""
from . import atlas_index, engine, keepset, mount, primer, rref

__version__ = "0.1.0"

__all__ = ["atlas_index", "keepset", "rref", "primer", "mount", "engine",
           "__version__"]
