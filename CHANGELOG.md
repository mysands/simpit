# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
