"""Tests for simpit_control.data — Store, persistence, schema."""
import json

import pytest

from simpit_control import data as sp_data


# ── ControlPaths ─────────────────────────────────────────────────────────────
class TestControlPaths:
    def test_under_constructs_layout(self, tmp_path):
        paths = sp_data.ControlPaths.under(tmp_path)
        assert paths.root == tmp_path
        assert paths.slaves_file == tmp_path / "slaves.json"
        assert paths.batfiles_file == tmp_path / "batfiles.json"


# ── Slave dataclass ──────────────────────────────────────────────────────────
class TestSlave:
    def test_to_from_dict_roundtrip(self):
        s = sp_data.Slave(id="slave_x", name="CENTERLEFT",
                           host="10.0.0.5", udp_port=49100, tcp_port=49101)
        out = sp_data.Slave.from_dict(s.to_dict())
        assert out == s

    def test_from_dict_supplies_default_ports(self):
        s = sp_data.Slave.from_dict({"id": "x", "name": "A", "host": "h"})
        assert s.udp_port == 49100
        assert s.tcp_port == 49101


# ── BatFile dataclass ────────────────────────────────────────────────────────
class TestBatFile:
    def test_applies_to_slave_when_cascade_and_no_targets(self):
        b = sp_data.BatFile(id="b", name="N", script_name="s",
                            cascade=True, target_slaves=None)
        assert b.applies_to_slave("any_slave_id")

    def test_applies_to_slave_when_targeted(self):
        b = sp_data.BatFile(id="b", name="N", script_name="s",
                            cascade=True, target_slaves=["a", "b"])
        assert b.applies_to_slave("a")
        assert not b.applies_to_slave("z")

    def test_does_not_apply_when_not_cascade(self):
        b = sp_data.BatFile(id="b", name="N", script_name="s",
                            cascade=False, target_slaves=None)
        assert not b.applies_to_slave("anyone")

    def test_from_dict_defaults(self):
        b = sp_data.BatFile.from_dict({
            "id": "x", "name": "X", "script_name": "x",
        })
        assert b.cascade is False
        assert b.content == ""
        assert b.local_path == ""
        assert b.target_slaves is None
        assert b.needs_admin is False
        assert b.state_probe is None


# ── Store: empty / fresh ─────────────────────────────────────────────────────
class TestEmptyStore:
    def test_fresh_dir_yields_empty_store(self, tmp_path):
        paths = sp_data.ControlPaths.under(tmp_path)
        store = sp_data.Store(paths)
        assert store.slaves() == []
        assert store.batfiles() == []

    def test_save_creates_files(self, tmp_path):
        paths = sp_data.ControlPaths.under(tmp_path)
        store = sp_data.Store(paths)
        store.save()
        assert paths.slaves_file.is_file()
        assert paths.batfiles_file.is_file()
        # Contents are valid JSON with version envelope.
        for f in (paths.slaves_file, paths.batfiles_file):
            data = json.loads(f.read_text())
            assert data["version"] == sp_data.SCHEMA_VERSION


# ── Slave CRUD ───────────────────────────────────────────────────────────────
class TestSlaveCRUD:
    def test_add_returns_slave_with_generated_id(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        s = store.add_slave(name="CENTERLEFT", host="10.0.0.5")
        assert s.id.startswith("slave_")
        assert store.get_slave(s.id) == s

    def test_add_persists_to_disk(self, tmp_path):
        paths = sp_data.ControlPaths.under(tmp_path)
        store1 = sp_data.Store(paths)
        store1.add_slave(name="X", host="h")
        # Re-load from disk
        store2 = sp_data.Store(paths)
        assert len(store2.slaves()) == 1

    def test_update_replaces_in_place(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        s = store.add_slave(name="X", host="h")
        s.name = "Y"
        store.update_slave(s)
        assert store.get_slave(s.id).name == "Y"

    def test_update_unknown_raises(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        ghost = sp_data.Slave(id="slave_ghost", name="X", host="h")
        with pytest.raises(KeyError):
            store.update_slave(ghost)

    def test_delete_removes(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        s = store.add_slave(name="X", host="h")
        store.delete_slave(s.id)
        assert store.get_slave(s.id) is None

    def test_delete_strips_targets_in_batfiles(self, tmp_path):
        # Critical: when a slave is removed, any batfile targeting it
        # MUST forget that target — otherwise the on-disk state has a
        # dangling reference and SYNC_PUSH could try to push to ghost.
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        s1 = store.add_slave(name="A", host="h1")
        s2 = store.add_slave(name="B", host="h2")
        store.add_batfile(name="cmd", script_name="cmd",
                          cascade=True, target_slaves=[s1.id, s2.id])
        store.delete_slave(s1.id)
        bats = store.batfiles()
        assert len(bats) == 1
        assert bats[0].target_slaves == [s2.id]

    def test_slaves_sorted_by_name(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        store.add_slave(name="ZULU", host="h")
        store.add_slave(name="alpha", host="h")
        store.add_slave(name="Mike", host="h")
        names = [s.name for s in store.slaves()]
        assert names == ["alpha", "Mike", "ZULU"]


# ── BatFile CRUD ─────────────────────────────────────────────────────────────
class TestBatFileCRUD:
    def test_add_persists(self, tmp_path):
        paths = sp_data.ControlPaths.under(tmp_path)
        store1 = sp_data.Store(paths)
        store1.add_batfile(name="Launch", script_name="launch_xplane",
                           cascade=True, content="echo hi\n")
        store2 = sp_data.Store(paths)
        assert len(store2.batfiles()) == 1

    def test_add_with_state_probe(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        store.add_batfile(name="Scenery", script_name="toggle_scenery",
                          cascade=True,
                          state_probe={"type": "folder_exists",
                                       "params": {"path": "/foo"}})
        bat = store.batfiles()[0]
        assert bat.state_probe["type"] == "folder_exists"

    def test_cascaded_for_slave(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        s = store.add_slave(name="X", host="h")
        store.add_batfile(name="all", script_name="a", cascade=True)
        store.add_batfile(name="local", script_name="l", cascade=False)
        store.add_batfile(name="targeted", script_name="t", cascade=True,
                          target_slaves=["other_id"])
        result = store.cascaded_for_slave(s.id)
        names = sorted(b.name for b in result)
        assert names == ["all"]


# ── Schema corruption recovery ───────────────────────────────────────────────
class TestCorruptionRecovery:
    def test_corrupt_slaves_file_yields_empty_list(self, tmp_path):
        paths = sp_data.ControlPaths.under(tmp_path); paths.ensure()
        paths.slaves_file.write_text("not valid json {{{")
        store = sp_data.Store(paths)
        assert store.slaves() == []

    def test_partial_slave_records_skipped(self, tmp_path):
        # A record missing required fields should be silently dropped
        # rather than crashing the whole load.
        paths = sp_data.ControlPaths.under(tmp_path); paths.ensure()
        paths.slaves_file.write_text(json.dumps({
            "version": 1,
            "slaves": [
                {"id": "ok", "name": "OK", "host": "h"},
                {"name": "missing_id"},  # bad
            ],
        }))
        store = sp_data.Store(paths)
        assert len(store.slaves()) == 1
