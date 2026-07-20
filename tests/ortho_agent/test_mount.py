"""Tests for simpit_ortho_agent.mount (supervision paths, no real rclone).

rclone launches are faked by monkeypatching subprocess.Popen; "the rc is
up" is faked at the rc_stats level. Configs point mount_root at tmp
paths — validation only runs on save(), so test configs may use them.
"""
from __future__ import annotations

import subprocess

from simpit_common.ortho_config import OrthoAgentConfig
from simpit_ortho_agent import mount as mt


def _cfg(tmp_path, **kw) -> OrthoAgentConfig:
    return OrthoAgentConfig(mount_root=str(tmp_path / "mnt"),
                            rc_addr="127.0.0.1:1", **kw)   # port 1: dead


def test_mount_up(tmp_path):
    assert not mt.mount_up(tmp_path / "mnt")
    (tmp_path / "mnt").mkdir()
    assert mt.mount_up(tmp_path / "mnt")


def test_ensure_mounted_short_circuits_when_drive_present(tmp_path, monkeypatch):
    """Drive up → True immediately, no rc probe, no launch."""
    (tmp_path / "mnt").mkdir()
    sup = mt.MountSupervisor(_cfg(tmp_path))
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not launch")))
    assert sup.ensure_mounted(wait_seconds=0.1) is True


def test_ensure_mounted_launches_rclone_and_waits(tmp_path, monkeypatch):
    """Drive down, rc dead, supervision on → launch + wait for the drive."""
    launched: list[list[str]] = []

    class FakeProc:
        pid = 4242
        def poll(self):
            return None
        def terminate(self):
            pass

    def fake_popen(cmd, *a, **k):
        launched.append(cmd)
        (tmp_path / "mnt").mkdir()          # "mount" appears immediately
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    sup = mt.MountSupervisor(_cfg(tmp_path))
    assert sup.ensure_mounted(wait_seconds=5.0) is True
    assert len(launched) == 1
    assert launched[0][:2] == ["rclone", "mount"]
    assert "--vfs-cache-mode" in launched[0]


def test_ensure_mounted_timeout_then_retry_succeeds(tmp_path, monkeypatch):
    """Launch that never yields a drive → False (logged); a later call
    does not double-launch while the child is still alive."""
    launched = []

    class FakeProc:
        pid = 4242
        def poll(self):
            return None                     # still running

    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, *a, **k: launched.append(cmd) or FakeProc())
    sup = mt.MountSupervisor(_cfg(tmp_path))
    assert sup.ensure_mounted(wait_seconds=0.1) is False
    assert sup.ensure_mounted(wait_seconds=0.1) is False
    assert len(launched) == 1               # no double-mount from retries
    (tmp_path / "mnt").mkdir()              # reconcile finally finished
    assert sup.ensure_mounted(wait_seconds=0.1) is True


def test_ensure_mounted_respects_supervise_mount_off(tmp_path, monkeypatch):
    """supervise_mount=False → report failure, never launch anything."""
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not launch")))
    sup = mt.MountSupervisor(_cfg(tmp_path, supervise_mount=False))
    assert sup.ensure_mounted(wait_seconds=0.1) is False


def test_ensure_mounted_waits_when_rc_is_up(tmp_path, monkeypatch):
    """rc answering means SOME rclone is reconciling — wait, don't spawn
    a second mount (the installer's logon helper owns it)."""
    monkeypatch.setattr(mt.MountSupervisor, "rc_stats", lambda self: {})
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not launch")))
    sup = mt.MountSupervisor(_cfg(tmp_path))
    assert sup.ensure_mounted(wait_seconds=0.1) is False    # still waiting
    (tmp_path / "mnt").mkdir()
    assert sup.ensure_mounted(wait_seconds=0.1) is True
