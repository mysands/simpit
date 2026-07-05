"""
Bat file (script) add/edit dialog.

Captures everything needed to register a script:
* name + script_name (base name used to find on slaves)
* cascade flag — checkbox; when on, content textarea becomes active
* content vs local_path — only one is used depending on cascade
* target slaves — None means all, or a checklist for specific subset
* needs_admin — surfaced as an admin badge in the UI
* state_probe — pick a probe type + parameters

Probe params are entered as an editable two-column name/value table.
Switching probe type pre-fills default param names for that type when
the table is currently empty.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

from simpit_common import probes as sp_probes

# Default param keys shown when the user selects a probe type on a blank table.
_PROBE_DEFAULTS: dict[str, dict[str, str]] = {
    "path_exists":      {"path": "${XPLANE_FOLDER}/"},
    "folder_exists":    {"path": "${XPLANE_FOLDER}/"},
    "file_contains":    {"path": "", "contains": ""},
    "process_running":  {"name": ""},
    "xplane_dataref":   {"dataref": ""},
    "script_exit_code": {},
}

from ... import data as sp_data
from ... import registry as sp_registry
from .. import theme
from ..controller import Controller


class BatFileDialog(tk.Toplevel):
    """Add or edit a registered script."""

    def __init__(self, parent: tk.Misc, controller: Controller,
                 existing: sp_data.BatFile | None = None,
                 on_save: Callable[[sp_data.BatFile], None] | None = None):
        super().__init__(parent)
        self.controller = controller
        self.existing   = existing
        self.on_save    = on_save

        self.title("Edit Script" if existing else "Add Script")
        self.configure(bg=theme.BG)
        self.resizable(True, True)
        self.geometry("640x760")
        self.minsize(560, 640)
        self.transient(parent)
        self.grab_set()

        self._build()

    def _build(self) -> None:
        e = self.existing
        self.var_name        = tk.StringVar(value=e.name if e else "")
        self.var_script_name = tk.StringVar(value=e.script_name if e else "")
        self.var_cascade     = tk.BooleanVar(value=e.cascade if e else True)
        self.var_local_path  = tk.StringVar(value=e.local_path if e else "")
        self.var_admin       = tk.BooleanVar(value=e.needs_admin if e else False)

        # target_slaves: None → all; list → specific ids
        existing_targets = e.target_slaves if e else None
        self.var_target_all = tk.BooleanVar(value=(existing_targets is None))

        # One BooleanVar per slave, keyed by slave id
        slaves = self.controller.store.slaves()
        self._slave_vars: dict[str, tk.BooleanVar] = {}
        for s in slaves:
            checked = (existing_targets is None or s.id in existing_targets)
            self._slave_vars[s.id] = tk.BooleanVar(value=checked)

        # Probe defaults
        existing_probe = (e.state_probe if (e and e.state_probe) else None)
        self.var_probe_type = tk.StringVar(
            value=existing_probe["type"] if existing_probe else "(none)")

        # ── Bottom buttons (built first so packing works with side=bottom) ──
        bottom = tk.Frame(self, bg=theme.BG)
        bottom.pack(fill="x", side="bottom", pady=12, padx=20)
        theme.make_button(bottom, "Cancel", self.destroy,
                          color=theme.BTN_DANGER, width=10).pack(side="left")
        theme.make_button(bottom, "Save", self._save,
                          color=theme.BTN_OK, width=10).pack(side="right")

        # ── Scrollable form area ──
        canvas = tk.Canvas(self, bg=theme.BG, bd=0, highlightthickness=0)
        scrollbar = tk.Scrollbar(self, orient="vertical",
                                 command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        form = tk.Frame(canvas, bg=theme.BG)
        canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # Form contents
        self._line(form, "DISPLAY NAME", self.var_name)
        self._line(form, "SCRIPT NAME (no extension)", self.var_script_name)

        # ── Cascade checkbox ──
        tk.Checkbutton(form, text="Cascade to slaves",
                       variable=self.var_cascade,
                       font=theme.FONT_BODY, bg=theme.BG, fg=theme.TEXT,
                       selectcolor=theme.ENTRY_BG,
                       activebackground=theme.BG,
                       activeforeground=theme.TEXT,
                       command=self._update_cascade_visibility,
                       ).pack(anchor="w", padx=20, pady=(8, 0))

        tk.Label(form,
                 text="When on: script content is pushed to slaves on sync.",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=44)

        # ── Target slaves ──
        tk.Label(form, text="TARGET SLAVES",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20, pady=(12, 2))

        target_frame = tk.Frame(form, bg=theme.ENTRY_BG,
                                highlightthickness=1,
                                highlightbackground=theme.BORDER)
        target_frame.pack(fill="x", padx=20)

        self._chk_all = tk.Checkbutton(
            target_frame, text="All slaves",
            variable=self.var_target_all,
            font=theme.FONT_BODY, bg=theme.ENTRY_BG, fg=theme.TEXT,
            selectcolor=theme.BG,
            activebackground=theme.ENTRY_BG,
            activeforeground=theme.TEXT,
            command=self._update_target_slaves,
        )
        self._chk_all.pack(anchor="w", padx=8, pady=(6, 2))

        # Per-slave checkboxes
        self._slave_chks: dict[str, tk.Checkbutton] = {}
        if slaves:
            for s in slaves:
                chk = tk.Checkbutton(
                    target_frame, text=s.name,
                    variable=self._slave_vars[s.id],
                    font=theme.FONT_BODY, bg=theme.ENTRY_BG, fg=theme.TEXT,
                    selectcolor=theme.BG,
                    activebackground=theme.ENTRY_BG,
                    activeforeground=theme.TEXT,
                )
                chk.pack(anchor="w", padx=24, pady=1)
                self._slave_chks[s.id] = chk
        else:
            tk.Label(target_frame, text="No slaves registered yet.",
                     font=theme.FONT_TINY, bg=theme.ENTRY_BG,
                     fg=theme.SUBTEXT).pack(anchor="w", padx=24, pady=(0, 6))

        # ── Content textarea (cascade=True) with "Load from file" button ──
        content_hdr = tk.Frame(form, bg=theme.BG)
        content_hdr.pack(fill="x", padx=20, pady=(12, 2))
        tk.Label(content_hdr, text="SCRIPT CONTENT (cascade only)",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(side="left")
        theme.make_button(content_hdr, "Load from file…",
                          self._load_content_from_file,
                          color=theme.BTN_HOV,
                          ).pack(side="right")
        self.txt_content = tk.Text(
            form, height=8, font=theme.FONT_MONO,
            bg=theme.ENTRY_BG, fg=theme.TEXT,
            insertbackground=theme.TEXT, relief="flat", bd=0, wrap="none",
        )
        self.txt_content.pack(fill="x", padx=20)
        if e:
            self.txt_content.insert("1.0", e.content)

        # ── Local path (cascade=False) with Browse button ──
        tk.Label(form, text="LOCAL PATH (non-cascade only)",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20, pady=(8, 2))
        local_row = tk.Frame(form, bg=theme.BG)
        local_row.pack(fill="x", padx=20)
        tk.Entry(local_row, textvariable=self.var_local_path,
                 font=theme.FONT_BODY,
                 bg=theme.ENTRY_BG, fg=theme.TEXT,
                 insertbackground=theme.TEXT, relief="flat", bd=0,
                 highlightthickness=1, highlightbackground=theme.BORDER,
                 highlightcolor=theme.ACCENT,
                 ).pack(side="left", fill="x", expand=True,
                        ipady=theme.TOUCH_PADY)
        theme.make_button(local_row, "Browse…",
                          self._browse_local_path,
                          color=theme.BTN_HOV,
                          ).pack(side="left", padx=(6, 0))

        # ── Admin checkbox ──
        tk.Checkbutton(form, text="Requires admin/elevation",
                       variable=self.var_admin,
                       font=theme.FONT_BODY, bg=theme.BG, fg=theme.TEXT,
                       selectcolor=theme.ENTRY_BG,
                       activebackground=theme.BG,
                       activeforeground=theme.TEXT,
                       ).pack(anchor="w", padx=20, pady=(12, 0))

        # ── State probe ──
        tk.Label(form, text="STATE PROBE (optional)",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20, pady=(16, 2))
        types = ["(none)"] + sp_probes.known_probe_types()
        self.var_probe_type.trace_add("write", lambda *_: self._on_probe_type_change())
        opt = tk.OptionMenu(form, self.var_probe_type, *types)
        opt.configure(bg=theme.ENTRY_BG, fg=theme.TEXT,
                      activebackground=theme.BTN_HOV,
                      activeforeground=theme.TEXT, relief="flat", bd=0,
                      highlightthickness=0,
                      font=theme.FONT_BODY)
        opt["menu"].configure(bg=theme.ENTRY_BG, fg=theme.TEXT,
                              font=theme.FONT_BODY)
        opt.pack(anchor="w", padx=20)

        # ── Probe params table ──
        tk.Label(form, text="PROBE PARAMS",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20, pady=(8, 2))

        outer = tk.Frame(form, bg=theme.ENTRY_BG,
                         highlightthickness=1,
                         highlightbackground=theme.BORDER)
        outer.pack(fill="x", padx=20, pady=(0, 12))

        hdr = tk.Frame(outer, bg=theme.ENTRY_BG)
        hdr.pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(hdr, text="NAME", font=theme.FONT_TINY,
                 bg=theme.ENTRY_BG, fg=theme.SUBTEXT,
                 width=20, anchor="w").pack(side="left")
        tk.Label(hdr, text="VALUE", font=theme.FONT_TINY,
                 bg=theme.ENTRY_BG, fg=theme.SUBTEXT,
                 anchor="w").pack(side="left")

        self._params_container = tk.Frame(outer, bg=theme.ENTRY_BG)
        self._params_container.pack(fill="x", padx=6, pady=2)
        self._param_rows: list[tuple[tk.StringVar, tk.StringVar, tk.Frame]] = []

        add_row_frame = tk.Frame(outer, bg=theme.ENTRY_BG)
        add_row_frame.pack(fill="x", padx=6, pady=(2, 6))
        theme.make_button(add_row_frame, "+ Add param",
                          lambda: self._add_param_row(),
                          color=theme.BTN_HOV).pack(side="left")

        existing_params = (existing_probe or {}).get("params") or {}
        if existing_params:
            for k, v in existing_params.items():
                self._add_param_row(str(k), str(v))
        else:
            self._add_param_row()

        # Apply initial enabled/disabled states
        self._update_target_slaves()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _line(self, parent: tk.Widget, label: str,
              var: tk.StringVar) -> None:
        tk.Label(parent, text=label, font=theme.FONT_TINY,
                 bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20, pady=(8, 2))
        tk.Entry(parent, textvariable=var, font=theme.FONT_BODY,
                 bg=theme.ENTRY_BG, fg=theme.TEXT,
                 insertbackground=theme.TEXT, relief="flat", bd=0,
                 highlightthickness=1, highlightbackground=theme.BORDER,
                 highlightcolor=theme.ACCENT,
                 ).pack(fill="x", padx=20, ipady=theme.TOUCH_PADY)

    def _scripts_dir(self) -> str:
        return str(sp_registry._scripts_dir())

    def _update_target_slaves(self) -> None:
        """Disable individual slave checkboxes when 'All slaves' is ticked."""
        state = "disabled" if self.var_target_all.get() else "normal"
        for chk in self._slave_chks.values():
            chk.configure(state=state)

    def _update_cascade_visibility(self) -> None:
        pass

    # ── Probe params table ────────────────────────────────────────────────────

    def _add_param_row(self, name: str = "", value: str = "") -> None:
        row = tk.Frame(self._params_container, bg=theme.ENTRY_BG)
        row.pack(fill="x", pady=1)

        var_name  = tk.StringVar(value=name)
        var_value = tk.StringVar(value=value)
        entry_tuple = (var_name, var_value, row)
        self._param_rows.append(entry_tuple)

        tk.Entry(row, textvariable=var_name, font=theme.FONT_BODY,
                 bg=theme.BG, fg=theme.TEXT,
                 insertbackground=theme.TEXT, relief="flat", bd=0,
                 highlightthickness=1, highlightbackground=theme.BORDER,
                 highlightcolor=theme.ACCENT, width=20,
                 ).pack(side="left", ipady=theme.TOUCH_PADY, padx=(0, 4))
        tk.Entry(row, textvariable=var_value, font=theme.FONT_BODY,
                 bg=theme.BG, fg=theme.TEXT,
                 insertbackground=theme.TEXT, relief="flat", bd=0,
                 highlightthickness=1, highlightbackground=theme.BORDER,
                 highlightcolor=theme.ACCENT,
                 ).pack(side="left", fill="x", expand=True,
                        ipady=theme.TOUCH_PADY)

        def _remove():
            self._param_rows.remove(entry_tuple)
            row.destroy()

        tk.Button(row, text="×", font=theme.FONT_BODY,
                  bg=theme.ENTRY_BG, fg=theme.SUBTEXT,
                  activebackground=theme.BTN_DANGER,
                  activeforeground=theme.TEXT,
                  relief="flat", bd=0, cursor="hand2",
                  command=_remove,
                  ).pack(side="left", padx=(4, 0))

    def _get_params(self) -> dict:
        return {
            vn.get().strip(): vv.get().strip()
            for vn, vv, _ in self._param_rows
            if vn.get().strip()
        }

    def _set_params(self, params: dict) -> None:
        for _, _, row in self._param_rows:
            row.destroy()
        self._param_rows.clear()
        for k, v in params.items():
            self._add_param_row(str(k), str(v))
        if not params:
            self._add_param_row()

    def _on_probe_type_change(self) -> None:
        ptype = self.var_probe_type.get()
        # Only pre-fill defaults when all name fields are blank (table is empty).
        all_blank = all(not vn.get().strip() for vn, _, __ in self._param_rows)
        if all_blank and ptype in _PROBE_DEFAULTS:
            self._set_params(_PROBE_DEFAULTS[ptype])

    # ── File pickers ─────────────────────────────────────────────────────────

    def _browse_local_path(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select script file",
            initialdir=self._scripts_dir(),
            filetypes=[
                ("Script files", "*.bat *.sh *.py *.ps1"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self.var_local_path.set(path)
        self._autofill_script_name(path)

    def _load_content_from_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Load script content from file",
            initialdir=self._scripts_dir(),
            filetypes=[
                ("Script files", "*.bat *.sh *.py *.ps1"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Could not read file", str(e), parent=self)
            return
        self.txt_content.delete("1.0", "end")
        self.txt_content.insert("1.0", content)
        self._autofill_script_name(path)

    def _autofill_script_name(self, path: str) -> None:
        """Set SCRIPT NAME from the file stem if the field is currently empty."""
        if self.var_script_name.get().strip():
            return
        self.var_script_name.set(Path(path).stem)

    # ── Probe ─────────────────────────────────────────────────────────────────

    def _build_state_probe(self) -> dict | None:
        ptype = self.var_probe_type.get()
        if ptype == "(none)" or not ptype:
            return None
        return {"type": ptype, "params": self._get_params()}

    # ── Target slaves ─────────────────────────────────────────────────────────

    def _build_target_slaves(self) -> list[str] | None:
        """Return None for all-slaves, or a list of selected slave ids."""
        if self.var_target_all.get():
            return None
        selected = [sid for sid, var in self._slave_vars.items()
                    if var.get()]
        return selected if selected else None

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            state_probe   = self._build_state_probe()
            target_slaves = self._build_target_slaves()
        except ValueError as e:
            messagebox.showerror("Invalid probe", str(e), parent=self)
            return

        content = self.txt_content.get("1.0", "end").rstrip()
        try:
            if self.existing is None:
                bat = self.controller.add_batfile(
                    name=self.var_name.get(),
                    script_name=self.var_script_name.get(),
                    cascade=bool(self.var_cascade.get()),
                    content=content,
                    local_path=self.var_local_path.get(),
                    target_slaves=target_slaves,
                    needs_admin=bool(self.var_admin.get()),
                    state_probe=state_probe,
                )
            else:
                bat = sp_data.BatFile(
                    id=self.existing.id,
                    name=self.var_name.get(),
                    script_name=self.var_script_name.get(),
                    cascade=bool(self.var_cascade.get()),
                    content=content,
                    local_path=self.var_local_path.get(),
                    target_slaves=target_slaves,
                    needs_admin=bool(self.var_admin.get()),
                    state_probe=state_probe,
                )
                self.controller.update_batfile(bat)
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e), parent=self)
            return
        if self.on_save is not None:
            self.on_save(bat)
        self.destroy()
