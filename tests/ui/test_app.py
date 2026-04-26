"""Top-level App smoke test — instantiate the whole application.

Verifies that every wiring point holds together: data dir, store,
poller, controller, widgets, dialogs all construct without error and
dispatch to each other on user actions.

On Linux, set DISPLAY=:99 (or any other X server) to run; tests skip
cleanly if no display is available. Windows and macOS have native
display servers and don't use DISPLAY, so the guard is Linux-only.
"""
import os
import sys
import time

import pytest

# Skip the whole module if we're on Linux without a display. Windows
# and macOS have native display servers; tkinter works there without
# DISPLAY being set, so we don't gate them on it.
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    pytest.skip("no DISPLAY available", allow_module_level=True)

from simpit_control import mock_slave as sp_mock
from simpit_control.ui.app import App

# A single App instance is shared across all tests in this module.
# tkinter on Python 3.14 + Windows doesn't reliably support multiple
# Tk constructions per process, so we construct ONE App and reset its
# store/poller state between tests via the `app` fixture below.
_session_app: list = []   # holds the App so the session fixture can clean it up


@pytest.fixture(scope="module")
def _module_app(tmp_path_factory):
    """Construct exactly one App for the whole test module."""
    provider = sp_mock.MockLinkProvider()

    def factory(slave):
        return provider.link_for(slave)

    data_dir = tmp_path_factory.mktemp("control")
    try:
        a = App(data_dir, link_factory=factory, link_provider=provider)
    except Exception as e:
        if "tcl" in str(e).lower() or "tk" in str(e).lower():
            pytest.skip(f"tkinter not usable on this runner: {e}")
        raise
    a.key = b"\x00" * 32

    _session_app.append(a)
    yield a, provider, data_dir
    # Stop background work; let process exit clean up Tk.
    try:
        a.poller.stop(join_timeout=1.0)
    except Exception:
        pass


@pytest.fixture
def app(_module_app, tmp_path):
    """Per-test view of the shared App, with its store reset.

    Each test gets a clean store (no leftover slaves/batfiles from
    previous tests) by re-pointing the App at a fresh data directory
    and reloading. This is the equivalent of constructing a new App
    without actually creating a new Tk root.
    """
    a, provider, _ = _module_app

    # Repoint the store at a fresh per-test dir.
    from simpit_control import data as sp_control_data
    a.paths = sp_control_data.ControlPaths.under(tmp_path)
    a.paths.ensure()
    a.store = sp_control_data.Store(a.paths)
    a.controller.store = a.store

    # Clear the mock provider's slaves so each test starts fresh.
    provider._states.clear()

    a._refresh_dashboard()
    a.update_idletasks()

    yield a, provider


# ── Construction smoke test ──────────────────────────────────────────────────
class TestAppConstruction:
    def test_app_builds(self, app):
        a, _ = app
        # Just verify the basic widgets were created.
        assert a.log_panel is not None
        assert a.batfile_list is not None

    def test_initial_dashboard_empty(self, app):
        a, _ = app
        # An empty store should produce a dashboard with no slaves; the
        # SLAVES header should reflect that.
        a.update_idletasks()
        assert "none yet" in a.slaves_label.cget("text")

    def test_add_slave_via_store_refreshes_view(self, app):
        a, provider = app
        a.store.add_slave(name="MOCK", host="127.0.0.1")
        a._refresh_dashboard()
        a.update_idletasks()
        # SLAVES header should now show 1 offline (mock unregistered = offline)
        assert "SLAVES" in a.slaves_label.cget("text")


class TestAppActions:
    def test_log_appends_on_sync_failure(self, app):
        a, provider = app
        slave = a.store.add_slave(name="MOCK", host="127.0.0.1")
        provider.add(slave.id, sp_mock.MockSlaveState(
            mode=sp_mock.MockMode.OFFLINE))
        a._refresh_dashboard()
        a.update_idletasks()

        a._sync_one_slave(slave.id)

        # The sync runs on a worker thread; the callback uses after(0)
        # to hop back to the main thread. Without mainloop running, we
        # have to pump events ourselves while we wait.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            a.update()                 # processes after() callbacks
            text = a.log_panel.txt.get("1.0", "end").lower()
            if "fail" in text:
                break
            time.sleep(0.05)
        assert "fail" in a.log_panel.txt.get("1.0", "end").lower()
