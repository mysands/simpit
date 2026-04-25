"""Tests for simpit_common.probes — extensible state queries."""
import os

import pytest

from simpit_common import probes


class TestEvaluateBasic:
    def test_unknown_type_returns_error(self):
        r = probes.evaluate({"type": "no_such_probe"})
        assert not r.ok

    def test_non_dict_returns_error(self):
        r = probes.evaluate("not a dict")  # type: ignore[arg-type]
        assert not r.ok

    def test_missing_type_returns_error(self):
        r = probes.evaluate({"params": {}})
        assert not r.ok


class TestPathExists:
    def test_present(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hi")
        r = probes.evaluate({"type": "path_exists", "params": {"path": str(f)}})
        assert r.ok and r.value == "present"

    def test_absent(self, tmp_path):
        r = probes.evaluate({"type": "path_exists",
                             "params": {"path": str(tmp_path / "nope")}})
        assert r.ok and r.value == "absent"

    def test_missing_param(self):
        r = probes.evaluate({"type": "path_exists", "params": {}})
        assert not r.ok


class TestFolderExists:
    def test_directory_returns_present(self, tmp_path):
        r = probes.evaluate({"type": "folder_exists",
                             "params": {"path": str(tmp_path)}})
        assert r.ok and r.value == "present"

    def test_file_returns_absent(self, tmp_path):
        # A file at the path is not a directory, so folder_exists must
        # report absent. This distinction matters for users tracking
        # whether a folder was renamed or replaced by a file.
        f = tmp_path / "thing"
        f.write_text("")
        r = probes.evaluate({"type": "folder_exists",
                             "params": {"path": str(f)}})
        assert r.ok and r.value == "absent"


class TestFileContains:
    def test_present(self, tmp_path):
        f = tmp_path / "hosts"
        f.write_text("# X-Plane update block\n127.0.0.1 x-plane.com\n")
        r = probes.evaluate({"type": "file_contains",
                             "params": {"path": str(f),
                                        "contains": "X-Plane update block"}})
        assert r.ok and r.value == "present"

    def test_absent(self, tmp_path):
        f = tmp_path / "hosts"
        f.write_text("nothing in particular")
        r = probes.evaluate({"type": "file_contains",
                             "params": {"path": str(f),
                                        "contains": "X-Plane update block"}})
        assert r.ok and r.value == "absent"

    def test_missing_file_returns_absent(self, tmp_path):
        r = probes.evaluate({"type": "file_contains",
                             "params": {"path": str(tmp_path / "nope"),
                                        "contains": "x"}})
        assert r.ok and r.value == "absent"


class TestProcessRunning:
    def test_self_is_running(self):
        import psutil
        my_name = psutil.Process(os.getpid()).name()
        r = probes.evaluate({"type": "process_running",
                             "params": {"name": my_name}})
        assert r.ok and r.value == "running"

    def test_unknown_not_running(self):
        r = probes.evaluate({"type": "process_running",
                             "params": {"name": "xyz_does_not_exist_99"}})
        assert r.ok and r.value == "not_running"


class TestEnvSubstitution:
    def test_env_var_substituted(self, tmp_path):
        # ${TARGET} in the path is replaced from the env dict at evaluation.
        r = probes.evaluate(
            {"type": "path_exists", "params": {"path": "${TARGET}/x"}},
            env={"TARGET": str(tmp_path)},
        )
        assert r.ok and r.value == "absent"

    def test_unknown_env_var_left_literal(self, tmp_path):
        # When the env doesn't define the var, we leave the placeholder in
        # place rather than guessing — it'll fail the path check loudly,
        # which is easier to debug than a silent empty-substitution.
        r = probes.evaluate(
            {"type": "path_exists", "params": {"path": "${UNDEFINED}/x"}},
            env={},
        )
        assert r.ok and r.value == "absent"


class TestRegistry:
    def test_known_probe_types_includes_built_ins(self):
        names = probes.known_probe_types()
        assert "path_exists" in names
        assert "folder_exists" in names
        assert "file_contains" in names
        assert "process_running" in names

    def test_register_adds_probe(self):
        def my_probe(params, env):
            return probes.ProbeResult(ok=True, value="custom")
        probes.register("my_test_probe_x", my_probe)
        r = probes.evaluate({"type": "my_test_probe_x"})
        assert r.ok and r.value == "custom"

    def test_register_rejects_duplicate(self):
        def fn(params, env):
            return probes.ProbeResult(ok=True, value="x")
        probes.register("dupe_name_test", fn)
        with pytest.raises(ValueError):
            probes.register("dupe_name_test", fn)
