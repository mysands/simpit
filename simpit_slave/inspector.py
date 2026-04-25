"""
simpit_slave.inspector
======================
Read-only state queries answered by the slave.

The inspector evaluates a list of probes (declared by Control in
``batfiles.json``) and returns the results in a structured snapshot.
Probes are pure data (see :mod:`simpit_common.probes`) so adding a new
state check requires NO changes here — Control sends a probe, the slave
evaluates it.

Snapshot also includes a few "always-on" facts the UI will always
benefit from regardless of what probes Control sends:

* ``hostname``         - so Control can sanity-check it's talking to the
                         slave it thinks it is.
* ``os``               - distinguishes Windows vs Linux fleets at a
                         glance in the UI.
* ``uptime_sec``       - useful for diagnosing 'agent crashed and
                         restarted while I was away' scenarios.
* ``version``          - simpit_slave package version, for upgrade
                         coordination.
* ``script_inventory`` - {cascaded: [...], local: [...]} so Control
                         can warn 'this button references a script that
                         isn't on this slave'.
* ``is_admin``         - whether the agent is running with elevated
                         privileges. Used for UI hints ('this script
                         requires admin and the agent isn't elevated').

Everything in this module is fast and side-effect-free — no script
execution, no network, no file writes. STATUS is the most frequently
called endpoint (every 5s per slave) so it MUST be cheap.
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from typing import Any

from simpit_common import platform as sp_platform
from simpit_common import probes as sp_probes

from . import data as sp_data

# Timestamp at which this process started; used to compute uptime_sec.
# Captured at import time so it represents the agent process lifetime,
# not the time of the first STATUS request.
_PROCESS_START = time.time()


@dataclass
class ProbeOutcome:
    """One probe evaluation result, in wire-friendly form."""
    name:   str           # the user-given probe label, e.g. "scenery"
    type:   str           # the probe type, e.g. "folder_exists"
    ok:     bool
    value:  str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type,
                "ok": self.ok, "value": self.value, "detail": self.detail}


@dataclass
class StatusSnapshot:
    """Everything an inspector returns for a STATUS request."""
    hostname:         str
    os:               str
    uptime_sec:       int
    version:          str
    is_admin:         bool
    script_inventory: dict[str, list[str]]
    probes:           list[ProbeOutcome]

    def to_dict(self) -> dict:
        return {
            "hostname":         self.hostname,
            "os":               self.os,
            "uptime_sec":       self.uptime_sec,
            "version":          self.version,
            "is_admin":         self.is_admin,
            "script_inventory": self.script_inventory,
            "probes":           [p.to_dict() for p in self.probes],
        }


def _eval_probe_request(req: dict, env: dict[str, str]) -> ProbeOutcome:
    """Translate one Control-supplied probe request into a ProbeOutcome.

    Each Control request looks like::

        {"name": "scenery_active", "type": "folder_exists",
         "params": {"path": "${XPLANE_FOLDER}/Custom Scenery"}}

    `name` is the operator's label for this probe; it survives the
    round-trip so Control's UI can map results back to the right card
    without relying on list ordering.
    """
    name = req.get("name", "(unnamed)")
    typ  = req.get("type", "")
    if not isinstance(name, str):
        name = str(name)
    if not isinstance(typ, str):
        typ = str(typ)

    # Build a probe dict shape that simpit_common.probes.evaluate accepts.
    probe_def = {"type": typ, "params": req.get("params") or {}}
    result = sp_probes.evaluate(probe_def, env=env)
    return ProbeOutcome(name=name, type=typ, ok=result.ok,
                        value=result.value, detail=result.detail)


def snapshot(
    paths: sp_data.SlavePaths,
    probe_requests: list[dict] | None = None,
    env: dict[str, str] | None = None,
    *,
    version: str = "0.1.0",
) -> StatusSnapshot:
    """Build a StatusSnapshot for the current state of this slave.

    `probe_requests` is whatever Control sent in the STATUS body's
    ``probes`` field. The slave evaluates each in order and returns
    matched outcomes — Control gets exactly the data it asked for.

    A request with no probes (None or []) is fine — the snapshot still
    contains all the always-on facts. That's how the poller's cheap
    "are you alive and what's your hostname" pings work.
    """
    outcomes: list[ProbeOutcome] = []
    if probe_requests:
        for req in probe_requests:
            if not isinstance(req, dict):
                outcomes.append(ProbeOutcome(
                    name="(invalid)", type="(invalid)",
                    ok=False, value="error",
                    detail={"error": "probe request must be an object"}))
                continue
            outcomes.append(_eval_probe_request(req, env or {}))

    return StatusSnapshot(
        hostname=socket.gethostname(),
        os=sp_platform.current_os(),
        uptime_sec=int(time.time() - _PROCESS_START),
        version=version,
        is_admin=sp_platform.is_admin(),
        script_inventory=sp_data.list_scripts(paths),
        probes=outcomes,
    )
