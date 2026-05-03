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


# ── Toggle pair (enable/disable scenery, etc.) ───────────────────────────────
class TestToggleRowPairing:
    """A pair of bats linked by ``pair_with`` collapses into a single
    row whose per-slave button label/dispatch flips based on each
    slave's probe state. This is the data feeding the UI's "click
    Disable, button becomes Enable" behavior."""

    def _make_store_with_pair(self, tmp_path, slaves: list[tuple[str, str]]):
        """Helper: Store with two paired bats and the given slaves.

        The pair mimics the scenery toggle. Convention: each half's
        probe answers 'is MY action available right now?' so the
        viewmodel just shows whichever half says "present."

          - disable's probe: invert=True on 'Custom Scenery DISABLED'
            -> "present" when DISABLED is *missing* (no snapshot yet,
            so disable is the action that creates one).
          - enable's probe: no invert on the same path
            -> "present" when DISABLED *exists* (a snapshot exists
            to restore from).

        Returns (store, disable_id, enable_id, slave_ids).
        """
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        slave_ids = []
        for name, host in slaves:
            slave_ids.append(store.add_slave(name=name, host=host).id)

        # Mirrors registry exactly: enable checks DISABLED present,
        # disable checks DISABLED absent (via invert).
        probe_disable = {
            "type": "folder_exists",
            "params": {"path": "${XPLANE_FOLDER}/Custom Scenery DISABLED",
                       "invert": True},
        }
        probe_enable = {
            "type": "folder_exists",
            "params": {"path": "${XPLANE_FOLDER}/Custom Scenery DISABLED"},
        }
        disable = store.add_batfile(
            name="Disable Custom Scenery",
            script_name="disable_custom_scenery",
            cascade=True, content="...",
            state_probe=probe_disable,
            pair_with="enable_custom_scenery",
        )
        enable = store.add_batfile(
            name="Enable Custom Scenery",
            script_name="enable_custom_scenery",
            cascade=True, content="...",
            state_probe=probe_enable,
            pair_with="disable_custom_scenery",
        )
        return store, disable.id, enable.id, slave_ids

    @staticmethod
    def _status(slave_id: str, probes: dict[str, str]):
        return sp_poller.SlaveStatus(
            slave_id=slave_id,
            state=sp_poller.SlaveState.ONLINE,
            probe_results=probes,
        )

    def test_pair_collapses_to_single_row(self, tmp_path):
        """Two paired bats produce ONE row in the dashboard, not two."""
        store, disable_id, enable_id, [s1] = self._make_store_with_pair(
            tmp_path, [("CENTERLEFT", "10.0.0.5")])
        # Probe for both halves — scenery currently enabled (DISABLED absent).
        # Under the new convention: each half says "present" iff its
        # action is available. Scenery enabled → disable available.
        statuses = {s1: self._status(s1, {
            disable_id: "present",  # disable's action IS available
            enable_id:  "absent",   # enable's action NOT available (no snapshot)
        })}
        dash = vm.DashboardVM.build(store, statuses, has_key=True)
        assert len(dash.batfiles) == 1, \
            f"paired bats should collapse to 1 row, got {len(dash.batfiles)}"

    def test_unpaired_bats_each_get_their_own_row(self, tmp_path):
        """Sanity: only paired bats collapse. A regular bat still
        produces its own row even when other paired bats exist."""
        store, _, _, _ = self._make_store_with_pair(
            tmp_path, [("A", "h")])
        store.add_batfile(name="Block X-Plane Updates",
                          script_name="block_xplane_updates",
                          cascade=True, content="...")
        dash = vm.DashboardVM.build(store, {}, has_key=True)
        # 1 collapsed pair + 1 standalone = 2 rows
        assert len(dash.batfiles) == 2

    def test_button_label_disable_when_scenery_enabled(self, tmp_path):
        """Slave currently has scenery ENABLED → button on that slave
        should say 'Disable Custom Scenery'."""
        store, disable_id, enable_id, [s1] = self._make_store_with_pair(
            tmp_path, [("CENTERLEFT", "10.0.0.5")])
        statuses = {s1: self._status(s1, {
            # Scenery enabled: disable's action is available, enable's isn't
            disable_id: "present",
            enable_id:  "absent",
        })}
        dash = vm.DashboardVM.build(store, statuses, has_key=True)
        row = dash.batfiles[0]
        assert row.button_label_per_slave[s1] == "Disable Custom Scenery"
        assert row.batfile_id_per_slave[s1] == disable_id

    def test_button_label_enable_when_scenery_disabled(self, tmp_path):
        """Slave currently has scenery DISABLED → button says 'Enable'."""
        store, disable_id, enable_id, [s1] = self._make_store_with_pair(
            tmp_path, [("CENTERLEFT", "10.0.0.5")])
        statuses = {s1: self._status(s1, {
            # Scenery disabled: enable's action available, disable's isn't
            disable_id: "absent",
            enable_id:  "present",
        })}
        dash = vm.DashboardVM.build(store, statuses, has_key=True)
        row = dash.batfiles[0]
        assert row.button_label_per_slave[s1] == "Enable Custom Scenery"
        assert row.batfile_id_per_slave[s1] == enable_id

    def test_per_slave_independence(self, tmp_path):
        """Slaves can independently be in different scenery states.
        Each slave's button flips independently."""
        store, disable_id, enable_id, [s1, s2] = self._make_store_with_pair(
            tmp_path, [("CENTERLEFT", "10.0.0.5"), ("RIGHT", "10.0.0.6")])
        statuses = {
            # s1: scenery on (need Disable -> disable's action available)
            s1: self._status(s1, {disable_id: "present", enable_id: "absent"}),
            # s2: scenery off (need Enable -> enable's action available)
            s2: self._status(s2, {disable_id: "absent",  enable_id: "present"}),
        }
        dash = vm.DashboardVM.build(store, statuses, has_key=True)
        row = dash.batfiles[0]
        assert row.batfile_id_per_slave[s1] == disable_id
        assert row.batfile_id_per_slave[s2] == enable_id
        assert "Disable" in row.button_label_per_slave[s1]
        assert "Enable"  in row.button_label_per_slave[s2]

    def test_unknown_probe_falls_back_to_primary_with_blank_label(
        self, tmp_path
    ):
        """When a slave has no probe result yet (just added, offline),
        we don't know which action to surface. The cell should fall
        back to the primary's id and a blank label (widget renders ▶
        like any other un-toggled script)."""
        store, _, _, [s1] = self._make_store_with_pair(
            tmp_path, [("NEW", "h")])
        # No statuses entry → no probe data
        dash = vm.DashboardVM.build(store, {}, has_key=True)
        row = dash.batfiles[0]
        assert row.button_label_per_slave[s1] == ""
        # Falls back to whichever bat is the row's primary
        assert row.batfile_id_per_slave[s1] == row.batfile_id

    def test_dangling_pair_reference_is_handled(self, tmp_path):
        """If a script claims to pair with a non-existent script (e.g.
        the user deleted one half), we don't crash — we just render
        the surviving half as a normal non-toggle row."""
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        store.add_slave(name="A", host="h")
        store.add_batfile(
            name="Disable Custom Scenery",
            script_name="disable_custom_scenery",
            cascade=True, content="...",
            pair_with="ghost_script",  # doesn't exist
        )
        dash = vm.DashboardVM.build(store, {}, has_key=True)
        assert len(dash.batfiles) == 1
        # Still produces a usable row — no exception, no missing fields
        row = dash.batfiles[0]
        assert row.name == "Disable Custom Scenery"

    def test_pair_collapse_is_deterministic(self, tmp_path):
        """Same store + same statuses → same primary bat across rebuilds.
        Otherwise the row label would jitter every refresh."""
        store, disable_id, enable_id, [s1] = self._make_store_with_pair(
            tmp_path, [("A", "h")])
        # Tied probe state — both "absent" (probes haven't reported in)
        statuses = {s1: self._status(s1, {})}
        names_seen = set()
        for _ in range(5):
            dash = vm.DashboardVM.build(store, statuses, has_key=True)
            names_seen.add(dash.batfiles[0].name)
        assert len(names_seen) == 1, \
            f"primary should be stable across rebuilds, got {names_seen}"

    def test_unpaired_row_per_slave_dispatch_falls_back_to_self(self, tmp_path):
        """For a non-paired bat, every slave's button must dispatch to
        the bat's own id (the existing default). Regression guard for
        the new per-slave maps."""
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        s1 = store.add_slave(name="A", host="h").id
        bat = store.add_batfile(name="Block X-Plane Updates",
                                script_name="block_xplane_updates",
                                cascade=True, content="...")
        dash = vm.DashboardVM.build(store, {}, has_key=True)
        row = dash.batfiles[0]
        assert row.batfile_id_per_slave[s1] == bat.id
        assert row.button_label_per_slave[s1] == ""
