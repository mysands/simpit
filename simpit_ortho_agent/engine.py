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

The fleet config is re-read on every SIM_OFFLINE→(IDLE|ACTIVE)
transition, so Control-side edits (ring size, zoom label, touch
cadence) reach running agents at the next sim session without a
restart. Endpoint changes (master IP/port, mount root) still need an
agent restart — the position feed and primer bind them at startup.

The mount gate runs inside the tick: if the drive is absent, the
supervisor gets a chance to wait/launch (see :mod:`.mount`) and the
tick skips priming — the next tick retries. All periodic work happens
in :meth:`Engine.tick` so tests can drive the machine step by step
without threads or wall-clock time.
"""
from __future__ import annotations

import logging
import threading
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
        self.primer = primer or Primer(root, config.touch_interval_seconds)
        self.supervisor = supervisor or MountSupervisor(config)
        self.scenery = scenery or SceneryIndex(root, config.active_zoom)
        self.state = SIM_OFFLINE
        self._last_keep: list[KeepAtlas] = []

    # ── one step ─────────────────────────────────────────────────────────
    def tick(self) -> str:
        """Run one state-machine step; returns the new state.

        Never raises for expected trouble (sim silent, mount down):
        those are states to sit in, not errors.
        """
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
        """Fleet config re-read on the way out of SIM_OFFLINE.

        Applies the keep-set-shaping fields in place. Fields bound at
        startup (master IP/port, mount root, cache sizing) are logged
        but need an agent restart to take effect.
        """
        new = ortho_config.load_effective(self._local_config_path)
        if new == self._cfg:
            return
        log.info("config changed on the fleet share — applying")
        if (new.master_ip != self._cfg.master_ip
                or new.xp_udp_port != self._cfg.xp_udp_port
                or new.mount_root != self._cfg.mount_root):
            log.warning("endpoint fields changed; restart the agent to "
                        "apply them")
        if new.active_zoom != self._cfg.active_zoom:
            self.scenery.active_zoom = new.active_zoom
            self.scenery.clear()
        self.primer.set_touch_interval(new.touch_interval_seconds)
        self._cfg = new

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
        interval = 1.0 / max(0.1, self._cfg.poll_hz)
        log.info("ortho agent up: master %s:%d, scenery %s, state %s",
                 self._cfg.master_ip, self._cfg.xp_udp_port,
                 self._cfg.scenery_root(), self.state)
        try:
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:                     # pragma: no cover
                    log.exception("tick crashed; continuing")
                stop.wait(interval)
        finally:
            self.primer.stop()
            self.feed.stop()
            log.info("ortho agent stopped (mount left running)")
