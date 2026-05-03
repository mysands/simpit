"""Tests for simpit_slave.executor — script execution."""

import pytest

from simpit_common import platform as sp_platform
from simpit_slave import data as sp_data
from simpit_slave import executor as sp_executor

EXT = sp_platform.script_extension()


def _write_script(paths, name, body):
    """Write a one-shot script. POSIX shebang on POSIX, plain bat on Windows."""
    paths.ensure()
    target = paths.cascaded / (name + EXT)
    if EXT == ".sh":
        target.write_text("#!/bin/sh\n" + body)
        target.chmod(0o755)
    else:
        target.write_text("@echo off\n" + body)
    return target


# ── Buffered execute ─────────────────────────────────────────────────────────
class TestExecute:
    def test_runs_and_captures_stdout(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        _write_script(paths, "hello", "echo hello world\n")
        result = sp_executor.execute(paths, "hello")
        assert result.found
        assert result.exit_code == 0
        assert "hello world" in result.stdout

    def test_returns_exit_code(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        _write_script(paths, "fail", "exit 7\n")
        result = sp_executor.execute(paths, "fail")
        assert result.exit_code == 7

    def test_missing_script(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        result = sp_executor.execute(paths, "no_such_thing")
        assert not result.found
        assert result.exit_code == -1
        assert "not found" in result.error

    def test_path_traversal_rejected(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        result = sp_executor.execute(paths, "../escape")
        assert not result.found

    def test_passes_env_overrides(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        # Echo an env var to verify Control's overrides reach the script.
        if EXT == ".sh":
            _write_script(paths, "echo_env", 'echo "var=$XPLANE_FOLDER"\n')
        else:
            _write_script(paths, "echo_env", "echo var=%XPLANE_FOLDER%\n")
        result = sp_executor.execute(
            paths, "echo_env",
            env_overrides={"XPLANE_FOLDER": "/some/path"},
        )
        assert "var=/some/path" in result.stdout

    def test_timeout_kills_runaway(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        if EXT == ".sh":
            _write_script(paths, "slow", "sleep 30\n")
        else:
            # Windows: ping with timeout as a sleep substitute.
            _write_script(paths, "slow", "ping -n 30 127.0.0.1 > nul\n")
        result = sp_executor.execute(paths, "slow", timeout_sec=1)
        assert result.exit_code == -1
        assert "timeout" in result.error

    def test_truncates_huge_output(self, tmp_path):
        # Write 5 MiB to stdout — should be clipped to MAX_OUTPUT_BYTES
        # and the truncated flag set.
        paths = sp_data.SlavePaths.under(tmp_path)
        if EXT == ".sh":
            _write_script(paths, "big",
                          "head -c $((5 * 1024 * 1024)) /dev/zero | tr '\\0' 'x'\n")
        else:
            pytest.skip("Windows .bat output-padding not portable enough")
        result = sp_executor.execute(paths, "big")
        assert result.truncated
        assert len(result.stdout) <= sp_executor.MAX_OUTPUT_BYTES

    def test_to_dict_serializable(self, tmp_path):
        # Result must round-trip through JSON for the wire.
        import json
        paths = sp_data.SlavePaths.under(tmp_path)
        _write_script(paths, "echo", "echo ok\n")
        d = sp_executor.execute(paths, "echo").to_dict()
        json.dumps(d)


# ── Streaming execute ────────────────────────────────────────────────────────
class TestExecuteStreaming:
    def test_yields_lines_and_finish(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path)
        if EXT == ".sh":
            _write_script(paths, "lines",
                          "echo line1\necho line2\necho line3\n")
        else:
            _write_script(paths, "lines",
                          "echo line1\necho line2\necho line3\n")

        items = list(sp_executor.execute_streaming(paths, "lines"))

        # Last item must always be StreamFinish.
        assert isinstance(items[-1], sp_executor.StreamFinish)
        # Earlier items are StreamLine — expect three.
        lines = [i for i in items if isinstance(i, sp_executor.StreamLine)]
        assert len(lines) >= 3
        text_blob = "\n".join(line.text for line in lines)
        assert "line1" in text_blob
        assert "line2" in text_blob

    def test_missing_script_yields_finish_only(self, tmp_path):
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        items = list(sp_executor.execute_streaming(paths, "nope"))
        assert len(items) == 1
        assert isinstance(items[0], sp_executor.StreamFinish)
        assert "not found" in items[0].error


# ── Log mirroring (regression: agent.log was empty before this fix) ──────────
class TestLogMirror:
    """The slave's default log level is INFO. Script execution events must
    be visible at INFO so operators can see what ran by tailing agent.log
    on the slave — without having to start the agent with -v."""

    def test_run_summary_logged_at_info(self, tmp_path, caplog):
        paths = sp_data.SlavePaths.under(tmp_path)
        _write_script(paths, "echo", "echo hi\n")
        with caplog.at_level("INFO", logger="simpit_slave.executor"):
            sp_executor.execute(paths, "echo")
        assert any("ran echo" in r.message and "exit=0" in r.message
                   for r in caplog.records), \
            "executor must log a run summary at INFO"

    def test_stdout_mirrored_at_info(self, tmp_path, caplog):
        paths = sp_data.SlavePaths.under(tmp_path)
        _write_script(paths, "echo", "echo distinctive_log_marker\n")
        with caplog.at_level("INFO", logger="simpit_slave.executor"):
            sp_executor.execute(paths, "echo")
        joined = "\n".join(r.message for r in caplog.records)
        assert "distinctive_log_marker" in joined, \
            "script stdout must be mirrored into the agent log at INFO"

    def test_stderr_mirrored_at_info(self, tmp_path, caplog):
        paths = sp_data.SlavePaths.under(tmp_path)
        if EXT == ".sh":
            _write_script(paths, "fail", "echo distinctive_err >&2\nexit 1\n")
        else:
            _write_script(paths, "fail", "echo distinctive_err 1>&2\nexit /b 1\n")
        with caplog.at_level("INFO", logger="simpit_slave.executor"):
            sp_executor.execute(paths, "fail")
        joined = "\n".join(r.message for r in caplog.records)
        assert "distinctive_err" in joined, \
            "script stderr must be mirrored into the agent log at INFO"

    def test_huge_stdout_capped_in_log(self, tmp_path, caplog):
        # The full output still rides home in EXEC_SCRIPT_RESULT — only the
        # *log mirror* is capped, so the slave's log doesn't blow up.
        paths = sp_data.SlavePaths.under(tmp_path)
        if EXT == ".sh":
            big = "a" * (sp_executor.LOG_MIRROR_BYTES * 2)
            _write_script(paths, "huge", f'printf %s "{big}"\n')
        else:
            pytest.skip("Windows .bat output-padding not portable enough")
        with caplog.at_level("INFO", logger="simpit_slave.executor"):
            result = sp_executor.execute(paths, "huge")
        assert result.exit_code == 0
        # Full output preserved on the result envelope...
        assert len(result.stdout) >= sp_executor.LOG_MIRROR_BYTES * 2
        # ...but the log mirror is bounded.
        for r in caplog.records:
            assert len(r.message) < sp_executor.LOG_MIRROR_BYTES * 2


# ── disable_custom_scenery script (POSIX path; bat tested manually) ──────────
class TestDisableCustomSceneryScript:
    """End-to-end smoke for the .sh going through the real executor.
    The .bat is a literal port — same control flow, same exit codes —
    so we exercise the .sh here and rely on Windows manual testing for
    the bat-specific quoting. Each test sets up an XPLANE_FOLDER fixture
    and runs the script via the executor as an agent would."""

    @pytest.fixture
    def xp_folder(self, tmp_path):
        f = tmp_path / "xplane"
        f.mkdir()
        return f

    @pytest.fixture
    def paths(self, tmp_path):
        import shutil
        from pathlib import Path
        p = sp_data.SlavePaths.under(tmp_path / "slave")
        p.ensure()
        src = Path(__file__).resolve().parents[2] / "simpit_control" \
            / "scripts" / "disable_custom_scenery.sh"
        dest = p.cascaded / "disable_custom_scenery.sh"
        shutil.copy(src, dest)
        import os; os.chmod(dest, 0o755)
        return p

    def _run(self, paths, xp_folder):
        return sp_executor.execute(
            paths, "disable_custom_scenery",
            env_overrides={"XPLANE_FOLDER": str(xp_folder)},
        )

    def test_enabled_and_default_present(self, paths, xp_folder):
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        (xp_folder / "Custom Scenery").mkdir()
        (xp_folder / "Custom Scenery" / "marker.txt").write_text("user-stuff")
        (xp_folder / "Custom Scenery DEFAULT").mkdir()
        (xp_folder / "Custom Scenery DEFAULT" / "default_marker.txt").write_text("d")

        result = self._run(paths, xp_folder)
        assert result.exit_code == 0, result.stderr
        # User content preserved as DISABLED
        assert (xp_folder / "Custom Scenery DISABLED" / "marker.txt").read_text() \
            == "user-stuff"
        # DEFAULT promoted to active
        assert (xp_folder / "Custom Scenery" / "default_marker.txt").exists()
        # DEFAULT consumed
        assert not (xp_folder / "Custom Scenery DEFAULT").exists()

    def test_enabled_present_default_missing(self, paths, xp_folder):
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        (xp_folder / "Custom Scenery").mkdir()
        (xp_folder / "Custom Scenery" / "marker.txt").write_text("x")

        result = self._run(paths, xp_folder)
        assert result.exit_code == 0, result.stderr
        assert (xp_folder / "Custom Scenery DISABLED" / "marker.txt").exists()
        # New empty Custom Scenery in place
        assert (xp_folder / "Custom Scenery").is_dir()
        assert list((xp_folder / "Custom Scenery").iterdir()) == []
        assert not (xp_folder / "Custom Scenery DEFAULT").exists()

    def test_enabled_missing_errors(self, paths, xp_folder):
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        result = self._run(paths, xp_folder)
        assert result.exit_code != 0
        assert "not found" in result.stderr.lower() \
            or "cannot disable" in result.stderr.lower()

    def test_disabled_already_exists_refuses(self, paths, xp_folder):
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        (xp_folder / "Custom Scenery").mkdir()
        (xp_folder / "Custom Scenery DISABLED").mkdir()
        result = self._run(paths, xp_folder)
        assert result.exit_code != 0
        assert "already exists" in result.stderr.lower()

    def test_xplane_folder_unset_errors(self, paths, tmp_path):
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        # Run with XPLANE_FOLDER explicitly empty (executor whitelists env)
        result = sp_executor.execute(
            paths, "disable_custom_scenery",
            env_overrides={"XPLANE_FOLDER": ""},
        )
        assert result.exit_code != 0
        assert "not set" in result.stderr.lower()
