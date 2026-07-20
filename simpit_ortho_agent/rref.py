"""
simpit_ortho_agent.rref
=======================
Continuous position feed from the X-Plane master over RREF UDP.

The master serves RREF to any number of subscribers, so every machine's
agent subscribes independently — same feed, zero coordination. Four
datarefs are streamed at the configured rate:

    sim/flightmodel/position/latitude
    sim/flightmodel/position/longitude
    sim/flightmodel/position/groundspeed   (m/s)
    sim/flightmodel/position/hpath         (ground track, deg true)

``hpath`` and not ``psi``: heading diverges from track in crosswind,
which would skew the 45 s lookahead projection by up to an atlas width.

RREF subscriptions silently expire (sim restart, scenery reload), so
the feed re-sends its subscriptions every ``RESUBSCRIBE_SECONDS``. The
engine treats a feed with no complete sample for >10 s as SIM_OFFLINE.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass

from simpit_common import xp_rref

log = logging.getLogger("simpit.ortho.rref")

POSITION_DATAREFS = {
    1: "sim/flightmodel/position/latitude",
    2: "sim/flightmodel/position/longitude",
    3: "sim/flightmodel/position/groundspeed",
    4: "sim/flightmodel/position/hpath",
}

# Subscriptions expire on the sim side; re-assert them at this cadence.
RESUBSCRIBE_SECONDS = 30.0


@dataclass(frozen=True)
class PositionSample:
    """One complete aircraft position sample.

    Attributes:
        lat: latitude in degrees.
        lon: longitude in degrees.
        gs: groundspeed in m/s.
        track: ground track (hpath), degrees true.
        monotonic: time.monotonic() when the sample became complete.
    """
    lat: float
    lon: float
    gs: float
    track: float
    monotonic: float


class PositionFeed:
    """Background RREF subscriber owning one UDP socket.

    Thread-safe: :meth:`latest` and :meth:`age` may be called from any
    thread while the receive loop runs.
    """

    def __init__(self, host: str, port: int, poll_hz: float = 1.0):
        """Set up the feed (no I/O until :meth:`start`).

        Args:
            host: X-Plane master IP.
            port: X-Plane UDP port (default fleet-wide: 49000).
            poll_hz: requested sample rate; RREF frequency is an
                integer, so this is rounded and floored at 1 Hz.
        """
        self._host = host
        self._port = port
        self._freq = max(1, round(poll_hz))
        self._lock = threading.Lock()
        self._latest: PositionSample | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        """Start the receive thread. Returns immediately."""
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="ortho-rref")
        self._thread.start()

    def stop(self, join_timeout: float = 3.0) -> None:
        """Unsubscribe (best-effort) and stop the receive thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)

    # ── consumer API ─────────────────────────────────────────────────────
    def latest(self) -> PositionSample | None:
        """The most recent complete sample, or None before the first one."""
        with self._lock:
            return self._latest

    def age(self) -> float:
        """Seconds since the last complete sample; +inf if none yet."""
        sample = self.latest()
        if sample is None:
            return float("inf")
        return time.monotonic() - sample.monotonic

    # ── receive loop ─────────────────────────────────────────────────────
    def _send_subscriptions(self, sock: socket.socket, freq: int) -> None:
        """(Re-)send one subscribe/unsubscribe packet per dataref."""
        for idx, ref in POSITION_DATAREFS.items():
            try:
                sock.sendto(xp_rref.request_packet(freq, idx, ref),
                            (self._host, self._port))
            except OSError as exc:
                log.debug("RREF send failed: %s", exc)

    def _run(self) -> None:
        values: dict[int, float] = {}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            self._send_subscriptions(sock, self._freq)
            last_subscribe = time.monotonic()
            while not self._stop.is_set():
                now = time.monotonic()
                if now - last_subscribe >= RESUBSCRIBE_SECONDS:
                    self._send_subscriptions(sock, self._freq)
                    last_subscribe = now
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    # e.g. WinError 10054: ICMP port-unreachable from a
                    # closed UDP port — sim not up yet. Same as silence.
                    continue
                values.update(xp_rref.decode_response(data))
                if all(idx in values for idx in POSITION_DATAREFS):
                    sample = PositionSample(
                        lat=values[1], lon=values[2], gs=values[3],
                        track=values[4], monotonic=time.monotonic())
                    with self._lock:
                        self._latest = sample
            self._send_subscriptions(sock, 0)
