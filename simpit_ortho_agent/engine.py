"""
simpit_ortho_agent.engine
=========================
The agent's state machine and main loop.

States
------
* ``SIM_OFFLINE`` — no complete RREF sample for >10 s. Priming and
  touching pause; the cache is left exactly as-is (pending work is
  dropped, the warm map is kept).
* ``IDLE``        — groundspeed < 2 m/s and the keep set unchanged.
  No recompute churn and no new priming; keep-warm touches continue on
  their normal cadence — they are the eviction defense and must not
  stop while the aircraft is parked.
* ``ACTIVE``      — the keep set changed (or the aircraft is moving):
  priming/keep-warm loop running.

The fleet config is re-read periodically (every ``CONFIG_POLL_SECONDS``)
and on every SIM_OFFLINE→(IDLE|ACTIVE) transition, so edits saved in
Control's Ortho Cache dialog reach running agents within about a
minute. Keep-set-shaping fields apply in place; endpoint fields
(master IP/port, poll rate, mount root) trigger an internal restart —
the engine stops its feed and primer and rebuilds every component from
the new config, which is the same as an agent restart without needing
process supervision. The primer's warm map is lost in that case, so
the next pass re-primes (cheap when the cache still holds the files).

The mount gate runs inside the tick: if the drive is absent, the
supervisor gets a chance to wait/launch (see :mod:`.mount`) and the
tick skips priming — the next tick retries. All periodic work happens
in :meth:`Engine.tick` so tests can drive the machine step by step
without threads or wall-clock time.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from simpit_common import ortho_config
from simpit_common.ortho_config import OrthoAgentConfig

from .atlas_index import SceneryIndex
from .keepset import KeepAtlas, compute_keep_set
from .mount import MountSupervisor, mount_up
from .primer import Primer
from .rref import PositionFeed

log = logging.getLogger("simpit.ortho.engine")

SIM_OFFLINE = "SIM_OFFLINE"
IDLE = "IDLE"
ACTIVE = "ACTIVE"

# No complete RREF sample for this long → SIM_OFFLINE.
OFFLINE_AFTER_SECONDS = 10.0
# Below this groundspeed the aircraft counts as stationary.
IDLE_GS_MS = 2.0
# How often the fleet config is re-read while running, so Control-side
# edits apply without anyone touching the machine.
CONFIG_POLL_SECONDS = 60.0

# Changing any of these means feed/primer/index bindings are stale →
# the engine rebuilds all components (internal restart).
_REBUILD_FIELDS = ("master_ip", "xp_udp_port", "poll_hz",
                   "mount_root", "remote_rel_root", "rc_addr",
                   "remote_target", "supervise_mount", "cache_dir",
                   "cache_max_gb", "cache_max_age")


class Engine:
    """Wires feed + keep set + primer + mount into one periodic tick."""

    def __init__(self, config: OrthoAgentConfig, local_config_path: Path,
                 feed: PositionFeed | None = None,
                 primer: Primer | None = None,
                 supervisor: MountSupervisor | None = None,
                 scenery: SceneryIndex | None = None):
        """Build the engine and its default components.

        Args:
            config: effective agent config (fleet-merged).
            local_config_path: this machine's ortho_agent.json — used
                for fleet re-reads on SIM_OFFLINE→ACTIVE transitions.
            feed: injected position feed (tests); default from config.
            primer: injected primer (tests); default from config.
            supervisor: injected mount supervisor (tests).
            scenery: injected folder index (tests).
        """
        self._cfg = config
        self._local_config_path = local_config_path
        root = config.scenery_root()
        self.feed = feed or PositionFeed(config.master_ip,
                                         config.xp_udp_port,
                                         config.poll_hz)
        self.primer = primer or Primer(root, config.touch_interval_seconds,
                                       config.prime_mbps)
        self.supervisor = supervisor or MountSupervisor(config)
        self.scenery = scenery or SceneryIndex(root, config.active_zoom)
        self.state = SIM_OFFLINE
        self._last_keep: list[KeepAtlas] = []
        self._started = False
        self._next_config_check = 0.0

    # ── one step ─────────────────────────────────────────────────────────
    def tick(self) -> str:
        """Run one state-machine step; returns the new state.

        Never raises for expected trouble (sim silent, mount down):
        those are states to sit in, not errors.
        """
        now = time.monotonic()
        if now >= self._next_config_check:
            self._next_config_check = now + CONFIG_POLL_SECONDS
            self._reload_config()

        if self.feed.age() > OFFLINE_AFTER_SECONDS or not self._cfg.enabled:
            self._enter(SIM_OFFLINE)
            return self.state

        if self.state == SIM_OFFLINE:
            self._reload_config()

        if not mount_up(Path(self._cfg.mount_root)):
            if not self.supervisor.ensure_mounted():
                # Sim is up but scenery isn't — stay put, retry next tick.
                return self.state

        sample = self.feed.latest()
        keep = compute_keep_set(sample.lat, sample.lon, sample.track,
                                sample.gs, self._cfg.n_rings,
                                self._cfg.lookahead_seconds, self.scenery)
        changed = keep != self._last_keep
        self._last_keep = keep
        self._enter(IDLE if sample.gs < IDLE_GS_MS and not changed
                    else ACTIVE)
        queued = self.primer.schedule([a.rel_path() for a in keep])
        if queued:
            log.debug("keep set %d atlases, %d queued", len(keep), queued)
        return self.state

    def _enter(self, state: str) -> None:
        """Transition with logging; entering SIM_OFFLINE pauses the primer."""
        if state == self.state:
            return
        log.info("%s -> %s", self.state, state)
        self.state = state
        if state == SIM_OFFLINE:
            self.primer.clear_pending()

    def _reload_config(self) -> None:
        """Re-read the effective config and apply whatever changed.

        Keep-set-shaping fields apply in place. Endpoint fields (see
        ``_REBUILD_FIELDS``) trigger an internal restart: every
        component is stopped and rebuilt from the new config.
        """
        try:
            new = ortho_config.load_effective(self._local_config_path)
        except Exception as exc:                       # noqa: BLE001
            log.warning("config reload failed (kept current): %s", exc)
            return
        if new == self._cfg:
            return
        log.info("config changed — applying")
        if any(getattr(new, f) != getattr(self._cfg, f)
               for f in _REBUILD_FIELDS):
            self._rebuild_components(new)
            return
        if new.active_zoom != self._cfg.active_zoom:
            self.scenery.active_zoom = new.active_zoom
            self.scenery.clear()
        self.primer.set_touch_interval(new.touch_interval_seconds)
        self.primer.set_bandwidth(new.prime_mbps)
        self._cfg = new

    def _rebuild_components(self, new: OrthoAgentConfig) -> None:
        """Internal restart: rebuild feed/primer/supervisor/index.

        Equivalent to restarting the agent process (Control saved an
        endpoint change), minus the process supervision: old threads
        stop, fresh components bind the new config, and priming state
        starts over.
        """
        log.info("endpoint config changed — restarting agent components")
        self.feed.stop()
        self.primer.stop()
        self._cfg = new
        root = new.scenery_root()
        self.feed = PositionFeed(new.master_ip, new.xp_udp_port,
                                 new.poll_hz)
        self.primer = Primer(root, new.touch_interval_seconds,
                             new.prime_mbps)
        self.supervisor = MountSupervisor(new)
        self.scenery = SceneryIndex(root, new.active_zoom)
        self._last_keep = []
        self._enter(SIM_OFFLINE)      # fresh feed has no sample yet
        if self._started:
            self.feed.start()
            self.primer.start()

    # ── run loop ─────────────────────────────────────────────────────────
    def run(self, stop: threading.Event) -> None:
        """Start components and tick until `stop` is set.

        A tick crash MUST NOT kill the agent (same failure-isolation
        rule as the slave listener): it is logged and the loop
        continues. On shutdown the feed and primer stop, but a
        supervisor-launched rclone is left RUNNING — killing the mount
        would rip memory-mapped textures out from under X-Plane.
        """
        self.feed.start()
        self.primer.start()
        self._started = True
        log.info("ortho agent up: master %s:%d, scenery %s, state %s",
                 self._cfg.master_ip, self._cfg.xp_udp_port,
                 self._cfg.scenery_root(), self.state)
        try:
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:                     # pragma: no cover
                    log.exception("tick crashed; continuing")
                # Interval from the live config: poll_hz may have been
                # changed by a reload since the last iteration.
                stop.wait(1.0 / max(0.1, self._cfg.poll_hz))
        finally:
            self.primer.stop()
            self.feed.stop()
            log.info("ortho agent stopped (mount left running)")
