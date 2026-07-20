"""
simpit_common.xp_rref
=====================
Wire helpers for X-Plane's RREF UDP dataref protocol.

Background
----------
Two places used to hand-roll the same ``struct`` packing for X-Plane's
RREF protocol: the ``xplane_dataref`` probe in :mod:`simpit_common.probes`
and the live ortho verifier (``tests/live/ortho_checks.py``). The ortho
cache agent adds a third, continuously-streaming consumer, so the packet
build/decode logic lives here once and everybody imports it (per the
refactor handoff's duplicate-plumbing cleanup — do not create more
copies).

Protocol summary (X-Plane 11/12, unchanged for years):

* Subscribe: send ``RREF\\0`` + ``<freq:i32><idx:i32><dataref:400s>`` to
  X-Plane's UDP port (default 49000). ``freq`` is samples per second;
  ``0`` unsubscribes. ``idx`` is an arbitrary tag echoed back so one
  socket can multiplex many datarefs.
* Response: ``RREF\\0`` (5 bytes) followed by any number of 8-byte
  ``<idx:i32><value:f32>`` records.
* Subscriptions silently expire (sim restart, scenery reload), so
  long-lived consumers must re-send their subscriptions periodically.

Everything here is pure bytes-in/bytes-out — no sockets — so it is
unit-testable offline and safe to import from stdlib-only scripts.
"""
from __future__ import annotations

import struct

# One subscribe/unsubscribe request: "RREF\0" + freq + idx + dataref
# padded to 400 bytes. X-Plane ignores anything after the NUL terminator.
_REQUEST_STRUCT = "<4sxii400s"

# Each response record after the 5-byte header: index tag + float value.
_RECORD_STRUCT = "<if"
_RECORD_SIZE = struct.calcsize(_RECORD_STRUCT)

HEADER = b"RREF"


def request_packet(freq: int, idx: int, dataref: str) -> bytes:
    """Build one RREF subscribe/unsubscribe request packet.

    Args:
        freq: samples per second X-Plane should send; 0 unsubscribes.
        idx: caller-chosen tag echoed back in responses for this dataref.
        dataref: X-Plane dataref path (ASCII/latin-1).

    Returns:
        The 413-byte wire packet to send to X-Plane's UDP port.
    """
    return struct.pack(_REQUEST_STRUCT, HEADER, freq, idx,
                       dataref.encode("latin-1"))


def decode_response(data: bytes) -> dict[int, float]:
    """Parse an RREF response datagram into ``{idx: value}``.

    Non-RREF datagrams (other X-Plane UDP traffic on the same socket)
    decode to an empty dict rather than raising, so receive loops can
    call this on everything they get.

    Args:
        data: one received UDP datagram.

    Returns:
        Mapping of subscription tag to float value; empty if the
        datagram is not an RREF response.
    """
    if data[:4] != HEADER:
        return {}
    payload = data[5:]
    values: dict[int, float] = {}
    for off in range(0, len(payload) - _RECORD_SIZE + 1, _RECORD_SIZE):
        idx, value = struct.unpack_from(_RECORD_STRUCT, payload, off)
        values[idx] = value
    return values
