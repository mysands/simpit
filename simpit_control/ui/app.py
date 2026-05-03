"""
SimPit Control top-level application.

Wires together:
* Data store (slaves.json, batfiles.json)
* Security key (simpit.key)
* Poller (background state polling)
* Controller (user actions -> network calls)
* UI widgets (slave cards, batfile list, log panel)

Lifecycle
---------
1. Construct App with a data dir.
2. ``build_ui()`` constructs the Tk tree and starts the poller.
3. ``mainloop()`` blocks on Tk events.
4. On window close, the poller is stopped cleanly.

The App can also be constructed with a custom :class:`LinkProvider` for
the poller and matching :class:`LinkFactory` for the controller, which
is how the **debug fleet** mode works: pass a MockLinkProvider and the
whole app runs against simulated slaves with no network at all.
"""
from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from simpit_common import security as sp_security

from .. import data as sp_data
from .. import poller as sp_poller
from . import theme
from .controller import Controller, LinkFactory, RealLinkFactory
from .dialogs import BatFileDialog, SecuritySetupDialog, SlaveDialog
from .viewmodels import DashboardVM
from .widgets import BatFileListWidget, LogPanel, SlaveCardWidget

log = logging.getLogger("simpit.ui.app")

APP_TITLE = "SimPit Control"
VERSION   = "0.1.0"


class App(tk.Tk):
    """The main window."""

    # Drain interval (ms) for worker-result queue. Small enough to feel
    # instantaneous, large enough that we're not spinning needlessly.
    _DRAIN_INTERVAL_MS = 50

    def __init__(self, data_dir: Path,
                 link_factory: LinkFactory | None = None,
                 link_provider: sp_poller.LinkProvider | None = None):
        super().__init__()

        self.paths = sp_data.ControlPaths.under(data_dir)
        self.paths.ensure()
        self.store = sp_data.Store(self.paths)

        # ── Worker-result marshalling ──
        # Worker threads push (callable, args) tuples here; the main
        # thread drains the queue periodically via _drain_results.
        # This avoids calling self.after() from worker threads, which
        # is unsafe in tkinter when no mainloop is running and racy
        # even when one is.
        import queue as _queue
        self._results: _queue.Queue = _queue.Queue()

        # ── Security ──
        try:
            self.key = sp_security.load_key(self.paths.key_file)
        except (FileNotFoundError, ValueError):
            self.key = None

        # ── Wiring ──
        # If the caller supplied a factory/provider (debug fleet), use
        # those; otherwise build real ones from the loaded key.
        if link_factory is None:
            link_factory = RealLinkFactory(
                key=self.key if self.key else b"\x00" * 32)
        if link_provider is None:
            link_provider = sp_poller.RealLinkProvider(
                key=self.key if self.key else b"\x00" * 32)

        self.link_factory = link_factory
        self.poller = sp_poller.Poller(self.store, link_provider)
        self.controller = Controller(
            self.store, self.link_factory, self.poller)

        # ── Window ──
        self.title(f"{APP_TITLE} {VERSION}")
        self.configure(bg=theme.BG)
        self.minsize(720, 600)
        self.geometry("960x720")  # fallback if user un-maximizes
        # Launch maximized. tk has no portable "maximize" — Windows
        # uses state('zoomed'), Linux uses the -zoomed attribute,
        # macOS has neither and we fall back to a screen-size
        # geometry. Deferred via after() so the window has been
        # mapped first; calling state('zoomed') synchronously inside
        # __init__ is racy on some window managers.
        self.after(0, self._maximize)

        self._build_ui()

        # ── Start polling once UI is up ──
        self.poller.subscribe(self._on_poller_update)
        if self.key is not None:
            self.poller.start()

        # Begin draining the worker-result queue.
        self.after(self._DRAIN_INTERVAL_MS, self._drain_results)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Window-state helpers ──
    def _maximize(self) -> None:
        """Maximize the window in a cross-platform way."""
        # Windows
        try:
            self.state("zoomed")
            return
        except tk.TclError:
            pass
        # Linux / X11
        try:
            self.attributes("-zoomed", True)
            return
        except tk.TclError:
            pass
        # macOS and any other fallback: size to the screen.
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")

    # ── Public lifecycle helpers (entry point uses these) ──
    def maybe_show_first_run_notice(self) -> None:
        """Show the 'no key yet' welcome dialog if applicable.

        Kept out of __init__ so tests can construct an App without
        fighting a modal dialog. The CLI entry point calls this after
        constructing the App.
        """
        if self.key is None:
            self._notify_first_run()

    # ── UI construction ──
    def _build_ui(self) -> None:
        # Header
        hdr = tk.Frame(self, bg=theme.PANEL)
        hdr.pack(fill="x")
        tk.Label(hdr, text=APP_TITLE.upper(), font=theme.FONT_TITLE,
                 bg=theme.PANEL, fg=theme.ACCENT,
                 ).pack(side="left", padx=16, pady=10)
        tk.Label(hdr, text=f"v{VERSION}", font=theme.FONT_TINY,
                 bg=theme.PANEL, fg=theme.SUBTEXT,
                 ).pack(side="left", pady=10)

        # Header buttons
        theme.make_button(hdr, "+ Slave", self._add_slave,
                            color=theme.BTN_BG
                            ).pack(side="right", padx=(4, 16), pady=8)
        theme.make_button(hdr, "+ Script", self._add_batfile,
                            color=theme.BTN_BG
                            ).pack(side="right", padx=4, pady=8)
        theme.make_button(hdr, "Sync All", self._sync_all,
                            color=theme.BTN_BG
                            ).pack(side="right", padx=4, pady=8)
        theme.make_button(hdr, "🔑 Security", self._open_security,
                            color=theme.PANEL
                            ).pack(side="right", padx=4, pady=8)

        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x")

        # Body: split into slaves panel (top) and scripts panel (bottom)
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=12, pady=8)

        # Slaves area
        self.slaves_label = tk.Label(
            body, text="SLAVES", font=theme.FONT_TINY,
            bg=theme.BG, fg=theme.SUBTEXT)
        self.slaves_label.pack(anchor="w")
        self.slaves_frame = tk.Frame(body, bg=theme.BG)
        self.slaves_frame.pack(fill="x", pady=(2, 12))

        # Scripts area (scrollable)
        self.batfile_list = BatFileListWidget(
            body,
            on_run=self._run_script,
            on_edit=self._edit_batfile,
            on_delete=self._delete_batfile,
            on_add=self._add_batfile,
        )
        self.batfile_list.pack(fill="both", expand=True)

        # Footer log panel
        self.log_panel = LogPanel(self, height=6)
        self.log_panel.pack(fill="x", padx=12, pady=(0, 8))

        # Initial render
        self._refresh_dashboard()

    # ── Refresh from data ──
    def _refresh_dashboard(self) -> None:
        statuses = self.poller.all()
        dash = DashboardVM.build(self.store, statuses,
                                   has_key=self.key is not None)

        # Slaves header text
        if dash.slaves:
            self.slaves_label.config(
                text=(f"SLAVES   {dash.online_count} online   "
                      f"{dash.offline_count} offline"))
        else:
            self.slaves_label.config(
                text="SLAVES   (none yet — click + Slave to add)")

        # Slave cards (rebuild always — small N, simple)
        for w in self.slaves_frame.winfo_children():
            w.destroy()
        for vm in dash.slaves:
            card = SlaveCardWidget(
                self.slaves_frame, vm,
                on_sync=self._sync_one_slave,
                on_shutdown=self._shutdown_slave,
                on_edit=self._edit_slave,
                on_delete=self._delete_slave,
            )
            card.pack(side="left", padx=4, pady=2,
                      fill="y", anchor="n")
            card.configure(width=240)

        # Bat file list
        self.batfile_list.update_data(slaves=dash.slaves,
                                          rows=dash.batfiles)

    # ── Poller subscriber (called from poll thread) ──
    def _on_poller_update(self, snapshot: sp_poller.SlaveStatus) -> None:
        """Poller calls us from its background thread.

        We must NOT touch widgets here. Push a request to the
        thread-safe queue; the main thread's drain loop will pick it up
        and call _refresh_dashboard.
        """
        self._results.put((self._refresh_dashboard, ()))

    # ── Worker-thread marshalling ──────────────────────────────────────────
    def _from_worker(self, fn, *args) -> None:
        """Schedule `fn(*args)` to run on the main thread.

        Called by worker threads (controller callbacks, poller subscribers)
        whenever they need to update the UI. The actual call happens on
        the next drain tick.
        """
        self._results.put((fn, args))

    def _drain_results(self) -> None:
        """Main-thread loop: pull worker requests and run them.

        Called via after() so it runs on the main thread. A small batch
        cap prevents one slow request from blocking the UI if many
        results arrive at once — anything left over runs on the next
        tick.
        """
        BATCH_MAX = 32
        try:
            for _ in range(BATCH_MAX):
                fn, args = self._results.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    log.exception("worker-result handler crashed")
        except Exception:
            # Queue empty or some other transient error — fine.
            pass
        # Re-arm for the next tick. Done last so a crash above doesn't
        # silently stop draining.
        try:
            self.after(self._DRAIN_INTERVAL_MS, self._drain_results)
        except (tk.TclError, RuntimeError):
            # Window was destroyed; stop the loop.
            pass

    # ── Header actions ──
    def _open_security(self) -> None:
        SecuritySetupDialog(self, self.paths.key_file,
                              on_key_changed=self._on_key_changed)

    def _on_key_changed(self, new_key: bytes) -> None:
        self.key = new_key
        # Re-create link factory / provider with the new key.
        new_factory  = RealLinkFactory(key=new_key)
        new_provider = sp_poller.RealLinkProvider(key=new_key)
        self.controller.link_factory = new_factory
        # Stop and restart the poller so it picks up the new provider.
        self.poller.stop()
        self.poller = sp_poller.Poller(self.store, new_provider)
        self.poller.subscribe(self._on_poller_update)
        self.controller.poller = self.poller
        self.poller.start()
        self.log_panel.append("Security key updated; poller restarted.",
                                "ok")

    def _add_slave(self) -> None:
        SlaveDialog(self, self.controller, existing=None,
                     on_save=lambda s: (
                         self.log_panel.append(f"Added slave: {s.name}", "ok"),
                         self._refresh_dashboard()))

    def _add_batfile(self) -> None:
        BatFileDialog(self, self.controller, existing=None,
                       on_save=lambda b: (
                           self.log_panel.append(f"Added script: {b.name}", "ok"),
                           self._refresh_dashboard()))

    def _sync_all(self) -> None:
        self.log_panel.append("Syncing all slaves…", "accent")
        def cb(result):
            # Runs on a worker thread — marshal back to main.
            tag = "ok" if result.ok else "error"
            self._from_worker(self.log_panel.append, result.msg, tag)
            self._from_worker(self._refresh_dashboard)
        self.controller.sync_push_to_all(on_each=cb)

    # ── Per-slave actions ──
    def _sync_one_slave(self, slave_id: str) -> None:
        slave = self.store.get_slave(slave_id)
        if slave is None:
            return
        self.log_panel.append(f"Syncing {slave.name}…", "accent")
        def cb(result):
            # Worker-thread context.
            tag = "ok" if result.ok else "error"
            self._from_worker(self.log_panel.append, result.msg, tag)
            self._from_worker(self._refresh_dashboard)
        self.controller.sync_push_to_slave(slave_id, on_done=cb)

    def _shutdown_slave(self, slave_id: str) -> None:
        slave = self.store.get_slave(slave_id)
        if slave is None:
            return
        if not messagebox.askyesno(
                "Confirm shutdown",
                f"Power off {slave.name}? "
                "X-Plane and any other running programs will be terminated.",
                parent=self):
            return
        def cb(result):
            tag = "ok" if result.ok else "error"
            self._from_worker(self.log_panel.append, result.msg, tag)
        self.controller.shutdown_slave(slave_id, on_done=cb)

    def _edit_slave(self, slave_id: str) -> None:
        slave = self.store.get_slave(slave_id)
        if slave is None:
            return
        SlaveDialog(self, self.controller, existing=slave,
                     on_save=lambda s: (
                         self.log_panel.append(f"Updated {s.name}", "ok"),
                         self._refresh_dashboard()))

    def _delete_slave(self, slave_id: str) -> None:
        slave = self.store.get_slave(slave_id)
        if slave is None:
            return
        if not messagebox.askyesno(
                "Remove slave",
                f"Remove {slave.name} from SimPit Control?\n\n"
                "This does not affect the slave machine itself.",
                parent=self):
            return
        self.controller.delete_slave(slave_id)
        self.log_panel.append(f"Removed {slave.name}", "warn")
        self._refresh_dashboard()

    # ── Per-batfile actions ──
    def _run_script(self, slave_id: str, batfile_id: str) -> None:
        slave = self.store.get_slave(slave_id)
        bat   = self.store.get_batfile(batfile_id)
        if slave is None or bat is None:
            return
        self.log_panel.append(
            f"Running '{bat.name}' on {slave.name}…", "accent")
        def cb(result):
            # Worker-thread context.
            tag = "ok" if result.ok else "error"
            self._from_worker(self.log_panel.append, result.msg, tag)
            stdout = (result.body.get("stdout") or "").rstrip()
            if stdout:
                # Show first 5 lines inline; full output stays in
                # result.body for a future "view full output" dialog.
                for line in stdout.splitlines()[:5]:
                    self._from_worker(self.log_panel.append,
                                       f"   {line}", "muted")
        self.controller.exec_on_slave(slave_id, batfile_id, on_done=cb)

    def _edit_batfile(self, batfile_id: str) -> None:
        bat = self.store.get_batfile(batfile_id)
        if bat is None:
            return
        BatFileDialog(self, self.controller, existing=bat,
                       on_save=lambda b: (
                           self.log_panel.append(f"Updated {b.name}", "ok"),
                           self._refresh_dashboard()))

    def _delete_batfile(self, batfile_id: str) -> None:
        bat = self.store.get_batfile(batfile_id)
        if bat is None:
            return
        if not messagebox.askyesno(
                "Remove script",
                f"Remove script '{bat.name}'?",
                parent=self):
            return
        self.controller.delete_batfile(batfile_id)
        self.log_panel.append(f"Removed script: {bat.name}", "warn")
        self._refresh_dashboard()

    # ── First-run / lifecycle ──
    def _notify_first_run(self) -> None:
        # Defer the dialog until after main loop starts.
        self.after(300, lambda: messagebox.showinfo(
            "Welcome to SimPit Control",
            "No security key found.\n\n"
            "Click the 🔑 Security button to generate one before "
            "adding any slaves.",
            parent=self))

    def _on_close(self) -> None:
        try:
            self.poller.stop(join_timeout=1.0)
        except Exception:
            pass
        self.destroy()
