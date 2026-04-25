# SimPit Security

This document describes the threat model, the protections SimPit
applies, and what users need to do to operate safely.

For implementation details, see
[`simpit_common.security`](../simpit_common/security.py) and
[`simpit_common.protocol`](../simpit_common/protocol.py).

---

## Threat model

SimPit is designed to operate on a **trusted LAN** — your home network,
between your simulator PCs. The threat model assumes:

* The LAN may have other devices on it (smart TV, IoT, friends' laptops)
  whose owners didn't intend any harm but whose machines might be
  compromised.
* You do **not** expose SimPit ports to the public internet. The slave
  agent listens on UDP `49100` and TCP `49101` on every interface by
  default. You can restrict that with `--bind 192.168.1.x` if you want.
* The slave runs as a regular user (not root, not Administrator) unless
  individual scripts opt into elevation.

In-scope threats:

| Threat                                              | Mitigation              |
|-----------------------------------------------------|-------------------------|
| LAN attacker sends rogue commands to a slave        | HMAC signing            |
| Captured command replayed minutes/hours later       | 30-second freshness     |
| Tampered command (changed body, original signature) | Signature covers body   |
| Crafted script name to escape `cascaded/`           | Path-traversal rejection|
| Subprocess command-injection via crafted args       | `shell=False` always    |
| Stolen key file from logs/screenshots               | Show explicitly, never auto-copy |

Out-of-scope threats:

* **Compromised Control machine.** If the box running Control is
  compromised, the attacker has the key and can issue any command. We
  don't try to defend against this — the user has bigger problems.
* **Compromised slave machine.** Same: the slave has the key and is
  running scripts. Use OS-level protections (firewall, account
  separation) to limit blast radius.
* **Internet-side attackers.** SimPit does not perform NAT traversal
  or port forwarding. Don't expose these ports to the internet.

---

## Cryptography

* **Algorithm**: HMAC-SHA256.
* **Key length**: 32 bytes (256 bits) generated via `secrets.token_bytes`.
* **Comparison**: `hmac.compare_digest` (constant-time).
* **Encoding**: hex on disk, raw bytes in memory.

The HMAC covers a *canonical* JSON form of the envelope (sorted keys,
compact separators) so the signature is reproducible across languages
and library versions.

We don't use TLS or per-message AES because the threat model doesn't
require confidentiality — slave commands are not secrets, they're
operations whose effects are visible. Authenticating *who* sent them is
the entire game.

---

## Replay protection

Every envelope has a `ts` (unix-time float). The receiver compares it
to its own clock; if the difference is more than ±30 seconds, the
message is dropped silently.

This stops:

* **Replays** of captured packets after the window closes.
* **Severe clock drift** between machines (which would also break
  replay protection if we trusted any timestamp).

This does NOT stop a fast replay within the 30-second window. If you
need stronger protection (e.g. a per-message nonce table), open an
issue with your use case.

---

## Path-traversal protection

`simpit_slave.data.find_script` rejects any script name containing:

* `/` or `\` (path separators)
* `..` or `.` exact match (parent / current directory)
* NUL byte `\x00`
* The empty string

…and only looks under `cascaded/` and `local/` inside the slave's data
directory. Even with a forged signed message containing
`script_name: "../../../etc/passwd"`, the slave returns "script not
found" and runs nothing.

This is covered by tests in
[`tests/slave/test_data.py::TestFindScript::test_blocks_path_traversal`](../tests/slave/test_data.py).

---

## Subprocess hygiene

* `subprocess.Popen(..., shell=False)` — always. We hand argv as a list,
  the OS handles quoting.
* Environment is **whitelist + caller-overrides**, never the slave's
  raw `os.environ`. The whitelist is documented in
  `simpit_slave.executor._build_env`.
* Output is truncated at 4 MiB (configurable) to bound memory.
* Buffered execution has a default 5-minute timeout; runaway scripts
  are killed.

---

## Key management

### Generation

`security.generate_key()` uses `secrets.token_bytes(32)`. This pulls
from the OS CSPRNG. Don't roll your own key generation.

### Storage

Saved as hex text via `security.save_key`:

* **POSIX**: `chmod 0600` (owner read/write only).
* **Windows**: relies on the user profile's NTFS ACL. There's no
  cross-platform way to set 0600 equivalent without `pywin32`, which
  we deliberately avoid for portability.

Default location:

* **POSIX**: `~/.config/simpit/simpit.key`
* **macOS**: `~/Library/Application Support/simpit/simpit.key`
* **Windows**: `%APPDATA%\simpit\simpit.key`

### Distribution

The key MUST end up on every slave. SimPit's distribution model is:

1. Generate the key in Control.
2. Copy it (manual paste, USB stick, password manager, whatever you
   trust) to each slave's first-run prompt.
3. Both sides save it locally.

We deliberately don't auto-distribute over the network — see the
discussion of pairing protocols vs passphrase setup in the project
history. The passphrase model is simpler, easier to debug, and easier
to document for new users.

### Rotation

To rotate:

1. In Control, open Security and click `Generate New`. Confirm.
2. Copy the new key to every slave (delete the old `simpit.key` and
   re-run the slave with `simpit-slave`, paste new key).

There is no automated migration. Slaves with the old key will report
`SlaveBadResponse` until they're updated; Control surfaces this as
**ERROR** state with a "key/format mismatch" message.

---

## Reporting security issues

**Do NOT open a public GitHub issue for security bugs.** Email the
maintainer (address in the GitHub profile) with details. We'll
coordinate a fix and a coordinated disclosure timeline.

---

## What to audit if you're contributing

If you're touching any of these, please cc a maintainer on the PR:

* `simpit_common.protocol.canonical_payload` — changing the byte
  representation breaks signatures everywhere.
* `simpit_common.security.verify_and_parse` — the trust boundary.
* `simpit_slave.data.find_script` — the path-traversal gate.
* `simpit_slave.executor._build_env` — environment whitelist.
* `simpit_slave.agent.handle_*` — anything reachable from the network.
