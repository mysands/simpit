"""Shared fixtures for UI tests.

The single biggest pitfall when testing tkinter is that **creating
multiple ``Tk()`` instances in the same process is unsupported.**
Each ``Tk()`` constructs its own Tcl interpreter, and tearing one down
followed by constructing another can fail or leave Tcl in a partially
initialized state — particularly on Python 3.14 + Windows.

The canonical fix is one root per test session. Individual tests use
``Toplevel`` for short-lived windows, or — in our case — share the
session root via a fixture.

We deliberately don't destroy the root at session end. Letting the
process exit cleans up resources just as well, and explicit destroy
on a session root sometimes triggers the "init.tcl not found" failure
when pytest's collection has already torn down some import-time state.
"""
import sys

import pytest


# Skip ALL UI tests on Linux without a display. This complements the
# per-module guard in test_widgets.py and test_app.py — having both is
# belt-and-suspenders for cases where conftest is imported but the
# module-level guard hasn't run yet (e.g. fixture collection order).
def pytest_collection_modifyitems(config, items):
    import os
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        skip = pytest.mark.skip(reason="no DISPLAY available on Linux")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def tk_session_root():
    """One ``Tk()`` instance shared across the entire test session.

    Tests that need a parent for widgets request this fixture. Tests
    that need their own toplevel-style window (like the App tests)
    don't use this fixture directly — they create their App as the
    sole Tk root, which works as long as no other test has created
    AND destroyed a Tk before them.

    To make that ordering deterministic, the per-module guard in
    test_widgets.py uses this fixture (so its widgets are children of
    the session root, never their own Tk), while test_app.py creates
    fresh App-as-Tk instances. We never destroy the session root —
    Python's process exit handles cleanup.
    """
    if sys.platform.startswith("linux"):
        # On headless CI we still need a display. The conftest skip
        # above filters out test items, but the fixture can still be
        # constructed; guard against it anyway.
        import os
        if not os.environ.get("DISPLAY"):
            pytest.skip("no DISPLAY available")

    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    # Deliberately no destroy — see module docstring.
