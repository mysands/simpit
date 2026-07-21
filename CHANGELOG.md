# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **`simpit_ortho_agent`** package: per-machine ortho scenery cache
  agent (see `docs/ORTHO_AGENT.md`). Subscribes to the X-Plane
  master's position over RREF (ground track, not heading) and keeps a
  moving bubble of Ortho4XP atlases warm in the local rclone VFS cache
  — rings in atlas units around the aircraft and its 45 s along-track
  projection, mixed-zoom aware via a per-folder textures index
  (airport patches ride along with the base). One primer thread does
  8 MB sequential first reads then cheap keep-warm touches; eviction
  is left entirely to rclone's LRU size cap (the agent issues no rc
  eviction calls and never writes the cache dir — bulk evictions have
  crashed X-Plane before). Includes fallback mount supervision with
  double-mount safety, a SIM_OFFLINE/IDLE/ACTIVE state machine, and
  fleet config re-reads on each sim return. Ships as
  `simpit-ortho-agent` (console script + PyInstaller spec).
- **`simpit_common.tilemath`** — pure slippy-tile / atlas math shared
  by the agent and the live verifier; **`simpit_common.xp_rref`** —
  shared RREF wire helpers (extracted from the probe engine).
- Live ortho-chain checks now exercise the production tilemath/index
  code when run from a checkout (stdlib fallback copies remain for the
  bare two-file slave deployment), understand the
  whole-Custom-Scenery-junction layout, and compare the VFS cache to
  the per-machine `cache_max_gb` instead of a hardcoded cap.

- **`make_dummy_scenery`** standard script: builds a dummy-texture
  mirror of the ortho Custom Scenery for the X-Plane master (flight
  model machine) — identical `zOrtho4XP_Z*` folder names, DSFs and
  `.ter` files copied, every `textures/*.dds` atlas replaced by a
  ~300-byte uniform-color DXT1 stand-in, masks copied, Ortho4XP build
  intermediates and `*.bak` skipped. Incremental via per-folder
  `.simpit_dummy.json` markers (re-runs build only new tiles), with
  `--verify`, `--prune`, `--only`, `--dry-run`, `--color`, and a
  refusal to write anywhere inside the real scenery tree. Folder names
  match `set_scenery_profile.py`'s scanner, so the master's
  `scenery_packs.ini` is managed unchanged (covered by tests).
- Installer now deploys the ortho cache agent with the Ortho Scenery
  Cache step: installs `simpit-ortho-agent.exe`, seeds the machine's
  local `ortho_agent.json` from the mount answers (fleet folder
  derived as `\\<host>\<share-root>\simpit`; master stays localhost),
  and registers a titled console wrapper under HKCU Run. Upgrades and
  uninstall stop the agent first.
- Ortho agent: the fleet config is now polled every ~60 s, so settings
  saved in Control's Ortho Cache dialog apply to running agents within
  a minute — tuning fields in place, endpoint fields via an automatic
  internal restart (feed/primer rebuilt from the new config). The
  dialog notes the behavior.

### Changed
- `fleet_config_dir` no longer defaults to a site-specific UNC path:
  empty (the new default) means fleet distribution is off and the
  agent/Control work purely from the local `ortho_agent.json` without
  touching the network. Set the folder in Control's Ortho Cache dialog
  to enable fleet-wide config.
- `ortho_config` moved from `simpit_control` to `simpit_common` so
  Control's dialog and the ortho agent share the single loader; adds
  `scenery_root()`. Import paths updated (`simpit_control` kept no
  shim — update any external imports to `simpit_common.ortho_config`).
- **`set_scenery_profile`** standard script: rule-based
  `scenery_packs.ini` generator that switches the active Ortho4XP zoom
  level per tile (Z16 vs Z18) from named JSON profiles in
  `<Custom Scenery>/scenery_profiles/` (`vfr`, `ifr`, `hybrid`).
  Profiles support a default zoom plus overrides by explicit tile or by
  airport ICAO + tile radius (resolved via X-Plane's Global Airports
  `apt.dat`, cached). Only ortho lines are touched; timestamped ini
  backup, atomic write, X-Plane-running guard, `--dry-run`, `--status`,
  and `--list` modes. Registered with `cascade=False` because the
  scenery root is a shared NAS folder — revisit if slaves get
  per-machine Custom Scenery.

### Fixed
- Ortho agent: primer reads are now paced (`prime_mbps`, default
  24 MB/s, 0 = off; editable in Control's Ortho Cache dialog) and the
  agent lowers its own CPU/IO priority at startup. Unthrottled
  warm-cache primes ran at raw disk speed, and the 15-35-atlas burst
  at every atlas crossing starved X-Plane's scenery reads on the
  shared cache drive — confirmed live by A/B test as the cause of
  micro-stutters every ~15 s in flight (2026-07-19). Staying ahead of
  the aircraft needs only ~5-8 MB/s sustained, so the cap costs
  nothing.
- Ortho agent: atlas filenames with mixed-case providers now parse —
  the NAS holds ArcGIS atlases (`…_Arc18.dds`, e.g. all 2,543 in
  `Z18_+34-119`/KVNY) alongside Bing's `BI`, and the all-uppercase
  provider regex indexed such folders as empty, leaving the agent
  idle there. Found live at KVNY. A folder with no parseable atlases
  also no longer shadows the other zoom label's folder. Live-verified
  in flight out of KVNY: 8/8 chain checks on the LOCAL RREF feed —
  external visuals serve live position/groundspeed/track matching the
  master, so the localhost default needs no per-machine master IP.
- Control's SCRIPTS panel now scrolls: with many scripts registered,
  rows below the window edge were simply unreachable. Rows live in a
  canvas with an auto-hiding scrollbar and mouse-wheel support, and
  the scroll position survives the dashboard's periodic rebuilds
  (previously it would have snapped to the top every poll tick).

### Planned
- Streaming EXEC_SCRIPT mode wired through to a Control "view full
  output" dialog.
- Slave service installers (`systemd` unit, Windows Task Scheduler XML).
- PyInstaller binaries for Windows users without Python.

---

## [0.1.0] - 2026-04-25

Initial alpha release.

### Added

- **`simpit_common`** package
  - JSON-over-{UDP,TCP} protocol with HMAC-SHA256 signing and
    timestamp-based replay protection.
  - Cross-platform OS abstraction (`pathlib`, `psutil`, no Windows-only
    APIs).
  - Extensible probe engine for declarative state queries
    (`path_exists`, `folder_exists`, `file_contains`, `process_running`,
    `script_exit_code`).
  - Atomic key generation, save, and load with POSIX 0600 permissions.

- **`simpit_slave`** package
  - Headless UDP/TCP server with per-connection threading.
  - Buffered + streaming script executor with output-size cap and
    timeout enforcement.
  - Path-traversal rejection in script lookup.
  - Atomic SYNC_PUSH directory swap.
  - STATUS inspector with always-on facts (hostname, os, uptime,
    is_admin, script inventory) plus probe results.
  - First-run interactive key prompt; CLI flags for non-interactive
    deployments.

- **`simpit_control`** package
  - JSON-backed store for slaves and bat files with schema versioning
    and atomic writes.
  - Background poller with subscriber callbacks; only-on-change
    notifications.
  - SlaveLink for outbound UDP/TCP calls with typed error hierarchy.
  - MockLinkProvider with five failure modes (NORMAL, OFFLINE,
    TIMEOUT, BAD_KEY, SLOW) for unit tests and `--debug-fleet` mode.
  - tkinter UI: per-slave status cards with derived state, scripts
    panel with cascade indicators, activity log, security setup dialog.
  - Thread-safe worker→main marshalling via `queue.Queue` with
    periodic main-thread drain.

- **Tests**: 234 passing tests across protocol/security/platform/probes
  (84), slave (46), control core (52), UI (48), and end-to-end
  integration (4).

### Security

- Every wire message is signed; unverified messages are silently
  dropped.
- Slave NEVER accepts a script path containing `/`, `\`, `..`, or NUL.
- Subprocess execution is always `shell=False` with an explicit
  environment whitelist.
- Slaves and Control share a single secret; rotation is
  delete-and-regenerate with no automatic key migration.

[Unreleased]: https://github.com/mysands/simpit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mysands/simpit/releases/tag/v0.1.0
