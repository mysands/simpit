"""Tests for simpit_common.platform — OS abstraction layer."""
import os
import sys
from pathlib import Path

import pytest

from simpit_common import platform as sp


class TestCurrentOS:
    def test_returns_known_value(self):
        assert sp.current_os() in (sp.OS.WINDOWS, sp.OS.LINUX, sp.OS.MACOS)


class TestScriptExtension:
    def test_returns_correct_for_platform(self):
        ext = sp.script_extension()
        if sys.platform == "win32":
            assert ext == ".bat"
        else:
            assert ext == ".sh"

    def test_script_filename_appends_extension(self):
        assert sp.script_filename("foo").endswith(sp.script_extension())

    def test_script_filename_respects_explicit_extension(self):
        # If the user already supplied an extension, we must not double-append.
        assert sp.script_filename("custom.cmd") == "custom.cmd"


class TestHostsFilePath:
    def test_returns_path_object(self):
        assert isinstance(sp.hosts_file_path(), Path)

    def test_correct_path_per_os(self):
        path = sp.hosts_file_path()
        if sys.platform == "win32":
            assert "drivers" in str(path) and "etc" in str(path)
        else:
            assert str(path) == "/etc/hosts"


class TestProcessRunning:
    def test_known_running_process(self):
        # The test process itself: psutil.Process(os.getpid()).name() reliably
        # returns *something*. Pick our own process name and check.
        import psutil
        my_name = psutil.Process(os.getpid()).name()
        assert sp.process_running(my_name)

    def test_definitely_not_running(self):
        assert not sp.process_running("definitely_not_a_real_process_xyz_12345")

    def test_case_insensitive_by_default(self):
        import psutil
        my_name = psutil.Process(os.getpid()).name()
        assert sp.process_running(my_name.upper())


class TestBuildScriptInvocation:
    def test_returns_argv_and_cwd(self, tmp_path):
        script = tmp_path / ("test" + sp.script_extension())
        script.write_text("echo hi\n" if sys.platform != "win32"
                          else "@echo off\necho hi\n")
        cmd = sp.build_script_invocation(script)
        assert isinstance(cmd.argv, list)
        assert cmd.argv  # non-empty
        assert cmd.cwd == script.parent

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_posix_uses_sh_for_non_executable(self, tmp_path):
        script = tmp_path / "test.sh"
        script.write_text("echo hi\n")
        # Ensure not executable
        script.chmod(0o644)
        cmd = sp.build_script_invocation(script)
        assert cmd.argv[0] == "sh"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_posix_runs_directly_if_executable(self, tmp_path):
        script = tmp_path / "test.sh"
        script.write_text("#!/bin/sh\necho hi\n")
        script.chmod(0o755)
        cmd = sp.build_script_invocation(script)
        assert cmd.argv[0] == str(script)


class TestIsAdmin:
    def test_returns_bool(self):
        # Just verify it runs and returns a bool — actual value depends on
        # how the test runner is invoked.
        assert isinstance(sp.is_admin(), bool)


class TestAppDataDir:
    def test_returns_path_with_app_name(self):
        path = sp.app_data_dir("simpit")
        assert path.name == "simpit"

    def test_custom_app_name(self):
        assert sp.app_data_dir("myapp").name == "myapp"


class TestCanOpenSocket:
    def test_open_to_unreachable_returns_false(self):
        # Reserved TEST-NET-1 address; should never be reachable.
        assert not sp.can_open_socket("192.0.2.1", 49101, timeout=0.5)

    def test_open_to_listening_returns_true(self):
        # Bind a temp listener and verify we can connect to it.
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            assert sp.can_open_socket("127.0.0.1", port, timeout=1.0)
        finally:
            sock.close()


class TestPythonExecutable:
    def test_returns_string(self):
        assert isinstance(sp.python_executable(), str)
        assert sp.python_executable()  # non-empty
