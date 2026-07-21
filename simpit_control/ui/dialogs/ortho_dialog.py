"""Ortho cache agent settings dialog — fleet-wide TUNING only.

Edits the priming parameters every agent shares (rings, lookahead,
touch cadence, bandwidth, zoom label, waypoint aiming) and distributes
them via the fleet folder. Machine-specific settings — rclone remote,
mount drive, cache size/folder, rc address, master endpoint — are
deliberately NOT here: the installer owns them per machine, and a
fleet base carrying them would clobber each slave's install answers
(e.g. one machine's 460G cache cap overwritten by another's default).
Per-machine exceptions go in the ``ortho_agent.<hostname>.json``
overlay next to the fleet base.
"""
from __future__ import annotations

import dataclasses
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Callable

from simpit_common import ortho_config

from .. import theme


class OrthoConfigDialog(tk.Toplevel):
    """Modal dialog for the fleet-wide ortho agent tuning."""

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
        self.var_zoom      = tk.StringVar(value=str(c.active_zoom))
        self.var_rings     = tk.StringVar(value=str(c.n_rings))
        self.var_lookahead = tk.StringVar(value=f"{c.lookahead_seconds:g}")
        self.var_poll_hz   = tk.StringVar(value=f"{c.poll_hz:g}")
        self.var_touch     = tk.StringVar(value=f"{c.touch_interval_seconds:g}")
        self.var_prime_bw  = tk.StringVar(value=f"{c.prime_mbps:g}")
        self.var_wp_look   = tk.BooleanVar(value=c.waypoint_lookahead)
        self.var_hdg_off   = tk.StringVar(value=f"{c.heading_offset_deg:g}")

        self._heading("AGENT TUNING (applies to every machine)")
        self._check("Agents enabled fleet-wide", self.var_enabled)
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
                 text=("Saved tuning reaches running agents within ~1 min. "
                       "Machine-specific settings (mount drive, cache "
                       "size/folder, rclone remote, endpoints) are set by "
                       "the installer on each machine and are not "
                       "distributed from here; use the hostname overlay "
                       "for per-machine exceptions."),
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 wraplength=580, justify="left",
                 ).pack(anchor="w", padx=20, pady=(4, 0))

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=20, pady=(12, 12), side="bottom")
        theme.make_button(btns, "Cancel", self.destroy,
                          color=theme.BTN_DANGER, width=10).pack(side="left")
        theme.make_button(btns, "Save", self._save,
                          color=theme.BTN_OK, width=10).pack(side="right")

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
                       cursor="hand2",
                       ).pack(anchor="w", padx=20, pady=(6, 0))

    # ── Behavior ─────────────────────────────────────────────────────────
    def _collect(self) -> ortho_config.OrthoAgentConfig:
        """Apply the tuning fields onto the stored config.

        Machine-specific fields ride along untouched from the local
        copy — this dialog only ever changes what the fleet base
        distributes (plus the fleet folder itself).

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

        return dataclasses.replace(
            self.cfg,
            enabled=self.var_enabled.get(),
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
