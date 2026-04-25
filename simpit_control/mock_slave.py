"""
simpit_control.mock_slave
=========================
Simulated slave for testing and debugging — never touches the network.

Usage modes
-----------
1. **Unit tests.** A poller test injects a :class:`MockLinkProvider`
   that returns :class:`MockSlaveLink` instances. The poller can then be
   driven through every state transition with no real sockets.

2. **Manual debugging.** SimPit Control can run in a "debug fleet" mode
   that pretends to have real slaves but actually wires them up to
   MockSlaveLinks. Used to exercise UI layouts and edge cases without
   booting a slave VM.

Configurable failure modes
--------------------------
Each MockSlaveLink can be configured to:
* Return success normally.
* Time out (simulate offline).
* Return a malformed reply (simulate key mismatch / bad agent).
* Slow down by N seconds (simulate a struggling network).
* Override probe results (simulate scenery toggled, X-Plane running, etc.).

The :class:`MockFleet` helper builds a fleet of mock slaves and keeps
the failure-mode state in one place so tests can flip a slave
"offline", call the poller once, assert the cache shows OFFLINE, flip
it back, and assert recovery.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from . import data as sp_data
from . import slave_link as sp_link


class MockMode(str, Enum):
    """How a mock slave currently misbehaves (or doesn't)."""
    NORMAL    = "normal"
    OFFLINE   = "offline"      # raise SlaveUnreachable on every call
    TIMEOUT   = "timeout"      # raise SlaveTimeout on every call
    BAD_KEY   = "bad_key"      # raise SlaveBadResponse on every call
    SLOW      = "slow"         # sleep before replying (still succeeds)


@dataclass
class MockSlaveState:
    """Server-side simulated state for one mock slave.

    `probe_overrides` lets tests/debug pin specific probe outcomes:

        state.probe_overrides["bat_xyz"] = "running"

    A probe whose name appears here will return that value regardless
    of any logic. Probes not listed return "absent" — a sensible default
    that triggers ONLINE (not RUNNING).
    """
    mode:              MockMode = MockMode.NORMAL
    slow_seconds:      float    = 1.0
    probe_overrides:   dict[str, str] = field(default_factory=dict)
    hostname:          str = "mock-slave"
    os_name:           str = "linux"
    is_admin:          bool = False
    cascaded_scripts:  list[str] = field(default_factory=list)
    """Script base names currently 'present' in mock cascaded folder."""
    local_scripts:     list[str] = field(default_factory=list)


# ── The link itself ──────────────────────────────────────────────────────────
class MockSlaveLink:
    """Drop-in replacement for :class:`SlaveLink` using a MockSlaveState.

    Implements the same shape as the real link (same method names, same
    return/raise contract) so the poller can't tell the difference. We
    don't inherit because SlaveLink is a frozen dataclass and forcing
    that shape adds friction; duck-typing is enough.
    """

    def __init__(self, slave: sp_data.Slave, state: MockSlaveState):
        self.slave = slave
        self.state = state

    # ── PING ──
    def ping(self, timeout: float = 2.0) -> dict:
        self._maybe_misbehave()
        return {"ts": time.time()}

    # ── STATUS ──
    def status(self, probes: list[dict] | None = None,
               env: dict[str, str] | None = None,
               timeout: float = 2.0) -> dict:
        self._maybe_misbehave()
        outcomes = []
        for p in probes or []:
            name = p.get("name", "(unnamed)")
            value = self.state.probe_overrides.get(name, "absent")
            outcomes.append({
                "name": name, "type": p.get("type", ""),
                "ok": True, "value": value, "detail": {},
            })
        return {
            "hostname":         self.state.hostname,
            "os":               self.state.os_name,
            "uptime_sec":       42,
            "version":          "mock",
            "is_admin":         self.state.is_admin,
            "script_inventory": {
                "cascaded": list(self.state.cascaded_scripts),
                "local":    list(self.state.local_scripts),
            },
            "probes": outcomes,
        }

    # ── SHUTDOWN_PC ──
    def shutdown_pc(self, timeout: float = 2.0) -> dict:
        self._maybe_misbehave()
        return {"accepted": True}

    # ── EXEC_SCRIPT ──
    def exec_script(self, script_name: str,
                    env_overrides: dict[str, str] | None = None,
                    args: list[str] | None = None,
                    timeout_sec: int = 300,
                    deadline: float = 60.0) -> dict:
        self._maybe_misbehave()
        # Pretend the script ran successfully.
        return {
            "script_name": script_name,
            "found":       True,
            "exit_code":   0,
            "stdout":      f"[mock] ran {script_name}\n",
            "stderr":      "",
            "truncated":   False,
            "duration_ms": 10,
            "error":       "",
        }

    # ── SYNC_PUSH ──
    def sync_push(self, scripts: list[dict], deadline: float = 60.0) -> dict:
        self._maybe_misbehave()
        names = [s.get("name", "") for s in scripts]
        # Mutate the simulated state so subsequent STATUS calls reflect it.
        self.state.cascaded_scripts = list(names)
        return {"written": names, "skipped": [], "count": len(names)}

    # ── Failure injection ──
    def _maybe_misbehave(self) -> None:
        m = self.state.mode
        if m == MockMode.NORMAL:
            return
        if m == MockMode.SLOW:
            time.sleep(self.state.slow_seconds)
            return
        if m == MockMode.OFFLINE:
            raise sp_link.SlaveUnreachable("mock: offline")
        if m == MockMode.TIMEOUT:
            raise sp_link.SlaveTimeout("mock: timeout")
        if m == MockMode.BAD_KEY:
            raise sp_link.SlaveBadResponse("mock: signature mismatch")


# ── Provider that hands out mock links ───────────────────────────────────────
class MockLinkProvider:
    """LinkProvider that returns MockSlaveLinks from an internal table.

    Tests typically build one of these, populate it via :meth:`add`,
    then hand it to :class:`Poller` instead of a RealLinkProvider. The
    same instance can be queried by the test to flip failure modes.
    """

    def __init__(self):
        self._states: dict[str, MockSlaveState] = {}
        self._lock = threading.Lock()

    def add(self, slave_id: str,
            state: MockSlaveState | None = None) -> MockSlaveState:
        """Register a mock slave by id; returns its mutable state."""
        with self._lock:
            self._states[slave_id] = state or MockSlaveState()
            return self._states[slave_id]

    def state_for(self, slave_id: str) -> MockSlaveState:
        """Return the mutable state for a registered slave."""
        with self._lock:
            return self._states[slave_id]

    def link_for(self, slave: sp_data.Slave) -> Any:
        with self._lock:
            state = self._states.get(slave.id)
            if state is None:
                # An unregistered slave id behaves like an offline real
                # slave — saves tests from setting up boilerplate for
                # cases that don't care.
                state = MockSlaveState(mode=MockMode.OFFLINE)
                self._states[slave.id] = state
        return MockSlaveLink(slave=slave, state=state)
