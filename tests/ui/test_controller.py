"""Tests for simpit_control.ui.controller — operations using mock slaves."""
import threading

import pytest

from simpit_control import data as sp_data
from simpit_control import mock_slave as sp_mock
from simpit_control import poller as sp_poller
from simpit_control.ui import controller as sp_ctrl


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path):
    return sp_data.Store(sp_data.ControlPaths.under(tmp_path))


@pytest.fixture
def mock_factory():
    """Return a factory + the underlying provider so tests can flip modes."""
    provider = sp_mock.MockLinkProvider()
    def factory(slave):
        return provider.link_for(slave)
    factory.provider = provider  # type: ignore[attr-defined]
    return factory


# ── Slave validation ─────────────────────────────────────────────────────────
class TestAddSlave:
    def test_happy_path(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        s = c.add_slave(name="X", host="10.0.0.1")
        assert s in store.slaves()

    def test_strips_whitespace(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        s = c.add_slave(name="  X  ", host="  h  ")
        assert s.name == "X"
        assert s.host == "h"

    def test_rejects_empty_name(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        with pytest.raises(ValueError):
            c.add_slave(name="", host="h")

    def test_rejects_empty_host(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        with pytest.raises(ValueError):
            c.add_slave(name="X", host="")

    def test_rejects_out_of_range_port(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        with pytest.raises(ValueError):
            c.add_slave(name="X", host="h", udp_port=99999)


# ── BatFile validation ───────────────────────────────────────────────────────
class TestAddBatFile:
    def test_cascade_requires_content(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        with pytest.raises(ValueError):
            c.add_batfile(name="X", script_name="x",
                           cascade=True, content="")

    def test_non_cascade_requires_local_path(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        with pytest.raises(ValueError):
            c.add_batfile(name="X", script_name="x",
                           cascade=False, local_path="")

    def test_happy_path_cascade(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        b = c.add_batfile(name="X", script_name="x",
                           cascade=True, content="echo")
        assert b in store.batfiles()

    def test_happy_path_local(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        b = c.add_batfile(name="X", script_name="x",
                           cascade=False, local_path="/tmp/x.sh")
        assert b in store.batfiles()


# ── exec_on_slave ────────────────────────────────────────────────────────────
class TestExecOnSlave:
    def _wait_callback(self):
        """Helper: build a callback that records into a threading.Event."""
        results = []
        done = threading.Event()
        def cb(r):
            results.append(r)
            done.set()
        return cb, results, done

    def test_runs_script_on_slave(self, store, mock_factory):
        s = store.add_slave(name="X", host="h")
        b = store.add_batfile(name="C", script_name="cmd",
                               cascade=True, content="x")
        mock_factory.provider.add(s.id, sp_mock.MockSlaveState())
        c = sp_ctrl.Controller(store, mock_factory)

        cb, results, done = self._wait_callback()
        c.exec_on_slave(s.id, b.id, on_done=cb)
        assert done.wait(2.0)
        assert results[0].ok

    def test_exec_offline_slave_reports_failure(self, store, mock_factory):
        s = store.add_slave(name="X", host="h")
        b = store.add_batfile(name="C", script_name="cmd",
                               cascade=True, content="x")
        mock_factory.provider.add(
            s.id, sp_mock.MockSlaveState(mode=sp_mock.MockMode.OFFLINE))
        c = sp_ctrl.Controller(store, mock_factory)

        cb, results, done = self._wait_callback()
        c.exec_on_slave(s.id, b.id, on_done=cb)
        assert done.wait(2.0)
        assert not results[0].ok
        assert "offline" in results[0].msg.lower()

    def test_non_cascade_raises_immediately(self, store, mock_factory):
        s = store.add_slave(name="X", host="h")
        b = store.add_batfile(name="C", script_name="cmd",
                               cascade=False, local_path="/x")
        c = sp_ctrl.Controller(store, mock_factory)
        with pytest.raises(ValueError, match="non-cascaded"):
            c.exec_on_slave(s.id, b.id)


# ── sync_push_to_slave ───────────────────────────────────────────────────────
class TestSyncPushToSlave:
    def test_pushes_cascaded_set(self, store, mock_factory):
        s = store.add_slave(name="X", host="h")
        store.add_batfile(name="A", script_name="a",
                           cascade=True, content="echo a")
        store.add_batfile(name="B", script_name="b",
                           cascade=True, content="echo b")
        store.add_batfile(name="local", script_name="l",
                           cascade=False, local_path="/x")  # excluded

        mock_factory.provider.add(s.id, sp_mock.MockSlaveState())
        c = sp_ctrl.Controller(store, mock_factory)

        results = []
        done = threading.Event()
        c.sync_push_to_slave(s.id, on_done=lambda r: (
            results.append(r), done.set()))
        assert done.wait(2.0)
        assert results[0].ok
        # Only the two cascaded scripts should have been pushed.
        assert results[0].body["count"] == 2

    def test_marks_syncing_when_poller_provided(self, store, mock_factory):
        s = store.add_slave(name="X", host="h")
        mock_factory.provider.add(
            s.id, sp_mock.MockSlaveState(mode=sp_mock.MockMode.SLOW,
                                           slow_seconds=0.2))
        # Use a real poller so we can read state from it.
        provider = sp_poller.RealLinkProvider(key=b"x" * 32)
        poller = sp_poller.Poller(store, provider)
        c = sp_ctrl.Controller(store, mock_factory, poller=poller)

        c.sync_push_to_slave(s.id)
        # Should be SYNCING now (or very shortly after).
        assert poller.get(s.id).state == sp_poller.SlaveState.SYNCING


# ── shutdown_slave ───────────────────────────────────────────────────────────
class TestShutdownSlave:
    def test_acks(self, store, mock_factory):
        s = store.add_slave(name="X", host="h")
        mock_factory.provider.add(s.id, sp_mock.MockSlaveState())
        c = sp_ctrl.Controller(store, mock_factory)
        results = []
        done = threading.Event()
        c.shutdown_slave(s.id, on_done=lambda r: (
            results.append(r), done.set()))
        assert done.wait(2.0)
        assert results[0].ok


# ── Unknown id handling ──────────────────────────────────────────────────────
class TestUnknownId:
    def test_exec_unknown_slave_raises(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        with pytest.raises(KeyError):
            c.exec_on_slave("ghost", "bat_x")

    def test_sync_unknown_slave_raises(self, store, mock_factory):
        c = sp_ctrl.Controller(store, mock_factory)
        with pytest.raises(KeyError):
            c.sync_push_to_slave("ghost")
