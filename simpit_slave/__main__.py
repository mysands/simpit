"""
Slave agent entry point.

Run with::

    python -m simpit_slave [--data-dir DIR] [--udp-port N] [--tcp-port N]
                           [--no-broadcast]

On first run, if no key file exists at the resolved location, the agent
prompts on stdin for a passphrase produced by SimPit Control. That key
gets saved with restrictive permissions and is used for all further
sessions.

Typical service deployment writes the key file out-of-band (e.g. via
configuration management) so this stdin prompt is only the
single-machine convenience path.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from simpit_common import security as sp_security

from . import agent as sp_agent
from . import data as sp_data


def _setup_logging(verbose: bool, log_file: Path | None = None) -> None:
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


def _ensure_key(key_file: Path, prompt: bool) -> bytes:
    """Load the shared key, prompting the user if missing.

    Returns key bytes. Exits with a clear error message if interactive
    setup is impossible (no TTY) and the key file is missing — that
    case usually means somebody installed the agent as a service
    without provisioning the key first.
    """
    try:
        return sp_security.load_key(key_file)
    except FileNotFoundError:
        pass
    if not prompt or not sys.stdin.isatty():
        print(f"ERROR: no key found at {key_file}.\n"
              "Generate one in SimPit Control and copy it here, then retry.",
              file=sys.stderr)
        sys.exit(1)

    print(f"\nNo key found at {key_file}.")
    print("Paste the key from SimPit Control "
          "(64 hex characters) and press Enter:")
    text = sys.stdin.readline()
    try:
        key = sp_security.key_from_text(text)
    except ValueError as e:
        print(f"That doesn't look like a valid key: {e}", file=sys.stderr)
        sys.exit(1)
    sp_security.save_key(key_file, key)
    print(f"Saved to {key_file}")
    return key


def _run_script_mode(script_path: Path, env_file: Path | None) -> int:
    """Hidden re-entry mode used by the elevated execution path.

    The slave invokes itself as ``simpit-slave.exe --run-script PATH
    [--env-file ENV.json]`` to run a single script under whatever
    privilege level the OS gave this process. The elevated child
    runs runpy on the target script, with environment loaded from
    the JSON file if supplied. stdout/stderr go to whatever the
    parent (PowerShell's Start-Process redirection) wired up — we
    don't capture them here.

    Returns the script's exit code so the parent can surface it.
    """
    import json
    import os
    import runpy

    if env_file is not None:
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                env = json.load(f)
        except OSError as e:
            print(f"ERROR: cannot read env file {env_file}: {e}",
                  file=sys.stderr)
            return 1
        if not isinstance(env, dict):
            print(f"ERROR: env file is not a JSON object: {env_file}",
                  file=sys.stderr)
            return 1
        os.environ.clear()
        os.environ.update({str(k): str(v) for k, v in env.items()})

    # runpy.run_path handles .py with __name__ == "__main__" semantics
    # so the script's `if __name__ == "__main__"` block runs.
    try:
        runpy.run_path(str(script_path), run_name="__main__")
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m simpit_slave",
        description="SimPit slave agent (UDP/TCP listener).")
    parser.add_argument("--data-dir", type=Path,
                        default=sp_data.default_data_dir(),
                        help="Where the agent stores key + scripts.")
    parser.add_argument("--udp-port", type=int,
                        default=sp_agent.sp_protocol.DEFAULT_UDP_PORT)
    parser.add_argument("--tcp-port", type=int,
                        default=sp_agent.sp_protocol.DEFAULT_TCP_PORT)
    parser.add_argument("--bind", default="0.0.0.0",
                        help="Interface to bind (0.0.0.0 = all).")
    parser.add_argument("--no-broadcast", action="store_true",
                        help="Disable periodic SLAVE_ONLINE broadcast.")
    parser.add_argument("--no-prompt", action="store_true",
                        help="Don't prompt for a missing key — exit instead.")
    parser.add_argument("-v", "--verbose", action="store_true")
    # Hidden re-entry mode used by the elevated-execution path. When
    # this flag is set the agent does NOT start; it just runs one
    # script and exits. Documented in executor._execute_elevated_windows.
    parser.add_argument("--run-script", type=Path, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--env-file", type=Path, default=None,
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    # Re-entry path: short-circuit before any agent startup.
    if args.run_script is not None:
        return _run_script_mode(args.run_script, args.env_file)

    paths = sp_data.SlavePaths.under(args.data_dir)
    paths.ensure()
    _setup_logging(args.verbose, log_file=paths.log_file)
    key = _ensure_key(paths.key_file, prompt=not args.no_prompt)

    cfg = sp_agent.AgentConfig(
        bind_host=args.bind,
        udp_port=args.udp_port,
        tcp_port=args.tcp_port,
        broadcast=not args.no_broadcast,
    )
    a = sp_agent.Agent(paths=paths, key=key, config=cfg)
    a.start()

    # Run until SIGINT/SIGTERM. We don't busy-loop; signal handlers set
    # an event that the main thread waits on.
    import threading
    stop = threading.Event()

    def _on_signal(signum, frame):
        stop.set()
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    try:
        while not stop.is_set():
            stop.wait(timeout=1.0)
    finally:
        a.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
