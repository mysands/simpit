"""Tests for simpit_ortho_agent.engine (state machine, no threads).

Engine.tick() is driven directly with fake feed/primer/supervisor;
the scenery index and keep-set math run for real over tmp_path
fixtures so the state machine is tested against true geometry.
"""
from __future__ import annotations

import json
import time
from pathlib import Path  # noqa: F401 (type comment in _hermetic_cfg)

from simpit_common import tilemath as tm
from simpit_common.ortho_config import OrthoAgentConfig
from simpit_ortho_agent import engine as eng
from simpit_ortho_agent import mount as mt
from simpit_ortho_agent.atlas_index import SceneryIndex
from simpit_ortho_agent.rref import PositionSample
from tests.ortho_agent.conftest import grid_names, make_folder

X16, Y16 = tm.latlon_to_atlas(42.5, -72.5, 16)
CENTER = tm.tile_to_latlon(X16 + 8, Y16 + 8, 16)


class FakeFeed:
    """Position feed with test-controlled sample and age."""

    def __init__(self):
        self.sample: PositionSample | None = None
        self.age_s = float("inf")

    def set(self, lat, lon, gs, track, **wp):
        self.sample = PositionSample(lat, lon, gs, track, time.monotonic(),
                                     **wp)
        self.age_s = 0.0

    def latest(self):
        return self.sample

    def age(self):
        return self.age_s

    def start(self): ...
    def stop(self): ...


class FakePrimer:
    """Records what the engine asked for."""

    def __init__(self):
        self.schedules: list[list[str]] = []
        self.cleared = 0
        self.touch_interval = None

    def schedule(self, paths):
        self.schedules.append(list(paths))
        return len(paths)

    def clear_pending(self):
        self.cleared += 1

    def set_touch_interval(self, seconds):
        self.touch_interval = seconds

    def set_bandwidth(self, prime_mbps):
        self.prime_mbps = prime_mbps

    def start(self): ...
    def stop(self): ...


class FakeSupervisor:
    def __init__(self, result=True):
        self.result = result
        self.calls = 0

    def ensure_mounted(self, wait_seconds=0.0):
        self.calls += 1
        return self.result


def _hermetic_cfg(tmp_path, root, **cfg_kw) -> tuple[OrthoAgentConfig, "Path"]:
    """Config + on-disk local copy that never touches the real NAS.

    fleet_config_dir points inside tmp_path so load_effective() during
    SIM_OFFLINE→ACTIVE reloads stays offline, and the local copy is
    written raw (save() would reject a tmp_path mount_root).
    """
    cfg = OrthoAgentConfig(mount_root=str(root), n_rings=2,
                           fleet_config_dir=str(tmp_path / "fleet"),
                           **cfg_kw)
    local = tmp_path / "ortho_agent.json"
    local.write_text(json.dumps(cfg.to_dict()), encoding="utf-8")
    return cfg, local


def _engine(tmp_path, **cfg_kw):
    """Engine over a real scenery fixture with fakes around it."""
    root = tmp_path / "mnt"
    make_folder(root, "zOrtho4XP_Z18_+42-073",
                grid_names(42.5, -72.5, 16, 40, 42, -73))
    cfg, local = _hermetic_cfg(tmp_path, root, **cfg_kw)
    e = eng.Engine(cfg, local, feed=FakeFeed(), primer=FakePrimer(),
                   supervisor=FakeSupervisor(),
                   scenery=SceneryIndex(root, cfg.active_zoom))
    return e


def test_starts_offline_and_stays_until_a_sample_arrives(tmp_path):
    e = _engine(tmp_path)
    assert e.tick() == eng.SIM_OFFLINE
    assert e.primer.schedules == []          # priming paused


def test_active_when_moving(tmp_path):
    e = _engine(tmp_path)
    e.feed.set(*CENTER, gs=250.0, track=90.0)
    assert e.tick() == eng.ACTIVE
    assert len(e.primer.schedules) == 1 and e.primer.schedules[0]


def test_idle_when_parked_and_keep_set_unchanged(tmp_path):
    """First tick primes (keep set went empty→ring = changed, ACTIVE);
    once the keep set is stable and gs < 2 the state settles to IDLE —
    but schedule() keeps being called so keep-warm touches continue."""
    e = _engine(tmp_path)
    e.feed.set(*CENTER, gs=0.5, track=0.0)
    assert e.tick() == eng.ACTIVE
    assert e.tick() == eng.IDLE
    assert e.tick() == eng.IDLE
    assert len(e.primer.schedules) == 3      # touches still scheduled


def test_taxi_speed_above_threshold_is_active(tmp_path):
    e = _engine(tmp_path)
    e.feed.set(*CENTER, gs=eng.IDLE_GS_MS + 0.1, track=0.0)
    e.tick()
    assert e.tick() == eng.ACTIVE            # moving, even if keep unchanged


def test_rref_timeout_pauses_priming(tmp_path):
    """>10 s of RREF silence → SIM_OFFLINE, pending dropped, cache kept."""
    e = _engine(tmp_path)
    e.feed.set(*CENTER, gs=250.0, track=90.0)
    assert e.tick() == eng.ACTIVE
    schedules_before = len(e.primer.schedules)
    e.feed.age_s = eng.OFFLINE_AFTER_SECONDS + 1
    assert e.tick() == eng.SIM_OFFLINE
    assert e.primer.cleared == 1
    assert len(e.primer.schedules) == schedules_before   # no new work


def test_config_reloads_on_offline_to_active_transition(tmp_path, monkeypatch):
    """Fleet edits (zoom flip, touch cadence) apply on sim return."""
    e = _engine(tmp_path)
    e.feed.set(*CENTER, gs=0.0, track=0.0)
    e.tick()                                  # OFFLINE → ACTIVE: reload #1
    changed = OrthoAgentConfig(mount_root=e._cfg.mount_root, n_rings=2,
                               fleet_config_dir=e._cfg.fleet_config_dir,
                               active_zoom=16,
                               touch_interval_seconds=120.0)
    monkeypatch.setattr(eng.ortho_config, "load_effective",
                        lambda path, hostname=None: changed)
    e.feed.age_s = eng.OFFLINE_AFTER_SECONDS + 1
    e.tick()                                  # back to SIM_OFFLINE
    e.feed.age_s = 0.0
    e.tick()                                  # reload #2 sees the edit
    assert e._cfg.active_zoom == 16
    assert e.scenery.active_zoom == 16
    assert e.primer.touch_interval == 120.0


def test_periodic_reload_applies_mid_session(tmp_path, monkeypatch):
    """Control saves a tuning change while the sim is flying: the agent
    picks it up on the next config poll, without a sim restart."""
    e = _engine(tmp_path)
    e.feed.set(*CENTER, gs=250.0, track=90.0)
    assert e.tick() == eng.ACTIVE
    changed = OrthoAgentConfig(mount_root=e._cfg.mount_root, n_rings=2,
                               fleet_config_dir=e._cfg.fleet_config_dir,
                               touch_interval_seconds=240.0)
    monkeypatch.setattr(eng.ortho_config, "load_effective",
                        lambda path, hostname=None: changed)
    e._next_config_check = 0.0            # due now
    e.tick()
    assert e.primer.touch_interval == 240.0
    assert e.state == eng.ACTIVE          # no restart for in-place fields


def test_endpoint_change_restarts_components(tmp_path, monkeypatch):
    """A master-IP (or other endpoint) change from Control rebuilds the
    feed/primer/supervisor from the new config — the 'agent restart'."""
    e = _engine(tmp_path)
    e.feed.set(*CENTER, gs=250.0, track=90.0)
    assert e.tick() == eng.ACTIVE
    old_feed, old_primer = e.feed, e.primer

    class FakeCls:
        def __init__(self, *a, **k):
            self.args = a
            self.started = False
        def start(self):
            self.started = True
        def stop(self, *a, **k): ...
        def clear_pending(self): ...
        def age(self):
            return float("inf")        # fresh feed: no sample yet
        def latest(self):
            return None

    monkeypatch.setattr(eng, "PositionFeed", FakeCls)
    monkeypatch.setattr(eng, "Primer", FakeCls)
    monkeypatch.setattr(eng, "MountSupervisor", FakeCls)
    changed = OrthoAgentConfig(mount_root=e._cfg.mount_root, n_rings=2,
                               fleet_config_dir=e._cfg.fleet_config_dir,
                               master_ip="192.168.10.10")
    monkeypatch.setattr(eng.ortho_config, "load_effective",
                        lambda path, hostname=None: changed)
    e._next_config_check = 0.0
    e.tick()
    assert e.feed is not old_feed and e.primer is not old_primer
    assert e.feed.args[0] == "192.168.10.10"
    assert e._cfg.master_ip == "192.168.10.10"
    assert e.state == eng.SIM_OFFLINE       # fresh feed, no sample yet
    assert not e.feed.started               # engine not started (test mode)


def test_waypoint_aims_the_lookahead(tmp_path):
    """Track east but active GPS waypoint north: the keep set follows
    the waypoint bearing (tier-1 flight-plan awareness)."""
    e = _engine(tmp_path)
    # Northbound waypoint 25 nm out, nose = track (no wind).
    e.feed.set(*CENTER, gs=250.0, track=90.0,
               psi=90.0, wp_rel_bearing=-90.0, wp_distance_nm=25.0)
    e.tick()
    north = {p for p in e.primer.schedules[-1]}
    e2 = _engine(tmp_path)
    e2.feed.set(*CENTER, gs=250.0, track=90.0)      # no waypoint
    e2.tick()
    east = {p for p in e2.primer.schedules[-1]}
    assert north != east                             # ring aimed differently


def test_waypoint_lookahead_can_be_disabled(tmp_path):
    e = _engine(tmp_path, waypoint_lookahead=False)
    e.feed.set(*CENTER, gs=250.0, track=90.0,
               psi=90.0, wp_rel_bearing=-90.0, wp_distance_nm=25.0)
    e.tick()
    with_wp_off = {p for p in e.primer.schedules[-1]}
    e2 = _engine(tmp_path)
    e2.feed.set(*CENTER, gs=250.0, track=90.0)
    e2.tick()
    assert with_wp_off == {p for p in e2.primer.schedules[-1]}


def test_mount_down_skips_priming_and_retries(tmp_path):
    """Sim up but no drive: no keep-set work, supervisor asked each tick."""
    e = _engine(tmp_path)
    e.supervisor.result = False
    e._cfg.mount_root = str(tmp_path / "gone")     # drive vanished
    # Keep the on-disk copy in agreement so the OFFLINE→up config
    # reload doesn't "restore" the old mount_root.
    e._local_config_path.write_text(json.dumps(e._cfg.to_dict()),
                                    encoding="utf-8")
    e.feed.set(*CENTER, gs=250.0, track=90.0)
    e.tick(); e.tick()
    assert e.primer.schedules == []
    assert e.supervisor.calls == 2


def test_disabled_config_behaves_like_offline(tmp_path):
    e = _engine(tmp_path, enabled=False)
    e.feed.set(*CENTER, gs=250.0, track=90.0)
    assert e.tick() == eng.SIM_OFFLINE
    assert e.primer.schedules == []


def test_agent_only_ever_uses_vfs_stats_on_the_rc_api(tmp_path, monkeypatch):
    """The whole agent may health-check via vfs/stats but must NEVER
    issue cache-eviction (or any other) rc calls — eviction is rclone's
    LRU job alone (an eviction burst has crashed X-Plane before)."""
    commands: list[str] = []

    def recording_rc_post(rc_addr, command, params=None, timeout=5.0):
        commands.append(command)
        raise OSError("rc down")             # worst case: everything retried

    monkeypatch.setattr(mt, "rc_post", recording_rc_post)
    root = tmp_path / "mnt"
    make_folder(root, "zOrtho4XP_Z18_+42-073",
                grid_names(42.5, -72.5, 16, 40, 42, -73))
    cfg, local = _hermetic_cfg(tmp_path, root, supervise_mount=False)
    e = eng.Engine(cfg, local, feed=FakeFeed(), primer=FakePrimer(),
                   scenery=SceneryIndex(root, cfg.active_zoom))
    e.feed.set(*CENTER, gs=250.0, track=90.0)
    e.tick()
    # Also exercise the supervisor's mount-down probe path.
    e._cfg.mount_root = str(tmp_path / "gone")
    e.tick()
    assert set(commands) <= {"vfs/stats"}
