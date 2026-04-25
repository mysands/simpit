"""Tests for simpit_common.security — HMAC, key generation, persistence."""
import json
import os
import time

import pytest

from simpit_common import protocol as p
from simpit_common import security as s


# ── Generation ───────────────────────────────────────────────────────────────
class TestGenerateKey:
    def test_returns_correct_length(self):
        k = s.generate_key()
        assert isinstance(k, bytes)
        assert len(k) == s.KEY_BYTES

    def test_keys_are_unique(self):
        # Astronomically unlikely to collide, so any collision = bug.
        keys = {s.generate_key() for _ in range(50)}
        assert len(keys) == 50


class TestKeyText:
    def test_to_text_is_hex(self):
        k = b"\x00" * s.KEY_BYTES
        assert s.key_to_text(k) == "00" * s.KEY_BYTES

    def test_roundtrip(self):
        k = s.generate_key()
        assert s.key_from_text(s.key_to_text(k)) == k

    def test_from_text_strips_whitespace(self):
        k = s.generate_key()
        text = "  " + s.key_to_text(k) + "\n  "
        assert s.key_from_text(text) == k

    def test_from_text_case_insensitive(self):
        k = s.generate_key()
        assert s.key_from_text(s.key_to_text(k).upper()) == k

    def test_from_text_rejects_short_input(self):
        with pytest.raises(ValueError):
            s.key_from_text("aabb")

    def test_from_text_rejects_non_hex(self):
        with pytest.raises(ValueError):
            s.key_from_text("z" * 64)


# ── Sign / verify ────────────────────────────────────────────────────────────
class TestSignVerify:
    def test_sign_is_deterministic(self):
        k = s.generate_key()
        a = s.sign(b"hello", k)
        b = s.sign(b"hello", k)
        assert a == b

    def test_sign_changes_with_payload(self):
        k = s.generate_key()
        assert s.sign(b"a", k) != s.sign(b"b", k)

    def test_sign_changes_with_key(self):
        a = s.sign(b"hello", s.generate_key())
        b = s.sign(b"hello", s.generate_key())
        assert a != b

    def test_verify_accepts_correct(self):
        k = s.generate_key()
        sig = s.sign(b"hello", k)
        assert s.verify(b"hello", sig, k)

    def test_verify_rejects_tampered_payload(self):
        k = s.generate_key()
        sig = s.sign(b"hello", k)
        assert not s.verify(b"hellp", sig, k)

    def test_verify_rejects_wrong_key(self):
        k1, k2 = s.generate_key(), s.generate_key()
        sig = s.sign(b"hello", k1)
        assert not s.verify(b"hello", sig, k2)

    def test_verify_rejects_truncated_sig(self):
        k = s.generate_key()
        sig = s.sign(b"hello", k)
        assert not s.verify(b"hello", sig[:-1], k)


# ── Envelope-level helpers ───────────────────────────────────────────────────
class TestEnvelopeSigning:
    def test_sign_envelope_does_not_mutate(self):
        k = s.generate_key()
        env = p.make_envelope("PING", ts=1000.0)
        signed = s.sign_envelope(env, k)
        assert env.sig == ""           # original unchanged
        assert signed.sig != ""

    def test_verify_and_parse_accepts_signed(self):
        k = s.generate_key()
        env = p.make_envelope("PING", ts=time.time())
        signed = s.sign_envelope(env, k)
        wire = signed.to_json_bytes()
        out = s.verify_and_parse(wire, k)
        assert out.cmd == "PING"

    def test_verify_and_parse_rejects_bad_sig(self):
        k1, k2 = s.generate_key(), s.generate_key()
        env = p.make_envelope("PING", ts=time.time())
        wire = s.sign_envelope(env, k1).to_json_bytes()
        with pytest.raises(p.ProtocolError):
            s.verify_and_parse(wire, k2)

    def test_verify_and_parse_rejects_expired(self):
        k = s.generate_key()
        # ts far in the past — replay attack scenario
        env = p.make_envelope("PING", ts=time.time() - 3600)
        wire = s.sign_envelope(env, k).to_json_bytes()
        with pytest.raises(p.ExpiredTimestamp):
            s.verify_and_parse(wire, k)

    def test_verify_and_parse_rejects_tampered_body(self):
        # Sign one envelope, modify the body in transit, expect rejection.
        k = s.generate_key()
        env = p.make_envelope("STATUS", body={"x": 1}, ts=time.time())
        wire = s.sign_envelope(env, k).to_json_bytes()
        tampered_obj = json.loads(wire.decode())
        tampered_obj["body"] = {"x": 999}
        tampered = json.dumps(tampered_obj).encode()
        with pytest.raises(p.ProtocolError):
            s.verify_and_parse(tampered, k)


# ── Persistence ──────────────────────────────────────────────────────────────
class TestKeyPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "simpit.key"
        original = s.generate_key()
        s.save_key(path, original)
        loaded = s.load_key(path)
        assert loaded == original

    def test_save_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "simpit.key"
        s.save_key(path, s.generate_key())
        assert path.exists()

    def test_save_atomic_on_existing_file(self, tmp_path):
        # Re-saving over an existing key should not leave a .tmp behind.
        path = tmp_path / "simpit.key"
        s.save_key(path, s.generate_key())
        s.save_key(path, s.generate_key())
        assert not (tmp_path / "simpit.key.tmp").exists()

    @pytest.mark.skipif(os.name != "posix", reason="posix-only permission test")
    def test_save_sets_owner_only_perms_on_posix(self, tmp_path):
        path = tmp_path / "simpit.key"
        s.save_key(path, s.generate_key())
        mode = os.stat(path).st_mode & 0o777
        # Should be 0600: owner read/write, no group, no other.
        assert mode == 0o600

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            s.load_key(tmp_path / "nope.key")

    def test_load_corrupted_file_raises_value_error(self, tmp_path):
        path = tmp_path / "simpit.key"
        path.write_text("not hex at all !!!")
        with pytest.raises(ValueError):
            s.load_key(path)


# ── Default key path ─────────────────────────────────────────────────────────
class TestDefaultKeyPath:
    def test_returns_path_under_simpit_dir(self):
        path = s.default_key_path()
        assert path.name == s.KEY_FILENAME
        assert path.parent.name == "simpit"
