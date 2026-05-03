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

# How much script stdout/stderr we mirror into agent.log. The full output
# always goes back to Control via EXEC_SCRIPT_RESULT; the log mirror is
# just so an operator looking at the slave directly can see what
# happened without firing up Control. 4 KB easily fits any of our
# scripts' real output and bounds log growth on the slave.
LOG_MIRROR_BYTES = 4 * 1024


def _log_script_output(script_name: str, exit_code: int, duration_ms: int,
                       stdout: str, stderr: str, suffix: str = "") -> None:
    """Mirror a script's outcome into the agent log at INFO level.

    Why INFO and not DEBUG: the slave's default level is INFO, so DEBUG
    messages never reach agent.log. Operators expect to be able to
    inspect agent.log on the slave to see which scripts ran and what
    they printed — without having to start the agent with -v.

    Output is trimmed to LOG_MIRROR_BYTES so a chatty script can't
    flood agent.log; the full text still rides home in the
    EXEC_SCRIPT_RESULT envelope to Control.
    """
    log.info("ran %s%s: exit=%d in %dms",
             script_name, suffix, exit_code, duration_ms)
    if stdout:
        body = stdout if len(stdout) <= LOG_MIRROR_BYTES \
            else stdout[:LOG_MIRROR_BYTES] + "\n…[truncated]"
        # rstrip avoids a trailing blank line in the log between the
        # last script line and the next agent log entry.
        log.info("%s stdout:\n%s", script_name, body.rstrip())
    if stderr:
        body = stderr if len(stderr) <= LOG_MIRROR_BYTES \
            else stderr[:LOG_MIRROR_BYTES] + "\n…[truncated]"
        log.info("%s stderr:\n%s", script_name, body.rstrip())


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
def _execute_elevated_windows(
    script_path, env: dict, script_name: str, started: float,
    timeout_sec: int,
) -> "ExecResult":
    """Launch a script in an elevated child process via UAC.

    Mechanism: PowerShell ``Start-Process -Verb RunAs`` triggers the
    UAC prompt on the slave's interactive desktop. The user there
    must click Yes for the script to proceed; clicking No surfaces
    as a "user declined" error.

    The integrity-level boundary normally blocks pipe redirection
    from a medium-IL parent (the slave) to a high-IL child (the
    elevated process). Workaround: tell PowerShell to redirect the
    child's stdout/stderr to temp files, then read them after the
    child exits. This works because the redirection happens
    *inside* the elevated context, where the child has full access
    to the temp paths the slave (medium-IL) created beforehand.

    Re-entry pattern: we invoke ``sys.executable`` (which is
    simpit-slave.exe in a bundle, the python interpreter from
    source) with a hidden ``--run-script`` mode that just runpy's
    the target. This means we don't need to assume a system Python
    exists on the slave or worry about PATH lookups.
    """
    import json
    import tempfile

    # Write env to a temp file so we don't have to quote it through
    # the powershell command line. The elevated child reads it and
    # sets os.environ before runpy.
    fd, env_path = tempfile.mkstemp(prefix="simpit-env-", suffix=".json")
    os.close(fd)
    out_path = env_path + ".out"
    err_path = env_path + ".err"
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            json.dump(env, f)

        # Build the ArgumentList passed to the elevated child. Each
        # element is a separate token to Start-Process.
        # ``--run-script`` is the slave's hidden re-entry mode (see
        # simpit_slave/__main__.py).
        re_entry_args = [
            "--run-script", str(script_path),
            "--env-file",   env_path,
        ]
        # PowerShell's -ArgumentList wants comma-separated quoted
        # tokens. Build that string carefully — embedded quotes are
        # the only thing that breaks here.
        def _ps_quote(s: str) -> str:
            return "'" + s.replace("'", "''") + "'"
        arglist = ",".join(_ps_quote(a) for a in re_entry_args)

        # Note: -WindowStyle Hidden makes the elevated PowerShell
        # itself invisible. The UAC prompt still appears (it's on
        # the secure desktop). The script's window is hidden too.
        ps_command = (
            f"$p = Start-Process "
            f"-FilePath {_ps_quote(sys.executable)} "
            f"-ArgumentList {arglist} "
            f"-Verb RunAs "
            f"-WindowStyle Hidden "
            f"-RedirectStandardOutput {_ps_quote(out_path)} "
            f"-RedirectStandardError  {_ps_quote(err_path)} "
            f"-PassThru -Wait; exit $p.ExitCode"
        )
        log.debug("executor: elevated ps command = %s", ps_command)

        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-Command", ps_command],
                capture_output=True, text=True,
                timeout=timeout_sec,
                # Hide the parent powershell's own window. The UAC
                # prompt isn't affected by this — it's on the secure
                # desktop. CREATE_NO_WINDOW = 0x08000000.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                script_name=script_name, found=True, exit_code=-1,
                stdout="", stderr="elevated child timed out",
                truncated=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                error="timeout",
            )
        except FileNotFoundError:
            # No powershell on PATH — extremely unusual on Windows
            # but possible on a stripped Server Core install.
            return ExecResult(
                script_name=script_name, found=True, exit_code=-1,
                stdout="", stderr="powershell.exe not found on PATH",
                truncated=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                error="powershell missing",
            )

        rc = proc.returncode

        # PowerShell returns rc=1 with this distinctive message when
        # the user clicks No on the UAC prompt. Surface it cleanly so
        # Control's log doesn't just say "exit 1, no output."
        ps_stderr = proc.stderr or ""
        if rc != 0 and "operation was canceled by the user" in ps_stderr.lower():
            return ExecResult(
                script_name=script_name, found=True, exit_code=-1,
                stdout="", stderr="user declined UAC prompt",
                truncated=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                error="UAC declined",
            )

        # Read what the elevated child produced. Files may be missing
        # if Start-Process itself failed before redirection took
        # effect — treat that as empty.
        def _read(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            except OSError:
                return ""
        stdout = _read(out_path)
        stderr = _read(err_path)

        # If the elevated path produced nothing at all but ps had
        # something to say, surface that — it's usually the real error.
        if not stdout and not stderr and ps_stderr:
            stderr = f"powershell: {ps_stderr.strip()}"

        truncated = False
        if len(stdout) > MAX_OUTPUT_BYTES:
            stdout = stdout[:MAX_OUTPUT_BYTES]; truncated = True
        if len(stderr) > MAX_OUTPUT_BYTES:
            stderr = stderr[:MAX_OUTPUT_BYTES]; truncated = True

        _log_script_output(script_name, rc,
                           int((time.monotonic() - started) * 1000),
                           stdout, stderr, suffix=" (elevated)")
        return ExecResult(
            script_name=script_name, found=True, exit_code=rc,
            stdout=stdout, stderr=stderr, truncated=truncated,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        for p in (env_path, out_path, err_path):
            try:
                os.unlink(p)
            except OSError:
                pass



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
    _log_script_output(script_name, exit_code,
                       int((time.monotonic() - started) * 1000),
                       stdout, stderr, suffix=" (inprocess)")
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
    needs_admin: bool = False,
) -> ExecResult:
    """Run a script to completion and return everything captured.

    ``needs_admin``: if True and the agent is not already elevated,
    the script is launched in an elevated child process (Windows:
    PowerShell ``Start-Process -Verb RunAs`` -> UAC prompt on the
    slave's desktop). The user at the slave must approve the prompt
    or the script returns with a permission error.

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

    # If admin is needed AND we're not already elevated AND we're on
    # Windows, hand off to the elevated path (UAC prompt). On every
    # other combination — already-elevated, POSIX, or admin not
    # needed — fall through to the normal in-process or subprocess
    # flow.
    if (needs_admin and os.name == "nt"
            and not sp_platform.is_admin()):
        log.info("executor: %s needs admin; launching elevated child",
                 script_name)
        return _execute_elevated_windows(
            script_path, env, script_name, started, timeout_sec)

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
    _log_script_output(script_name, rc,
                       int((time.monotonic() - started) * 1000),
                       stdout, stderr)
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
