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
