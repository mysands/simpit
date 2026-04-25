"""Tests for simpit_slave.inspector — STATUS snapshot construction."""

from simpit_common import platform as sp_platform
from simpit_slave import data as sp_data
from simpit_slave import inspector as sp_inspector


class TestSnapshotAlwaysOnFields:
    def test_includes_hostname(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        snap = sp_inspector.snapshot(paths)
        assert snap.hostname  # non-empty

    def test_includes_os(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        snap = sp_inspector.snapshot(paths)
        assert snap.os in (sp_platform.OS.WINDOWS,
                           sp_platform.OS.LINUX,
                           sp_platform.OS.MACOS)

    def test_includes_uptime(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        snap = sp_inspector.snapshot(paths)
        assert isinstance(snap.uptime_sec, int)
        assert snap.uptime_sec >= 0

    def test_includes_is_admin_bool(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        snap = sp_inspector.snapshot(paths)
        assert isinstance(snap.is_admin, bool)

    def test_includes_script_inventory(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        ext = sp_platform.script_extension()
        (paths.cascaded / ("script_a" + ext)).write_text("a")
        snap = sp_inspector.snapshot(paths)
        assert "script_a" in snap.script_inventory["cascaded"]


class TestSnapshotProbes:
    def test_no_probes_returns_empty_list(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        snap = sp_inspector.snapshot(paths)
        assert snap.probes == []

    def test_probe_results_carry_name(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        target = tmp_path / "exists.txt"
        target.write_text("x")
        snap = sp_inspector.snapshot(paths, probe_requests=[
            {"name": "my_check", "type": "path_exists",
             "params": {"path": str(target)}},
        ])
        assert len(snap.probes) == 1
        assert snap.probes[0].name == "my_check"
        assert snap.probes[0].value == "present"

    def test_invalid_probe_yields_error_outcome(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        snap = sp_inspector.snapshot(paths, probe_requests=[
            {"name": "broken", "type": "no_such_type"},
        ])
        assert len(snap.probes) == 1
        assert not snap.probes[0].ok

    def test_env_substitution_works(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        target = tmp_path / "thing"
        target.mkdir()
        snap = sp_inspector.snapshot(paths,
            probe_requests=[{
                "name": "scenery", "type": "folder_exists",
                "params": {"path": "${BASE}/thing"},
            }],
            env={"BASE": str(tmp_path)},
        )
        assert snap.probes[0].value == "present"


class TestSnapshotSerialization:
    def test_to_dict_is_json_serializable(self, tmp_path):
        import json
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        snap = sp_inspector.snapshot(paths, probe_requests=[
            {"name": "x", "type": "path_exists",
             "params": {"path": str(tmp_path)}},
        ])
        # Must round-trip through JSON so it can go on the wire.
        json.dumps(snap.to_dict())
