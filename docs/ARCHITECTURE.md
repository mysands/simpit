# SimPit Architecture

This document describes how SimPit is organized internally. If you're
writing code or trying to understand why a particular file exists, this
is the place to start.

For an end-user overview, see [the README](../README.md).

---

## Layout

```
simpit_common/              shared primitives (no GUI imports)
├── protocol.py             wire envelope, command catalogue, transport routing
├── security.py             HMAC sign/verify + key gen/persistence
├── platform.py             OS abstraction (paths, processes, scripts)
└── probes.py               extensible state-query primitives

simpit_slave/               headless agent
├── data.py                 SlavePaths, find_script (path-traversal safe)
├── executor.py             buffered + streaming script execution
├── inspector.py            STATUS snapshot builder
├── agent.py                UDP + TCP server
└── __main__.py             CLI entry, first-run key prompt

simpit_control/             GUI controller
├── data.py                 Store: slaves.json + batfiles.json
├── slave_link.py           outbound network calls (synchronous)
├── poller.py               background polling, subscriber callbacks
├── mock_slave.py           simulated slaves for tests + debug fleet
├── __main__.py             CLI entry
└── ui/                     tkinter UI
    ├── theme.py            colors, fonts, button factory
    ├── viewmodels.py       pure-logic display objects (testable)
    ├── controller.py       business logic for UI actions
    ├── app.py              Tk root, wires everything together
    ├── widgets/            slave_card, log_panel, batfile_list, tooltip
    └── dialogs/            security_setup, slave_dialog, batfile_dialog
```

---

## Layering rule

Imports flow **downward only** in this list:

```
simpit_control.ui      (tkinter)
   ↓
simpit_control core    (data, slave_link, poller, mock_slave)
   ↓
simpit_common          (protocol, security, platform, probes)
```

`simpit_slave` sits parallel to `simpit_control` core — they don't
import each other. Anything they share goes in `simpit_common`.

`simpit_common` imports nothing from `simpit_*` and nothing UI-related.
This is enforced by convention; tests catch most violations because
`simpit_common` ships without tkinter as a dependency.

---

## Wire protocol

Every message is a JSON envelope:

```json
{
  "v":    1,
  "ts":   1745432187.451,
  "cmd":  "EXEC_SCRIPT",
  "body": {...},
  "sig":  "<hex HMAC-SHA256>"
}
```

The signature covers a **canonical** byte representation of `(v, ts,
cmd, body)` produced by `protocol.canonical_payload` — keys are sorted
and separators are compact so both sides reach the same bytes
regardless of language or library.

### Transport routing

Each command declares a transport (`UDP` or `TCP`) in
`protocol.COMMANDS`:

| Command              | Transport | Why                             |
|----------------------|-----------|---------------------------------|
| `PING`               | UDP       | tiny heartbeat, fire-and-forget |
| `STATUS`             | UDP       | tiny request, small reply       |
| `SHUTDOWN_PC`        | UDP       | one ack message                 |
| `SLAVE_ONLINE`       | UDP       | broadcast announcement          |
| `EXEC_SCRIPT`        | TCP       | reply may carry full stdout     |
| `SYNC_PUSH`          | TCP       | request carries every script    |

TCP messages are length-prefixed (`<4-byte big-endian length><payload>`)
so the receiver knows how much to read. This keeps framing trivial
without leaking JSON-parser implementation details.

### Validation order

When the slave receives a message it runs (in this order):

1. **Parse envelope** (`protocol.parse_envelope`) — JSON must be valid,
   required fields present with correct types, version match, command
   in catalogue.
2. **Freshness** (`protocol.is_fresh`) — `ts` must be within 30 s of
   the receiver's clock. Replays older than that are silently dropped.
3. **Signature** (`security.verify`) — recompute the HMAC over the
   canonical bytes and compare with `hmac.compare_digest`.

Any failure → silent drop. Sending an error reply would be a side
channel oracle.

---

## Threading model

### Slave

* One UDP listener thread (via `socketserver.ThreadingUDPServer`).
* One TCP accept thread that spawns one thread per accepted connection.
* One broadcast thread that periodically emits `SLAVE_ONLINE`.

All threads are daemon threads. Stop is signalled via a
`threading.Event`; the accept loop polls every 0.5 s so shutdown is
prompt without a busy spin.

### Control

* One tkinter main thread (event loop owns all widget access).
* One poller thread (polls slaves, calls subscriber callbacks).
* One worker thread per controller operation (sync, exec, shutdown).
  These are short-lived — created per request, finished when the
  network call returns.

### Worker → main marshalling

Worker threads must NOT touch widgets directly. They push
`(callable, args)` tuples onto `App._results` (a `queue.Queue`); the
main thread runs `App._drain_results` on a 50 ms timer, pops at most
32 items, and executes them.

This is deliberately a different pattern from calling
`self.after(0, ...)` from a worker. `after` from a non-main thread is
not officially supported by tkinter and breaks completely when no
mainloop is running (e.g. unit tests). The queue pattern is correct
in both cases.

---

## Derived state

Status comes from polling — Control never trusts a saved flag.

For each registered slave, every poll cycle (default: 5 s):

1. Build a probe list from registered batfiles' `state_probe` fields.
2. Send `STATUS` to the slave with that probe list.
3. Decode the reply: each probe outcome by name, plus always-on facts
   (hostname, os, uptime, is_admin, script inventory).
4. Translate probe outcomes into a `SlaveState` (UNKNOWN / OFFLINE /
   ONLINE / RUNNING / SYNCING / ERROR).
5. Store the result in `Poller._cache`.
6. Notify subscribers IF the state actually changed.

The "only-on-change" behaviour matters: a polling cycle every 5 s with
an unchanged state should not cause UI rebuilds. The cache is the
single source of truth for what the UI shows.

---

## Probe engine

`simpit_common.probes.evaluate(probe, env)` takes a probe dict like:

```python
{"type": "folder_exists",
 "params": {"path": "${XPLANE_FOLDER}/Custom Scenery"}}
```

…substitutes `${ENV_VAR}` references from `env`, dispatches to the
registered evaluator for the type, and returns a `ProbeResult`.

To add a new probe type, **don't modify the slave or Control** — call
`probes.register(name, fn)` once at import time and any code path that
uses `evaluate()` picks up the new type. This is the architecture's
main extensibility point.

---

## Data model

### `slaves.json` (Control owns)

```json
{
  "version": 1,
  "slaves": [
    {
      "id":       "slave_a1b2c3d4",
      "name":     "CENTERLEFT",
      "host":     "10.168.168.176",
      "udp_port": 49100,
      "tcp_port": 49101,
      "notes":    ""
    }
  ]
}
```

### `batfiles.json` (Control owns)

```json
{
  "version": 1,
  "batfiles": [
    {
      "id":            "bat_e5f6a7b8",
      "name":          "Launch X-Plane",
      "script_name":   "launch_xplane",
      "cascade":       true,
      "content":       "@echo off\n...",
      "local_path":    "",
      "target_slaves": null,
      "needs_admin":   false,
      "state_probe":   {
        "type":   "process_running",
        "params": {"name": "X-Plane.exe"}
      }
    }
  ]
}
```

* `target_slaves: null` means cascade applies to **every** slave.
* `target_slaves: [...]` is a whitelist of slave ids.
* When a slave is deleted, Control automatically strips its id from
  every `target_slaves` list (see `Store.delete_slave`).

### Slave-side files

* `simpit.key`     — shared secret (mode 0600 on POSIX).
* `cascaded/`      — scripts pushed by Control. Owned by SYNC_PUSH;
  any file not in the latest push is deleted.
* `local/`         — operator-managed scripts. Control never touches
  this directory.

---

## Adding a new command

The protocol layer is intentionally small. Adding a command is:

1. Add the name to `protocol.COMMANDS` with a transport.
2. Optionally add a result name (`FOO_RESULT`) to the same table.
3. Handle it in `simpit_slave.agent.handle_envelope` (UDP) or
   `simpit_slave.agent.handle_tcp_connection` (TCP).
4. Add a method to `simpit_control.slave_link.SlaveLink` that builds
   the request and parses the reply.
5. Add tests in `tests/common/test_protocol.py`,
   `tests/slave/test_agent.py`, and
   `tests/control/test_slave_link.py`.
6. If the UI surfaces it, add a method on
   `simpit_control.ui.controller.Controller` and a button somewhere.

---

## Testing strategy

| Layer                          | Tool                        | What it proves                |
|--------------------------------|-----------------------------|-------------------------------|
| `simpit_common`                | `pytest`                    | Pure logic correctness        |
| `simpit_slave` data/exec       | `pytest`                    | OS-level behaviour            |
| `simpit_slave.agent`           | `pytest` + loopback sockets | Wire format end-to-end        |
| `simpit_control` core          | `pytest` + MockLinkProvider | Polling + business logic      |
| `simpit_control.ui` viewmodels | `pytest` (no Tk)            | Display rules                 |
| `simpit_control.ui` widgets    | `pytest` + Xvfb             | Rendering, callbacks          |
| `tests/integration/`           | `pytest`                    | Real Control ↔ real Slave     |

UI tests skip cleanly when no display is available, so the same
`pytest tests/` invocation works on a developer laptop and a headless
CI runner (CI uses `xvfb-run`).
