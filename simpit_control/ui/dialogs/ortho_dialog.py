"""Ortho cache agent settings dialog.

Edits the master copy of ``ortho_agent.json`` in the Control data dir
(see :mod:`simpit_control.ortho_config`). The rclone mount command is
shown as a live read-only preview so the user always sees exactly what
the agent will launch — it is derived from the fields, never edited.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Callable

from simpit_common import ortho_config

from .. import theme


class OrthoConfigDialog(tk.Toplevel):
    """Modal dialog for the ortho cache agent settings."""

    def __init__(self, parent: tk.Misc, config_path: Path,
                 on_save: Callable[[ortho_config.OrthoAgentConfig], None]
                 | None = None):
        super().__init__(parent)
        self.config_path = config_path
        self.on_save = on_save
        self.cfg = ortho_config.load_or_default(config_path)

        self.title("Ortho Cache Agent")
        self.configure(bg=theme.BG)
        self.resizable(False, True)
        self.transient(parent)
        self.grab_set()

        self._build()
        self.update_idletasks()
        self.geometry(f"640x{self.winfo_reqheight()}")

    # ── UI construction ──────────────────────────────────────────────────
    def _build(self) -> None:
        c = self.cfg
        self.var_enabled   = tk.BooleanVar(value=c.enabled)
        self.var_remote    = tk.StringVar(value=c.remote_target)
        self.var_mount     = tk.StringVar(value=c.mount_root)
        self.var_cache_gb  = tk.StringVar(value=str(c.cache_max_gb))
        self.var_cache_age = tk.StringVar(value=c.cache_max_age)
        self.var_rc_addr   = tk.StringVar(value=c.rc_addr)
        self.var_supervise = tk.BooleanVar(value=c.supervise_mount)
        self.var_master_ip = tk.StringVar(value=c.master_ip)
        self.var_udp_port  = tk.StringVar(value=str(c.xp_udp_port))
        self.var_zoom      = tk.StringVar(value=str(c.active_zoom))
        self.var_rings     = tk.StringVar(value=str(c.n_rings))
        self.var_lookahead = tk.StringVar(value=f"{c.lookahead_seconds:g}")
        self.var_poll_hz   = tk.StringVar(value=f"{c.poll_hz:g}")
        self.var_touch     = tk.StringVar(value=f"{c.touch_interval_seconds:g}")
        self.var_prime_bw  = tk.StringVar(value=f"{c.prime_mbps:g}")
        self.var_wp_look   = tk.BooleanVar(value=c.waypoint_lookahead)
        self.var_hdg_off   = tk.StringVar(value=f"{c.heading_offset_deg:g}")

        self._heading("NAS / MOUNT")
        self._entry_row([("RCLONE REMOTE (remote:share/path)", self.var_remote, 3)])
        self._entry_row([("MOUNT DRIVE (e.g. X:/)", self.var_mount, 1),
                         ("CACHE SIZE (GB)", self.var_cache_gb, 1),
                         ("CACHE MAX AGE", self.var_cache_age, 1)])
        self.var_cache_dir = tk.StringVar(value=c.cache_dir)
        self._entry_row([("RC ADDRESS (host:port)", self.var_rc_addr, 1),
                         ("CACHE FOLDER (blank = rclone default)",
                          self.var_cache_dir, 2)])
        self._check("Supervise mount (launch rclone if the drive is missing)",
                    self.var_supervise)

        self._heading("POSITION FEED (X-PLANE MASTER)")
        self._entry_row([("MASTER IP", self.var_master_ip, 2),
                         ("UDP PORT", self.var_udp_port, 1)])

        self._heading("AGENT PARAMETERS")
        self._check("Agent enabled", self.var_enabled)
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="x", padx=20, pady=(8, 2))
        tk.Label(row, text="ACTIVE ZOOM (scenery set)", font=theme.FONT_TINY,
                 bg=theme.BG, fg=theme.SUBTEXT).pack(side="left")
        zoom = tk.OptionMenu(row, self.var_zoom, "18", "16")
        zoom.configure(bg=theme.ENTRY_BG, fg=theme.TEXT, relief="flat",
                       highlightthickness=1,
                       highlightbackground=theme.BORDER,
                       activebackground=theme.BTN_HOV,
                       activeforeground=theme.TEXT, bd=0)
        zoom["menu"].configure(bg=theme.PANEL, fg=theme.TEXT)
        zoom.pack(side="left", padx=(8, 0))
        self._entry_row([("KEEP-SET RINGS (atlases)", self.var_rings, 1),
                         ("LOOKAHEAD (s)", self.var_lookahead, 1),
                         ("POLL RATE (Hz)", self.var_poll_hz, 1)])
        self._entry_row([("KEEP-WARM TOUCH (s)", self.var_touch, 1),
                         ("PRIME BW (MB/s, 0=off)", self.var_prime_bw, 1),
                         ("HEADING OFFSET (°, v2)", self.var_hdg_off, 1)])
        self._check("Aim lookahead at the active GPS waypoint "
                    "(falls back to ground track)", self.var_wp_look)

        self._heading("FLEET DISTRIBUTION")
        self.var_fleet_dir = tk.StringVar(value=c.fleet_config_dir)
        self._entry_row([("FLEET CONFIG FOLDER (UNC; agents read from here; "
                          "per-machine overlay: ortho_agent.<hostname>.json)",
                          self.var_fleet_dir, 1)])
        tk.Label(self,
                 text=("Saved settings reach running agents within ~1 min. "
                       "Endpoint changes (master IP, mount, cache) make each "
                       "agent restart its components automatically."),
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 wraplength=580, justify="left",
                 ).pack(anchor="w", padx=20, pady=(4, 0))

        self._heading("RCLONE MOUNT COMMAND (derived)")
        self.preview = tk.Label(self, text="", font=theme.FONT_MONO,
                                bg=theme.SECTION_BG, fg=theme.SUBTEXT,
                                justify="left", anchor="w", wraplength=580)
        self.preview.pack(fill="x", padx=20, pady=(2, 4), ipadx=8, ipady=8)

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=20, pady=(12, 12), side="bottom")
        theme.make_button(btns, "Cancel", self.destroy,
                          color=theme.BTN_DANGER, width=10).pack(side="left")
        theme.make_button(btns, "Save", self._save,
                          color=theme.BTN_OK, width=10).pack(side="right")

        # Live preview: any field change re-derives the mount command.
        for var in (self.var_remote, self.var_mount, self.var_cache_gb,
                    self.var_cache_age, self.var_rc_addr, self.var_cache_dir):
            var.trace_add("write", lambda *_: self._update_preview())
        self._update_preview()

    def _heading(self, text: str) -> None:
        tk.Label(self, text=text, font=theme.FONT_HEADING,
                 bg=theme.BG, fg=theme.ACCENT,
                 ).pack(anchor="w", padx=20, pady=(16, 2))

    def _entry_row(self, fields: list[tuple[str, tk.StringVar, int]]) -> None:
        """One row of labelled entries side by side.

        Args:
            fields: (label, var, weight) triples; weight sets relative
                column width so short numeric fields don't sprawl.
        """
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="x", padx=20, pady=(8, 2))
        for col, (label, var, weight) in enumerate(fields):
            cell = tk.Frame(row, bg=theme.BG)
            cell.grid(row=0, column=col, sticky="ew",
                      padx=(0 if col == 0 else 8, 0))
            row.columnconfigure(col, weight=weight)
            tk.Label(cell, text=label, font=theme.FONT_TINY,
                     bg=theme.BG, fg=theme.SUBTEXT).pack(anchor="w")
            tk.Entry(cell, textvariable=var, font=theme.FONT_BODY,
                     bg=theme.ENTRY_BG, fg=theme.TEXT,
                     insertbackground=theme.TEXT, relief="flat", bd=0,
                     highlightthickness=1,
                     highlightbackground=theme.BORDER,
                     highlightcolor=theme.ACCENT,
                     ).pack(fill="x", ipady=theme.TOUCH_PADY // 2)

    def _check(self, text: str, var: tk.BooleanVar) -> None:
        tk.Checkbutton(self, text=text, variable=var,
                       font=theme.FONT_SMALL, bg=theme.BG, fg=theme.TEXT,
                       selectcolor=theme.ENTRY_BG,
                       activebackground=theme.BG,
                       activeforeground=theme.TEXT,
                       ).pack(anchor="w", padx=20, pady=(6, 0))

    # ── Behavior ─────────────────────────────────────────────────────────
    def _collect(self) -> ortho_config.OrthoAgentConfig:
        """Build a config object from the fields.

        Raises:
            ValueError: when a numeric field does not parse; named per
                field so the user knows what to fix.
        """
        def num(var: tk.StringVar, label: str, conv=float):
            try:
                return conv(var.get().strip())
            except ValueError as exc:
                raise ValueError(f"{label} must be a number, got "
                                 f"{var.get()!r}.") from exc

        return ortho_config.OrthoAgentConfig(
            enabled=self.var_enabled.get(),
            master_ip=self.var_master_ip.get().strip(),
            xp_udp_port=num(self.var_udp_port, "UDP port", int),
            remote_target=self.var_remote.get().strip(),
            mount_root=self.var_mount.get().strip(),
            remote_rel_root=self.cfg.remote_rel_root,
            cache_max_gb=num(self.var_cache_gb, "Cache size", int),
            cache_max_age=self.var_cache_age.get().strip(),
            rc_addr=self.var_rc_addr.get().strip(),
            cache_dir=self.var_cache_dir.get().strip(),
            supervise_mount=self.var_supervise.get(),
            active_zoom=num(self.var_zoom, "Active zoom", int),
            n_rings=num(self.var_rings, "Keep-set rings", int),
            lookahead_seconds=num(self.var_lookahead, "Lookahead"),
            poll_hz=num(self.var_poll_hz, "Poll rate"),
            touch_interval_seconds=num(self.var_touch, "Touch interval"),
            prime_mbps=num(self.var_prime_bw, "Prime bandwidth"),
            waypoint_lookahead=self.var_wp_look.get(),
            heading_offset_deg=num(self.var_hdg_off, "Heading offset"),
            fleet_config_dir=self.var_fleet_dir.get().strip(),
        )

    def _update_preview(self) -> None:
        """Re-derive the mount command from the current field values."""
        try:
            cmd = self._collect().build_rclone_cmd()
            self.preview.config(text=" ".join(cmd), fg=theme.SUBTEXT)
        except ValueError:
            self.preview.config(text="(fix numeric fields to see the "
                                     "mount command)", fg=theme.RED)

    def _save(self) -> None:
        try:
            cfg = self._collect()
            warning = ortho_config.save_fleet(cfg, self.config_path)
        except ValueError as exc:
            messagebox.showerror("Invalid ortho settings", str(exc),
                                 parent=self)
            return
        if warning:
            # Local copy is saved; only the NAS distribution failed.
            messagebox.showwarning("Fleet copy not written", warning,
                                   parent=self)
        if self.on_save is not None:
            self.on_save(cfg)
        self.destroy()
