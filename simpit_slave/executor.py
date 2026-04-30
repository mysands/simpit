"""
simpit_slave.executor
=====================
Runs scripts on the slave machine.

Two execution modes:

* **Buffered** (default): wait for the script to finish, return full
  stdout + stderr + exit code in one shot. Used for short-lived scripts
  where streaming UX adds no value and the calling code can just block.
* **Streaming**: yield output lines as they come, with a final exit
  status. Used by the TCP handler for long-running scripts so Control's
  log panel updates live rather than going silent for minutes.

Security boundaries enforced here:

1. Script name must resolve via :func:`simpit_slave.data.find_script`,
   which already blocks path traversal. This module never accepts an
   absolute path or relative path with separators — only base names.
2. Subprocess always invoked with ``shell=False``. The script content
   itself can do whatever it wants (it's running as the agent user) but
   we don't add an extra layer of shell-string interpretation that
   could be exploited via crafted script names.
3. Environment is built from a whitelist + caller-provided overrides.
   We do NOT just inherit ``os.environ`` because the slave service's
   environment is unrelated to what the caller meant.

Everything is cross-platform via :mod:`simpit_common.platform`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import Iterator

from simpit_common import platform as sp_platform

from . import data as sp_data

# Hard cap on how long a buffered execution will wait before forcing
# termination. The streaming variant has no such cap because output
# flowing through is itself a liveness signal for the caller.
DEFAULT_TIMEOUT_SEC = 300

# Hard cap on captured output size (bytes) for the buffered path. Prevents
# a runaway script from filling memory while we wait. 4 MiB is generous
# for any sane bat/sh — anything larger is a bug.
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


# ── Result types ─────────────────────────────────────────────────────────────
@dataclass
class ExecResult:
    """Buffered-mode result."""
    script_name: str
    found:       bool
    exit_code:   int          # -1 if not found / timeout / spawn error
    stdout:      str          # may be truncated to MAX_OUTPUT_BYTES
    stderr:      str          # ditto
    truncated:   bool         # true if either stream was clipped
    duration_ms: int
    error:       str = ""     # populated when spawn/timeout failed

    def to_dict(self) -> dict:
        """Serializable form for inclusion in EXEC_SCRIPT_RESULT body."""
        return {
            "script_name": self.script_name,
            "found":       self.found,
            "exit_code":   self.exit_code,
            "stdout":      self.stdout,
            "stderr":      self.stderr,
            "truncated":   self.truncated,
            "duration_ms": self.duration_ms,
            "error":       self.error,
        }


# ── Environment construction ─────────────────────────────────────────────────
def _build_env(overrides: dict[str, str] | None) -> dict[str, str]:
    """Construct the environment passed to a script.

    We pass through a small whitelist of host vars (PATH, system root,
    user info) plus whatever Control supplied in its EXEC_SCRIPT body.
    Caller-supplied values override host values for the same key, which
    is what users want — Control says "XPLANE_FOLDER=...", that wins.

    Whitelisting (vs. inheriting everything) means the slave's service
    environment doesn't leak into user scripts. If a script needs a host
    variable that isn't on this list, the user should pass it via
    Control's overrides explicitly so the dependency is documented.
    """
    keep = {
        "PATH", "PATHEXT", "SystemRoot", "SystemDrive", "windir",
        "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ",
        "ProgramFiles", "ProgramFiles(x86)", "ComSpec",
    }
    env = {k: v for k, v in os.environ.items() if k in keep}
    if overrides:
        env.update({str(k): str(v) for k, v in overrides.items()})
    return env


# ── Buffered execution ───────────────────────────────────────────────────────
def _execute_python_inprocess(
    script_path, env: dict, env_overrides: dict,
    script_name: str, started: float,
) -> "ExecResult":
    """Run a .py script in-process using runpy.run_path.

    Captures stdout/stderr and restores them after. Sets os.environ to
    the script's env for the duration. This avoids depending on
    sys.executable which is the .exe itself in a PyInstaller bundle.
    """
    import io
    import runpy

    old_env = os.environ.copy()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    exit_code = 0
    error = ""
    try:
        os.environ.clear()
        os.environ.update(env)
        sys.stdout = buf_out
        sys.stderr = buf_err
        try:
            runpy.run_path(str(script_path), run_name="__main__")
        except SystemExit as e:
            exit_code = int(e.code) if e.code is not None else 0
        except Exception as e:
            buf_err.write(f"ERROR: {e}\n")
            exit_code = 1
            error = str(e)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        os.environ.clear()
        os.environ.update(old_env)

    stdout = buf_out.getvalue()
    stderr = buf_err.getvalue()
    log.debug("executor (inprocess): exit=%d stdout=%r stderr=%r",
              exit_code, stdout[:200], stderr[:200])
    return ExecResult(
        script_name=script_name, found=True,
        exit_code=exit_code,
        stdout=stdout, stderr=stderr, truncated=False,
        duration_ms=int((time.monotonic() - started) * 1000),
        error=error,
    )


def execute(
    paths: sp_data.SlavePaths,
    script_name: str,
    env_overrides: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> ExecResult:
    """Run a script to completion and return everything captured.

    Errors that prevent the script from running at all (not found, spawn
    failure, timeout) are reported via ``found=False`` or
    ``exit_code=-1`` plus the ``error`` field, so the caller doesn't
    need to distinguish exception flavours — just inspect the result.
    """
    started = time.monotonic()

    script_path = sp_data.find_script(paths, script_name)
    if script_path is None:
        log.debug("executor: script not found: %s", script_name)
        return ExecResult(
            script_name=script_name, found=False, exit_code=-1,
            stdout="", stderr="", truncated=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            error="script not found",
        )

    cmd = sp_platform.build_script_invocation(script_path, extra_args)
    env = _build_env(env_overrides)
    log.debug("executor: running %s argv=%s env_keys=%s",
              script_path, cmd.argv, list(env.keys()))

    # .py scripts are run in-process via runpy so we don't depend on
    # sys.executable (which is the .exe itself in a PyInstaller bundle).
    # stdout/stderr are captured by temporarily redirecting sys.stdout/stderr.
    if script_path.suffix.lower() == ".py":
        return _execute_python_inprocess(
            script_path, env, env_overrides, script_name, started)

    try:
        proc = subprocess.Popen(
            cmd.argv, cwd=str(cmd.cwd), env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, shell=False,
            # CREATE_NO_WINDOW: no console flash on Windows.
            # CREATE_NEW_PROCESS_GROUP: prevents grandchild processes
            # (e.g. PowerShell spawning schtasks) from inheriting our
            # pipes and causing a deadlock on communicate().
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                          getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as e:
        return ExecResult(
            script_name=script_name, found=True, exit_code=-1,
            stdout="", stderr="", truncated=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"spawn failed: {e}",
        )

    truncated = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        return ExecResult(
            script_name=script_name, found=True, exit_code=-1,
            stdout=(stdout or "")[:MAX_OUTPUT_BYTES],
            stderr=(stderr or "")[:MAX_OUTPUT_BYTES],
            truncated=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"timeout after {timeout_sec}s",
        )

    stdout = stdout or ""
    stderr = stderr or ""
    if len(stdout) > MAX_OUTPUT_BYTES:
        stdout = stdout[:MAX_OUTPUT_BYTES]
        truncated = True
    if len(stderr) > MAX_OUTPUT_BYTES:
        stderr = stderr[:MAX_OUTPUT_BYTES]
        truncated = True

    rc = int(proc.returncode if proc.returncode is not None else -1)
    log.debug("executor: exit=%d stdout=%r stderr=%r", rc,
              stdout[:200], stderr[:200])
    return ExecResult(
        script_name=script_name, found=True,
        exit_code=rc,
        stdout=stdout, stderr=stderr, truncated=truncated,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


# ── Streaming execution ──────────────────────────────────────────────────────
@dataclass
class StreamLine:
    """One emission from streaming execution."""
    stream: str            # 'stdout' or 'stderr'
    text:   str            # one line, no trailing newline


@dataclass
class StreamFinish:
    """Final emission from streaming execution."""
    exit_code: int
    duration_ms: int
    error: str = ""


def execute_streaming(
    paths: sp_data.SlavePaths,
    script_name: str,
    env_overrides: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
    timeout_sec: int | None = None,
) -> Iterator[StreamLine | StreamFinish]:
    """Yield ``StreamLine`` per output line then a final ``StreamFinish``.

    Used by the TCP EXEC_SCRIPT handler when Control wants live updates.
    Reads stdout and stderr concurrently via two helper threads; otherwise
    a chatty stderr-only script would silently buffer waiting for stdout
    EOF (or vice versa).

    Memory is bounded by the line — no full output is retained.
    """
    started = time.monotonic()

    script_path = sp_data.find_script(paths, script_name)
    if script_path is None:
        yield StreamFinish(exit_code=-1, duration_ms=0,
                           error="script not found")
        return

    cmd = sp_platform.build_script_invocation(script_path, extra_args)
    env = _build_env(env_overrides)

    try:
        proc = subprocess.Popen(
            cmd.argv, cwd=str(cmd.cwd), env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, shell=False, bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                          getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as e:
        yield StreamFinish(exit_code=-1, duration_ms=0,
                           error=f"spawn failed: {e}")
        return

    # Pump each pipe into a queue from background threads.
    import queue
    q: queue.Queue[StreamLine | None] = queue.Queue()

    def _pump(stream_name: str, fp):
        try:
            for line in fp:
                q.put(StreamLine(stream=stream_name, text=line.rstrip("\n")))
        finally:
            q.put(None)  # sentinel: this pump is done

    t_out = threading.Thread(target=_pump, args=("stdout", proc.stdout),
                             daemon=True)
    t_err = threading.Thread(target=_pump, args=("stderr", proc.stderr),
                             daemon=True)
    t_out.start(); t_err.start()

    pumps_alive = 2
    deadline = (started + timeout_sec) if timeout_sec else None

    while pumps_alive > 0:
        try:
            remaining = (deadline - time.monotonic()) if deadline else 1.0
            if remaining is not None and remaining <= 0:
                proc.kill()
                yield StreamFinish(
                    exit_code=-1,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=f"timeout after {timeout_sec}s",
                )
                return
            item = q.get(timeout=max(0.1, remaining if remaining else 1.0))
        except Exception:
            continue
        if item is None:
            pumps_alive -= 1
        else:
            yield item

    proc.wait()
    yield StreamFinish(
        exit_code=int(proc.returncode if proc.returncode is not None else -1),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
