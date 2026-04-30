"""
simpit_common.platform
======================
OS abstraction layer.

Goals:
* Use only stdlib + ``psutil`` so the codebase stays installable anywhere
  Python runs.
* Centralize every "if Windows else" branch here so the rest of the app
  is platform-clean and easy to read.
* Provide read-only inspection helpers (process running? folder exists?)
  that the slave's probe engine builds on.

What this module deliberately does NOT do:
* Touch the Windows registry. Anything in the original codebase that
  used ``reg add`` lives only inside individual user scripts now.
* Use ``win32api`` / ``pywin32``. Pure stdlib + ``psutil`` is enough for
  everything we need.
* Decide *what* to launch — only *how* to launch on this OS.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil


# ── OS identification ────────────────────────────────────────────────────────
class OS:
    WINDOWS = "windows"
    LINUX   = "linux"
    MACOS   = "macos"


def current_os() -> str:
    """Return one of OS.WINDOWS / OS.LINUX / OS.MACOS.

    Falls back to OS.LINUX for anything Unix-y we don't recognize so that
    POSIX-flavoured behaviour (script ext, paths) is the default. We don't
    raise on unknown platforms — this code should run on a Raspberry Pi
    or BSD even if we haven't explicitly tested there.
    """
    if sys.platform == "win32":
        return OS.WINDOWS
    if sys.platform == "darwin":
        return OS.MACOS
    return OS.LINUX


# ── Script extensions ────────────────────────────────────────────────────────
def script_extension() -> str:
    """Return ``.bat`` on Windows, ``.sh`` elsewhere."""
    return ".bat" if current_os() == OS.WINDOWS else ".sh"


def script_filename(name: str) -> str:
    """Append the right extension to a base script name.

    Examples
    --------
    >>> script_filename("launch_xplane")  # on Linux
    'launch_xplane.sh'
    """
    if "." in name:  # caller already supplied an extension; respect it
        return name
    return name + script_extension()


# ── Hosts file ───────────────────────────────────────────────────────────────
def hosts_file_path() -> Path:
    """Location of the system hosts file on this OS."""
    if current_os() == OS.WINDOWS:
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        return Path(sysroot) / "System32" / "drivers" / "etc" / "hosts"
    # POSIX (Linux, macOS, BSD): always /etc/hosts
    return Path("/etc/hosts")


# ── Process detection (probe primitive) ──────────────────────────────────────
def process_running(name: str, *, case_sensitive: bool = False) -> bool:
    """True if any running process's name matches `name`.

    Cross-platform via ``psutil.process_iter``. On Windows the comparison
    is case-insensitive by default to match how users think about
    ``X-Plane.exe`` vs ``x-plane.exe``. ``psutil`` already trims to the
    base name (no path), so callers pass e.g. ``"X-Plane.exe"`` or just
    ``"x-plane"`` — we'll match either form on Windows.
    """
    target = name if case_sensitive else name.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            pname = proc.info["name"] or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not case_sensitive:
            pname = pname.lower()
        if pname == target:
            return True
        # Be lenient: also match when caller gave a name without extension
        if not case_sensitive and current_os() == OS.WINDOWS:
            if pname.rsplit(".", 1)[0] == target:
                return True
    return False


# ── Script invocation ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ScriptCommand:
    """Resolved invocation for a script: argv + working directory.

    Yielded by :func:`build_script_invocation` so callers don't have to
    juggle subprocess arguments themselves.
    """
    argv: list[str]
    cwd:  Path


def build_script_invocation(script_path: Path,
                            extra_args: list[str] | None = None) -> ScriptCommand:
    """How to invoke `script_path` as a subprocess on this OS.

    Windows: ``cmd.exe /c "<path>" <args...>``.  We use ``cmd.exe`` rather
    than letting Python's shell=True handling pick it because that varies
    between Python versions and we want predictable behaviour.

    POSIX: if the script has the executable bit, run it directly; else
    fall back to ``sh "<path>"``. Direct execution lets shebang lines
    (``#!/usr/bin/env bash``) work as users expect.

    Note: ``shell=False`` is always used by callers — quoting/escaping is
    the OS's responsibility once we hand it argv as a list.
    """
    extra_args = list(extra_args or [])
    if current_os() == OS.WINDOWS:
        if script_path.suffix.lower() == ".py":
            # In a PyInstaller onefile bundle, sys.executable is the .exe
            # itself, not the Python interpreter — can't use it to run .py
            # scripts as subprocesses. Use cmd.exe with the bundled Python
            # if available, otherwise fall back to cmd.exe (won't work for
            # .py but gives a clear error). The executor handles .py scripts
            # in-process via runpy to avoid this entirely.
            argv = ["cmd.exe", "/c", str(script_path), *extra_args]
        else:
            argv = ["cmd.exe", "/c", str(script_path), *extra_args]
    else:
        if script_path.suffix.lower() == ".py":
            argv = [sys.executable, str(script_path), *extra_args]
        elif os.access(script_path, os.X_OK):
            argv = [str(script_path), *extra_args]
        else:
            argv = ["sh", str(script_path), *extra_args]
    return ScriptCommand(argv=argv, cwd=script_path.parent)


# ── Privilege detection (probe primitive + diagnostic) ───────────────────────
def is_admin() -> bool:
    """True if the current process is running with elevated privileges.

    Windows: uses ``ctypes.windll.shell32.IsUserAnAdmin``.
    POSIX:   true iff effective uid is 0 (root).

    Failures are treated as 'not admin' rather than raising — callers use
    this to *gate* operations, so a False-on-error default is the safe
    answer.
    """
    if current_os() == OS.WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return False


# ── App data directory ──────────────────────────────────────────────────────
def app_data_dir(app: str = "simpit") -> Path:
    """Best-practice writable config directory for the named app.

    POSIX:   $XDG_CONFIG_HOME/<app>  or  ~/.config/<app>
    Windows: %APPDATA%/<app>
    macOS:   ~/Library/Application Support/<app>

    Used for storing simpit.key, slaves.json, batfiles.json on Control
    and simpit.key + cascaded/ on slave. Never store anything here that
    a typical user would want to back up by browsing the filesystem;
    that's what export/import is for.
    """
    o = current_os()
    if o == OS.WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif o == OS.MACOS:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME",
                                   Path.home() / ".config"))
    return base / app


# ── Reachability (used by Control's poller) ──────────────────────────────────
def can_open_socket(host: str, port: int, timeout: float = 1.0) -> bool:
    """True iff a TCP connect to (host, port) succeeds within `timeout`.

    We use TCP-connect rather than ICMP ping because:
    * ICMP requires raw sockets / privilege on most systems.
    * What we actually care about is "is the agent listening?", not "is
      the box pingable?" — a slave whose agent crashed would still ping.

    The slave agent's TCP listener (port 49101 by default) is the right
    target. UDP-only hosts can't be reachability-tested cheaply, which is
    fine: the poller queues a STATUS over UDP and treats no-reply as
    offline after a short wait.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# ── Convenience: which `python` are we? ──────────────────────────────────────
def python_executable() -> str:
    """Path to the currently running Python interpreter.

    Used when constructing self-launching commands (e.g. installing the
    slave as a systemd service) so we don't accidentally pick a different
    interpreter from PATH.
    """
    return sys.executable or shutil.which("python3") or "python3"
