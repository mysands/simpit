"""Ortho cache agent configuration (single loader, shared fleet-wide).

Control edits the master copy (``ortho_agent.json`` in the Control data
dir) via the Ortho Cache dialog; the per-machine ortho cache agent
(:mod:`simpit_ortho_agent`) loads its effective config through
:func:`load_effective`. Lives in ``simpit_common`` so both sides import
the one loader (moved here from ``simpit_control`` when the agent
landed). The file is JSON, not TOML, per RULES.md v0.6 §6 rule 8
(machine-distributed, script-rewritten config — the Z16/Z18 scenery
toggle rewrites ``active_zoom`` in place).

The schema mirrors the ortho agent handoff. ``rclone_cmd`` is never
edited directly: it is derived from the remote target, mount drive,
cache size/age and rc address on every save, so the mount command can
never drift from the fields shown in the UI.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA_VERSION = 1

_MOUNT_ROOT_RE = re.compile(r"^[A-Za-z]:[/\\]?$")
_RC_ADDR_RE    = re.compile(r"^[\w.\-]+:\d{1,5}$")


@dataclass
class OrthoAgentConfig:
    """Settings for the per-machine ortho cache agent.

    Attributes mirror ortho_agent.json (see the ortho agent handoff).
    `remote_target`, `cache_max_gb` and `cache_max_age` exist so the
    rclone mount command can be derived rather than hand-maintained.
    """
    enabled:                bool  = True
    master_ip:              str   = "127.0.0.1"
    xp_udp_port:            int   = 49000
    remote_target:          str   = "randhawanas:XPlane12/Custom Scenery"
    mount_root:             str   = "X:/"
    remote_rel_root:        str   = ""
    # 160 GB: 120 GB still thrashed (continuous evict+refetch pinned at
    # cap) once flights left the WUS/ZLA Z16 bbox, where hybrid falls
    # back to full-nationwide Z18. 50 GB thrashed even worse.
    cache_max_gb:           int   = 160
    cache_max_age:          str   = "8760h"
    rc_addr:                str   = "127.0.0.1:5572"
    # Where rclone keeps the on-disk VFS cache. Empty = rclone default
    # (%LOCALAPPDATA%\rclone). Point at another drive when C: is tight.
    cache_dir:              str   = ""
    supervise_mount:        bool  = True
    active_zoom:            int   = 18
    n_rings:                int   = 4
    lookahead_seconds:      float = 45.0
    poll_hz:                float = 1.0
    touch_interval_seconds: float = 60.0
    # Primer read-bandwidth cap in MB/s (0 = unthrottled). Staying ahead
    # of the aircraft needs only ~5-8 MB/s sustained; unthrottled bursts
    # (hundreds of MB at disk speed per atlas crossing) starve X-Plane's
    # own scenery reads on the same cache drive — measured as micro-
    # stutters every ~15 s in flight, 2026-07-19.
    prime_mbps:             float = 24.0
    # Aim the lookahead ring at the active GPS waypoint when one exists
    # (tier-1 flight-plan awareness); the ground track remains the
    # automatic fallback. Off = always dead-reckon along hpath.
    waypoint_lookahead:     bool  = True
    heading_offset_deg:     float = 0.0
    # Fleet distribution: the folder (UNC or local) holding the
    # authoritative copy every machine reads. Site-specific, so there
    # is NO baked-in default: empty means fleet distribution is off and
    # each machine runs purely on its local file (also avoids probing a
    # dead UNC path on every load for setups without a NAS). Set it in
    # Control's Ortho Cache dialog / the installer; keep it OUTSIDE
    # Custom Scenery so X-Plane's scenery scan never sees it (e.g.
    # \\YourNAS\XPlane12\simpit next to the scenery share).
    fleet_config_dir:       str   = ""

    # ── Derivation ───────────────────────────────────────────────────────
    def mount_drive(self) -> str:
        """Return the mount point as rclone expects it (e.g. "X:")."""
        return self.mount_root.rstrip("/\\")

    def scenery_root(self) -> Path:
        """The mounted Custom Scenery level the agent works under.

        ``remote_rel_root`` is empty when the mount points straight at
        Custom Scenery (the standard setup); otherwise it is the
        mount-relative path down to it.
        """
        root = Path(self.mount_root)
        rel = self.remote_rel_root.strip("/\\")
        return root / rel if rel else root

    def build_rclone_cmd(self) -> list[str]:
        """Derive the rclone mount command from the current settings.

        Returns:
            Argument list for launching the supervised mount. Note the
            effectively-infinite --vfs-cache-max-age: 0 would purge
            primed atlases within a minute (see handoff).
        """
        cmd = [
            "rclone", "mount", self.remote_target, self.mount_drive(),
        ]
        if self.cache_dir.strip():
            cmd += ["--cache-dir", self.cache_dir.strip()]
        return cmd + [
            "--vfs-cache-mode", "full",
            "--vfs-cache-max-size", f"{self.cache_max_gb}G",
            "--vfs-cache-max-age", self.cache_max_age,
            # Ortho DSFs open thousands of tiny .ter files: long dir/attr
            # cache + fast fingerprint avoid an SMB round trip per open.
            # Cleaner poll must stay SHORT: a 15m interval let the cache
            # overshoot the cap by 70 GB, and the recovery eviction burst
            # deleted textures X-Plane had memory-mapped (fatal
            # EXCEPTION_IN_PAGE_ERROR, 2026-07-06). 2m caps overshoot at
            # ~poll x NAS throughput and keeps evictions small and cold.
            "--vfs-cache-poll-interval", "2m",
            "--vfs-fast-fingerprint",
            "--dir-cache-time", "12h",
            "--attr-timeout", "60s",
            "--rc", "--rc-addr", self.rc_addr, "--rc-no-auth",
        ]

    # ── Validation ───────────────────────────────────────────────────────
    def validate(self) -> None:
        """Check every field; raise ValueError listing all problems.

        Raises:
            ValueError: one message per invalid field, newline-joined,
                so the dialog can show everything at once.
        """
        errors: list[str] = []
        if ":" not in self.remote_target:
            errors.append(
                "Remote target must be an rclone remote path like "
                "'randhawanas:XPlane12/Custom Scenery'.")
        if not _MOUNT_ROOT_RE.match(self.mount_root):
            errors.append(
                f"Mount root must be a drive letter like 'X:/', got "
                f"{self.mount_root!r}.")
        if not 1 <= self.cache_max_gb <= 2000:
            errors.append(f"Cache size must be 1-2000 GB, got {self.cache_max_gb}.")
        if not re.match(r"^\d+[hmd]$|^off$", self.cache_max_age):
            errors.append(
                f"Cache max age must be like '8760h' (or 'off'), got "
                f"{self.cache_max_age!r}. Never use 0 — it purges the cache.")
        if not _RC_ADDR_RE.match(self.rc_addr):
            errors.append(f"rc address must be host:port, got {self.rc_addr!r}.")
        if not self.master_ip.strip():
            errors.append("Master IP must not be empty.")
        if not 1 <= self.xp_udp_port <= 65535:
            errors.append(f"X-Plane UDP port must be 1-65535, got {self.xp_udp_port}.")
        if self.active_zoom not in (16, 18):
            errors.append(f"Active zoom must be 16 or 18, got {self.active_zoom}.")
        if not 1 <= self.n_rings <= 16:
            errors.append(f"Keep-set rings must be 1-16, got {self.n_rings}.")
        if not 0 <= self.lookahead_seconds <= 600:
            errors.append(f"Lookahead must be 0-600 s, got {self.lookahead_seconds}.")
        if not 0.1 <= self.poll_hz <= 10:
            errors.append(f"Poll rate must be 0.1-10 Hz, got {self.poll_hz}.")
        if not 10 <= self.touch_interval_seconds <= 3600:
            errors.append(
                f"Touch interval must be 10-3600 s, got "
                f"{self.touch_interval_seconds}.")
        if not 0 <= self.prime_mbps <= 1000:
            errors.append(
                f"Prime bandwidth must be 0-1000 MB/s (0 = unthrottled), "
                f"got {self.prime_mbps}.")
        if not -180 <= self.heading_offset_deg <= 180:
            errors.append(
                f"Heading offset must be -180..180°, got "
                f"{self.heading_offset_deg}.")
        if errors:
            raise ValueError("\n".join(errors))

    # ── Serialization ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """Return the on-disk shape, including the derived rclone_cmd."""
        d = asdict(self)
        d["schema_version"] = SCHEMA_VERSION
        d["rclone_cmd"] = self.build_rclone_cmd()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OrthoAgentConfig":
        """Build a config from a dict, ignoring unknown keys.

        Unknown keys are dropped and missing keys take defaults so the
        loader tolerates both older and hand-edited files. The stored
        rclone_cmd is ignored — it is always re-derived.

        Args:
            d: parsed JSON object.

        Returns:
            OrthoAgentConfig with every known field coerced to its type.
        """
        kwargs = {}
        defaults = cls()
        for name in cls.__dataclass_fields__:  # type: ignore[attr-defined]
            if name not in d:
                continue
            default = getattr(defaults, name)
            value = d[name]
            try:
                kwargs[name] = type(default)(value)
            except (TypeError, ValueError):
                kwargs[name] = default
        return cls(**kwargs)


def load_or_default(path: Path) -> OrthoAgentConfig:
    """Load the config file, or return defaults if absent/corrupt.

    Args:
        path: location of ortho_agent.json.

    Returns:
        Parsed config, or a default OrthoAgentConfig when the file does
        not exist or does not parse (corrupt files are not overwritten
        until the user saves).
    """
    try:
        return OrthoAgentConfig.from_dict(
            json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return OrthoAgentConfig()


def save(config: OrthoAgentConfig, path: Path) -> None:
    """Validate and atomically write the config to disk.

    Args:
        config: settings to persist.
        path: destination ortho_agent.json.

    Raises:
        ValueError: from validate() when any field is invalid; nothing
            is written in that case.
    """
    config.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config.to_dict(), indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


# ── Fleet distribution ───────────────────────────────────────────────────
# The authoritative copy lives on the NAS (fleet_config_dir); every
# machine keeps a local copy as bootstrap/fallback for when the share is
# unreachable. Per-machine differences (enabled, heading_offset_deg) go
# in an optional overlay named ortho_agent.<HOSTNAME>.json next to the
# base file — only the keys present in the overlay override the base.

FLEET_BASENAME = "ortho_agent.json"


def fleet_path(config: OrthoAgentConfig) -> Path | None:
    """Authoritative fleet config path, or None when distribution is off.

    An empty ``fleet_config_dir`` means "no fleet folder" — the config
    is site-specific (someone else's rig has a different NAS or none at
    all), so there is deliberately no baked-in default path.
    """
    folder = config.fleet_config_dir.strip()
    return Path(folder) / FLEET_BASENAME if folder else None


def save_fleet(config: OrthoAgentConfig, local_path: Path) -> str | None:
    """Persist the config locally and, if configured, to the fleet folder.

    The local save happens first and must succeed; the fleet save is
    best-effort so an unreachable NAS never loses the user's edits.
    With no fleet folder configured, only the local copy is written.

    Args:
        config: settings to persist.
        local_path: this machine's local ortho_agent.json.

    Returns:
        None on full success, else a one-line warning describing why the
        fleet copy was not written (the local copy is still saved).

    Raises:
        ValueError: from validate(); nothing is written in that case.
    """
    save(config, local_path)
    target = fleet_path(config)
    if target is None:
        return None
    try:
        save(config, target)
    except OSError as exc:
        return (f"Saved locally, but could not write the fleet copy to "
                f"{target}: {exc}")
    return None


def load_effective(local_path: Path, hostname: str | None = None) -> OrthoAgentConfig:
    """Resolve the config a machine should actually run with.

    Load order: local copy (bootstrap — tells us where the fleet folder
    is), then the fleet base file if reachable, then the per-machine
    overlay. Each stage only refines the previous one, so a machine
    with no NAS access still runs on its cached local copy — and a
    setup with no fleet folder configured (empty ``fleet_config_dir``)
    skips the fleet stages entirely, never touching the network.

    Args:
        local_path: this machine's local ortho_agent.json.
        hostname: overlay selector; defaults to this machine's hostname.

    Returns:
        Effective OrthoAgentConfig after fleet + overlay merging.
    """
    import socket
    merged = _read_json(local_path) or {}
    cfg = OrthoAgentConfig.from_dict(merged)
    base_path = fleet_path(cfg)
    if base_path is None:
        return cfg
    base = _read_json(base_path)
    if base is not None:
        merged.update(base)
    host = (hostname or socket.gethostname()).lower()
    overlay = _read_json(base_path.parent / f"ortho_agent.{host}.json")
    if overlay is not None:
        merged.update(overlay)
    return OrthoAgentConfig.from_dict(merged)


def _read_json(path: Path) -> dict | None:
    """Return parsed JSON from `path`, or None if absent/unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
