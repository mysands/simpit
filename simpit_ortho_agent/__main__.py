"""
Ortho cache agent entry point.

Run with::

    python -m simpit_ortho_agent [--config PATH] [-v]

The agent reads its effective config via
:func:`simpit_common.ortho_config.load_effective`: local cached copy →
fleet base on the NAS → per-hostname overlay. A machine with no NAS
access runs on its cached local copy; a machine with no local copy yet
runs on (and writes) the built-in defaults, so first boot before
Control has ever pushed a config still works.

Deployment: Windows runs this as an at-logon Task Scheduler task in the
interactive session (NOT a service — it only needs file reads and
localhost/LAN UDP, and services live in an isolated session). Linux and
macOS deployment is a stub in v1 — the code itself is portable; see
docs/ORTHO_AGENT.md for the unit-file sketch.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from simpit_common import ortho_config
from simpit_common import platform as sp_platform

from .engine import Engine


def default_config_path() -> Path:
    """Per-user location of this machine's local ortho_agent.json."""
    return sp_platform.app_data_dir("simpit-ortho-agent") / "ortho_agent.json"


def _setup_logging(verbose: bool, log_file: Path | None) -> None:
    """Configure logging once for the agent process."""
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def _lower_process_priority() -> None:
    """Drop CPU and I/O priority so priming yields to X-Plane.

    Best-effort: psutil is a repo dependency but the agent must still
    run without it (bare python on a slave).
    """
    try:
        import psutil
        proc = psutil.Process()
        if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):     # Windows
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            proc.ionice(psutil.IOPRIO_LOW)
        else:                                                  # POSIX
            proc.nice(10)
    except Exception as exc:                                   # noqa: BLE001
        logging.getLogger("simpit.ortho").debug(
            "could not lower process priority: %s", exc)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m simpit_ortho_agent",
        description="SimPit per-machine ortho scenery cache agent.")
    parser.add_argument("--config", type=Path, default=default_config_path(),
                        help="Local ortho_agent.json (fleet copy and "
                             "hostname overlay are merged on top).")
    parser.add_argument("--log-file", type=Path, default=None,
                        help="Also log to this file (default: agent.log "
                             "next to the config).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    log_file = args.log_file or args.config.parent / "agent.log"
    _setup_logging(args.verbose, log_file)
    log = logging.getLogger("simpit.ortho")

    cfg = ortho_config.load_effective(args.config)
    if not args.config.is_file():
        # First boot before Control ever pushed a config: persist the
        # effective config as the local bootstrap copy (best-effort).
        try:
            ortho_config.save(cfg, args.config)
        except (OSError, ValueError) as exc:
            log.warning("could not write bootstrap config %s: %s",
                        args.config, exc)
    if not cfg.enabled:
        log.info("agent disabled in config (%s) — exiting", args.config)
        return 0

    _lower_process_priority()
    engine = Engine(cfg, args.config)

    stop = threading.Event()

    def _on_signal(signum, frame):
        stop.set()
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    engine.run(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
