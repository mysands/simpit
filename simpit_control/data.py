"""
simpit_control.data
===================
On-disk state owned by SimPit Control.

Layout (under :func:`default_data_dir`)::

    <data_dir>/
        simpit.key       - shared HMAC secret (mode 0600 on POSIX)
        slaves.json      - list of registered slaves
        batfiles.json    - list of registered scripts + their probes

Both JSON files use a versioned envelope so future upgrades can migrate
cleanly. The on-disk shape is stable enough that hand-editing is fine
in a pinch — every field is human-meaningful and there are no opaque
identifiers we generate that the user couldn't reproduce.

Concurrency model
-----------------
A single Control process owns these files. The data layer holds an
in-memory copy and persists on every mutation via atomic temp+rename.
There's no file-watch — multiple Control instances on the same data
directory are not supported (and not useful).

Why dataclasses, not Pydantic
-----------------------------
The schemas are tiny and the validation we need is shallow. Adding
Pydantic just for this would mean another dependency for users to
install on every slave and Control machine. ``dataclasses`` + a few
explicit checks does the job and stays in stdlib.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from simpit_common import platform as sp_platform
from simpit_common import security as sp_security

SCHEMA_VERSION = 1


# ── Layout ───────────────────────────────────────────────────────────────────
def default_data_dir() -> Path:
    """Where Control stores its files by default."""
    return sp_platform.app_data_dir("simpit-control")


@dataclass(frozen=True)
class ControlPaths:
    root:           Path
    key_file:       Path
    slaves_file:    Path
    batfiles_file:  Path

    @classmethod
    def under(cls, root: Path) -> "ControlPaths":
        root = Path(root)
        return cls(
            root          = root,
            key_file      = root / sp_security.KEY_FILENAME,
            slaves_file   = root / "slaves.json",
            batfiles_file = root / "batfiles.json",
        )

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


# ── Slaves ───────────────────────────────────────────────────────────────────
@dataclass
class Slave:
    """One known slave machine.

    `id` is a Control-local opaque identifier — generated when the slave
    is added, never displayed to the user. Used to refer to a slave from
    other entities (e.g. batfile.target_slaves) without depending on
    name/host which can change.

    `name` is a human label shown in the UI ("CENTERLEFT").
    `host` is what gets passed to socket.connect — IP or DNS name.
    """
    id:        str
    name:      str
    host:      str
    udp_port:  int = 49100
    tcp_port:  int = 49101
    notes:     str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Slave":
        return cls(
            id        = str(d["id"]),
            name      = str(d["name"]),
            host      = str(d["host"]),
            udp_port  = int(d.get("udp_port", 49100)),
            tcp_port  = int(d.get("tcp_port", 49101)),
            notes     = str(d.get("notes", "")),
        )


# ── Bat files (registered scripts) ───────────────────────────────────────────
@dataclass
class BatFile:
    """One registered script.

    Cascade semantics
    -----------------
    cascade=True:   `content` is pushed to all (or selected) slaves on
                    SYNC_PUSH. The script lives on the slave when run.

    cascade=False:  `local_path` is the absolute path on Control's own
                    machine. Used for things Control runs locally (e.g.
                    "Open Synology Drive on this machine"). Slaves can
                    never see/use these.

    Targeting
    ---------
    target_slaves:  None  = applies to ALL slaves (the usual case).
                    [...] = list of slave ids that get this script.

    State probe
    -----------
    Optional probe definition that the slave inspector evaluates. The
    UI uses the result to render this script's status (e.g. ON/OFF for
    a toggle). See :mod:`simpit_common.probes`.
    """
    id:             str
    name:           str               # display label
    script_name:    str               # base name without extension
    cascade:        bool = False
    content:        str = ""          # used when cascade=True
    local_path:     str = ""          # used when cascade=False
    target_slaves:  list[str] | None = None
    needs_admin:    bool = False
    state_probe:    dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BatFile":
        targets = d.get("target_slaves")
        if targets is not None and not isinstance(targets, list):
            targets = None
        return cls(
            id            = str(d["id"]),
            name          = str(d["name"]),
            script_name   = str(d["script_name"]),
            cascade       = bool(d.get("cascade", False)),
            content       = str(d.get("content", "")),
            local_path    = str(d.get("local_path", "")),
            target_slaves = targets,
            needs_admin   = bool(d.get("needs_admin", False)),
            state_probe   = d.get("state_probe") if isinstance(d.get("state_probe"), dict) else None,
        )

    def applies_to_slave(self, slave_id: str) -> bool:
        """True if this batfile should reach the given slave on cascade."""
        if not self.cascade:
            return False
        if self.target_slaves is None:
            return True
        return slave_id in self.target_slaves


# ── Persistence: atomic JSON read/write ──────────────────────────────────────
def _read_json(path: Path, default: Any) -> Any:
    """Read a JSON file, returning `default` if missing or unreadable.

    Doesn't raise on parse errors so a damaged file at least lets the
    app start with a fresh empty state — the user can then re-register
    things rather than being locked out.
    """
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    """Atomic write: temp file + rename. Same pattern as security.save_key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False),
                   encoding="utf-8")
    tmp.replace(path)


# ── Store: in-memory cache with persistence ──────────────────────────────────
class Store:
    """Owns slaves + batfiles in memory; persists on every mutation.

    Public methods are thin and mutation-style — the UI calls
    ``store.add_slave(...)`` and the file is updated. Internally we keep
    a dict by id so lookups are O(1); on-disk we serialize as a list so
    JSON stays human-friendly.

    No locking is provided — Control is single-threaded for mutations
    (everything goes through the Tk main thread). Background threads
    (poller) only READ the store, and copies of the immutable
    dataclasses survive the dict swap safely enough.
    """

    def __init__(self, paths: ControlPaths):
        self.paths   = paths
        self._slaves:   dict[str, Slave]   = {}
        self._batfiles: dict[str, BatFile] = {}
        self.load()

    # ── Load / save ──
    def load(self) -> None:
        """Replace in-memory state from disk. Called once at startup."""
        slaves_raw = _read_json(self.paths.slaves_file,
                                default={"version": SCHEMA_VERSION,
                                         "slaves": []})
        bats_raw   = _read_json(self.paths.batfiles_file,
                                default={"version": SCHEMA_VERSION,
                                         "batfiles": []})

        self._slaves = {}
        for s in slaves_raw.get("slaves", []):
            try:
                slave = Slave.from_dict(s)
            except (KeyError, ValueError, TypeError):
                continue   # skip corrupt entries; user re-adds them
            self._slaves[slave.id] = slave

        self._batfiles = {}
        for b in bats_raw.get("batfiles", []):
            try:
                bat = BatFile.from_dict(b)
            except (KeyError, ValueError, TypeError):
                continue
            self._batfiles[bat.id] = bat

    def save(self) -> None:
        """Persist current state to disk. Idempotent."""
        self.paths.ensure()
        _write_json(self.paths.slaves_file, {
            "version": SCHEMA_VERSION,
            "slaves": [s.to_dict() for s in self._slaves.values()],
        })
        _write_json(self.paths.batfiles_file, {
            "version": SCHEMA_VERSION,
            "batfiles": [b.to_dict() for b in self._batfiles.values()],
        })

    # ── Slave CRUD ──
    def slaves(self) -> list[Slave]:
        """Stable-ordered list (by name) for UI iteration."""
        return sorted(self._slaves.values(), key=lambda s: s.name.lower())

    def get_slave(self, slave_id: str) -> Slave | None:
        return self._slaves.get(slave_id)

    def add_slave(self, name: str, host: str,
                  udp_port: int = 49100, tcp_port: int = 49101,
                  notes: str = "") -> Slave:
        """Register a new slave. Generates a fresh id."""
        slave_id = _new_id("slave")
        slave = Slave(id=slave_id, name=name, host=host,
                      udp_port=udp_port, tcp_port=tcp_port, notes=notes)
        self._slaves[slave_id] = slave
        self.save()
        return slave

    def update_slave(self, slave: Slave) -> None:
        """Replace an existing slave by id."""
        if slave.id not in self._slaves:
            raise KeyError(f"unknown slave id: {slave.id}")
        self._slaves[slave.id] = slave
        self.save()

    def delete_slave(self, slave_id: str) -> None:
        if slave_id in self._slaves:
            del self._slaves[slave_id]
            # Also strip this slave from any batfile target lists.
            for b in self._batfiles.values():
                if b.target_slaves and slave_id in b.target_slaves:
                    b.target_slaves = [s for s in b.target_slaves
                                       if s != slave_id]
            self.save()

    # ── BatFile CRUD ──
    def batfiles(self) -> list[BatFile]:
        return sorted(self._batfiles.values(), key=lambda b: b.name.lower())

    def get_batfile(self, batfile_id: str) -> BatFile | None:
        return self._batfiles.get(batfile_id)

    def add_batfile(self, **kwargs) -> BatFile:
        """Add a new bat file. Required kwargs: name, script_name."""
        bat_id = _new_id("bat")
        bat = BatFile(id=bat_id, **kwargs)
        self._batfiles[bat_id] = bat
        self.save()
        return bat

    def update_batfile(self, batfile: BatFile) -> None:
        if batfile.id not in self._batfiles:
            raise KeyError(f"unknown batfile id: {batfile.id}")
        self._batfiles[batfile.id] = batfile
        self.save()

    def delete_batfile(self, batfile_id: str) -> None:
        self._batfiles.pop(batfile_id, None)
        self.save()

    # ── Cascade helpers ──
    def cascaded_for_slave(self, slave_id: str) -> list[BatFile]:
        """Return all cascade=True batfiles that target the given slave.

        Used to construct the SYNC_PUSH payload for a specific slave.
        Order is stable so re-syncing without changes produces an
        identical wire payload, which makes 'no-op' detection trivial.
        """
        return [b for b in self.batfiles()
                if b.applies_to_slave(slave_id)]


# ── Helpers ──────────────────────────────────────────────────────────────────
def _new_id(prefix: str) -> str:
    """Short opaque id like 'slave_a1b2c3d4'.

    Random hex (not uuid) because the displayable form is shorter and
    easier to spot in logs. 32 bits of entropy is plenty for a single
    SimPit deployment with at most a handful of slaves and a few dozen
    scripts.
    """
    return f"{prefix}_{secrets.token_hex(4)}"
