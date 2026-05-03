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
        self.resizable(False, True)
        self.minsize(480, 400)
        self.transient(parent)
        self.grab_set()

        self._build()
        # Let tkinter size the window to fit content, then prevent
        # horizontal resize only. update_idletasks() flushes geometry.
        self.update_idletasks()
        self.geometry(f"480x{self.winfo_reqheight()}")

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

        # ── Known env vars ───────────────────────────────────────────────────
        tk.Label(self, text="X-PLANE CONFIGURATION",
                 font=theme.FONT_HEADING, bg=theme.BG, fg=theme.ACCENT,
                 ).pack(anchor="w", padx=20, pady=(16, 4))

        existing_env = e.env if e else {}

        self.var_xplane_folder = tk.StringVar(
            value=existing_env.get("XPLANE_FOLDER", ""))
        self.var_sim_exe = tk.StringVar(
            value=existing_env.get("SIM_EXE_NAME", "X-Plane.exe"))

        self._field("XPLANE_FOLDER  (e.g. C:\\X-Plane 12\\)",
                    self.var_xplane_folder)
        self._field("SIM_EXE_NAME   (e.g. X-Plane.exe)",
                    self.var_sim_exe)

        # ── Backup configuration ─────────────────────────────────────────────
        # Two fields kept optional: a slave that has no BACKUP_FOLDER set
        # simply can't run backup_xplane / restore_xplane, which is fine —
        # those scripts are opt-in and not every slave needs to back up.
        tk.Label(self, text="BACKUP CONFIGURATION (OPTIONAL)",
                 font=theme.FONT_HEADING, bg=theme.BG, fg=theme.ACCENT,
                 ).pack(anchor="w", padx=20, pady=(16, 4))

        self.var_backup_folder = tk.StringVar(
            value=existing_env.get("BACKUP_FOLDER", ""))
        # BACKUP_KEEP defaults to 2 inside the script when absent. We
        # leave the field blank by default so the script's default is
        # what most slaves get — only filled in if you want to override.
        self.var_backup_keep = tk.StringVar(
            value=existing_env.get("BACKUP_KEEP", ""))

        self._field("BACKUP_FOLDER  (e.g. \\\\NAS\\backups\\simpit)",
                    self.var_backup_folder)
        self._field("BACKUP_KEEP    (number of archives to retain; blank = 2)",
                    self.var_backup_keep)

        # Buttons
        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=20, pady=(16, 12), side="bottom")
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
                 ).pack(fill="x", padx=20, ipady=theme.TOUCH_PADY)

    def _build_env(self) -> dict[str, str]:
        """Build env dict from the hardwired fields.

        Empty fields are dropped — absent env keys mean "use the
        script's default behavior" rather than "set the value to
        empty string," which would propagate to the slave and likely
        confuse the script's own preflight checks.
        """
        env = {}
        xplane_folder = self.var_xplane_folder.get().strip()
        sim_exe = self.var_sim_exe.get().strip()
        backup_folder = self.var_backup_folder.get().strip()
        backup_keep = self.var_backup_keep.get().strip()

        if xplane_folder:
            # Ensure trailing backslash on Windows paths
            if xplane_folder and not xplane_folder.endswith(("\\", "/")):
                xplane_folder += "\\"
            env["XPLANE_FOLDER"] = xplane_folder
        if sim_exe:
            env["SIM_EXE_NAME"] = sim_exe
        if backup_folder:
            # Don't auto-append a trailing slash here — UNC paths in
            # particular work fine without one and adding it can
            # produce ugly double-slashes downstream.
            env["BACKUP_FOLDER"] = backup_folder
        if backup_keep:
            # Validate at the dialog layer so the user gets immediate
            # feedback rather than a script failure on the slave.
            try:
                n = int(backup_keep)
            except ValueError as exc:
                raise ValueError(
                    f"BACKUP_KEEP must be an integer, got {backup_keep!r}"
                ) from exc
            if n < 1:
                raise ValueError(
                    f"BACKUP_KEEP must be >= 1, got {n}"
                )
            env["BACKUP_KEEP"] = str(n)
        return env

    def _save(self) -> None:
        try:
            udp = int(self.var_udp.get())
            tcp = int(self.var_tcp.get())
        except ValueError:
            messagebox.showerror("Invalid port", "Ports must be integers.",
                                  parent=self)
            return
        try:
            env = self._build_env()
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self)
            return
        try:
            if self.existing is None:
                slave = self.controller.add_slave(
                    name=self.var_name.get(), host=self.var_host.get(),
                    udp_port=udp, tcp_port=tcp,
                    notes=self.var_notes.get(), env=env)
            else:
                slave = sp_data.Slave(
                    id=self.existing.id,
                    name=self.var_name.get(), host=self.var_host.get(),
                    udp_port=udp, tcp_port=tcp,
                    notes=self.var_notes.get(), env=env)
                self.controller.update_slave(slave)
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e), parent=self)
            return
        if self.on_save is not None:
            self.on_save(slave)
        self.destroy()
