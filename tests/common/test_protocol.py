"""Tests for simpit_common.protocol — envelope shape, parsing, validation."""
import json
import time

import pytest

from simpit_common import protocol as p


# ── Envelope construction & serialization ────────────────────────────────────
class TestEnvelope:
    def test_make_envelope_sets_version_and_timestamp(self):
        env = p.make_envelope("PING")
        assert env.v == p.PROTOCOL_VERSION
        assert env.cmd == "PING"
        assert env.body is None
        assert env.sig == ""
        assert abs(env.ts - time.time()) < 1.0

    def test_make_envelope_rejects_unknown_command(self):
        with pytest.raises(ValueError, match="Unknown command"):
            p.make_envelope("GO_BANANAS")

    def test_make_envelope_with_explicit_timestamp(self):
        env = p.make_envelope("PING", ts=12345.0)
        assert env.ts == 12345.0

    def test_envelope_to_json_bytes_roundtrip(self):
        env = p.make_envelope("STATUS", body={"foo": "bar"}, ts=1000.0)
        wire = env.to_json_bytes()
        decoded = json.loads(wire.decode())
        assert decoded["v"] == 1
        assert decoded["cmd"] == "STATUS"
        assert decoded["body"] == {"foo": "bar"}


# ── Canonical payload ────────────────────────────────────────────────────────
class TestCanonicalPayload:
    def test_deterministic_for_same_input(self):
        a = p.canonical_payload(1, 100.0, "PING", None)
        b = p.canonical_payload(1, 100.0, "PING", None)
        assert a == b

    def test_key_order_does_not_matter(self):
        # Body with keys in different orders should produce identical bytes
        # because canonical_payload sorts keys.
        a = p.canonical_payload(1, 100.0, "STATUS", {"a": 1, "b": 2})
        b = p.canonical_payload(1, 100.0, "STATUS", {"b": 2, "a": 1})
        assert a == b

    def test_excludes_sig(self):
        # Signing must not include the sig field itself; we test the negative.
        out = p.canonical_payload(1, 100.0, "PING", None)
        assert b"sig" not in out


# ── Parse envelope ───────────────────────────────────────────────────────────
class TestParseEnvelope:
    def _wire(self, **overrides):
        msg = {"v": 1, "ts": time.time(), "cmd": "PING",
               "body": None, "sig": "x"}
        msg.update(overrides)
        return json.dumps(msg).encode()

    def test_parses_well_formed(self):
        env = p.parse_envelope(self._wire())
        assert env.cmd == "PING"

    def test_rejects_invalid_json(self):
        with pytest.raises(p.MalformedEnvelope):
            p.parse_envelope(b"not json{{{")

    def test_rejects_non_object_top_level(self):
        with pytest.raises(p.MalformedEnvelope):
            p.parse_envelope(b'["array"]')

    def test_rejects_missing_field(self):
        bad = json.dumps({"v": 1, "ts": 0.0, "cmd": "PING"}).encode()
        with pytest.raises(p.MalformedEnvelope):
            p.parse_envelope(bad)

    def test_rejects_wrong_version(self):
        with pytest.raises(p.WrongVersion):
            p.parse_envelope(self._wire(v=999))

    def test_rejects_unknown_command(self):
        with pytest.raises(p.UnknownCommand):
            p.parse_envelope(self._wire(cmd="MAKE_TEA"))

    def test_accepts_int_or_float_timestamp(self):
        # JSON spec says numbers; we store ts as float internally but the
        # wire might come from a producer that sent int. Accept both.
        env = p.parse_envelope(self._wire(ts=100))
        assert env.ts == 100.0


# ── Freshness ────────────────────────────────────────────────────────────────
class TestIsFresh:
    def test_within_window(self):
        now = 1000.0
        assert p.is_fresh(995.0, now=now)
        assert p.is_fresh(1005.0, now=now)

    def test_outside_window(self):
        now = 1000.0
        assert not p.is_fresh(900.0, now=now)
        assert not p.is_fresh(1100.0, now=now)

    def test_custom_tolerance(self):
        assert p.is_fresh(900.0, now=1000.0, tolerance=200.0)
        assert not p.is_fresh(700.0, now=1000.0, tolerance=200.0)


# ── Transport classification ─────────────────────────────────────────────────
class TestTransport:
    def test_known_commands_have_transport(self):
        # Every entry in COMMANDS must yield a valid transport.
        for cmd in p.COMMANDS:
            t = p.cmd_transport(cmd)
            assert t in (p.Transport.UDP, p.Transport.TCP)

    def test_exec_script_uses_tcp(self):
        # Critical correctness: full stdout could exceed UDP MTU, so this
        # MUST be TCP. Test guards against accidental change.
        assert p.cmd_transport("EXEC_SCRIPT") == p.Transport.TCP
        assert p.cmd_transport("SYNC_PUSH")   == p.Transport.TCP

    def test_ping_uses_udp(self):
        assert p.cmd_transport("PING") == p.Transport.UDP

    def test_unknown_command_raises(self):
        with pytest.raises(ValueError):
            p.cmd_transport("BLAH")
