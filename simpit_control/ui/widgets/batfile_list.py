"""
Scripts list panel.

Shows every registered batfile as a row with run/edit/delete actions
plus a per-slave probe status grid (so the user can see at a glance
which slaves currently have scenery enabled, which have updates blocked,
etc.).

Like the slave card, this widget binds to view-models and re-renders
when given a fresh list. The only state carried across re-renders is
the scroll position: the dashboard rebuilds this list on every poll
tick, and without restoring the previous fraction the view would snap
back to the top every few seconds.

Rows live inside a Canvas-hosted frame so the list scrolls when it
outgrows the window (tk frames don't scroll by themselves); the
scrollbar auto-hides while everything fits.
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
        self._canvas: tk.Canvas | None = None
        self._build()

    # ── Public ──
    def update_data(self, slaves: list[SlaveCardVM],
                     rows: list[BatFileRowVM]) -> None:
        """Re-render with a new dashboard slice, keeping scroll position."""
        fraction = self._canvas.yview()[0] if self._canvas else 0.0
        self._slaves = slaves
        self._rows = rows
        self._unbind_wheel()      # handlers reference soon-dead widgets
        for w in self.winfo_children():
            w.destroy()
        self._canvas = None
        self._build()
        if self._canvas is not None and fraction > 0.0:
            self.update_idletasks()          # scrollregion must exist first
            self._canvas.yview_moveto(fraction)

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

        # Scrollable row region: Canvas + inner frame + auto-hide bar.
        container = tk.Frame(self, bg=theme.BG)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, bg=theme.BG, highlightthickness=0,
                           bd=0, yscrollincrement=24)
        vsb = tk.Scrollbar(container, orient="vertical",
                           command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        # vsb is packed on demand by _sync_scrollbar.
        self._canvas = canvas
        self._vsb = vsb

        inner = tk.Frame(canvas, bg=theme.BG)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_change(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all") or (0, 0, 0, 0))
            self._sync_scrollbar(inner, canvas, vsb)

        inner.bind("<Configure>", _on_inner_change)
        canvas.bind("<Configure>",
                    lambda e: (canvas.itemconfigure(window, width=e.width),
                               self._sync_scrollbar(inner, canvas, vsb)))
        # Wheel events land on whichever child is under the cursor, so
        # bind globally only while the pointer is over the list.
        canvas.bind("<Enter>", lambda e: self._bind_wheel(inner, canvas))
        canvas.bind("<Leave>", lambda e: self._unbind_wheel())

        # One row per batfile
        for row in self._rows:
            self._draw_row(inner, row)

    # ── Scrolling ──
    def _sync_scrollbar(self, inner: tk.Frame, canvas: tk.Canvas,
                        vsb: tk.Scrollbar) -> None:
        """Show the scrollbar only while the rows overflow the canvas."""
        if inner.winfo_reqheight() > canvas.winfo_height():
            if not vsb.winfo_ismapped():
                vsb.pack(side="right", fill="y")
        elif vsb.winfo_ismapped():
            vsb.pack_forget()
            canvas.yview_moveto(0.0)

    def _bind_wheel(self, inner: tk.Frame, canvas: tk.Canvas) -> None:
        def _wheel(event):
            if inner.winfo_reqheight() <= canvas.winfo_height():
                return                       # nothing to scroll
            if getattr(event, "num", None) == 4:       # X11 wheel up
                step = -1
            elif getattr(event, "num", None) == 5:     # X11 wheel down
                step = 1
            else:                                      # Windows/mac delta
                step = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
            canvas.yview_scroll(step, "units")
        self.bind_all("<MouseWheel>", _wheel)
        self.bind_all("<Button-4>", _wheel)
        self.bind_all("<Button-5>", _wheel)

    def _unbind_wheel(self) -> None:
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")

    def _draw_row(self, parent: tk.Widget, row: BatFileRowVM) -> None:
        frame = tk.Frame(parent, bg=theme.SECTION_BG, bd=0,
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

        # Per-slave run buttons (cascade only) + probe value if any.
        # Only show slaves this script actually targets (from the VM's
        # per-slave dispatch map — untargeted slaves are absent from it).
        targeted_slaves = [s for s in self._slaves
                           if s.slave_id in row.batfile_id_per_slave]
        if row.cascade and targeted_slaves:
            grid = tk.Frame(frame, bg=theme.SECTION_BG)
            grid.pack(fill="x", padx=10, pady=(6, 8))
            for slave in targeted_slaves:
                cell = tk.Frame(grid, bg=theme.PANEL)
                cell.pack(side="left", padx=(0, 4))

                lbl = tk.Label(cell, text=slave.name, font=theme.FONT_TINY,
                               bg=theme.PANEL, fg=theme.SUBTEXT,
                               padx=6, pady=2)
                lbl.pack(side="left")

                # Probe value badge — shown prominently so the operator
                # can see state at a glance (e.g. "present" = PE connected).
                probe_value = row.probe_status_per_slave.get(slave.slave_id, "")
                if row.has_probe and probe_value:
                    if probe_value in ("present", "running"):
                        badge_fg = theme.GREEN
                        badge_bg = theme.SECTION_BG
                    elif probe_value == "absent":
                        badge_fg = theme.SUBTEXT
                        badge_bg = theme.PANEL
                    else:
                        badge_fg = theme.AMBER
                        badge_bg = theme.PANEL
                    tk.Label(cell, text=probe_value,
                             font=theme.FONT_TINY,
                             bg=badge_bg, fg=badge_fg,
                             padx=4, pady=1,
                             relief="flat",
                             ).pack(side="left", padx=(2, 0))

                # Run button — label and target flip per-slave for toggle pairs.
                disabled = slave.is_offline
                btn_label = row.button_label_per_slave.get(slave.slave_id, "")
                btn_text  = btn_label if btn_label else "▶"
                target_id = row.batfile_id_per_slave.get(slave.slave_id,
                                                         row.batfile_id)
                btn = tk.Button(
                    cell, text=btn_text, font=theme.FONT_TINY,
                    bg=theme.BTN_BG if not disabled else theme.GREY,
                    fg=theme.TEXT, relief="flat", bd=0,
                    cursor="hand2" if not disabled else "arrow",
                    padx=6, pady=2,
                    command=(
                        (lambda s=slave.slave_id, b=target_id:
                         self._on_run(s, b))
                        if (self._on_run and not disabled)
                        else (lambda: None)
                    ),
                )
                btn.pack(side="left", padx=(2, 0))
        else:
            # Spacer so all rows have similar height
            tk.Frame(frame, bg=theme.SECTION_BG, height=4).pack()
