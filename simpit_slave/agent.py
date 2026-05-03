"""
simpit_slave.agent
==================
The actual UDP + TCP server.

Architecture
------------
Two listeners run concurrently inside the same process:

* **UDP** (default port 49100) for short request/response: PING, STATUS,
  SHUTDOWN_PC. Single-threaded since handlers are cheap.
* **TCP** (default port 49101) for streamed/long-output traffic:
  EXEC_SCRIPT, SYNC_PUSH. Each connection gets a worker thread so a
  10-minute X-Plane launch doesn't block sync pushes.

Both listeners share:

* The same shared-secret key (loaded once at startup).
* The same SlavePaths layout.
* The same dispatch logic via :func:`handle_envelope` for UDP messages
  and :func:`handle_tcp_connection` for TCP. Tests can drive these
  directly without sockets.

Lifecycle
---------
``Agent.start()`` boots both listeners and the SLAVE_ONLINE broadcaster.
``Agent.stop()`` flips a sentinel and unblocks the sockets. The agent is
designed to be either a long-running service or the subject of a unit
test that boots/tears it down quickly.

Failure isolation
-----------------
A handler exception MUST NOT crash the agent. Every per-message branch
is wrapped; the offending message is logged at DEBUG and the listener
continues. Anything else would mean a single malformed packet from the
network kills the agent until it's manually restarted.
"""
from __future__ import annotations

import logging
import os
import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from typing import Any

from simpit_common import protocol as sp_protocol
from simpit_common import security as sp_security

from . import data as sp_data
from . import executor as sp_executor
from . import inspector as sp_inspector

log = logging.getLogger("simpit.slave")


# How often we (re-)broadcast SLAVE_ONLINE while running, in seconds.
# 60s is gentle enough to be invisible on the network and frequent enough
# that a Control restarting will see slaves within a minute.
ONLINE_BROADCAST_INTERVAL = 60.0


# ── Dispatch ─────────────────────────────────────────────────────────────────
def _ok(cmd: str, body: Any, key: bytes) -> bytes:
    """Build a signed response envelope as wire bytes."""
    env = sp_protocol.make_envelope(cmd, body=body)
    signed = sp_security.sign_envelope(env, key)
    return signed.to_json_bytes()


def handle_envelope(env: sp_protocol.Envelope,
                    paths: sp_data.SlavePaths,
                    key: bytes) -> bytes | None:
    """Handle a *parsed and verified* envelope (UDP path).

    Returns response bytes to send back, or None if no response is
    appropriate (e.g. SLAVE_ONLINE is one-way). All long-running or
    high-output commands are routed via TCP, never here.
    """
    if env.cmd == "PING":
        return _ok("PING_RESULT", {"ts": time.time()}, key)

    if env.cmd == "STATUS":
        body = env.body if isinstance(env.body, dict) else {}
        # Control resolves ${VAR} in probe params before sending, so we
        # don't need an env block here. The empty dict still lets the
        # slave-side _expand fallback handle any literal ${VAR} a probe
        # sneaks through (older Control, manual inspector, etc.) — those
        # just stay as-is, which surfaces visibly in the UI.
        snap = sp_inspector.snapshot(
            paths,
            probe_requests=body.get("probes"),
            env={},
        )
        return _ok("STATUS_RESULT", snap.to_dict(), key)

    if env.cmd == "SHUTDOWN_PC":
        # Caller acknowledges the shutdown request; actual power-off
        # happens via subprocess after we've sent the reply.
        ack = _ok("SHUTDOWN_PC_RESULT", {"accepted": True}, key)
        # The actual shutdown call goes through executor to keep platform
        # logic centralized. Doing it here would re-introduce the
        # subprocess-shutdown branch the original codebase had hardcoded.
        threading.Timer(0.5, _trigger_shutdown).start()
        return ack

    if env.cmd == "SLAVE_ONLINE":
        # This shouldn't arrive at a slave, but if it does, ignore.
        return None

    log.debug("unhandled UDP cmd: %s", env.cmd)
    return None


def _trigger_shutdown() -> None:
    """Issue the OS power-off command. Best-effort — never raises."""
    import subprocess

    from simpit_common import platform as sp_platform
    try:
        if sp_platform.current_os() == sp_platform.OS.WINDOWS:
            subprocess.Popen(["shutdown", "/s", "/t", "5", "/f"])
        else:
            subprocess.Popen(["shutdown", "-h", "+1"])
    except OSError as e:
        log.warning("shutdown failed: %s", e)


# ── UDP server ───────────────────────────────────────────────────────────────
class _UDPHandler(socketserver.BaseRequestHandler):
    """Per-datagram handler. The server stores `paths` and `key` on itself."""

    def handle(self) -> None:
        data, sock = self.request
        paths: sp_data.SlavePaths = self.server.paths       # type: ignore[attr-defined]
        key:   bytes              = self.server.key         # type: ignore[attr-defined]
        try:
            env = sp_security.verify_and_parse(data, key)
        except sp_protocol.ProtocolError as e:
            # Silent drop — answering would be a side-channel oracle.
            log.debug("UDP rejected from %s: %s", self.client_address, e)
            return
        try:
            reply = handle_envelope(env, paths, key)
        except Exception as e:                    # pragma: no cover
            log.exception("UDP handler crash for %s: %s", env.cmd, e)
            return
        if reply is not None:
            try:
                sock.sendto(reply, self.client_address)
            except OSError as e:
                log.warning("UDP reply send failed: %s", e)


class _ThreadingUDPServer(socketserver.ThreadingUDPServer):
    daemon_threads = True
    allow_reuse_address = True


# ── TCP server ───────────────────────────────────────────────────────────────
def _read_message(conn: socket.socket, max_bytes: int = 16 * 1024 * 1024) -> bytes:
    """Read a length-prefixed message off a TCP socket.

    Wire format on TCP: ``<4-byte big-endian length><payload bytes>``.
    JSON itself doesn't frame so we add a length prefix; otherwise we'd
    need to look for closing braces (fragile and slow).
    """
    # 4-byte length
    hdr = b""
    while len(hdr) < 4:
        chunk = conn.recv(4 - len(hdr))
        if not chunk:
            return b""
        hdr += chunk
    n = int.from_bytes(hdr, "big")
    if n <= 0 or n > max_bytes:
        return b""
    # Body
    out = bytearray()
    while len(out) < n:
        chunk = conn.recv(min(65536, n - len(out)))
        if not chunk:
            return b""
        out += chunk
    return bytes(out)


def _send_message(conn: socket.socket, payload: bytes) -> None:
    """Send a length-prefixed message."""
    conn.sendall(len(payload).to_bytes(4, "big") + payload)


def handle_tcp_connection(conn: socket.socket,
                          paths: sp_data.SlavePaths,
                          key: bytes) -> None:
    """One TCP request/response cycle.

    A connection carries exactly ONE request. The slave reads it,
    processes it (possibly emitting many response messages for
    streaming EXEC_SCRIPT), and closes. This keeps state per-connection
    trivial and matches HTTP-style mental models.
    """
    raw = _read_message(conn)
    if not raw:
        return
    try:
        env = sp_security.verify_and_parse(raw, key)
    except sp_protocol.ProtocolError as e:
        log.debug("TCP rejected: %s", e)
        return

    if env.cmd == "EXEC_SCRIPT":
        _handle_exec_script(conn, env, paths, key)
        return

    if env.cmd == "SYNC_PUSH":
        _handle_sync_push(conn, env, paths, key)
        return

    log.debug("unhandled TCP cmd: %s", env.cmd)


def _handle_exec_script(conn: socket.socket, env: sp_protocol.Envelope,
                        paths: sp_data.SlavePaths, key: bytes) -> None:
    """Run a script and send back the full result.

    Buffered mode for now (single response). Streaming mode can add
    multiple intermediate messages later — the wire framing already
    supports it because we use length-prefixed messages.
    """
    body = env.body if isinstance(env.body, dict) else {}
    script_name = body.get("script_name", "")
    env_overrides = body.get("env") or {}
    log.info("EXEC_SCRIPT %s env_keys=%s", script_name, list(env_overrides.keys()))
    extra_args = body.get("args") or []
    timeout = int(body.get("timeout_sec", sp_executor.DEFAULT_TIMEOUT_SEC))
    needs_admin = bool(body.get("needs_admin", False))

    result = sp_executor.execute(
        paths, script_name=script_name,
        env_overrides=env_overrides, extra_args=extra_args,
        timeout_sec=timeout, needs_admin=needs_admin,
    )
    reply = _ok("EXEC_SCRIPT_RESULT", result.to_dict(), key)
    try:
        _send_message(conn, reply)
    except OSError as e:
        log.warning("EXEC_SCRIPT reply send failed: %s", e)


def _handle_sync_push(conn: socket.socket, env: sp_protocol.Envelope,
                      paths: sp_data.SlavePaths, key: bytes) -> None:
    """Replace cascaded scripts with the pushed set, ack the result."""
    body = env.body if isinstance(env.body, dict) else {}
    raw_scripts = body.get("scripts") or []
    scripts = []
    for s in raw_scripts:
        if not isinstance(s, dict):
            continue
        scripts.append(sp_data.CascadedScript(
            name=str(s.get("name", "")),
            content=str(s.get("content", "")),
            os=s.get("os"),
        ))
    summary = sp_data.apply_sync_push(paths, scripts)
    reply = _ok("SYNC_ACK", summary, key)
    try:
        _send_message(conn, reply)
    except OSError as e:
        log.warning("SYNC_ACK send failed: %s", e)


# ── Online broadcaster ───────────────────────────────────────────────────────
def _broadcast_online(key: bytes, port: int, stop_event: threading.Event) -> None:
    """Periodically broadcast SLAVE_ONLINE so Control discovers us.

    Uses UDP broadcast to 255.255.255.255. Routers/firewalls may drop
    it; that's fine — the absence of a broadcast just means Control has
    to learn about the slave via explicit add. The broadcast is a
    convenience, not a requirement.
    """
    import socket as _sock
    sk = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
    sk.setsockopt(_sock.SOL_SOCKET, _sock.SO_BROADCAST, 1)
    try:
        while not stop_event.is_set():
            try:
                env = sp_protocol.make_envelope(
                    "SLAVE_ONLINE",
                    body={"hostname": _sock.gethostname()})
                wire = sp_security.sign_envelope(env, key).to_json_bytes()
                sk.sendto(wire, ("255.255.255.255", port))
            except OSError as e:
                log.debug("online broadcast failed: %s", e)
            stop_event.wait(ONLINE_BROADCAST_INTERVAL)
    finally:
        sk.close()


# ── Composite agent ──────────────────────────────────────────────────────────
@dataclass
class AgentConfig:
    """All the knobs an agent run accepts. Defaults are LAN-friendly."""
    bind_host: str = "0.0.0.0"
    udp_port:  int = sp_protocol.DEFAULT_UDP_PORT
    tcp_port:  int = sp_protocol.DEFAULT_TCP_PORT
    broadcast: bool = True


class Agent:
    """Top-level agent: owns sockets, threads, lifecycle."""

    def __init__(self, paths: sp_data.SlavePaths, key: bytes,
                 config: AgentConfig | None = None):
        self.paths  = paths
        self.key    = key
        self.config = config or AgentConfig()
        self._stop  = threading.Event()
        self._udp_server: _ThreadingUDPServer | None = None
        self._udp_thread: threading.Thread | None = None
        self._tcp_thread: threading.Thread | None = None
        self._tcp_socket: socket.socket | None = None
        self._broadcast_thread: threading.Thread | None = None

    # ── start ──
    def start(self) -> None:
        """Bind sockets and start serving. Returns immediately."""
        self.paths.ensure()

        # UDP via socketserver — handles concurrency cleanly.
        self._udp_server = _ThreadingUDPServer(
            (self.config.bind_host, self.config.udp_port), _UDPHandler)
        self._udp_server.paths = self.paths        # type: ignore[attr-defined]
        self._udp_server.key   = self.key          # type: ignore[attr-defined]
        # Mark the underlying socket non-inheritable so subprocess children
        # (e.g. X-Plane) don't inherit it. X-Plane's MicroProfile WebServer
        # otherwise crashes trying to use an inherited Winsock handle.
        try:
            os.set_inheritable(self._udp_server.socket.fileno(), False)
        except (OSError, AttributeError):
            pass
        self._udp_thread = threading.Thread(
            target=self._udp_server.serve_forever,
            kwargs={"poll_interval": 0.5},
            daemon=True, name="simpit-udp")
        self._udp_thread.start()

        # TCP managed manually so we can spawn one thread per connection
        # without the limits of socketserver's mixin shapes for our needs.
        self._tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp_socket.bind((self.config.bind_host, self.config.tcp_port))
        self._tcp_socket.listen(8)
        try:
            os.set_inheritable(self._tcp_socket.fileno(), False)
        except (OSError, AttributeError):
            pass
        self._tcp_thread = threading.Thread(
            target=self._tcp_loop, daemon=True, name="simpit-tcp")
        self._tcp_thread.start()

        if self.config.broadcast:
            self._broadcast_thread = threading.Thread(
                target=_broadcast_online,
                args=(self.key, self.config.udp_port, self._stop),
                daemon=True, name="simpit-broadcast")
            self._broadcast_thread.start()

        log.info("agent started: udp=%s tcp=%s",
                 self.config.udp_port, self.config.tcp_port)

    # ── stop ──
    def stop(self, join_timeout: float = 5.0) -> None:
        """Signal listeners to exit and wait briefly for them to finish."""
        self._stop.set()
        if self._udp_server is not None:
            self._udp_server.shutdown()
            self._udp_server.server_close()
        if self._tcp_socket is not None:
            try:
                self._tcp_socket.close()
            except OSError:
                pass
        for t in (self._udp_thread, self._tcp_thread, self._broadcast_thread):
            if t is not None:
                t.join(timeout=join_timeout)

    # ── tcp accept loop ──
    def _tcp_loop(self) -> None:
        assert self._tcp_socket is not None
        while not self._stop.is_set():
            try:
                self._tcp_socket.settimeout(0.5)
                conn, addr = self._tcp_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                # Socket closed during stop()
                return
            t = threading.Thread(
                target=self._handle_tcp_safe, args=(conn,),
                daemon=True, name="simpit-tcp-conn")
            t.start()

    def _handle_tcp_safe(self, conn: socket.socket) -> None:
        try:
            handle_tcp_connection(conn, self.paths, self.key)
        except Exception:                           # pragma: no cover
            log.exception("TCP handler crashed")
        finally:
            try:
                conn.close()
            except OSError:
                pass
