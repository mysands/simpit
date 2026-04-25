"""
Per-slave status card.

This is the visual answer to todo #3 — derived state UI. The widget
binds to a :class:`SlaveCardVM` and has only two responsibilities:

1. Render the current view-model.
2. Wire its action buttons (sync, shutdown, delete) to callbacks.

It owns no state and computes nothing — change comes by re-binding a
new view-model. That makes the widget rendering testable in isolation:
build a VM in a unit test, instantiate the widget under Xvfb, assert
the labels say what we expect.

Layout
------
    ┌─────────────────────────┐
    │ ▌ NAME           ●      │   <- 3px state-color strip
    │   host:port             │
    │   probe1: ON | probe2…  │
    │   42s ago               │
    │   [Sync]  [Shutdown]    │
    └─────────────────────────┘

When state == OFFLINE: the whole card greys out (faded text + grey
strip), and action buttons are disabled. When state == SYNCING: blue
strip + buttons disabled.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from .. import theme
from ..viewmodels import SlaveCardVM


class SlaveCardWidget(tk.Frame):
    """Render a SlaveCardVM and trigger callbacks on user actions."""

    def __init__(self, parent: tk.Widget, vm: SlaveCardVM,
                 on_sync:     Callable[[str], None] | None = None,
                 on_shutdown: Callable[[str], None] | None = None,
                 on_delete:   Callable[[str], None] | None = None,
                 on_edit:     Callable[[str], None] | None = None):
        super().__init__(parent, bg=theme.SECTION_BG, bd=0,
                          highlightthickness=0)
        self._vm          = vm
        self._on_sync     = on_sync
        self._on_shutdown = on_shutdown
        self._on_delete   = on_delete
        self._on_edit     = on_edit
        self._build()

    # ── Public ──
    def update_vm(self, vm: SlaveCardVM) -> None:
        """Re-render with a new view-model. Cheap — full rebuild."""
        self._vm = vm
        for child in self.winfo_children():
            child.destroy()
        self._build()

    @property
    def vm(self) -> SlaveCardVM:
        return self._vm

    # ── Build ──
    def _build(self) -> None:
        vm = self._vm
        # Top color strip — state indicator
        tk.Frame(self, bg=vm.state_color, height=3).pack(fill="x")

        # Header row: name + state badge
        hdr = tk.Frame(self, bg=theme.SECTION_BG)
        hdr.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(hdr, text=vm.name, font=theme.FONT_HEADING,
                 bg=theme.SECTION_BG,
                 fg=theme.SUBTEXT if vm.is_offline else theme.TEXT,
                 ).pack(side="left")
        tk.Label(hdr, text=vm.state_text, font=theme.FONT_SMALL,
                 bg=theme.SECTION_BG, fg=vm.state_color,
                 ).pack(side="right")

        # Host line
        tk.Label(self, text=vm.host_label, font=theme.FONT_MONO,
                 bg=theme.SECTION_BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=12)

        # Probe summary (only if non-empty)
        if vm.probe_summary:
            lbl = tk.Label(self, text=vm.probe_summary,
                           font=theme.FONT_SMALL,
                           bg=theme.SECTION_BG,
                           fg=(theme.SUBTEXT if vm.is_offline
                               else theme.ACCENT),
                           )
            lbl.pack(anchor="w", padx=12, pady=(4, 0))

        # Last seen / error line
        bottom_text = vm.error_text or f"last seen {vm.last_seen_text}"
        bottom_color = (theme.RED if vm.error_text
                        else theme.SUBTEXT)
        tk.Label(self, text=bottom_text, font=theme.FONT_TINY,
                 bg=theme.SECTION_BG, fg=bottom_color,
                 ).pack(anchor="w", padx=12, pady=(4, 4))

        # Action row
        actions = tk.Frame(self, bg=theme.SECTION_BG)
        actions.pack(fill="x", padx=8, pady=(4, 8))

        disabled = vm.is_offline or vm.is_syncing
        self._mk_action(actions, "Sync",     self._sync,     disabled)
        self._mk_action(actions, "Shutdown", self._shutdown, disabled)
        if self._on_edit is not None:
            self._mk_action(actions, "Edit", self._edit, False,
                              color=theme.PANEL)
        if self._on_delete is not None:
            self._mk_action(actions, "✕", self._delete, False,
                              color=theme.BTN_DANGER, width=3)

    def _mk_action(self, parent: tk.Widget, label: str,
                    fn: Callable[[], None], disabled: bool,
                    color: str = theme.BTN_BG,
                    width: int | None = None) -> None:
        kwargs = dict(
            text=label, font=theme.FONT_SMALL,
            bg=color if not disabled else theme.GREY,
            fg=theme.TEXT, relief="flat", bd=0,
            cursor="hand2" if not disabled else "arrow",
            padx=10, pady=4,
            command=fn if not disabled else (lambda: None),
        )
        if width is not None:
            kwargs["width"] = width
        btn = tk.Button(parent, **kwargs)
        btn.pack(side="left", padx=(0, 4))

    # ── Actions ──
    def _sync(self) -> None:
        if self._on_sync:
            self._on_sync(self._vm.slave_id)

    def _shutdown(self) -> None:
        if self._on_shutdown:
            self._on_shutdown(self._vm.slave_id)

    def _edit(self) -> None:
        if self._on_edit:
            self._on_edit(self._vm.slave_id)

    def _delete(self) -> None:
        if self._on_delete:
            self._on_delete(self._vm.slave_id)
