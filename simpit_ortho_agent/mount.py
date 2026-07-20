"""
simpit_ortho_agent.mount
========================
rclone mount supervision (fallback path) and rc health checks.

On installed machines the mount already comes up at logon: the SimPit
installer generates ``ortho_mount.bat`` and registers it under HKCU
Run, running rclone in a visible console window titled "SimPit Ortho
Mount". The agent's supervision is therefore a FALLBACK for machines
where that helper is absent or has died — and it must never
double-mount, so it checks accessibility first and treats a responding
rc port as "a mount process already exists, just wait".

The wait matters: the drive letter only appears once rclone finishes
reconciling its cache with the remote (~3 min at 200 GB), so "drive not
there" right after boot is normal, not a failure.

The rc port is used for health/status ONLY (``vfs/stats``). The agent
never issues cache-eviction rc calls — there is no per-file eviction in
rclone's rc API, and bulk cache deletion while the sim runs has crashed
X-Plane before (see the package docstring).
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.request
from pathlib import Path

from simpit_common.ortho_config import OrthoAgentConfig

log = logging.getLogger("simpit.ortho.mount")

# How long to wait for the drive letter after (re)starting rclone. Cache
# reconciliation can hold it back for minutes, so the engine calls
# ensure_mounted() in a retry loop rather than treating this as fatal.
MOUNT_WAIT_SECONDS = 60.0


def rc_post(rc_addr: str, command: str, params: dict | None = None,
            timeout: float = 5.0) -> dict:
    """POST a command to the rclone rc API and return the JSON response.

    Args:
        rc_addr: host:port of the rc listener (e.g. "127.0.0.1:5572").
        command: rc command path (e.g. "vfs/stats").
        params: JSON-serializable request body, or None for empty.
        timeout: socket timeout in seconds.

    Returns:
        Decoded JSON response.

    Raises:
        OSError: if the rc endpoint is unreachable.
    """
    body = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{rc_addr}/{command}", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def mount_up(mount_root: Path) -> bool:
    """True iff the mount's drive/folder is present and listable."""
    try:
        return mount_root.is_dir()
    except OSError:
        return False


class MountSupervisor:
    """Keeps the rclone mount available (fallback to the logon helper)."""

    def __init__(self, config: OrthoAgentConfig):
        """Bind the supervisor to the agent config.

        Args:
            config: effective agent config; supplies mount_root,
                rc_addr, supervise_mount and the derived rclone command.
        """
        self._cfg = config
        self._proc: subprocess.Popen | None = None

    # ── health ───────────────────────────────────────────────────────────
    def rc_stats(self) -> dict | None:
        """One ``vfs/stats`` snapshot, or None when rc is unreachable."""
        try:
            return rc_post(self._cfg.rc_addr, "vfs/stats")
        except (OSError, ValueError) as exc:
            log.debug("rc unreachable on %s: %s", self._cfg.rc_addr, exc)
            return None

    # ── supervision ──────────────────────────────────────────────────────
    def ensure_mounted(self, wait_seconds: float = MOUNT_WAIT_SECONDS) -> bool:
        """Make sure the mount is up, launching rclone only as a last resort.

        Decision order (double-mount safety):

        1. Drive present → done.
        2. rc answers → some rclone is already mounting/reconciling
           (logon helper or our own child); just wait for the drive.
        3. ``supervise_mount`` enabled → launch rclone from the derived
           command, then wait for the drive.

        Args:
            wait_seconds: how long to wait for the drive to appear
                before giving up this round.

        Returns:
            True when the mount is up. False → the caller's retry loop
            tries again next tick (an error has been logged).
        """
        root = Path(self._cfg.mount_root)
        if mount_up(root):
            return True
        if self.rc_stats() is None:
            if not self._cfg.supervise_mount:
                log.error("mount %s is down and supervise_mount is off",
                          root)
                return False
            if self._proc is not None and self._proc.poll() is None:
                log.info("rclone child still starting (pid %d)",
                         self._proc.pid)
            else:
                self._launch()
        else:
            log.info("rc is up but %s not present yet — rclone is "
                     "reconciling its cache; waiting", root)
        return self._wait_for_mount(root, wait_seconds)

    def stop(self) -> None:
        """Terminate a supervisor-launched rclone (never someone else's)."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None

    def _launch(self) -> None:
        """Start rclone from the config-derived command (detached)."""
        cmd = self._cfg.build_rclone_cmd()
        log.warning("mount %s down and no rc — launching: %s",
                    self._cfg.mount_root, " ".join(cmd))
        try:
            self._proc = subprocess.Popen(cmd)
        except OSError as exc:
            log.error("failed to launch rclone: %s", exc)
            self._proc = None

    def _wait_for_mount(self, root: Path, wait_seconds: float) -> bool:
        """Poll for the drive letter until it appears or time runs out."""
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if mount_up(root):
                log.info("mount %s is up", root)
                return True
            time.sleep(2.0)
        log.error("mount %s did not appear within %.0fs — will retry",
                  root, wait_seconds)
        return False
