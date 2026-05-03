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


# ── enable_custom_scenery script (POSIX path) ────────────────────────────────
class TestEnableCustomSceneryScript:
    """End-to-end smoke for the .sh going through the real executor.
    Symmetric inverse of disable: verifies the user's preserved scenery
    is restored to active and the baseline (if any) is preserved as
    DEFAULT for the next disable cycle."""

    @pytest.fixture
    def xp_folder(self, tmp_path):
        f = tmp_path / "xplane"
        f.mkdir()
        return f

    @pytest.fixture
    def paths(self, tmp_path):
        import shutil, os
        from pathlib import Path
        p = sp_data.SlavePaths.under(tmp_path / "slave")
        p.ensure()
        scripts_root = Path(__file__).resolve().parents[2] / "simpit_control" / "scripts"
        for name in ("enable_custom_scenery.sh", "disable_custom_scenery.sh"):
            dest = p.cascaded / name
            shutil.copy(scripts_root / name, dest)
            os.chmod(dest, 0o755)
        return p

    def _run(self, paths, name, xp_folder):
        return sp_executor.execute(
            paths, name,
            env_overrides={"XPLANE_FOLDER": str(xp_folder)},
        )

    def test_disabled_present_no_current_scenery(self, paths, xp_folder):
        """After disable then a manual delete of empty Custom Scenery —
        DISABLED is the only thing present. Enable should still proceed,
        skipping the baseline-preserve step."""
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        (xp_folder / "Custom Scenery DISABLED").mkdir()
        (xp_folder / "Custom Scenery DISABLED" / "marker.txt").write_text("user")

        result = self._run(paths, "enable_custom_scenery", xp_folder)
        assert result.exit_code == 0, result.stderr
        assert (xp_folder / "Custom Scenery" / "marker.txt").read_text() == "user"
        assert not (xp_folder / "Custom Scenery DISABLED").exists()
        assert not (xp_folder / "Custom Scenery DEFAULT").exists()

    def test_disabled_present_with_baseline_preserved(self, paths, xp_folder):
        """The post-disable state: 'Custom Scenery' (the empty DEFAULT)
        present alongside 'Custom Scenery DISABLED' (user content).
        Enable should preserve the empty/DEFAULT and restore user content."""
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        (xp_folder / "Custom Scenery").mkdir()  # empty (was DEFAULT)
        (xp_folder / "Custom Scenery DISABLED").mkdir()
        (xp_folder / "Custom Scenery DISABLED" / "user.txt").write_text("user-data")

        result = self._run(paths, "enable_custom_scenery", xp_folder)
        assert result.exit_code == 0, result.stderr
        assert (xp_folder / "Custom Scenery" / "user.txt").read_text() == "user-data"
        # Baseline preserved as DEFAULT (was the empty Custom Scenery)
        assert (xp_folder / "Custom Scenery DEFAULT").is_dir()
        assert list((xp_folder / "Custom Scenery DEFAULT").iterdir()) == []
        assert not (xp_folder / "Custom Scenery DISABLED").exists()

    def test_disabled_missing_errors(self, paths, xp_folder):
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        (xp_folder / "Custom Scenery").mkdir()
        result = self._run(paths, "enable_custom_scenery", xp_folder)
        assert result.exit_code != 0
        assert "nothing to re-enable" in result.stderr.lower() \
            or "not found" in result.stderr.lower()

    def test_default_already_exists_refuses(self, paths, xp_folder):
        """If both Custom Scenery and Custom Scenery DEFAULT exist, the
        baseline-preserve step has nowhere to go. Refuse rather than
        silently lose data."""
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        (xp_folder / "Custom Scenery").mkdir()
        (xp_folder / "Custom Scenery DISABLED").mkdir()
        (xp_folder / "Custom Scenery DEFAULT").mkdir()

        result = self._run(paths, "enable_custom_scenery", xp_folder)
        assert result.exit_code != 0
        assert "default" in result.stderr.lower()

    def test_xplane_folder_unset_errors(self, paths):
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        result = sp_executor.execute(
            paths, "enable_custom_scenery",
            env_overrides={"XPLANE_FOLDER": ""},
        )
        assert result.exit_code != 0
        assert "not set" in result.stderr.lower()

    def test_round_trip_preserves_user_scenery(self, paths, xp_folder):
        """The motivating regression: disable → enable → disable should
        leave user content intact. Before this rewrite, enable required
        Custom Scenery to be absent and erroring made the round-trip
        impossible."""
        if EXT != ".sh":
            pytest.skip(".sh-only end-to-end")
        # Initial state: user has real scenery
        (xp_folder / "Custom Scenery").mkdir()
        (xp_folder / "Custom Scenery" / "user_airport.ini").write_text("kbed_v2")

        # disable → enable → disable
        r1 = self._run(paths, "disable_custom_scenery", xp_folder)
        assert r1.exit_code == 0, f"disable #1: {r1.stderr}"
        r2 = self._run(paths, "enable_custom_scenery", xp_folder)
        assert r2.exit_code == 0, f"enable: {r2.stderr}"
        r3 = self._run(paths, "disable_custom_scenery", xp_folder)
        assert r3.exit_code == 0, f"disable #2: {r3.stderr}"

        # User content survived the round-trip
        assert (xp_folder / "Custom Scenery DISABLED" / "user_airport.ini") \
            .read_text() == "kbed_v2"
        # Custom Scenery is the empty/DEFAULT again
        assert (xp_folder / "Custom Scenery").is_dir()
        assert list((xp_folder / "Custom Scenery").iterdir()) == []


# ── backup_xplane / restore_xplane (cross-platform .py) ──────────────────────
class TestBackupXplaneScript:
    """End-to-end via the real executor. backup_xplane.py is .py so it
    runs in-process via runpy on both platforms."""

    @pytest.fixture
    def xp_folder(self, tmp_path):
        """A fake X-Plane install with Aircraft, Output, plugins, plus
        a Custom Scenery subtree we expect to be excluded."""
        f = tmp_path / "xplane"; f.mkdir()
        (f / "Aircraft" / "DA40").mkdir(parents=True)
        (f / "Aircraft" / "DA40" / "DA40.acf").write_text("aircraft v1")
        (f / "Output" / "preferences").mkdir(parents=True)
        (f / "Output" / "preferences" / "X-Plane.prf").write_text("settings v1")
        (f / "Resources" / "plugins").mkdir(parents=True)
        (f / "Resources" / "plugins" / "FlyWithLua.xpl").write_text("plugin v1")
        # The intentionally-excluded big folder
        (f / "Custom Scenery" / "KBED").mkdir(parents=True)
        (f / "Custom Scenery" / "KBED" / "huge.dsf").write_text("X" * 10_000)
        (f / "X-Plane.exe").write_text("v12")
        return f

    @pytest.fixture
    def backup_dir(self, tmp_path):
        f = tmp_path / "backups"; f.mkdir()
        return f

    @pytest.fixture
    def paths(self, tmp_path):
        import shutil
        from pathlib import Path
        p = sp_data.SlavePaths.under(tmp_path / "slave"); p.ensure()
        scripts = Path(__file__).resolve().parents[2] / "simpit_control" / "scripts"
        for name in ("backup_xplane.py", "restore_xplane.py"):
            shutil.copy(scripts / name, p.cascaded / name)
        return p

    @staticmethod
    def _list_archive(path):
        """Return sorted list of member names in a .zip or .tar.gz."""
        import zipfile, tarfile
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                return sorted(n for n in zf.namelist()
                              if not n.endswith("/"))
        with tarfile.open(path, "r:*") as tf:
            return sorted(m.name for m in tf.getmembers() if m.isfile())

    @staticmethod
    def _archives(backup_dir, host_glob="*"):
        out = []
        for ext in (".zip", ".tar.gz"):
            out.extend(backup_dir.glob(f"xplane-{host_glob}-*{ext}"))
        return sorted(out)

    def _run(self, paths, name, env):
        return sp_executor.execute(paths, name, env_overrides=env)

    # ── backup behavior ────────────────────────────────────────────────
    def test_creates_archive_with_hostname_in_name(self, paths, xp_folder, backup_dir):
        import socket
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        r = self._run(paths, "backup_xplane", env)
        assert r.exit_code == 0, r.stderr
        archives = self._archives(backup_dir)
        assert len(archives) == 1
        # Hostname is embedded; same gethostname() is used inside the script
        assert socket.gethostname() in archives[0].name \
            or "unknown" in archives[0].name  # hostnames with bad chars

    def test_excludes_custom_scenery(self, paths, xp_folder, backup_dir):
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        r = self._run(paths, "backup_xplane", env)
        assert r.exit_code == 0
        members = self._list_archive(self._archives(backup_dir)[0])
        # Custom Scenery must NOT appear at any depth in the archive
        assert not any("Custom Scenery" in m for m in members), \
            f"Custom Scenery should be excluded; found: " \
            f"{[m for m in members if 'Custom Scenery' in m]}"
        # Other content WAS captured
        assert any("DA40.acf" in m for m in members)
        assert any("X-Plane.prf" in m for m in members)

    def test_filename_has_iso_date_stamp(self, paths, xp_folder, backup_dir):
        import re
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        self._run(paths, "backup_xplane", env)
        archives = self._archives(backup_dir)
        # YYYY-MM-DD_HHMMSS somewhere in the name
        assert re.search(r"\d{4}-\d{2}-\d{2}_\d{6}", archives[0].name), \
            f"date stamp missing from {archives[0].name}"

    # ── prune behavior ─────────────────────────────────────────────────
    def test_prune_keeps_default_two(self, paths, xp_folder, backup_dir):
        import time
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        for _ in range(3):
            r = self._run(paths, "backup_xplane", env)
            assert r.exit_code == 0
            time.sleep(1.05)  # ensure distinct mtime / filename stamp
        archives = self._archives(backup_dir)
        assert len(archives) == 2, \
            f"default keep=2 should have pruned to 2, got {len(archives)}"

    def test_prune_keep_overridable(self, paths, xp_folder, backup_dir):
        import time
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir),
               "BACKUP_KEEP": "4"}
        for _ in range(5):
            r = self._run(paths, "backup_xplane", env)
            assert r.exit_code == 0
            time.sleep(1.05)
        archives = self._archives(backup_dir)
        assert len(archives) == 4, \
            f"BACKUP_KEEP=4 should have pruned to 4, got {len(archives)}"

    def test_prune_does_not_touch_other_hosts_archives(
        self, paths, xp_folder, backup_dir
    ):
        """Critical isolation guarantee: when several slaves share one
        BACKUP_FOLDER, each slave's prune step must only ever delete
        files matching its own hostname pattern."""
        import time, os, shutil
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        # Seed two backups for THIS host
        self._run(paths, "backup_xplane", env)
        time.sleep(1.05)
        self._run(paths, "backup_xplane", env)
        # Drop two archives that look like they came from a different
        # slave. They use a hostname that this script cannot produce.
        own = self._archives(backup_dir)[0]
        # Path.suffix on foo.tar.gz returns '.gz' — use the full tail
        ext = ".tar.gz" if own.name.endswith(".tar.gz") else own.suffix
        other_old = backup_dir / f"xplane-CENTERLEFT-2025-01-01_120000{ext}"
        other_new = backup_dir / f"xplane-CENTERLEFT-2025-12-31_235959{ext}"
        shutil.copy(own, other_old)
        shutil.copy(own, other_new)
        # Make them genuinely older than this host's
        old_t = time.time() - 86400 * 30
        os.utime(other_old, (old_t, old_t))
        os.utime(other_new, (old_t + 1, old_t + 1))

        # Run another backup → triggers prune of THIS host only
        time.sleep(1.05)
        self._run(paths, "backup_xplane", env)

        remaining = {p.name for p in self._archives(backup_dir)}
        assert other_old.name in remaining, \
            "prune wrongly deleted another slave's older backup"
        assert other_new.name in remaining, \
            "prune wrongly deleted another slave's newer backup"

    # ── error handling ─────────────────────────────────────────────────
    def test_missing_xplane_folder_errors(self, paths, backup_dir):
        env = {"XPLANE_FOLDER": "", "BACKUP_FOLDER": str(backup_dir)}
        r = self._run(paths, "backup_xplane", env)
        assert r.exit_code != 0
        assert "XPLANE_FOLDER" in r.stderr

    def test_missing_backup_folder_errors(self, paths, xp_folder):
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": ""}
        r = self._run(paths, "backup_xplane", env)
        assert r.exit_code != 0
        assert "BACKUP_FOLDER" in r.stderr

    def test_bad_keep_value_errors(self, paths, xp_folder, backup_dir):
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir),
               "BACKUP_KEEP": "not_a_number"}
        r = self._run(paths, "backup_xplane", env)
        assert r.exit_code != 0
        assert "BACKUP_KEEP" in r.stderr

    def test_creates_backup_folder_if_missing(self, paths, xp_folder, tmp_path):
        new_dst = tmp_path / "new" / "nested" / "backups"
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(new_dst)}
        r = self._run(paths, "backup_xplane", env)
        assert r.exit_code == 0, r.stderr
        assert new_dst.is_dir()
        assert len(self._archives(new_dst)) == 1


class TestRestoreXplaneScript:
    """Restore is the symmetric inverse: extract + overwrite, leave
    Custom Scenery alone, leave non-archived files alone."""

    # Re-use the same fixtures as backup tests
    xp_folder = TestBackupXplaneScript.xp_folder
    backup_dir = TestBackupXplaneScript.backup_dir
    paths = TestBackupXplaneScript.paths

    def _run(self, paths, name, env):
        return sp_executor.execute(paths, name, env_overrides=env)

    def _seed_backup(self, paths, xp_folder, backup_dir):
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        r = self._run(paths, "backup_xplane", env)
        assert r.exit_code == 0, r.stderr
        return env

    def test_restore_overwrites_archived_files(self, paths, xp_folder, backup_dir):
        env = self._seed_backup(paths, xp_folder, backup_dir)
        # Modify after backup
        (xp_folder / "Aircraft" / "DA40" / "DA40.acf").write_text("CORRUPTED")
        (xp_folder / "Output" / "preferences" / "X-Plane.prf").write_text("CORRUPTED")
        r = self._run(paths, "restore_xplane", env)
        assert r.exit_code == 0, r.stderr
        # Originals restored
        assert (xp_folder / "Aircraft" / "DA40" / "DA40.acf") \
            .read_text() == "aircraft v1"
        assert (xp_folder / "Output" / "preferences" / "X-Plane.prf") \
            .read_text() == "settings v1"

    def test_restore_leaves_non_archived_files_alone(
        self, paths, xp_folder, backup_dir
    ):
        env = self._seed_backup(paths, xp_folder, backup_dir)
        # Add a new file post-backup. Restore is "overwrite," not "wipe."
        new_file = xp_folder / "Output" / "user_added_post_backup.txt"
        new_file.write_text("must survive")
        r = self._run(paths, "restore_xplane", env)
        assert r.exit_code == 0
        assert new_file.read_text() == "must survive"

    def test_restore_leaves_custom_scenery_alone(
        self, paths, xp_folder, backup_dir
    ):
        env = self._seed_backup(paths, xp_folder, backup_dir)
        # Modify Custom Scenery — the live install
        marker = xp_folder / "Custom Scenery" / "KBED" / "live_edit.txt"
        marker.write_text("scenery edit post-backup")
        r = self._run(paths, "restore_xplane", env)
        assert r.exit_code == 0
        assert marker.read_text() == "scenery edit post-backup"
        # Original scenery file also untouched
        assert (xp_folder / "Custom Scenery" / "KBED" / "huge.dsf").exists()

    def test_restore_picks_newest_when_multiple(
        self, paths, xp_folder, backup_dir
    ):
        import time
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        # Backup #1 with content "v1"
        self._run(paths, "backup_xplane", env)
        time.sleep(1.05)
        # Modify, then backup #2 with new content
        (xp_folder / "Aircraft" / "DA40" / "DA40.acf").write_text("aircraft V2")
        self._run(paths, "backup_xplane", env)
        # Now scribble — restore should pull V2 (the newer one)
        (xp_folder / "Aircraft" / "DA40" / "DA40.acf").write_text("scribble")
        r = self._run(paths, "restore_xplane", env)
        assert r.exit_code == 0
        assert (xp_folder / "Aircraft" / "DA40" / "DA40.acf") \
            .read_text() == "aircraft V2", "should restore newest backup"

    def test_restore_explicit_backup_file(self, paths, xp_folder, backup_dir):
        import time
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        # Seed two backups
        self._run(paths, "backup_xplane", env)
        time.sleep(1.05)
        # Find the first archive's name before we make a second
        first_name = sorted(p.name for p in TestBackupXplaneScript._archives(backup_dir))[0]
        (xp_folder / "Aircraft" / "DA40" / "DA40.acf").write_text("aircraft V2")
        self._run(paths, "backup_xplane", env)
        # Scribble, then ask for the OLDER one explicitly
        (xp_folder / "Aircraft" / "DA40" / "DA40.acf").write_text("scribble")
        env_with_pick = dict(env, BACKUP_FILE=first_name)
        r = self._run(paths, "restore_xplane", env_with_pick)
        assert r.exit_code == 0
        # The first backup contained "aircraft v1"
        assert (xp_folder / "Aircraft" / "DA40" / "DA40.acf") \
            .read_text() == "aircraft v1"

    def test_restore_no_archives_errors(self, paths, xp_folder, backup_dir):
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        r = self._run(paths, "restore_xplane", env)
        assert r.exit_code != 0
        assert "no archives" in r.stderr.lower() or "not found" in r.stderr.lower()

    def test_restore_rejects_path_traversal_in_explicit_filename(
        self, paths, xp_folder, backup_dir
    ):
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir),
               "BACKUP_FILE": "../escape.zip"}
        r = self._run(paths, "restore_xplane", env)
        assert r.exit_code != 0

    def test_restore_unsafe_archive_members_skipped(
        self, paths, xp_folder, backup_dir
    ):
        """Hand-roll an archive containing ../escaping members — the
        restore script should skip them and still succeed on safe ones."""
        import tarfile, io, socket
        # Build a tar in BACKUP_FOLDER matching the host's prefix
        host = socket.gethostname() or "unknown"
        # sanitize same way the script does
        bad = '<>:"/\\|?*\x00'
        host = "".join("_" if c in bad else c for c in host).strip(". ") or "unknown"
        tar_path = backup_dir / f"xplane-{host}-2026-05-03_120000.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            # Safe member
            data = b"safe content"
            info = tarfile.TarInfo(name="Output/safe.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            # Unsafe member — should be skipped
            info2 = tarfile.TarInfo(name="../../../escape.txt")
            info2.size = len(data)
            tf.addfile(info2, io.BytesIO(data))
        env = {"XPLANE_FOLDER": str(xp_folder), "BACKUP_FOLDER": str(backup_dir)}
        r = self._run(paths, "restore_xplane", env)
        assert r.exit_code == 0
        # The safe one landed where expected
        assert (xp_folder / "Output" / "safe.txt").read_text() == "safe content"
        # The unsafe one didn't escape
        escape = backup_dir.parent.parent / "escape.txt"
        assert not escape.exists(), "path-traversal member should have been skipped"


# ── Elevated execution dispatch ──────────────────────────────────────────────
class TestElevatedDispatch:
    """The executor's needs_admin path. The actual UAC handoff requires
    Windows + an interactive desktop, so we can't exercise it end-to-end
    in CI. We CAN verify the dispatch decision and that the path is a
    no-op when admin isn't needed or the slave is already elevated."""

    def test_needs_admin_false_runs_normally(self, tmp_path, monkeypatch):
        """Default path: no admin, runs in-process, no powershell call."""
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        script = paths.cascaded / "echo.py"
        script.write_text("print('ran')\n")

        called = {"powershell": False}
        real_run = __import__("subprocess").run
        def fake_run(argv, *a, **kw):
            if argv and argv[0] == "powershell":
                called["powershell"] = True
            return real_run(argv, *a, **kw)
        monkeypatch.setattr("subprocess.run", fake_run)

        result = sp_executor.execute(paths, "echo", needs_admin=False)
        assert result.exit_code == 0
        assert "ran" in result.stdout
        assert called["powershell"] is False, \
            "needs_admin=False must not invoke powershell"

    def test_needs_admin_skipped_when_already_elevated(
        self, tmp_path, monkeypatch
    ):
        """If the agent is already running elevated, we bypass the
        powershell handoff and run in-process — that's both faster and
        keeps stdout/stderr in the normal capture path."""
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        script = paths.cascaded / "echo.py"
        script.write_text("print('elevated already')\n")
        # Pretend we're admin
        monkeypatch.setattr("simpit_common.platform.is_admin",
                            lambda: True)
        called = {"powershell": False}
        real_run = __import__("subprocess").run
        def fake_run(argv, *a, **kw):
            if argv and argv[0] == "powershell":
                called["powershell"] = True
            return real_run(argv, *a, **kw)
        monkeypatch.setattr("subprocess.run", fake_run)

        result = sp_executor.execute(paths, "echo", needs_admin=True)
        assert result.exit_code == 0
        assert "elevated already" in result.stdout
        assert called["powershell"] is False, \
            "already-elevated must not re-elevate"

    def test_needs_admin_on_posix_runs_normally(self, tmp_path, monkeypatch):
        """On POSIX the elevated dispatch is Windows-only. needs_admin
        falls through to normal execution; the script's own
        permission check (e.g. 'cannot write to /etc/hosts') is what
        would fail. Tests on a POSIX runner exercise this path."""
        if EXT != ".sh":
            pytest.skip("POSIX-only check")
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        script = paths.cascaded / "echo.sh"
        script.write_text("#!/bin/sh\necho posix\n")
        import os; os.chmod(script, 0o755)
        result = sp_executor.execute(paths, "echo", needs_admin=True)
        assert result.exit_code == 0
        assert "posix" in result.stdout

    def test_needs_admin_passes_through_exec_signature(
        self, tmp_path, monkeypatch
    ):
        """Default value for needs_admin is False (backwards compat)."""
        paths = sp_data.SlavePaths.under(tmp_path); paths.ensure()
        script = paths.cascaded / "echo.py"
        script.write_text("print('default')\n")
        # No needs_admin kwarg at all — old call sites must still work
        result = sp_executor.execute(paths, "echo")
        assert result.exit_code == 0


# ── --run-script re-entry mode ───────────────────────────────────────────────
class TestRunScriptMode:
    """The hidden CLI mode the elevated child uses to runpy a target."""

    def test_runs_script_with_env_file(self, tmp_path):
        import json, subprocess, sys, os

        script = tmp_path / "t.py"
        script.write_text(
            "import os, sys\n"
            "print('VAL=' + os.environ.get('TEST_VAR', ''))\n"
            "sys.exit(7)\n"
        )
        env_file = tmp_path / "env.json"
        env_file.write_text(json.dumps({"TEST_VAR": "hello world"}))

        result = subprocess.run(
            [sys.executable, "-m", "simpit_slave",
             "--run-script", str(script),
             "--env-file", str(env_file)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 7, \
            f"script's exit code should propagate, got {result.returncode}"
        assert "VAL=hello world" in result.stdout

    def test_runs_script_without_env_file(self, tmp_path):
        """env-file is optional. Without it, the script runs with the
        current process's environment."""
        import subprocess, sys

        script = tmp_path / "t.py"
        script.write_text("print('no env'); import sys; sys.exit(0)\n")
        result = subprocess.run(
            [sys.executable, "-m", "simpit_slave",
             "--run-script", str(script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "no env" in result.stdout

    def test_script_exception_returns_nonzero(self, tmp_path):
        """An uncaught exception in the script should exit non-zero
        with the exception text in stderr — not crash the re-entry."""
        import subprocess, sys

        script = tmp_path / "t.py"
        script.write_text("raise RuntimeError('boom')\n")
        result = subprocess.run(
            [sys.executable, "-m", "simpit_slave",
             "--run-script", str(script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        assert "boom" in result.stderr

    def test_missing_env_file_errors_cleanly(self, tmp_path):
        import subprocess, sys
        script = tmp_path / "t.py"
        script.write_text("print('ok')\n")
        result = subprocess.run(
            [sys.executable, "-m", "simpit_slave",
             "--run-script", str(script),
             "--env-file", str(tmp_path / "does-not-exist.json")],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        assert "env file" in result.stderr.lower()

    def test_invalid_env_json_errors_cleanly(self, tmp_path):
        import subprocess, sys
        script = tmp_path / "t.py"
        script.write_text("print('ok')\n")
        bad = tmp_path / "bad.json"
        bad.write_text("[not, an, object]")
        result = subprocess.run(
            [sys.executable, "-m", "simpit_slave",
             "--run-script", str(script),
             "--env-file", str(bad)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0

    def test_run_script_does_not_start_agent(self, tmp_path):
        """Critical: --run-script must short-circuit before the agent
        loop tries to bind ports. Otherwise the elevated child would
        try to grab the same UDP/TCP ports the parent agent owns."""
        import subprocess, sys

        script = tmp_path / "fast.py"
        script.write_text("import sys; sys.exit(0)\n")
        # If the agent loop ran, this would either bind ports (bad) or
        # block waiting for SIGINT (worse). Either way the timeout
        # would expire. A successful 0-exit means the agent didn't run.
        result = subprocess.run(
            [sys.executable, "-m", "simpit_slave",
             "--run-script", str(script)],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0, \
            "--run-script should short-circuit before agent startup"
