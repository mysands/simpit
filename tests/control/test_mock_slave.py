"""Tests for simpit_control.mock_slave — failure injection."""
import time

import pytest

from simpit_control import data as sp_data
from simpit_control import mock_slave as sp_mock
from simpit_control import slave_link as sp_link


@pytest.fixture
def slave():
    return sp_data.Slave(id="x", name="X", host="h")


# ── Modes ────────────────────────────────────────────────────────────────────
class TestModes:
    def test_normal_returns_dict(self, slave):
        link = sp_mock.MockSlaveLink(slave, sp_mock.MockSlaveState())
        assert "ts" in link.ping()

    def test_offline_raises_unreachable(self, slave):
        state = sp_mock.MockSlaveState(mode=sp_mock.MockMode.OFFLINE)
        link = sp_mock.MockSlaveLink(slave, state)
        with pytest.raises(sp_link.SlaveUnreachable):
            link.ping()

    def test_timeout_raises_timeout(self, slave):
        state = sp_mock.MockSlaveState(mode=sp_mock.MockMode.TIMEOUT)
        link = sp_mock.MockSlaveLink(slave, state)
        with pytest.raises(sp_link.SlaveTimeout):
            link.ping()

    def test_bad_key_raises_bad_response(self, slave):
        state = sp_mock.MockSlaveState(mode=sp_mock.MockMode.BAD_KEY)
        link = sp_mock.MockSlaveLink(slave, state)
        with pytest.raises(sp_link.SlaveBadResponse):
            link.ping()

    def test_slow_succeeds_after_delay(self, slave):
        state = sp_mock.MockSlaveState(mode=sp_mock.MockMode.SLOW,
                                        slow_seconds=0.1)
        link = sp_mock.MockSlaveLink(slave, state)
        t0 = time.time()
        link.ping()
        assert time.time() - t0 >= 0.1


# ── Status / probes ──────────────────────────────────────────────────────────
class TestStatusFromMock:
    def test_status_uses_state_overrides(self, slave):
        state = sp_mock.MockSlaveState()
        state.probe_overrides["bat_xyz"] = "running"
        link = sp_mock.MockSlaveLink(slave, state)
        body = link.status(probes=[
            {"name": "bat_xyz", "type": "process_running",
             "params": {"name": "X-Plane"}},
        ])
        assert body["probes"][0]["value"] == "running"

    def test_status_unmatched_probe_returns_absent(self, slave):
        link = sp_mock.MockSlaveLink(slave, sp_mock.MockSlaveState())
        body = link.status(probes=[
            {"name": "no_override", "type": "x"},
        ])
        assert body["probes"][0]["value"] == "absent"

    def test_status_includes_inventory(self, slave):
        state = sp_mock.MockSlaveState(cascaded_scripts=["a", "b"])
        link = sp_mock.MockSlaveLink(slave, state)
        body = link.status()
        assert body["script_inventory"]["cascaded"] == ["a", "b"]


# ── Sync push mutates state ──────────────────────────────────────────────────
class TestSyncPush:
    def test_push_updates_inventory(self, slave):
        state = sp_mock.MockSlaveState()
        link = sp_mock.MockSlaveLink(slave, state)
        body = link.sync_push([
            {"name": "alpha", "content": "x"},
            {"name": "beta",  "content": "y"},
        ])
        assert body["count"] == 2
        # Subsequent STATUS should reflect the pushed inventory.
        st = link.status()
        assert "alpha" in st["script_inventory"]["cascaded"]


# ── MockLinkProvider ─────────────────────────────────────────────────────────
class TestProvider:
    def test_unknown_slave_id_defaults_to_offline(self, slave):
        provider = sp_mock.MockLinkProvider()
        link = provider.link_for(slave)
        with pytest.raises(sp_link.SlaveUnreachable):
            link.ping()

    def test_state_for_returns_mutable(self):
        provider = sp_mock.MockLinkProvider()
        provider.add("slave_x")
        st = provider.state_for("slave_x")
        st.mode = sp_mock.MockMode.OFFLINE
        # Future link should reflect mutation.
        slave = sp_data.Slave(id="slave_x", name="X", host="h")
        link = provider.link_for(slave)
        with pytest.raises(sp_link.SlaveUnreachable):
            link.ping()
