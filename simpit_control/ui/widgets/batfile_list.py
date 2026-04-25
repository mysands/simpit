"""
Scripts list panel.

Shows every registered batfile as a row with run/edit/delete actions
plus a per-slave probe status grid (so the user can see at a glance
which slaves currently have scenery enabled, which have updates blocked,
etc.).

Like the slave card, this widget binds to view-models and re-renders
when given a fresh list. No internal state.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from .. import theme
from ..viewmodels import BatFileRowVM, SlaveCardVM
from .tooltip import Tooltip


class BatFileListWidget(tk.Frame):
    """List of registered scripts with action buttons."""

    def __init__(self, parent: tk.Widget,
                 slaves: list[SlaveCardVM] | None = None,
                 rows: list[BatFileRowVM] | None = None,
                 on_run:    Callable[[str, str], None] | None = None,
                 on_edit:   Callable[[str], None] | None = None,
                 on_delete: Callable[[str], None] | None = None,
                 on_add:    Callable[[], None] | None = None):
        super().__init__(parent, bg=theme.BG)
        self._slaves = slaves or []
        self._rows = rows or []
        self._on_run    = on_run
        self._on_edit   = on_edit
        self._on_delete = on_delete
        self._on_add    = on_add
        self._build()

    # ── Public ──
    def update_data(self, slaves: list[SlaveCardVM],
                     rows: list[BatFileRowVM]) -> None:
        """Re-render with a new dashboard slice."""
        self._slaves = slaves
        self._rows = rows
        for w in self.winfo_children():
            w.destroy()
        self._build()

    # ── Build ──
    def _build(self) -> None:
        # Header bar
        hdr = tk.Frame(self, bg=theme.BG)
        hdr.pack(fill="x", pady=(0, 4))
        tk.Label(hdr, text="SCRIPTS", font=theme.FONT_TINY,
                 bg=theme.BG, fg=theme.SUBTEXT).pack(side="left")
        if self._on_add is not None:
            tk.Button(hdr, text="+ Add Script", font=theme.FONT_SMALL,
                      bg=theme.BTN_BG, fg=theme.TEXT, relief="flat",
                      bd=0, cursor="hand2", padx=10, pady=4,
                      command=self._on_add).pack(side="right")

        if not self._rows:
            tk.Label(self,
                     text="No scripts registered. Click + Add Script to begin.",
                     font=theme.FONT_BODY, bg=theme.BG, fg=theme.SUBTEXT,
                     pady=20).pack()
            return

        # One row per batfile
        for row in self._rows:
            self._draw_row(row)

    def _draw_row(self, row: BatFileRowVM) -> None:
        frame = tk.Frame(self, bg=theme.SECTION_BG, bd=0,
                          highlightthickness=0)
        frame.pack(fill="x", pady=(0, 2), padx=2)

        # Top line: name + cascade indicator + admin tag
        top = tk.Frame(frame, bg=theme.SECTION_BG)
        top.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(top, text=row.name, font=theme.FONT_BODY_BOLD,
                 bg=theme.SECTION_BG, fg=theme.TEXT).pack(side="left")
        if row.cascade:
            tag = tk.Label(top, text="📡 cascade", font=theme.FONT_TINY,
                            bg=theme.SECTION_BG, fg=theme.ACCENT)
            tag.pack(side="left", padx=(8, 0))
            Tooltip(tag, "Pushed to slaves on sync")
        if row.needs_admin:
            tk.Label(top, text="⚠ admin", font=theme.FONT_TINY,
                     bg=theme.SECTION_BG, fg=theme.AMBER,
                     ).pack(side="left", padx=(8, 0))

        # Action buttons on the right
        actions = tk.Frame(top, bg=theme.SECTION_BG)
        actions.pack(side="right")
        if self._on_edit:
            tk.Button(actions, text="✎", font=theme.FONT_SMALL,
                      bg=theme.PANEL, fg=theme.TEXT, relief="flat",
                      bd=0, cursor="hand2", padx=8, pady=2,
                      command=lambda r=row: self._on_edit(r.batfile_id)
                      ).pack(side="left", padx=(0, 2))
        if self._on_delete:
            tk.Button(actions, text="✕", font=theme.FONT_SMALL,
                      bg=theme.BTN_DANGER, fg=theme.TEXT, relief="flat",
                      bd=0, cursor="hand2", padx=8, pady=2,
                      command=lambda r=row: self._on_delete(r.batfile_id)
                      ).pack(side="left")

        # Subline: script_name + target count
        sub = f"{row.script_name}  •  {row.target_count}"
        tk.Label(frame, text=sub, font=theme.FONT_TINY,
                 bg=theme.SECTION_BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=10)

        # Per-slave run buttons (cascade only) + probe value if any
        if row.cascade and self._slaves:
            grid = tk.Frame(frame, bg=theme.SECTION_BG)
            grid.pack(fill="x", padx=10, pady=(6, 8))
            for slave in self._slaves:
                cell = tk.Frame(grid, bg=theme.PANEL)
                cell.pack(side="left", padx=(0, 4))

                lbl = tk.Label(cell, text=slave.name, font=theme.FONT_TINY,
                                bg=theme.PANEL, fg=theme.SUBTEXT,
                                padx=6, pady=2)
                lbl.pack(side="left")

                # Probe value badge if applicable
                probe_value = row.probe_status_per_slave.get(slave.slave_id,
                                                                "")
                if row.has_probe and probe_value:
                    color = (theme.GREEN if probe_value in ("present",
                                                              "running")
                             else theme.SUBTEXT)
                    tk.Label(cell, text=f" {probe_value}",
                             font=theme.FONT_TINY,
                             bg=theme.PANEL, fg=color, padx=2,
                             ).pack(side="left")

                # Run button (disabled if slave offline)
                disabled = slave.is_offline
                btn = tk.Button(
                    cell, text="▶", font=theme.FONT_TINY,
                    bg=theme.BTN_BG if not disabled else theme.GREY,
                    fg=theme.TEXT, relief="flat", bd=0,
                    cursor="hand2" if not disabled else "arrow",
                    padx=6, pady=2,
                    command=(
                        (lambda s=slave.slave_id, b=row.batfile_id:
                         self._on_run(s, b))
                        if (self._on_run and not disabled)
                        else (lambda: None)
                    ),
                )
                btn.pack(side="left", padx=(2, 0))
        else:
            # Spacer so all rows have similar height
            tk.Frame(frame, bg=theme.SECTION_BG, height=4).pack()
