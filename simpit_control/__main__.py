"""
SimPit Control entry point.

Run with::

    python -m simpit_control [--data-dir DIR] [--debug-fleet]

``--debug-fleet`` mode wires the app to a MockLinkProvider populated
with a couple of fake slaves so the UI can be exercised without any
real network or hardware. Useful for screenshotting, demoing,
iterating on layout, or reproducing UI bugs without touching the actual
simpit.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import data as sp_data
from . import mock_slave as sp_mock
from . import registry as sp_registry
from .ui.app import App


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_debug_fleet(data_dir: Path) -> tuple:
    """Construct a MockLinkProvider + factory pre-populated with fake slaves."""
    provider = sp_mock.MockLinkProvider()

    # Pre-populate the store so the user sees something. We don't write
    # to disk — the debug fleet is intentionally ephemeral.
    store = sp_data.Store(sp_data.ControlPaths.under(data_dir))

    # Create a few example slaves only if the store is empty so we
    # don't overwrite a real user's data.
    if not store.slaves():
        s1 = store.add_slave(name="CENTERLEFT", host="10.0.0.5",
                              notes="Mock slave for debug")
        s2 = store.add_slave(name="RIGHT",      host="10.0.0.6",
                              notes="Mock slave for debug")
        provider.add(s1.id, sp_mock.MockSlaveState(
            hostname="centerleft", os_name="windows"))
        provider.add(s2.id, sp_mock.MockSlaveState(
            hostname="right",      os_name="windows",
            mode=sp_mock.MockMode.OFFLINE))

    def factory(slave):
        return provider.link_for(slave)
    return factory, provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m simpit_control",
        description="SimPit Control — GUI for managing simpit slaves.")
    parser.add_argument("--data-dir", type=Path,
                        default=sp_data.default_data_dir(),
                        help="Where Control stores its files.")
    parser.add_argument("--debug-fleet", action="store_true",
                        help="Run with simulated slaves (no network).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    if args.debug_fleet:
        factory, provider = _build_debug_fleet(args.data_dir)
        app = App(args.data_dir,
                   link_factory=factory, link_provider=provider)
    else:
        # Seed standard scripts on first run (or after an upgrade that
        # adds new standard scripts). Safe to call every time — idempotent.
        paths = sp_data.ControlPaths.under(args.data_dir)
        store = sp_data.Store(paths)
        n = sp_registry.seed_registry(store)
        if n:
            logging.getLogger(__name__).info(
                "Seeded %d standard script(s) from registry.", n)
        app = App(args.data_dir)
    app.maybe_show_first_run_notice()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
