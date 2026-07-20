"""Tests for simpit_ortho_agent.rref (loopback UDP, no real X-Plane).

Follows the fake-responder pattern from the probe tests: a loopback
socket plays X-Plane, answering RREF subscriptions with canned records.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from simpit_ortho_agent import rref as rr


class FakeXPlane:
    """Loopback RREF responder: answers every subscribe burst it sees."""

    def __init__(self, values: dict[int, float]):
        self.values = values
        self.subscribes = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            if data[:4] != b"RREF":
                continue
            freq, idx = struct.unpack_from("<ii", data, 5)
            if freq <= 0:
                continue
            self.subscribes += 1
            payload = b"RREF\x00" + b"".join(
                struct.pack("<if", i, v) for i, v in self.values.items())
            self.sock.sendto(payload, addr)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
        self.sock.close()


@pytest.fixture()
def sim():
    fake = FakeXPlane({1: 42.898, 2: -72.271, 3: 65.0, 4: 92.5})
    yield fake
    fake.close()


def _wait_sample(feed: rr.PositionFeed, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sample = feed.latest()
        if sample is not None:
            return sample
        time.sleep(0.02)
    raise AssertionError("no complete position sample arrived")


def test_feed_assembles_full_samples(sim):
    """All four datarefs land in one PositionSample with finite age."""
    feed = rr.PositionFeed("127.0.0.1", sim.port, poll_hz=1.0)
    feed.start()
    try:
        sample = _wait_sample(feed)
        assert (sample.lat, sample.lon) == (pytest.approx(42.898),
                                            pytest.approx(-72.271))
        assert sample.gs == pytest.approx(65.0)
        assert sample.track == pytest.approx(92.5)
        assert feed.age() < 3.0
    finally:
        feed.stop()


def test_feed_age_is_infinite_before_first_sample():
    feed = rr.PositionFeed("127.0.0.1", 1, poll_hz=1.0)   # nobody there
    assert feed.latest() is None
    assert feed.age() == float("inf")


def test_feed_resubscribes(sim, monkeypatch):
    """Subscriptions are re-sent periodically (they expire sim-side)."""
    monkeypatch.setattr(rr, "RESUBSCRIBE_SECONDS", 0.1)
    feed = rr.PositionFeed("127.0.0.1", sim.port, poll_hz=1.0)
    feed.start()
    try:
        _wait_sample(feed)
        first = sim.subscribes
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and sim.subscribes < first + 4:
            time.sleep(0.05)
        assert sim.subscribes >= first + 4    # ≥1 full re-subscribe burst
    finally:
        feed.stop()
