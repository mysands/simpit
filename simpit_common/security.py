"""
simpit_common.security
======================
HMAC-SHA256 signing/verification and key management.

The threat model is LAN-local: any device on the same network could
otherwise send rogue commands to a slave agent listening on UDP/TCP.
A shared secret + signed envelopes is sufficient to defeat that without
introducing a CA, TLS, or pairing dance.

Every wire message is signed via :func:`sign` over a canonical byte
representation produced by :func:`simpit_common.protocol.canonical_payload`.
Receivers reject any message whose signature mismatches OR whose timestamp
is outside the freshness window — neither alone is sufficient.

Key file format
---------------
A single line of hex (64 chars = 32 bytes of entropy from `secrets`).
Stored at the path returned by :func:`default_key_path` (per-platform).
File permissions are tightened to owner-only on POSIX; on Windows we
rely on the user profile's ACL since `chmod 0600` has no real effect.

Re-keying is intentionally simple: delete the file and regenerate. Any
slave that still holds the old key will silently reject Control's
messages until it gets the new one — exactly the behaviour we want.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

from . import protocol

# Length of the random secret in BYTES. 32 bytes = 256 bits, well past the
# practical security margin for HMAC-SHA256 and matches the digest size.
KEY_BYTES = 32

# File name used by both Control and slave when storing the shared secret.
# Same name on every platform so docs/installers can refer to it uniformly.
KEY_FILENAME = "simpit.key"


# ── Generation ───────────────────────────────────────────────────────────────
def generate_key() -> bytes:
    """Return KEY_BYTES of cryptographically random bytes.

    Uses :mod:`secrets` so the OS RNG is involved; never use `random` here.
    """
    return secrets.token_bytes(KEY_BYTES)


def key_to_text(key: bytes) -> str:
    """Render a key as a hex string suitable for display and copy/paste.

    Hex (not base64) because it's unambiguous on every keyboard and the
    space cost is negligible for 32 bytes.
    """
    if len(key) != KEY_BYTES:
        raise ValueError(f"expected {KEY_BYTES} bytes, got {len(key)}")
    return key.hex()


def key_from_text(text: str) -> bytes:
    """Inverse of :func:`key_to_text`. Tolerant of whitespace/case.

    Raises ValueError on anything that isn't a valid 32-byte hex string;
    the GUI uses the exception message directly in error popups.
    """
    cleaned = text.strip().lower().replace(" ", "")
    try:
        key = bytes.fromhex(cleaned)
    except ValueError as e:
        raise ValueError(f"not a valid hex key: {e}") from e
    if len(key) != KEY_BYTES:
        raise ValueError(f"expected {KEY_BYTES} bytes, got {len(key)}")
    return key


# ── Signing & verification ───────────────────────────────────────────────────
def sign(payload: bytes, key: bytes) -> str:
    """HMAC-SHA256 of payload, returned as lowercase hex.

    `payload` is exactly the bytes from
    :func:`simpit_common.protocol.canonical_payload` — never the full
    envelope including `sig`, never the parsed dict.
    """
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify(payload: bytes, expected_sig: str, key: bytes) -> bool:
    """Constant-time signature check.

    Returns False (not raises) on any mismatch. Callers turn that into a
    silent drop. Using :func:`hmac.compare_digest` defeats timing attacks
    that would otherwise leak prefix length.
    """
    actual = sign(payload, key)
    # compare_digest can raise on length mismatch in some old Python
    # versions; coerce both sides to str of equal-length first.
    if len(actual) != len(expected_sig):
        return False
    return hmac.compare_digest(actual, expected_sig)


# ── Envelope helpers ─────────────────────────────────────────────────────────
def sign_envelope(env: protocol.Envelope, key: bytes) -> protocol.Envelope:
    """Return a new envelope with `sig` populated; does not mutate input."""
    payload = protocol.canonical_payload(env.v, env.ts, env.cmd, env.body)
    return protocol.Envelope(
        v=env.v, ts=env.ts, cmd=env.cmd, body=env.body,
        sig=sign(payload, key),
    )


def verify_and_parse(raw: bytes, key: bytes,
                     now: float | None = None) -> protocol.Envelope:
    """Full security check on wire bytes.

    Performs (in order):
    1. Structural parse via :func:`protocol.parse_envelope`
    2. Timestamp freshness (rejects replays and severely drifted clocks)
    3. HMAC verification

    Returns the validated envelope on success. Raises a
    :class:`protocol.ProtocolError` subclass otherwise — callers should
    log at debug level and drop the message without responding (responding
    would be a side-channel oracle).
    """
    env = protocol.parse_envelope(raw)
    if not protocol.is_fresh(env.ts, now=now):
        raise protocol.ExpiredTimestamp(
            f"timestamp {env.ts} outside freshness window")
    payload = protocol.canonical_payload(env.v, env.ts, env.cmd, env.body)
    if not verify(payload, env.sig, key):
        raise protocol.ProtocolError("signature mismatch")
    return env


# ── Persistence ──────────────────────────────────────────────────────────────
def save_key(path: Path, key: bytes) -> None:
    """Write key as hex text to `path` with restrictive permissions.

    Creates parent directories if needed. On POSIX the file ends up 0600
    (owner read/write only). Windows ACL behaviour relies on the file
    inheriting profile-level protections — there is no portable way to
    reproduce 0600 on NTFS without `pywin32`, which we deliberately avoid
    to stay cross-platform with stdlib alone.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = key_to_text(key)
    # Write atomically: tmp file + rename so a crash mid-write doesn't
    # leave a half-written key that would fail to load on next start.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)


def load_key(path: Path) -> bytes:
    """Read a key written by :func:`save_key`.

    Raises FileNotFoundError if the file doesn't exist (callers can use
    that to trigger first-run pairing UX) and ValueError if the contents
    are not a valid 32-byte hex string.
    """
    text = Path(path).read_text(encoding="utf-8")
    return key_from_text(text)


def default_key_path() -> Path:
    """Best-practice on-disk location for `simpit.key` on this platform.

    POSIX:   ~/.config/simpit/simpit.key
    Windows: %APPDATA%/simpit/simpit.key
    macOS:   ~/Library/Application Support/simpit/simpit.key

    Both Control and slave use the same default; nothing prevents an
    operator from passing an explicit path via CLI/config when they
    want a different one (e.g. on a shared admin machine).
    """
    import sys
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME",
                                   Path.home() / ".config"))
    return base / "simpit" / KEY_FILENAME
