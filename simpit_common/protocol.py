"""
simpit_common.protocol
======================
Wire format for messages exchanged between SimPit Control and slave agents.

Every message is a JSON envelope:

    {
        "v":    1,                  # protocol version
        "ts":   1745432187.451,     # unix timestamp (float seconds)
        "cmd":  "EXEC_SCRIPT",      # command name
        "body": {...},              # command-specific payload (object or null)
        "sig":  "<hex>"             # HMAC-SHA256 of canonical(v|ts|cmd|body)
    }

The signature covers everything except `sig` itself, computed via
:func:`canonical_payload`. Verification rebuilds the same canonical bytes
and compares with :func:`hmac.compare_digest`.

Transport selection
-------------------
Short, fire-and-forget messages use UDP (port 49100). Anything that may
carry large payloads (script stdout/stderr, full status snapshots with
cascaded content) uses TCP (port 49101) to avoid UDP's MTU/reliability
limits.

Command catalogue
-----------------
PING               UDP   ping/heartbeat
STATUS             UDP   request inspector snapshot
STATUS_RESULT      UDP   inspector reply
EXEC_SCRIPT        TCP   run a script, stream/collect output
EXEC_SCRIPT_RESULT TCP   exit code + full stdout/stderr
SYNC_PUSH          TCP   replace cascaded scripts on slave
SYNC_ACK           TCP   slave confirms sync applied
SHUTDOWN_PC        UDP   power off slave
SLAVE_ONLINE       UDP   slave -> control broadcast on agent startup

Adding a new command is intentionally cheap: register the name in
:data:`COMMANDS` and the transport it uses; nothing else in the protocol
layer needs to change. Higher layers decide what `body` shapes mean.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
DEFAULT_UDP_PORT = 49100
DEFAULT_TCP_PORT = 49101

# Maximum size of any single UDP datagram we'll send. Practical safe ceiling
# below the 65 KB IP limit; anything larger should use TCP.
UDP_MAX_BYTES = 8192

# How far apart sender/receiver clocks may drift before a message is rejected.
# 30 seconds is generous enough to survive minor NTP drift on a LAN while
# keeping any captured packet useless within a minute.
DEFAULT_TIMESTAMP_TOLERANCE_SEC = 30.0


# ── Transport classification ─────────────────────────────────────────────────
class Transport:
    UDP = "udp"
    TCP = "tcp"


# Each command declares its preferred transport. Higher layers pick the
# socket based on this. New commands added here are immediately usable
# anywhere `cmd_transport()` is consulted.
COMMANDS: dict[str, str] = {
    "PING":               Transport.UDP,
    "PING_RESULT":        Transport.UDP,
    "STATUS":             Transport.UDP,
    "STATUS_RESULT":      Transport.UDP,
    "EXEC_SCRIPT":        Transport.TCP,
    "EXEC_SCRIPT_RESULT": Transport.TCP,
    "SYNC_PUSH":          Transport.TCP,
    "SYNC_ACK":           Transport.TCP,
    "SHUTDOWN_PC":        Transport.UDP,
    "SHUTDOWN_PC_RESULT": Transport.UDP,
    "SLAVE_ONLINE":       Transport.UDP,
}


def cmd_transport(cmd: str) -> str:
    """Return Transport.UDP or Transport.TCP for a known command.

    Raises ValueError for unknown commands rather than silently defaulting,
    so typos surface immediately rather than as mysterious network behaviour.
    """
    try:
        return COMMANDS[cmd]
    except KeyError as e:
        raise ValueError(f"Unknown command: {cmd!r}") from e


# ── Envelope ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Envelope:
    """Parsed, validated message envelope.

    Frozen so it can be safely passed around and used as a dict key/set member.
    Construction does NOT verify the signature — callers use
    :func:`verify_and_parse` for that. This split exists so unit tests and
    the mock slave can synthesize envelopes without keys when convenient.
    """
    v:    int
    ts:   float
    cmd:  str
    body: Any
    sig:  str = ""

    def to_json_bytes(self) -> bytes:
        """Serialize to UTF-8 JSON bytes ready for the wire."""
        return json.dumps(
            {"v": self.v, "ts": self.ts, "cmd": self.cmd,
             "body": self.body, "sig": self.sig},
            separators=(",", ":"), sort_keys=False,
        ).encode("utf-8")


def canonical_payload(v: int, ts: float, cmd: str, body: Any) -> bytes:
    """Bytes-to-be-signed for an envelope, excluding `sig`.

    Sort keys and use compact separators to make the byte stream
    deterministic regardless of which language/library produced it.
    Both sender and receiver MUST construct the canonical form identically
    or the HMAC will mismatch.
    """
    return json.dumps(
        {"v": v, "ts": ts, "cmd": cmd, "body": body},
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")


def make_envelope(cmd: str, body: Any = None,
                  ts: float | None = None) -> Envelope:
    """Build an unsigned envelope. Use :func:`sign_envelope` to add sig."""
    if cmd not in COMMANDS:
        raise ValueError(f"Unknown command: {cmd!r}")
    return Envelope(
        v=PROTOCOL_VERSION,
        ts=ts if ts is not None else time.time(),
        cmd=cmd,
        body=body,
        sig="",
    )


# ── Parsing & validation ─────────────────────────────────────────────────────
class ProtocolError(Exception):
    """Base class for any protocol-layer rejection.

    Slave/control should treat ProtocolError subclasses as 'silently drop
    this message'. We define subclasses so logs/tests can distinguish the
    failure reason without parsing strings.
    """


class MalformedEnvelope(ProtocolError):
    """JSON could not be parsed, required fields missing, wrong types."""


class WrongVersion(ProtocolError):
    """Protocol version mismatch — likely older client talking to newer server."""


class ExpiredTimestamp(ProtocolError):
    """ts is too far from local clock; possible replay or severe drift."""


class UnknownCommand(ProtocolError):
    """cmd not in COMMANDS table."""


def parse_envelope(raw: bytes) -> Envelope:
    """Parse and shallow-validate an envelope from wire bytes.

    Does NOT verify signature or timestamp freshness — those checks live in
    :func:`verify_and_parse` so this function is reusable in tests and tools
    that don't need full security validation (e.g. a packet inspector).
    """
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise MalformedEnvelope(f"not valid JSON: {e}") from e

    if not isinstance(obj, dict):
        raise MalformedEnvelope("top-level must be an object")

    required = {"v": int, "ts": (int, float), "cmd": str, "sig": str}
    for key, typ in required.items():
        if key not in obj:
            raise MalformedEnvelope(f"missing field: {key}")
        if not isinstance(obj[key], typ):
            raise MalformedEnvelope(f"field {key} has wrong type")

    if "body" not in obj:
        raise MalformedEnvelope("missing field: body")

    if obj["v"] != PROTOCOL_VERSION:
        raise WrongVersion(f"got v={obj['v']} expected v={PROTOCOL_VERSION}")

    if obj["cmd"] not in COMMANDS:
        raise UnknownCommand(f"unknown command: {obj['cmd']!r}")

    return Envelope(v=obj["v"], ts=float(obj["ts"]),
                    cmd=obj["cmd"], body=obj["body"], sig=obj["sig"])


def is_fresh(ts: float, now: float | None = None,
             tolerance: float = DEFAULT_TIMESTAMP_TOLERANCE_SEC) -> bool:
    """True if ts is within `tolerance` seconds of `now` (defaults to time.time())."""
    if now is None:
        now = time.time()
    return abs(now - ts) <= tolerance
