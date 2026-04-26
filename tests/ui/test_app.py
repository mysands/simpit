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


@pytest.fixture
def app(tmp_path):
    """Build an App backed by mock slaves; tear it down after the test."""
    provider = sp_mock.MockLinkProvider()

    def factory(slave):
        return provider.link_for(slave)

    a = App(tmp_path, link_factory=factory, link_provider=provider)
    # Avoid the first-run dialog blocking the test; simulate having a key.
    a.key = b"\x00" * 32
    yield a, provider
    try:
        a._on_close()
    except Exception:
        pass


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
