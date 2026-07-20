"""
simpit_ortho_agent.primer
=========================
The priming worker: local reads that make rclone's VFS cache warm.

With ``--vfs-cache-mode full``, any read through the mount lands the
data in the local disk cache, and file *access* keeps its LRU slot
fresh. So warming is nothing but reads:

* **Prime** (first time): sequential 8 MB reads to EOF — the whole
  ~10.7 MB atlas ends up cached.
* **Touch** (keep-warm, thereafter): read a few KB so the cache item's
  access time stays fresh without traffic.

There is deliberately NO eviction here (see the package docstring):
atlases that leave the keep set are dropped from the warm map so they
simply stop being touched, and rclone's LRU at the size cap does the
rest. Dropping them also matters for correctness: once an atlas is
evicted by rclone, a touch would only re-cache its first few KB, so a
re-entering atlas must go through a full prime again (cheap if the
cache still holds it — the read comes from local disk).

One daemon worker thread drains a work queue of atlas paths that the
engine refills nearest-first each tick; a refill *replaces* the pending
queue so priorities never go stale mid-turn.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("simpit.ortho.primer")

PRIME_CHUNK_BYTES = 8 * 1024 * 1024
TOUCH_BYTES = 16 * 1024


class Primer:
    """Owns the warm map and the single priming worker thread.

    Thread-safe: :meth:`schedule` is called from the engine thread while
    the worker drains the queue.
    """

    def __init__(self, scenery_root: Path,
                 touch_interval_seconds: float = 60.0):
        """Set up the primer (no I/O until :meth:`start`).

        Args:
            scenery_root: mounted Custom Scenery level; keep-set paths
                are relative to it.
            touch_interval_seconds: how often each keep-set atlas gets
                re-touched to stay warm.
        """
        self._root = Path(scenery_root)
        self._touch_interval = touch_interval_seconds
        # rel_path -> monotonic time of last successful prime/touch.
        self._warm: dict[str, float] = {}
        self._pending: deque[str] = deque()
        self._queued: set[str] = set()
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        """Start the worker thread. Returns immediately."""
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="ortho-primer")
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        """Stop the worker after its current file finishes."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)

    # ── engine API ───────────────────────────────────────────────────────
    def schedule(self, keep_paths: list[str]) -> int:
        """Refill the work queue from the current keep set.

        Args:
            keep_paths: scenery-root-relative atlas paths, nearest-first
                (the engine's ordering is preserved verbatim).

        Returns:
            Number of paths queued this call (0 = everything warm and
            recently touched — nothing to do).

        Enqueues every keep path that is either not yet warm (→ full
        prime) or last touched more than ``touch_interval_seconds`` ago
        (→ keep-warm touch). Replaces whatever was pending so the
        nearest-first order can't go stale mid-turn. Warm entries that
        left the keep set are forgotten: they stop being touched, which
        is the whole eviction story.
        """
        now = time.monotonic()
        keep = set(keep_paths)
        with self._cond:
            due = [p for p in keep_paths
                   if p not in self._warm
                   or now - self._warm[p] >= self._touch_interval]
            for stale in [p for p in self._warm if p not in keep]:
                del self._warm[stale]
            self._pending = deque(due)
            self._queued = set(due)
            if due:
                self._cond.notify_all()
        return len(due)

    def clear_pending(self) -> None:
        """Drop queued work but KEEP the warm map (SIM_OFFLINE pause).

        Going offline must not look like an empty keep set — the warm
        map survives so priming resumes with touches, not full re-reads,
        when the sim comes back.
        """
        with self._cond:
            self._pending.clear()
            self._queued.clear()

    def set_touch_interval(self, seconds: float) -> None:
        """Apply a config-reload change to the keep-warm cadence."""
        with self._cond:
            self._touch_interval = seconds

    def is_warm(self, rel_path: str) -> bool:
        """True if the path has been fully primed and not dropped since."""
        with self._cond:
            return rel_path in self._warm

    def idle(self) -> bool:
        """True when the queue is empty (tests / status logging)."""
        with self._cond:
            return not self._pending

    # ── worker ───────────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                while not self._pending and not self._stop.is_set():
                    self._cond.wait(timeout=1.0)
                if self._stop.is_set():
                    return
                rel_path = self._pending.popleft()
                self._queued.discard(rel_path)
                full = rel_path not in self._warm
            ok = self._read(rel_path, full)
            with self._cond:
                if ok:
                    self._warm[rel_path] = time.monotonic()
                else:
                    # Missing (water/unbuilt or NAS hiccup): don't mark
                    # warm — the next touch_interval cycle retries.
                    self._warm.pop(rel_path, None)

    def _read(self, rel_path: str, full: bool) -> bool:
        """Prime or touch one atlas. Returns False if unreadable."""
        path = self._root / rel_path
        start = time.perf_counter()
        total = 0
        try:
            with open(path, "rb") as fh:
                if full:
                    while True:
                        block = fh.read(PRIME_CHUNK_BYTES)
                        if not block:
                            break
                        total += len(block)
                else:
                    total = len(fh.read(TOUCH_BYTES))
        except OSError as exc:
            log.debug("skip %s: %s", rel_path, exc)
            return False
        if full:
            log.info("primed %s (%.1f MB in %.2fs)", rel_path,
                     total / 1e6, time.perf_counter() - start)
        return True
