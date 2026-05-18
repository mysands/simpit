"""
simpit_control.poller
=====================
Background thread that keeps Control's view of slave state fresh.

Responsibilities
----------------
* Periodically PING + STATUS each known slave on a configurable cadence.
* Translate raw replies into a high-level :class:`SlaveStatus` cached
  per-slave.
* Fan out updates via subscribed callbacks so the UI re-renders only
  when something actually changed.

Design notes
------------
**One thread, all slaves.**  We could have one thread per slave but the
total work is tiny (a few UDP round-trips every 5s) and a single thread
serializes the cache mutations cleanly. If a fleet ever gets large
enough for this to matter, the natural upgrade is a thread pool keyed
by slave id.

**Callbacks on the polling thread, not Tk.**  We deliver updates to
subscribers from the poller thread. Tk-bound subscribers MUST hop back
to the main thread via ``root.after(0, ...)`` themselves; the poller
doesn't import tkinter so the core stays GUI-agnostic and unit-testable.

**Decoupling from real network.**  The poller doesn't know about
sockets — it talks to a :class:`LinkProvider` interface that returns a
:class:`SlaveLink`-like object per slave. Tests inject a provider that
returns mock links, so polling logic can be exercised without any real
network at all.

**No saved state.**  Per the architecture decision: button/script state
in the UI is derived from the latest poll, never from a stored flag.
The poller's cache IS the state. If the poller hasn't yet polled, the
UI shows OFFLINE/UNKNOWN; that's correct, not a bug.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

from simpit_common import protocol as sp_protocol
from simpit_common import security as sp_security

_reg_log = logging.getLogger("simpit.registration")

from simpit_common import probes as sp_probes

from . import data as sp_data
from . import slave_link as sp_link


# ── Status enum ──────────────────────────────────────────────────────────────
class SlaveState(str, Enum):
    """High-level lifecycle state shown on each slave card.

    Values are strings so they serialize cleanly into logs/snapshots.

    Transitions
    -----------
    ``UNKNOWN``  - newly added / no poll yet
    ``OFFLINE``  - PING failed (timeout / unreachable)
    ``ONLINE``   - PING ok, STATUS ok, X-Plane probe says not running
    ``RUNNING``  - PING ok, STATUS ok, X-Plane probe says running
    ``SYNCING``  - SYNC_PUSH in flight (set by Control's sync workflow)
    ``ERROR``    - PING ok but STATUS replies failed verify (key mismatch)

    The values are deliberate: a slave whose AGENT is up but whose KEY
    is wrong is ERROR, not OFFLINE — different remediation. The user
    sees a distinct red 'KEY MISMATCH' rather than 'try the network'.
    """
    UNKNOWN = "unknown"
    OFFLINE = "offline"
    ONLINE  = "online"
    RUNNING = "running"
    SYNCING = "syncing"
    ERROR   = "error"


@dataclass
class SlaveStatus:
    """Latest known status for one slave.

    Held by the poller, copy-given to subscribers. Frozen at the
    dataclass level isn't quite right because we mutate the same
    instance in-place under the lock; subscribers always receive a
    snapshot copy via :meth:`copy`.
    """
    slave_id:        str
    state:           SlaveState = SlaveState.UNKNOWN
    last_seen:       float = 0.0           # unix time of last successful poll
    last_attempt:    float = 0.0           # unix time of last poll attempt
    error:           str = ""              # populated when state == ERROR
    snapshot:        dict | None = None    # last STATUS reply body (raw)
    probe_results:   dict[str, str] = field(default_factory=dict)
    """Map of probe name -> probe value (e.g. "scenery": "present")."""

    def copy(self) -> "SlaveStatus":
        """Shallow snapshot suitable for handing to subscribers."""
        return SlaveStatus(
            slave_id      = self.slave_id,
            state         = self.state,
            last_seen     = self.last_seen,
            last_attempt  = self.last_attempt,
            error         = self.error,
            snapshot      = dict(self.snapshot) if self.snapshot else None,
            probe_results = dict(self.probe_results),
        )


# ── Link provider interface ──────────────────────────────────────────────────
class LinkProvider(Protocol):
    """Anything that can produce a SlaveLink-like object for a Slave id.

    Implemented in production by :class:`RealLinkProvider` below.
    Implemented in tests by a function that returns a mock — see
    :mod:`simpit_control.mock_slave`.
    """
    def link_for(self, slave: sp_data.Slave) -> sp_link.SlaveLink: ...


@dataclass
class RealLinkProvider:
    """Default provider — returns real SlaveLink objects bound to live sockets."""
    key: bytes

    def link_for(self, slave: sp_data.Slave) -> sp_link.SlaveLink:
        return sp_link.SlaveLink(slave=slave, key=self.key)


# ── Poll cadence ─────────────────────────────────────────────────────────────
@dataclass
class PollCadence:
    """How often we ask each kind of question.

    Values are seconds. Picked to balance freshness vs network noise:
    * ping/status: every 5s — feels live, costs almost nothing.
    * heavy probes (script_inventory churn, hosts file): every 30s.

    The UI decides which probes are 'heavy' by tagging their definitions
    with ``poll_interval`` (not yet wired through, but the data model
    supports it). For now everything runs at ping_interval — easy to
    refine once the UI has data to show.
    """
    ping_interval:   float = 5.0
    status_interval: float = 5.0


# ── The poller ───────────────────────────────────────────────────────────────
Subscriber = Callable[[SlaveStatus], None]


class Poller:
    """Background polling loop.

    Lifecycle: ``start()`` -> ``stop()``. Safe to call ``stop()`` more
    than once. Subscribers added before ``start()`` will receive every
    update; subscribers added after will receive updates from then on.

    Thread safety
    -------------
    The internal cache is guarded by a single Lock. All public methods
    are safe to call from any thread; subscriber callbacks run on the
    poller thread (subscribers must hop to UI thread themselves).
    """

    def __init__(self, store: sp_data.Store, provider: LinkProvider,
                 cadence: PollCadence | None = None):
        self.store    = store
        self.provider = provider
        self.cadence  = cadence or PollCadence()

        self._cache: dict[str, SlaveStatus] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._subs:  list[Subscriber] = []

    # ── Subscription ──
    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        """Register a callback. Returns an unsubscribe function."""
        self._subs.append(fn)
        def unsubscribe():
            try:
                self._subs.remove(fn)
            except ValueError:
                pass
        return unsubscribe

    # ── Reading ──
    def get(self, slave_id: str) -> SlaveStatus:
        """Latest status for a slave (creates UNKNOWN if not seen yet)."""
        with self._lock:
            cur = self._cache.get(slave_id)
            if cur is None:
                cur = SlaveStatus(slave_id=slave_id)
                self._cache[slave_id] = cur
            return cur.copy()

    def all(self) -> dict[str, SlaveStatus]:
        """Snapshot of every cached status, keyed by slave id."""
        with self._lock:
            return {sid: st.copy() for sid, st in self._cache.items()}

    # ── Manual triggers (used by sync workflow) ──
    def mark_syncing(self, slave_id: str) -> None:
        """Flip a slave to SYNCING state. UI uses this for the blue strip."""
        with self._lock:
            cur = self._cache.setdefault(slave_id, SlaveStatus(slave_id=slave_id))
            cur.state = SlaveState.SYNCING
            cur.last_attempt = time.time()
            snap = cur.copy()
        self._notify(snap)

    # ── Lifecycle ──
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="simpit-poller")
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)

    # ── Loop ──
    def _run(self) -> None:
        # We poll every slave on every tick. The cadence config governs
        # how long we sleep between ticks; per-probe intervals would go
        # here as a small scheduler if/when needed.
        while not self._stop.is_set():
            slaves = self.store.slaves()
            for s in slaves:
                if self._stop.is_set():
                    break
                self._poll_one(s)
            self._stop.wait(timeout=self.cadence.ping_interval)

    def _poll_one(self, slave: sp_data.Slave) -> None:
        """Run a single poll cycle against one slave and update cache."""
        link = self.provider.link_for(slave)
        now = time.time()

        # Build the probe list from registered batfiles that have probes.
        # Resolve any ${VAR} references in probe params using slave.env
        # before sending — the slave evaluates literal params, so we don't
        # need to ship the env block on every 5-second STATUS poll.
        slave_env: dict[str, str] = dict(slave.env) if slave.env else {}
        probes = []
        for b in self.store.batfiles():
            if b.state_probe:
                resolved_params = sp_probes.resolve_params(
                    b.state_probe.get("params", {}), slave_env)
                probes.append({
                    # `name` defaults to batfile id so probes can be looked up
                    # back on Control by id without parsing labels.
                    "name":   b.id,
                    "type":   b.state_probe.get("type", ""),
                    "params": resolved_params,
                })

        try:
            body = link.status(probes=probes)
        except sp_link.SlaveBadResponse as e:
            self._set_state(slave.id, SlaveState.ERROR,
                            error=f"key/format mismatch: {e}",
                            last_attempt=now)
            return
        except (sp_link.SlaveUnreachable, sp_link.SlaveTimeout) as e:
            self._set_state(slave.id, SlaveState.OFFLINE,
                            error=str(e), last_attempt=now)
            return

        # Decode probe outcomes by id back to a friendly map.
        probe_results = {}
        for p in body.get("probes", []) or []:
            probe_results[p.get("name", "")] = p.get("value", "")

        # Decide RUNNING vs ONLINE. We look for the conventional
        # "x-plane running" probe by convention. If absent, the slave is
        # ONLINE; the UI can derive RUNNING per-batfile from the probes
        # map directly.
        new_state = SlaveState.ONLINE
        for value in probe_results.values():
            if value == "running":
                new_state = SlaveState.RUNNING
                break

        self._set_state(slave.id, new_state, error="",
                        last_attempt=now, last_seen=now,
                        snapshot=body, probe_results=probe_results)

    def _set_state(self, slave_id: str, state: SlaveState,
                   error: str = "", last_attempt: float = 0.0,
                   last_seen: float | None = None,
                   snapshot: dict | None = None,
                   probe_results: dict[str, str] | None = None) -> None:
        with self._lock:
            cur = self._cache.setdefault(slave_id, SlaveStatus(slave_id=slave_id))
            changed = (cur.state != state or
                       cur.error != error or
                       (probe_results is not None and
                        cur.probe_results != probe_results))
            cur.state = state
            cur.error = error
            cur.last_attempt = last_attempt or cur.last_attempt
            if last_seen is not None:
                cur.last_seen = last_seen
            if snapshot is not None:
                cur.snapshot = snapshot
            if probe_results is not None:
                cur.probe_results = probe_results
            snap = cur.copy()
        if changed:
            self._notify(snap)

    def _notify(self, snap: SlaveStatus) -> None:
        for fn in list(self._subs):
            try:
                fn(snap)
            except Exception:                          # pragma: no cover
                # A buggy subscriber must never crash the poller. Errors
                # are swallowed; the subscriber is responsible for its
                # own logging.
                pass


# ── Auto-registration listener ───────────────────────────────────────────────
RegisterCallback = Callable[[str, str, int, int, dict], None]
"""Called when a new slave announces itself.

Args:
    name     — display name from slave-config.json
    host     — IP address the datagram arrived from
    udp_port — slave's UDP port
    tcp_port — slave's TCP port
    env      — environment variables (XPLANE_FOLDER, etc.)
"""


class RegistrationListener:
    """Background UDP listener that auto-registers slaves.

    Binds to ``0.0.0.0:<udp_port>`` and watches for ``SLAVE_ONLINE``
    datagrams that carry a ``register_name`` field — written by the
    installer into the slave's ``slave-config.json``.  On receipt it
    fires ``on_register`` (on the listener thread) so the app can add
    the slave to the store and refresh the UI.

    Gracefully no-ops if the port is already in use (e.g. a slave is
    running on the same machine as Control).
    """

    def __init__(self, key: bytes, udp_port: int,
                 on_register: RegisterCallback):
        self._key        = key
        self._key_lock   = threading.Lock()
        self.udp_port    = udp_port
        self.on_register = on_register
        self._stop       = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self) -> None:
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sk.settimeout(1.0)
            sk.bind(("0.0.0.0", self.udp_port))
            self._sock = sk
        except OSError as e:
            _reg_log.warning(
                "RegistrationListener could not bind to port %d: %s — "
                "auto-registration disabled.", self.udp_port, e)
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="simpit-reg-listener")
        self._thread.start()
        _reg_log.info("Registration listener started on UDP %d", self.udp_port)

    def stop(self, join_timeout: float = 2.0) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=join_timeout)

    def update_key(self, new_key: bytes) -> None:
        """Hot-swap the verification key (called when user rotates the key).

        Thread-safe: both this writer and the receiver thread hold
        ``_key_lock`` when touching ``_key``, so there is no torn read.
        """
        with self._key_lock:
            self._key = new_key

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            host = addr[0]
            with self._key_lock:
                current_key = self._key
            try:
                env = sp_security.verify_and_parse(data, current_key)
            except sp_protocol.ProtocolError:
                continue   # wrong key or malformed — ignore silently
            if env.cmd != "SLAVE_ONLINE":
                continue
            body = env.body if isinstance(env.body, dict) else {}
            name = body.get("register_name", "")
            if not name:
                continue   # no registration request — normal broadcast
            env_vars  = body.get("register_env") or {}
            udp_port  = int(body.get("udp_port", sp_protocol.DEFAULT_UDP_PORT))
            tcp_port  = int(body.get("tcp_port", sp_protocol.DEFAULT_TCP_PORT))
            _reg_log.info("Registration request from %s: name=%r", host, name)
            try:
                self.on_register(name, host, udp_port, tcp_port,
                                 {str(k): str(v) for k, v in env_vars.items()
                                  if isinstance(env_vars, dict)})
            except Exception:
                _reg_log.exception("on_register callback raised")
