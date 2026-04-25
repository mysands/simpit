"""Tests for simpit_control.ui.viewmodels — pure logic, no Tk needed."""
import time

from simpit_control import data as sp_data
from simpit_control import poller as sp_poller
from simpit_control.ui import viewmodels as vm


# ── SlaveCardVM ──────────────────────────────────────────────────────────────
class TestSlaveCardVM:
    def test_basic_construction(self):
        slave = sp_data.Slave(id="s1", name="CENTERLEFT",
                               host="10.0.0.5", udp_port=49100, tcp_port=49101)
        status = sp_poller.SlaveStatus(
            slave_id="s1", state=sp_poller.SlaveState.ONLINE,
            last_seen=time.time())
        card = vm.SlaveCardVM.build(slave, status)
        assert card.name == "CENTERLEFT"
        assert card.host_label == "10.0.0.5:49100"
        assert card.state_text == "ONLINE"
        assert not card.is_offline

    def test_offline_flag(self):
        slave = sp_data.Slave(id="s1", name="X", host="h")
        status = sp_poller.SlaveStatus(
            slave_id="s1", state=sp_poller.SlaveState.OFFLINE)
        card = vm.SlaveCardVM.build(slave, status)
        assert card.is_offline
        assert card.state_text == "OFFLINE"

    def test_running_state_color(self):
        slave = sp_data.Slave(id="s1", name="X", host="h")
        status = sp_poller.SlaveStatus(
            slave_id="s1", state=sp_poller.SlaveState.RUNNING,
            last_seen=time.time())
        card = vm.SlaveCardVM.build(slave, status)
        # RUNNING uses green per the theme.
        from simpit_control.ui import theme
        assert card.state_color == theme.GREEN

    def test_error_text_carries_through(self):
        slave = sp_data.Slave(id="s1", name="X", host="h")
        status = sp_poller.SlaveStatus(
            slave_id="s1", state=sp_poller.SlaveState.ERROR,
            error="key mismatch detected")
        card = vm.SlaveCardVM.build(slave, status)
        assert "key mismatch" in card.error_text

    def test_last_seen_text_humanized(self):
        slave = sp_data.Slave(id="s1", name="X", host="h")
        status = sp_poller.SlaveStatus(
            slave_id="s1", state=sp_poller.SlaveState.ONLINE,
            last_seen=1000.0)
        card = vm.SlaveCardVM.build(slave, status, now=1030.0)
        assert "30s ago" in card.last_seen_text

    def test_last_seen_dash_when_never(self):
        slave = sp_data.Slave(id="s1", name="X", host="h")
        status = sp_poller.SlaveStatus(slave_id="s1")  # last_seen=0
        card = vm.SlaveCardVM.build(slave, status, now=1000.0)
        assert card.last_seen_text == "—"

    def test_probe_summary_shows_interesting_values_first(self):
        # Values like "running" and "present" should show up before
        # the "absent" entries because that's what the user wants to see
        # at a glance.
        slave = sp_data.Slave(id="s1", name="X", host="h")
        status = sp_poller.SlaveStatus(
            slave_id="s1", state=sp_poller.SlaveState.ONLINE,
            probe_results={"a": "absent", "b": "running", "c": "absent"})
        card = vm.SlaveCardVM.build(slave, status)
        assert card.probe_summary.startswith("b: running")


# ── BatFileRowVM ─────────────────────────────────────────────────────────────
class TestBatFileRowVM:
    def test_target_count_all_slaves(self):
        bat = sp_data.BatFile(id="b1", name="X", script_name="x",
                                cascade=True, target_slaves=None)
        row = vm.BatFileRowVM.build(bat, ["s1", "s2"], {})
        assert row.target_count == "all slaves"

    def test_target_count_specific(self):
        bat = sp_data.BatFile(id="b1", name="X", script_name="x",
                                cascade=True, target_slaves=["s1", "s2"])
        row = vm.BatFileRowVM.build(bat, ["s1", "s2"], {})
        assert row.target_count == "2 slaves"

    def test_target_count_singular(self):
        bat = sp_data.BatFile(id="b1", name="X", script_name="x",
                                cascade=True, target_slaves=["s1"])
        row = vm.BatFileRowVM.build(bat, ["s1"], {})
        assert row.target_count == "1 slave"

    def test_probe_status_per_slave_populated(self):
        bat = sp_data.BatFile(id="b1", name="X", script_name="x",
                                cascade=True,
                                state_probe={"type": "folder_exists",
                                             "params": {"path": "/x"}})
        probes = {"s1": {"b1": "present"}, "s2": {"b1": "absent"}}
        row = vm.BatFileRowVM.build(bat, ["s1", "s2"], probes)
        assert row.has_probe is True
        assert row.probe_status_per_slave == {"s1": "present",
                                                "s2": "absent"}

    def test_no_probe_means_empty_per_slave_map(self):
        bat = sp_data.BatFile(id="b1", name="X", script_name="x",
                                cascade=True)
        row = vm.BatFileRowVM.build(bat, ["s1", "s2"], {})
        assert row.has_probe is False
        assert row.probe_status_per_slave == {}


# ── DashboardVM ──────────────────────────────────────────────────────────────
class TestDashboardVM:
    def test_aggregates_slaves_and_batfiles(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        s1 = store.add_slave(name="A", host="h1")
        s2 = store.add_slave(name="B", host="h2")
        store.add_batfile(name="cmd", script_name="cmd",
                           cascade=True, content="x")
        statuses = {
            s1.id: sp_poller.SlaveStatus(
                slave_id=s1.id, state=sp_poller.SlaveState.ONLINE),
            s2.id: sp_poller.SlaveStatus(
                slave_id=s2.id, state=sp_poller.SlaveState.OFFLINE),
        }
        dash = vm.DashboardVM.build(store, statuses, has_key=True)
        assert len(dash.slaves) == 2
        assert len(dash.batfiles) == 1
        assert dash.online_count == 1
        assert dash.offline_count == 1

    def test_running_counts_as_online(self, tmp_path):
        # RUNNING is conceptually 'online and X-Plane up' — we count it
        # in online_count so the UI summary doesn't show "0 online" while
        # X-Plane is launched everywhere.
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        s = store.add_slave(name="A", host="h")
        statuses = {
            s.id: sp_poller.SlaveStatus(
                slave_id=s.id, state=sp_poller.SlaveState.RUNNING),
        }
        dash = vm.DashboardVM.build(store, statuses, has_key=True)
        assert dash.online_count == 1

    def test_missing_status_yields_unknown(self, tmp_path):
        # When the poller hasn't yet polled a freshly-added slave, the
        # statuses dict won't contain it — the dashboard should still
        # render with a default UNKNOWN entry.
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        store.add_slave(name="A", host="h")
        dash = vm.DashboardVM.build(store, {}, has_key=True)
        assert len(dash.slaves) == 1
        assert dash.slaves[0].state_value == "unknown"
