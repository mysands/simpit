"""Tests for simpit_slave.data — paths, script lookup, sync push."""
import os

import pytest

from simpit_common import platform as sp_platform
from simpit_slave import data as sp_data

# Determine the script extension we're working with on this platform.
EXT = sp_platform.script_extension()


# ── SlavePaths ───────────────────────────────────────────────────────────────
class TestSlavePaths:
    def test_under_creates_layout(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        assert paths.root == tmp_path
        assert paths.key_file.parent == tmp_path
        assert paths.cascaded.parent == tmp_path
        assert paths.local.parent == tmp_path

    def test_ensure_creates_directories(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path / "data")
        paths.ensure()
        assert paths.root.is_dir()
        assert paths.cascaded.is_dir()
        assert paths.local.is_dir()

    def test_ensure_is_idempotent(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        paths.ensure()
        paths.ensure()  # should not raise


# ── find_script ──────────────────────────────────────────────────────────────
class TestFindScript:
    def setup_method(self):
        self.scripts = []

    def _make(self, paths, where, name):
        """Helper: create a script in cascaded/ or local/."""
        p = getattr(paths, where) / (name + EXT)
        p.write_text("#!/bin/sh\necho hi\n" if EXT == ".sh"
                     else "@echo off\necho hi\n")
        if os.name == "posix":
            p.chmod(0o755)
        return p

    def test_finds_in_cascaded(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        self._make(paths, "cascaded", "launch")
        assert sp_data.find_script(paths, "launch") is not None

    def test_finds_in_local(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        self._make(paths, "local", "calibrate")
        assert sp_data.find_script(paths, "calibrate") is not None

    def test_cascaded_wins_over_local(self, tmp_path):
        # If the same name exists in both, cascaded must win — that's
        # the defined behaviour so Control's pushes can't be silently
        # overridden by a stale local file.
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        cascaded = self._make(paths, "cascaded", "shared")
        self._make(paths, "local", "shared")  # collision-by-design
        found = sp_data.find_script(paths, "shared")
        assert found == cascaded

    def test_missing_returns_none(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        assert sp_data.find_script(paths, "nope") is None

    def test_blocks_path_traversal(self, tmp_path):
        # Critical security property: a forged EXEC_SCRIPT body must not
        # be able to walk out of the script dirs.
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        for evil in ["../etc/passwd", "..\\windows\\system32",
                     "/absolute/path", "C:\\Windows\\cmd",
                     "subdir/script", "script\x00x"]:
            assert sp_data.find_script(paths, evil) is None, evil

    def test_blocks_dot_names(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        assert sp_data.find_script(paths, ".") is None
        assert sp_data.find_script(paths, "..") is None
        assert sp_data.find_script(paths, "") is None

    def test_rejects_non_string(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        assert sp_data.find_script(paths, None) is None  # type: ignore[arg-type]
        assert sp_data.find_script(paths, 42) is None    # type: ignore[arg-type]


# ── list_scripts ─────────────────────────────────────────────────────────────
class TestListScripts:
    def test_empty_when_no_scripts(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        out = sp_data.list_scripts(paths)
        assert out == {"cascaded": [], "local": []}

    def test_lists_base_names_only(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        (paths.cascaded / ("alpha" + EXT)).write_text("a")
        (paths.local    / ("beta"  + EXT)).write_text("b")
        out = sp_data.list_scripts(paths)
        assert "alpha" in out["cascaded"]
        assert "beta"  in out["local"]

    def test_handles_missing_dirs(self, tmp_path):
        # If somebody deletes cascaded/ behind our back, list shouldn't crash.
        paths = sp_data.SlavePaths.under(tmp_path)
        # don't call ensure
        out = sp_data.list_scripts(paths)
        assert out == {"cascaded": [], "local": []}


# ── apply_sync_push ──────────────────────────────────────────────────────────
class TestApplySyncPush:
    def test_writes_scripts(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        scripts = [
            sp_data.CascadedScript(name="alpha", content="echo A\n"),
            sp_data.CascadedScript(name="beta",  content="echo B\n"),
        ]
        result = sp_data.apply_sync_push(paths, scripts)
        assert result["count"] == 2
        assert (paths.cascaded / ("alpha" + EXT)).read_text().startswith("echo A")
        assert (paths.cascaded / ("beta"  + EXT)).read_text().startswith("echo B")

    def test_replaces_old_scripts(self, tmp_path):
        # Anything not in the new push must be removed — that's the
        # contract of a "full replace" sync.
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        (paths.cascaded / ("oldscript" + EXT)).write_text("old")
        scripts = [sp_data.CascadedScript(name="newscript", content="new\n")]
        sp_data.apply_sync_push(paths, scripts)
        assert not (paths.cascaded / ("oldscript" + EXT)).exists()
        assert (paths.cascaded / ("newscript" + EXT)).exists()

    def test_skips_os_mismatched(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        other_os = ("linux" if sp_platform.current_os() != sp_platform.OS.LINUX
                    else "windows")
        scripts = [
            sp_data.CascadedScript(name="for_us", content="ours"),
            sp_data.CascadedScript(name="for_them", content="theirs",
                                    os=other_os),
        ]
        result = sp_data.apply_sync_push(paths, scripts)
        assert result["count"] == 1
        assert "for_us" in result["written"]
        assert any(s["name"] == "for_them" and s["reason"] == "os_mismatch"
                   for s in result["skipped"])

    def test_skips_bad_names(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        scripts = [
            sp_data.CascadedScript(name="../evil", content="x"),
            sp_data.CascadedScript(name="ok", content="ok"),
        ]
        result = sp_data.apply_sync_push(paths, scripts)
        assert "ok" in result["written"]
        assert any(s["reason"] == "bad_name" for s in result["skipped"])
        # And critically nothing was created outside the cascaded dir.
        assert not (tmp_path.parent / "evil").exists()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX-only chmod test")
    def test_marks_executable_on_posix(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        sp_data.apply_sync_push(paths, [
            sp_data.CascadedScript(name="x", content="#!/bin/sh\necho hi\n"),
        ])
        path = paths.cascaded / "x.sh"
        assert os.access(path, os.X_OK)
