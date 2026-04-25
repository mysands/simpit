"""
Bat file (script) add/edit dialog.

Captures everything needed to register a script:
* name + script_name (base name used to find on slaves)
* cascade flag — checkbox; when on, content textarea becomes active
* content vs local_path — only one is used depending on cascade
* target slaves — None means all, or a checklist for specific subset
* needs_admin — surfaced as an admin badge in the UI
* state_probe — pick a probe type + parameters

Probe configuration is intentionally kept simple. The user picks a type
from a dropdown; the params are entered as raw JSON in a small textarea.
That's a power-user shape but it's the most flexible way to support an
extensible probe set without a custom form per type.
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from simpit_common import probes as sp_probes

from ... import data as sp_data
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
        self.geometry("640x720")
        self.minsize(560, 600)
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

        # Probe defaults
        existing_probe = (e.state_probe if (e and e.state_probe) else None)
        self.var_probe_type = tk.StringVar(
            value=existing_probe["type"] if existing_probe
            else "(none)")

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

        # Cascade checkbox
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

        # Content textarea (cascade=True)
        tk.Label(form, text="SCRIPT CONTENT (cascade only)",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20, pady=(12, 2))
        self.txt_content = tk.Text(
            form, height=8, font=theme.FONT_MONO,
            bg=theme.ENTRY_BG, fg=theme.TEXT,
            insertbackground=theme.TEXT, relief="flat", bd=0, wrap="none",
        )
        self.txt_content.pack(fill="x", padx=20)
        if e:
            self.txt_content.insert("1.0", e.content)

        # Local path field (cascade=False)
        self._line(form, "LOCAL PATH (non-cascade only)",
                    self.var_local_path)

        # Admin checkbox
        tk.Checkbutton(form, text="Requires admin/elevation",
                        variable=self.var_admin,
                        font=theme.FONT_BODY, bg=theme.BG, fg=theme.TEXT,
                        selectcolor=theme.ENTRY_BG,
                        activebackground=theme.BG,
                        activeforeground=theme.TEXT,
                        ).pack(anchor="w", padx=20, pady=(12, 0))

        # State probe
        tk.Label(form, text="STATE PROBE (optional)",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20, pady=(16, 2))
        types = ["(none)"] + sp_probes.known_probe_types()
        opt = tk.OptionMenu(form, self.var_probe_type, *types)
        opt.configure(bg=theme.ENTRY_BG, fg=theme.TEXT,
                       activebackground=theme.BTN_HOV,
                       activeforeground=theme.TEXT, relief="flat", bd=0,
                       highlightthickness=0,
                       font=theme.FONT_BODY)
        opt["menu"].configure(bg=theme.ENTRY_BG, fg=theme.TEXT,
                                font=theme.FONT_BODY)
        opt.pack(anchor="w", padx=20)

        tk.Label(form, text="PROBE PARAMS (JSON object)",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20, pady=(8, 2))
        self.txt_params = tk.Text(
            form, height=4, font=theme.FONT_MONO,
            bg=theme.ENTRY_BG, fg=theme.TEXT,
            insertbackground=theme.TEXT, relief="flat", bd=0, wrap="word",
        )
        self.txt_params.pack(fill="x", padx=20, pady=(0, 12))
        if existing_probe and existing_probe.get("params"):
            self.txt_params.insert("1.0", json.dumps(
                existing_probe["params"], indent=2))
        else:
            self.txt_params.insert("1.0", '{\n  "path": "${XPLANE_FOLDER}/Custom Scenery"\n}')

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
                 ).pack(fill="x", padx=20, ipady=5)

    def _update_cascade_visibility(self) -> None:
        # Visual hint only — fields stay visible always so the user can
        # see what they'd configure either way. Validation enforces the
        # right one is filled in.
        pass

    def _build_state_probe(self) -> dict | None:
        ptype = self.var_probe_type.get()
        if ptype == "(none)" or not ptype:
            return None
        params_text = self.txt_params.get("1.0", "end").strip()
        if not params_text:
            return {"type": ptype, "params": {}}
        try:
            params = json.loads(params_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"PROBE PARAMS must be valid JSON: {e}") from e
        if not isinstance(params, dict):
            raise ValueError("PROBE PARAMS must be a JSON object")
        return {"type": ptype, "params": params}

    def _save(self) -> None:
        try:
            state_probe = self._build_state_probe()
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
                    target_slaves=self.existing.target_slaves,
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
