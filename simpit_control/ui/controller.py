"""
simpit_control.ui.controller
============================
Operations triggered by the UI: add slave, run script, sync, etc.

The controller is the single place where business logic lives between
the UI and the data/network layers. Widgets call controller methods;
the controller mutates the Store and dispatches work to the slave_link
or poller. Crucially, the controller doesn't import tkinter — every
method is callable from a unit test.

Threading
---------
Every controller method that does network I/O runs the network call on
a worker thread and reports completion via a callback. The UI registers
callbacks that hop back to the Tk main thread via ``root.after(0, ...)``.

The controller doesn't manage threads itself beyond firing them; we
don't need a queue or pool because each user click is independent and
the volumes are tiny. If a future operation needs to be cancellable
mid-flight (e.g. a long sync), we'll add a token, but YAGNI for now.

Operation results
-----------------
Each network operation returns an :class:`OpResult` describing success
or failure. The UI uses these to update the activity log and surface
errors in dialogs. Pure success/failure with a free-form message is
sufficient — anything more structured belongs in the result body
returned by the underlying call.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import data as sp_data
from .. import poller as sp_poller
from .. import slave_link as sp_link

log = logging.getLogger("simpit.ui.controller")


# ── Result shape ─────────────────────────────────────────────────────────────
@dataclass
class OpResult:
    """Generic outcome of a controller operation.

    `body` carries the parsed slave reply where applicable so callers
    can inspect details (exit_code, stdout, etc.) without making the
    callback signature operation-specific.
    """
    ok:    bool
    msg:   str
    body:  dict = field(default_factory=dict)


OpCallback = Callable[[OpResult], None]


# ── Link factory protocol ────────────────────────────────────────────────────
class LinkFactory:
    """Anything that can build a SlaveLink for a given Slave.

    In production: a thin closure over the loaded key. In tests: a
    factory that returns MockSlaveLink. This is the same idea as the
    poller's :class:`LinkProvider` — keeping it injectable lets tests
    drive the controller against mocks without monkeypatching.
    """
    def __call__(self, slave: sp_data.Slave) -> Any:
        raise NotImplementedError


@dataclass
class RealLinkFactory:
    """Default factory — wraps a static key into per-slave SlaveLinks."""
    key: bytes
    def __call__(self, slave: sp_data.Slave) -> sp_link.SlaveLink:
        return sp_link.SlaveLink(slave=slave, key=self.key)


# ── The controller ───────────────────────────────────────────────────────────
class Controller:
    """High-level operations the UI invokes.

    Constructor takes the data store, the link factory, and the poller
    (used to flip slaves into SYNCING during a sync push). Tests can
    pass any objects with the right shape.
    """

    def __init__(self,
                 store:        sp_data.Store,
                 link_factory: LinkFactory,
                 poller:       sp_poller.Poller | None = None):
        self.store        = store
        self.link_factory = link_factory
        self.poller       = poller

    # ── Slave CRUD ──
    def add_slave(self, name: str, host: str,
                  udp_port: int = 49100, tcp_port: int = 49101,
                  notes: str = "",
                  env: dict[str, str] | None = None) -> sp_data.Slave:
        """Validate inputs and add to the store. Synchronous.

        Validation lives here rather than in the data layer because the
        *user-facing* validation rules (name non-empty, port in range)
        are UX-driven; the data layer accepts what it accepts.
        """
        name = name.strip()
        host = host.strip()
        if not name:
            raise ValueError("name cannot be empty")
        if not host:
            raise ValueError("host cannot be empty")
        if not (1 <= udp_port <= 65535):
            raise ValueError(f"udp_port out of range: {udp_port}")
        if not (1 <= tcp_port <= 65535):
            raise ValueError(f"tcp_port out of range: {tcp_port}")
        return self.store.add_slave(name=name, host=host,
                                     udp_port=udp_port, tcp_port=tcp_port,
                                     notes=notes, env=env or {})

    def update_slave(self, slave: sp_data.Slave) -> None:
        if not slave.name.strip():
            raise ValueError("name cannot be empty")
        if not slave.host.strip():
            raise ValueError("host cannot be empty")
        self.store.update_slave(slave)

    def delete_slave(self, slave_id: str) -> None:
        self.store.delete_slave(slave_id)

    # ── BatFile CRUD ──
    def add_batfile(self, *, name: str, script_name: str,
                    cascade: bool = False, content: str = "",
                    local_path: str = "",
                    target_slaves: list[str] | None = None,
                    needs_admin: bool = False,
                    state_probe: dict | None = None) -> sp_data.BatFile:
        name = name.strip()
        script_name = script_name.strip()
        if not name:
            raise ValueError("name cannot be empty")
        if not script_name:
            raise ValueError("script_name cannot be empty")
        if cascade and not content.strip():
            raise ValueError("cascade=True requires non-empty content")
        if not cascade and not local_path.strip():
            raise ValueError("non-cascade scripts require a local_path")
        return self.store.add_batfile(
            name=name, script_name=script_name,
            cascade=cascade, content=content, local_path=local_path,
            target_slaves=target_slaves, needs_admin=needs_admin,
            state_probe=state_probe)

    def update_batfile(self, batfile: sp_data.BatFile) -> None:
        if not batfile.name.strip():
            raise ValueError("name cannot be empty")
        if not batfile.script_name.strip():
            raise ValueError("script_name cannot be empty")
        self.store.update_batfile(batfile)

    def delete_batfile(self, batfile_id: str) -> None:
        self.store.delete_batfile(batfile_id)

    # ── Network operations (run on worker thread) ──
    def exec_on_slave(self, slave_id: str, batfile_id: str,
                       env: dict[str, str] | None = None,
                       on_done: OpCallback | None = None) -> None:
        """Run a script on a specific slave.

        For cascade=True scripts: the slave already has the content
        because of a prior SYNC_PUSH; we just request execution by
        script_name.

        For cascade=False scripts: this method intentionally raises
        because the slave doesn't have the content. The UI should route
        non-cascade executions to local execution instead (not yet
        implemented — out of scope for the slave-controller pair).
        """
        slave = self.store.get_slave(slave_id)
        bat   = self.store.get_batfile(batfile_id)
        if slave is None:
            raise KeyError(f"unknown slave: {slave_id}")
        if bat is None:
            raise KeyError(f"unknown batfile: {batfile_id}")
        if not bat.cascade:
            raise ValueError(
                "non-cascaded scripts cannot be run on a slave")

        def _run():
            link = self.link_factory(slave)
            # slave.env provides machine-specific defaults (XPLANE_FOLDER etc.);
            # caller-supplied env overrides for one-off parameter injection.
            merged_env = {**slave.env, **(env or {})}
            try:
                body = link.exec_script(bat.script_name, env_overrides=merged_env)
                ok = bool(body.get("found")) and body.get("exit_code") == 0
                msg = (f"{bat.name} on {slave.name} -> exit "
                       f"{body.get('exit_code')}")
                result = OpResult(ok=ok, msg=msg, body=body)
            except sp_link.SlaveError as e:
                result = OpResult(ok=False,
                                   msg=f"{bat.name} on {slave.name}: {e}",
                                   body={})
            if on_done is not None:
                on_done(result)

        threading.Thread(target=_run, daemon=True,
                         name="ctrl-exec").start()

    def sync_push_to_slave(self, slave_id: str,
                            on_done: OpCallback | None = None) -> None:
        """Push the cascaded script set for one slave.

        Marks the slave SYNCING in the poller cache for the duration so
        the UI shows the blue strip without us having to track it
        separately.
        """
        slave = self.store.get_slave(slave_id)
        if slave is None:
            raise KeyError(f"unknown slave: {slave_id}")

        cascaded = self.store.cascaded_for_slave(slave_id)
        scripts = []
        for b in cascaded:
            name = b.script_name
            # Python scripts: send with .py extension so the slave stores
            # them as .py and invokes via sys.executable, not cmd.exe/sh.
            if b.content.lstrip().startswith(("#!/usr/bin/env python",
                                               "\"\"\"", "import ", "# -*-")):
                if not name.endswith(".py"):
                    name = name + ".py"
            scripts.append({"name": name, "content": b.content})

        if self.poller is not None:
            self.poller.mark_syncing(slave_id)

        def _run():
            link = self.link_factory(slave)
            try:
                body = link.sync_push(scripts)
                msg = (f"sync to {slave.name}: "
                       f"{body.get('count', 0)} scripts")
                result = OpResult(ok=True, msg=msg, body=body)
            except sp_link.SlaveError as e:
                result = OpResult(ok=False,
                                   msg=f"sync to {slave.name} failed: {e}",
                                   body={})
            if on_done is not None:
                on_done(result)

        threading.Thread(target=_run, daemon=True,
                         name="ctrl-sync").start()

    def sync_push_to_all(self, on_each: OpCallback | None = None) -> None:
        """Push to every registered slave in parallel.

        Each slave gets its own thread (small N, low cost). The
        callback fires once per slave; the UI accumulates results.
        Slaves that are offline will simply fail in their thread and
        report via the callback like any other failure.
        """
        for slave in self.store.slaves():
            self.sync_push_to_slave(slave.id, on_done=on_each)

    def shutdown_slave(self, slave_id: str,
                        on_done: OpCallback | None = None) -> None:
        """Send SHUTDOWN_PC to a single slave."""
        slave = self.store.get_slave(slave_id)
        if slave is None:
            raise KeyError(f"unknown slave: {slave_id}")

        def _run():
            link = self.link_factory(slave)
            try:
                body = link.shutdown_pc()
                result = OpResult(ok=True,
                                   msg=f"shutdown sent to {slave.name}",
                                   body=body)
            except sp_link.SlaveError as e:
                result = OpResult(ok=False,
                                   msg=f"shutdown to {slave.name}: {e}",
                                   body={})
            if on_done is not None:
                on_done(result)

        threading.Thread(target=_run, daemon=True,
                         name="ctrl-shutdown").start()
