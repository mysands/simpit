"""
simpit_control.ui.viewmodels
============================
Pure-logic representations of what the UI displays.

A view-model takes raw objects from the data/poller layer and produces
display-ready strings, colors, and structured info. Widgets receive a
view-model and just render it — they don't compute anything.

Why this matters
----------------
The original codebase mixed "what should this button look like?" with
"how do I draw a button?" so changing display rules meant editing
widget code, often deep in event handler closures. Here, every "should"
question lives in a view-model with a name, a docstring, and a unit
test. Widgets are dumb.

This module imports nothing from tkinter. Tests can construct
view-models with fake Slave / SlaveStatus / BatFile inputs and assert
the rendered fields without a display server.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .. import data as sp_data
from .. import poller as sp_poller
from . import theme


# ── Slave card view-model ────────────────────────────────────────────────────
@dataclass(frozen=True)
class SlaveCardVM:
    """Everything the slave card widget needs to render itself.

    Built from a Slave + SlaveStatus pair. Frozen so callers can't
    accidentally mutate it after handing it to a widget; the widget
    rebuilds when state changes.
    """
    slave_id:        str
    name:            str
    host_label:      str          # "10.0.0.5:49100"
    state_text:      str          # "RUNNING"
    state_color:     str          # hex color for the strip/dot
    state_value:     str          # raw state string (lowercase)
    last_seen_text:  str          # "30s ago" / "—"
    error_text:      str          # populated when state == ERROR
    is_offline:      bool         # convenience for greying out
    is_syncing:      bool
    probe_summary:   str          # "scenery: ON  |  hosts: blocked"
    notes:           str          # operator notes from data.Slave

    @classmethod
    def build(cls, slave: sp_data.Slave,
              status: sp_poller.SlaveStatus,
              now: float | None = None) -> "SlaveCardVM":
        """Construct from data + poller status."""
        now = now if now is not None else time.time()
        state_value = status.state.value
        return cls(
            slave_id       = slave.id,
            name           = slave.name,
            host_label     = f"{slave.host}:{slave.udp_port}",
            state_text     = theme.label_for_state(state_value),
            state_color    = theme.color_for_state(state_value),
            state_value    = state_value,
            last_seen_text = _humanize_seen(status.last_seen, now),
            error_text     = status.error,
            is_offline     = state_value == "offline",
            is_syncing     = state_value == "syncing",
            probe_summary  = _summarize_probes(status.probe_results),
            notes          = slave.notes,
        )


# ── Bat file row view-model ──────────────────────────────────────────────────
@dataclass(frozen=True)
class BatFileRowVM:
    """One row in the scripts list.

    Carries enough information for the widget to render with no data
    layer access — including the per-slave status of this script's
    state probe (when one is configured).
    """
    batfile_id:    str
    name:          str
    script_name:   str
    cascade:       bool
    needs_admin:   bool
    target_count:  str           # "all slaves" / "2 slaves"
    has_probe:     bool
    probe_status_per_slave: dict[str, str]  # slave_id -> probe value
    """For each slave id this script targets, the latest probe value
    (e.g. 'present', 'running', 'absent'). Empty if no probe configured."""

    @classmethod
    def build(cls,
              bat: sp_data.BatFile,
              slave_ids_targeted: list[str],
              probe_results_by_slave: dict[str, dict[str, str]],
              ) -> "BatFileRowVM":
        """Construct from a bat file + the targeted slave ids + probe map.

        ``probe_results_by_slave`` is the poller cache restructured as
        ``{slave_id: {probe_name: value}}``. Looking up
        ``probe_results_by_slave[s][bat.id]`` gives the value.
        """
        if bat.target_slaves is None:
            target_count = "all slaves"
        else:
            n = len(bat.target_slaves)
            target_count = f"{n} slave{'s' if n != 1 else ''}"
        per_slave: dict[str, str] = {}
        if bat.state_probe:
            for sid in slave_ids_targeted:
                value = probe_results_by_slave.get(sid, {}).get(bat.id, "")
                per_slave[sid] = value
        return cls(
            batfile_id   = bat.id,
            name         = bat.name,
            script_name  = bat.script_name,
            cascade      = bat.cascade,
            needs_admin  = bat.needs_admin,
            target_count = target_count,
            has_probe    = bool(bat.state_probe),
            probe_status_per_slave = per_slave,
        )


# ── Top-level dashboard view-model ───────────────────────────────────────────
@dataclass(frozen=True)
class DashboardVM:
    """Container view-model for the main window.

    Combines all slave cards + the batfile rows so the main window has
    a single object to bind to. Rebuilt by the controller whenever
    something changes.
    """
    slaves:   list[SlaveCardVM]
    batfiles: list[BatFileRowVM]
    has_key:  bool
    """False on first run before the user generates a key."""

    online_count:  int
    offline_count: int

    @classmethod
    def build(cls,
              store: sp_data.Store,
              statuses: dict[str, sp_poller.SlaveStatus],
              has_key: bool,
              now: float | None = None) -> "DashboardVM":
        """Aggregate the whole UI state from store + poller + key flag."""
        slave_cards = []
        online = offline = 0
        for slave in store.slaves():
            status = statuses.get(slave.id) or sp_poller.SlaveStatus(
                slave_id=slave.id)
            vm = SlaveCardVM.build(slave, status, now=now)
            slave_cards.append(vm)
            if vm.state_value == "online" or vm.state_value == "running":
                online += 1
            elif vm.state_value == "offline":
                offline += 1

        # Restructure probe results for batfile lookup.
        probes_by_slave = {sid: st.probe_results
                           for sid, st in statuses.items()}

        bat_rows = []
        all_slave_ids = [s.id for s in store.slaves()]
        for bat in store.batfiles():
            targeted = (all_slave_ids
                        if bat.target_slaves is None
                        else [sid for sid in bat.target_slaves
                              if sid in all_slave_ids])
            bat_rows.append(BatFileRowVM.build(
                bat, targeted, probes_by_slave))

        return cls(
            slaves   = slave_cards,
            batfiles = bat_rows,
            has_key  = has_key,
            online_count  = online,
            offline_count = offline,
        )


# ── Helpers ──────────────────────────────────────────────────────────────────
def _humanize_seen(ts: float, now: float) -> str:
    """Render a unix timestamp as a friendly relative string."""
    if ts <= 0:
        return "—"
    delta = max(0, now - ts)
    if delta < 5:
        return "just now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    return f"{int(delta // 3600)}h ago"


def _summarize_probes(probe_results: dict[str, str]) -> str:
    """One-line summary of probe outcomes for the slave card.

    Currently shows up to the first three values joined by `|`. The
    UI-side widget can pop a tooltip with the full list. The summary
    favours non-default values (something is 'running' / 'present')
    over absent ones — those are the ones a user wants to spot quickly.
    """
    if not probe_results:
        return ""
    # Prefer interesting values first.
    interesting = [(k, v) for k, v in probe_results.items()
                   if v not in ("absent", "not_running", "")]
    rest = [(k, v) for k, v in probe_results.items()
            if (k, v) not in interesting]
    ordered = (interesting + rest)[:3]
    return "  |  ".join(f"{k}: {v}" for k, v in ordered)
