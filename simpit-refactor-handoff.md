# SimPit Architecture Refactor — Handoff

Repo: github.com/mysands/simpit (work on a feature branch, e.g. `refactor/composition-root`)
Scope: `simpit_control` only unless noted. Slaves are headless UDP/TCP agents — do not add GUI deps to `simpit_slave` or `simpit_common`.

## Ground rules

- All 234+ existing tests must pass on Linux/Windows/macOS, Python 3.10–3.14. Run `pytest` before and after each phase.
- Cross-platform: pathlib + psutil only; no winreg/win32api.
- Prefer small, reviewable commits per phase. Prefer diffs/patches over wholesale file rewrites.
- Do not change the wire protocol (`simpit_common/protocol.py` envelope format, HMAC scheme, ports) except where Phase 3 explicitly says so. Control and slaves in the field must stay compatible within protocol v1.
- Update CHANGELOG.md per phase; this is a breaking-internal, non-breaking-external release train targeting v0.3.0.

## Phase 1 — Composition root + unified link factory + unified UI dispatch

The core clunk: `simpit_control/ui/app.py` (~580 lines) is a god object mixing wiring and UI, there are two parallel "make a SlaveLink" abstractions, and three different thread→UI marshalling patterns.

### 1a. Unify LinkFactory and LinkProvider
- Today: `Controller` uses `LinkFactory`/`RealLinkFactory` (`ui/controller.py`); `Poller` uses `LinkProvider`/`RealLinkProvider` (`poller.py`). Both produce a SlaveLink for a Slave.
- Replace with ONE protocol, `LinkFactory: (Slave) -> SlaveLink`, defined in `slave_link.py` (not under `ui/`). Poller and Controller both take it. Provide one `RealLinkFactory(key)` and one mock in `mock_slave.py`.
- Update tests that construct MockLinkProvider/MockLinkFactory to the single mock.

### 1b. Single UI dispatcher
- Today three patterns coexist: app.py queue.Queue + 50 ms `after()` drain loop; `ui/widgets/log_panel.py` calling `self.after(0, ...)` from worker threads; poller subscribers documented to hop threads themselves.
- Create `ui/dispatch.py` with a `Dispatcher` owning the queue + drain loop (keep the queue-drain approach — it's the safest of the three since it never calls `after()` from a worker thread). API: `dispatch(fn, *args)`, `attach(tk_root)`, `detach()`.
- Inject the Dispatcher into: poller subscription wrapper, controller callbacks, LogPanel (LogPanel.append becomes main-thread-only; callers dispatch).
- Delete the drain logic from app.py and the `after(0,...)` path from log_panel.

### 1c. Extract AppContext (composition root)
- New `simpit_control/context.py`: headless `AppContext` owning ControlPaths, Store, key loading, LinkFactory, Poller, Controller, RegistrationListener, and lifecycle (`start()`, `stop()`). No tkinter imports.
- `ui/app.py` shrinks to: construct/accept an AppContext, build widgets, connect Dispatcher, window management (`_maximize`, close protocol, first-run notice).
- Entry points (`__main__.py`, `launch_control.py`) build AppContext then hand it to App. Debug fleet mode = AppContext constructed with the mock LinkFactory.
- Tests: AppContext gets its own unit tests with no display server; existing UI tests should get simpler (construct AppContext with mocks, then App).

### 1d. Centralize key lifecycle
- Today the `b"\x00" * 32` fallback key appears in three places in app.py, and poller/controller/reg-listener each hold a private key copy, so setting a key requires restart.
- Add a `KeySession` (in `simpit_common/security.py` or context.py) holding the current key with `is_set`, `set_key(bytes)`, and change notification. Components hold the KeySession, not raw bytes; read at call time.
- Acceptance: completing SecuritySetupDialog activates poller + registration listener live, no restart. Never sign/verify with the zero key — components no-op while `is_set` is False.

## Phase 2 — Poller simplification

- Drop PING from the periodic cycle: a successful STATUS already proves reachability. Derive OFFLINE from STATUS timeout/unreachable; keep ERROR for verify-failure. PING stays in the protocol and SlaveLink for diagnostics.
- Simplify the SlaveState decision tree in `poller.py` accordingly; update transition docstring and tests.
- Verify slave-card update path diffs in place rather than destroy/recreate on each poll update (flicker risk). If it rebuilds, convert `SlaveCardWidget` to an `update(vm)` method that mutates existing widgets.

## Phase 3 — BatFile → Script rename

Mechanical but repo-wide; do as its own branch/PR after Phases 1–2 land.

- Rename: `BatFile`→`Script`, `batfiles.json`→`scripts.json`, `BatFileDialog`→`ScriptDialog`, `BatFileListWidget`→`ScriptListWidget`, `store.batfiles()`→`store.scripts()`, etc. Align with the existing `ScriptDef`/registry vocabulary.
- One-time silent migration in `Store.load()`: if `scripts.json` missing and `batfiles.json` present, read old, write new, keep the old file as `batfiles.json.bak`.
- Wire protocol: SYNC_PUSH body field names — if any say "batfiles", keep accepting the old field name when parsing (slaves in the field may be older) but emit the new one. If body fields are already script-named, no protocol change at all.
- Grep for stray "bat" strings in docs, README, tests, PyInstaller specs.

## Phase 4 — CustomTkinter migration (UI layer only)

- Add `customtkinter>=6.0.0` to simpit_control dependencies only (NOT simpit_slave/simpit_common). Pin in pyproject.toml.
- Replace tk widgets in `ui/` with CTk equivalents: CTk root, CTkFrame/CTkButton/CTkLabel/CTkEntry, CTkToplevel dialogs. `set_appearance_mode("dark")` replaces most of `theme.py`'s hand-rolled colors; keep theme.py as the single place for any remaining custom colors (state colors for slave cards).
- No CTkTreeview/CTkMenu exist — slave list is already card-based (`slave_card.py`), so build the per-slave state UI as CTkScrollableFrame of cards with colored state badges (OFFLINE/ONLINE/RUNNING/SYNCING/ERROR).
- PyInstaller: add `--collect-all customtkinter` (or spec-file equivalent datas) to `simpit-control.spec` only. Build and smoke-test the Windows exe.
- Tests: CTk widgets are canvas composites — do not assert on internal widget children or `cget("bg")`. Test view-models and controller (already tkinter-free); keep offscreen UI tests to construction + update smoke tests. Verify CTk 6.0.0 imports cleanly on Python 3.14 in CI first; if it fails, stop and report rather than working around.

## Verification checklist (each phase)

1. `pytest` green, all platforms in CI.
2. `python -m simpit_control` launches, debug fleet mode works with mock slaves.
3. Real-fleet smoke test items for Sandeep to run manually: slave auto-registration, EXEC_SCRIPT launch/quit X-Plane on CENTERLEFT/RIGHT, SYNC_PUSH, key setup flow without restart (after Phase 1d).
4. PyInstaller builds for control and slave both succeed; slave exe size unchanged (no new deps leaked into it).

## Explicitly out of scope

- Slave agent architecture, executor, inspector (unchanged).
- Script content in `simpit_control/scripts/`.
- The remote-launch session-isolation problem and X-Plane freeze-on-quit (separate investigations).
- Protocol v2, key rotation/re-pairing.
