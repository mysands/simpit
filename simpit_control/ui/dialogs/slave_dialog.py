"""
Slave add/edit dialog.

Same dialog handles both new-slave and edit-existing — pass an existing
Slave to edit it, or None to create a new one. Saves via the controller
on click; raises validation errors as in-dialog message boxes.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Callable

from ... import data as sp_data
from .. import theme
from ..controller import Controller


class SlaveDialog(tk.Toplevel):
    """Modal dialog for creating or editing a Slave entry."""

    def __init__(self, parent: tk.Misc, controller: Controller,
                 existing: sp_data.Slave | None = None,
                 on_save: Callable[[sp_data.Slave], None] | None = None):
        super().__init__(parent)
        self.controller = controller
        self.existing   = existing
        self.on_save    = on_save

        self.title("Edit Slave" if existing else "Add Slave")
        self.configure(bg=theme.BG)
        self.resizable(False, False)
        self.geometry("420x340")
        self.transient(parent)
        self.grab_set()

        self._build()

    def _build(self) -> None:
        e = self.existing
        self.var_name  = tk.StringVar(value=e.name if e else "")
        self.var_host  = tk.StringVar(value=e.host if e else "")
        self.var_udp   = tk.StringVar(value=str(e.udp_port if e else 49100))
        self.var_tcp   = tk.StringVar(value=str(e.tcp_port if e else 49101))
        self.var_notes = tk.StringVar(value=e.notes if e else "")

        self._field("NAME",  self.var_name)
        self._field("HOST (IP or DNS)", self.var_host)
        self._field("UDP PORT", self.var_udp)
        self._field("TCP PORT", self.var_tcp)
        self._field("NOTES (optional)", self.var_notes)

        # Buttons
        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=20, pady=(20, 12), side="bottom")
        theme.make_button(btns, "Cancel", self.destroy,
                            color=theme.BTN_DANGER, width=10).pack(side="left")
        theme.make_button(btns, "Save", self._save,
                            color=theme.BTN_OK, width=10).pack(side="right")

    def _field(self, label: str, var: tk.StringVar) -> None:
        tk.Label(self, text=label, font=theme.FONT_TINY,
                 bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20, pady=(8, 2))
        tk.Entry(self, textvariable=var, font=theme.FONT_BODY,
                 bg=theme.ENTRY_BG, fg=theme.TEXT,
                 insertbackground=theme.TEXT, relief="flat", bd=0,
                 highlightthickness=1, highlightbackground=theme.BORDER,
                 highlightcolor=theme.ACCENT,
                 ).pack(fill="x", padx=20, ipady=5)

    def _save(self) -> None:
        try:
            udp = int(self.var_udp.get())
            tcp = int(self.var_tcp.get())
        except ValueError:
            messagebox.showerror("Invalid port", "Ports must be integers.",
                                  parent=self)
            return
        try:
            if self.existing is None:
                slave = self.controller.add_slave(
                    name=self.var_name.get(), host=self.var_host.get(),
                    udp_port=udp, tcp_port=tcp,
                    notes=self.var_notes.get())
            else:
                slave = sp_data.Slave(
                    id=self.existing.id,
                    name=self.var_name.get(), host=self.var_host.get(),
                    udp_port=udp, tcp_port=tcp,
                    notes=self.var_notes.get())
                self.controller.update_slave(slave)
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e), parent=self)
            return
        if self.on_save is not None:
            self.on_save(slave)
        self.destroy()
