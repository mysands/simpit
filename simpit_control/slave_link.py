"""
simpit_control.slave_link
=========================
Outbound network calls from Control to one slave.

Public API is small and synchronous: each method sends one request and
returns the parsed verified reply (or raises on failure). Higher layers
(the poller, the UI) decide whether to call from a worker thread or
inline. Keeping the slave_link itself synchronous makes it trivial to
test against a mock slave bound to localhost — no event loops or
async/await ceremony required.

Errors
------
We define a small error hierarchy so callers can branch on cause without
parsing strings:

* :class:`SlaveUnreachable`  - TCP connect failed / UDP got no reply
* :class:`SlaveBadResponse`  - Reply didn't verify (wrong key, drift, tampered)
* :class:`SlaveTimeout`      - Reply didn't arrive within the deadline
* :class:`SlaveError`        - Catch-all base; the others are subclasses

The poller treats SlaveUnreachable as 'go offline'; SlaveBadResponse is
serious (key mismatch) and is surfaced to the UI as a hard error rather
than an offline indicator.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass

from simpit_common import protocol as sp_protocol
from simpit_common import security as sp_security

from . import data as sp_data


# ── Errors ───────────────────────────────────────────────────────────────────
class SlaveError(Exception):
    """Base class for any slave_link failure."""


class SlaveUnreachable(SlaveError):
    """The slave couldn't be contacted (no route, refused, no UDP reply)."""


class SlaveTimeout(SlaveError):
    """The slave was reached but didn't reply within the deadline."""


class SlaveBadResponse(SlaveError):
    """Reply was structurally invalid OR signature didn't verify.

    A signature mismatch is almost always a key-mismatch problem. We
    don't auto-rotate keys or re-pair — the user must intervene.
    """


# ── Default deadlines ───────────────────────────────────────────────────────
# Short, because Control polls every 5 s. A slave that takes >2s to reply
# is effectively dead from the user's perspective — better to mark it
# offline and try again next cycle than block the poller.
DEFAULT_UDP_TIMEOUT = 2.0

# Longer for TCP because EXEC_SCRIPT might cover a 60-second X-Plane launch.
# The deadline is overall — connect + send + wait for reply combined.
DEFAULT_TCP_TIMEOUT = 60.0


# ── Link object ──────────────────────────────────────────────────────────────
@dataclass
class SlaveLink:
    """A typed handle for talking to a single slave.

    One of these is constructed per slave; the poller holds N links for
    N slaves. The link itself is stateless — it doesn't keep persistent
    sockets — so it's safe to use from multiple threads.
    """
    slave: sp_data.Slave
    key:   bytes

    # ── PING ──
    def ping(self, timeout: float = DEFAULT_UDP_TIMEOUT) -> dict:
        """One-shot reachability check. Returns the slave's PING_RESULT body.

        Used by the poller to make the OFFLINE/ONLINE/RUNNING decision
        without touching anything heavyweight. PING is intentionally
        boring — if you need state, use :meth:`status` instead.
        """
        env = sp_protocol.make_envelope("PING")
        reply = self._udp_call(env, timeout=timeout)
        return reply.body if isinstance(reply.body, dict) else {}

    # ── STATUS ──
    def status(self, probes: list[dict] | None = None,
               timeout: float = DEFAULT_UDP_TIMEOUT) -> dict:
        """Request a STATUS snapshot.

        `probes` lets Control ask the slave to evaluate specific state
        queries (e.g. is the scenery folder enabled?). Probe params must
        already have any ``${VAR}`` references resolved — Control does
        that before sending so the slave can stay config-free. The slave
        returns always-on facts plus the requested probe outcomes.
        """
        body: dict = {}
        if probes is not None:
            body["probes"] = probes
        msg = sp_protocol.make_envelope("STATUS", body=body)
        reply = self._udp_call(msg, timeout=timeout)
        return reply.body if isinstance(reply.body, dict) else {}

    # ── SHUTDOWN_PC ──
    def shutdown_pc(self, timeout: float = DEFAULT_UDP_TIMEOUT) -> dict:
        """Ask the slave to power off. Slave acks before initiating."""
        env = sp_protocol.make_envelope("SHUTDOWN_PC")
        reply = self._udp_call(env, timeout=timeout)
        return reply.body if isinstance(reply.body, dict) else {}

    # ── EXEC_SCRIPT ──
    def exec_script(self, script_name: str,
                    env_overrides: dict[str, str] | None = None,
                    args: list[str] | None = None,
                    needs_admin: bool = False,
                    timeout_sec: int = 300,
                    deadline: float = DEFAULT_TCP_TIMEOUT) -> dict:
        """Run a script on the slave and get the full result.

        ``needs_admin``: hint to the slave that this script requires
        elevated privileges. The slave decides what to do with it
        (run-as on Windows; the result is the same to Control either
        way: a body with exit_code, stdout, stderr).

        Two timeouts: `timeout_sec` is the slave's *script* timeout (how
        long the script is allowed to run); `deadline` is *our* network
        timeout (slightly larger so we always hear back even if the slave
        had to kill a runaway). They have intentionally different units
        to make that distinction clear in call sites.
        """
        body = {
            "script_name": script_name,
            "env":         env_overrides or {},
            "args":        args or [],
            "needs_admin": needs_admin,
            "timeout_sec": timeout_sec,
        }
        env = sp_protocol.make_envelope("EXEC_SCRIPT", body=body)
        reply = self._tcp_call(env, timeout=deadline)
        return reply.body if isinstance(reply.body, dict) else {}

    # ── SYNC_PUSH ──
    def sync_push(self, scripts: list[dict],
                  deadline: float = DEFAULT_TCP_TIMEOUT) -> dict:
        """Push the cascaded script set to this slave.

        `scripts` is a list of dicts with keys: name, content, [os].
        Returns the slave's SYNC_ACK body (count, written, skipped).
        """
        env = sp_protocol.make_envelope("SYNC_PUSH",
                                          body={"scripts": scripts})
        reply = self._tcp_call(env, timeout=deadline)
        return reply.body if isinstance(reply.body, dict) else {}

    # ── Internals ──
    def _udp_call(self, env: sp_protocol.Envelope,
                  timeout: float) -> sp_protocol.Envelope:
        """Send a signed UDP envelope and parse the verified reply."""
        wire = sp_security.sign_envelope(env, self.key).to_json_bytes()
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.settimeout(timeout)
        try:
            try:
                sk.sendto(wire, (self.slave.host, self.slave.udp_port))
            except OSError as e:
                raise SlaveUnreachable(str(e)) from e
            try:
                data, _ = sk.recvfrom(65536)
            except socket.timeout as e:
                raise SlaveTimeout(f"no reply within {timeout}s") from e
            except OSError as e:
                raise SlaveUnreachable(str(e)) from e
        finally:
            sk.close()

        try:
            return sp_security.verify_and_parse(data, self.key)
        except sp_protocol.ProtocolError as e:
            raise SlaveBadResponse(str(e)) from e

    def _tcp_call(self, env: sp_protocol.Envelope,
                  timeout: float) -> sp_protocol.Envelope:
        """Send a length-prefixed signed envelope; read framed reply."""
        wire = sp_security.sign_envelope(env, self.key).to_json_bytes()
        sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sk.settimeout(timeout)
        try:
            try:
                sk.connect((self.slave.host, self.slave.tcp_port))
            except (socket.timeout, ConnectionRefusedError, OSError) as e:
                if isinstance(e, socket.timeout):
                    raise SlaveTimeout(f"connect timeout {timeout}s") from e
                raise SlaveUnreachable(str(e)) from e
            try:
                sk.sendall(len(wire).to_bytes(4, "big") + wire)
            except OSError as e:
                raise SlaveUnreachable(str(e)) from e

            # Read length prefix.
            hdr = b""
            try:
                while len(hdr) < 4:
                    chunk = sk.recv(4 - len(hdr))
                    if not chunk:
                        raise SlaveBadResponse("connection closed before length")
                    hdr += chunk
                n = int.from_bytes(hdr, "big")
                if n <= 0 or n > 64 * 1024 * 1024:
                    raise SlaveBadResponse(f"absurd reply length {n}")
                body = b""
                while len(body) < n:
                    chunk = sk.recv(min(65536, n - len(body)))
                    if not chunk:
                        raise SlaveBadResponse("short body read")
                    body += chunk
            except socket.timeout as e:
                raise SlaveTimeout(f"read timeout {timeout}s") from e
            except OSError as e:
                raise SlaveUnreachable(str(e)) from e
        finally:
            sk.close()

        try:
            return sp_security.verify_and_parse(body, self.key)
        except sp_protocol.ProtocolError as e:
            raise SlaveBadResponse(str(e)) from e
