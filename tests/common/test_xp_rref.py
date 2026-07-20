"""Tests for simpit_common.xp_rref (pure bytes, no sockets)."""
from __future__ import annotations

import struct

from simpit_common import xp_rref


def test_request_packet_layout():
    """Subscribe packets match X-Plane's documented RREF wire layout."""
    pkt = xp_rref.request_packet(5, 42, "sim/flightmodel/position/latitude")
    assert len(pkt) == struct.calcsize("<4sxii400s") == 413
    header, freq, idx, ref = struct.unpack("<4sxii400s", pkt)
    assert header == b"RREF"
    assert pkt[4] == 0                      # NUL after the 4-char header
    assert freq == 5 and idx == 42
    assert ref.rstrip(b"\x00") == b"sim/flightmodel/position/latitude"


def test_request_packet_unsubscribe_freq_zero():
    """freq=0 is the unsubscribe form — same layout, zero rate."""
    pkt = xp_rref.request_packet(0, 7, "some/ref")
    _, freq, idx, _ = struct.unpack("<4sxii400s", pkt)
    assert freq == 0 and idx == 7


def _response(*records: tuple[int, float]) -> bytes:
    """Build a fake RREF response datagram with the given records."""
    out = b"RREF\x00"
    for idx, value in records:
        out += struct.pack("<if", idx, value)
    return out


def test_decode_response_single_and_multi_record():
    """One datagram may carry any number of (idx, value) records."""
    assert xp_rref.decode_response(_response((1, 42.5))) == {1: 42.5}
    multi = xp_rref.decode_response(_response((1, 1.0), (2, -72.25), (4, 90.0)))
    assert multi == {1: 1.0, 2: -72.25, 4: 90.0}


def test_decode_response_ignores_foreign_datagrams():
    """Other X-Plane UDP traffic on the same socket decodes to empty."""
    assert xp_rref.decode_response(b"BECN\x00whatever") == {}
    assert xp_rref.decode_response(b"") == {}
    assert xp_rref.decode_response(b"RRE") == {}


def test_decode_response_ignores_trailing_partial_record():
    """A truncated final record is dropped, not misparsed."""
    data = _response((3, 128.0)) + b"\x01\x02\x03"   # 3 stray bytes
    assert xp_rref.decode_response(data) == {3: 128.0}


def test_round_trip_with_probe_convention():
    """The probe's idx-echo convention survives a build/decode round trip."""
    pkt = xp_rref.request_packet(1, 99, "x")
    _, _, idx, _ = struct.unpack("<4sxii400s", pkt)
    assert xp_rref.decode_response(_response((idx, 0.5))) == {99: 0.5}
