# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
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
