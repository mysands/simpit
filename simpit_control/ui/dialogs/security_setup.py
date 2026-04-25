"""
Security setup dialog.

First-run experience: user clicks "Generate Key", we produce a random
32-byte key, display it as hex in a copy-friendly text field, and
persist it. The user copies the key into each slave's first-run prompt.

Also used after the fact to view or rotate the key. Rotating means
overwriting the file — slaves with the old key will silently fail to
verify until they get the new one.

We deliberately make copying the key explicit (a Copy button + visible
text) rather than copying to clipboard automatically. The user pastes
into a separate slave install — better to show what they're handing
over than to have it disappear into the clipboard invisibly.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

from simpit_common import security as sp_security

from .. import theme


class SecuritySetupDialog(tk.Toplevel):
    """Modal dialog for generating, displaying, and rotating the SimPit key."""

    def __init__(self, parent: tk.Misc, key_file: Path,
                  on_key_changed: Callable[[bytes], None] | None = None):
        super().__init__(parent)
        self.key_file       = Path(key_file)
        self.on_key_changed = on_key_changed
        self._current_key: bytes | None = None

        self.title("SimPit Security")
        self.configure(bg=theme.BG)
        self.resizable(False, False)
        self.geometry("540x420")
        self.transient(parent)
        self.grab_set()

        self._build()
        self._load_existing()

    # ── Build ──
    def _build(self) -> None:
        # Header
        tk.Label(self, text="SIMPIT SECURITY KEY",
                 font=theme.FONT_TITLE, bg=theme.BG,
                 fg=theme.ACCENT).pack(pady=(20, 4))
        tk.Label(self,
                 text="A shared secret used to sign every command sent to a slave.",
                 font=theme.FONT_SMALL, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(pady=(0, 4))
        tk.Label(self, text=f"Stored at: {self.key_file}",
                 font=theme.FONT_TINY, bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(pady=(0, 16))

        # Key display
        tk.Label(self, text="KEY (hex):", font=theme.FONT_TINY,
                 bg=theme.BG, fg=theme.SUBTEXT,
                 ).pack(anchor="w", padx=20)
        self.txt = tk.Text(self, height=4, font=theme.FONT_MONO,
                            bg=theme.ENTRY_BG, fg=theme.GREEN,
                            insertbackground=theme.GREEN,
                            relief="flat", bd=0, wrap="char")
        self.txt.pack(fill="x", padx=20, pady=(2, 16))

        # Action buttons
        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=20)
        theme.make_button(btns, "Generate New",
                            self._generate, color=theme.BTN_OK
                            ).pack(side="left")
        theme.make_button(btns, "Copy",
                            self._copy, color=theme.BTN_BG
                            ).pack(side="left", padx=(8, 0))
        theme.make_button(btns, "Save to file…",
                            self._export, color=theme.BTN_BG
                            ).pack(side="left", padx=(8, 0))
        theme.make_button(btns, "Close",
                            self.destroy, color=theme.BTN_DANGER
                            ).pack(side="right")

        # Help text
        help_text = (
            "Each slave needs THIS exact key. On first run, the slave "
            "prompts you to paste it.\n\n"
            "Rotating the key invalidates every slave's previous copy. "
            "Slaves will go ERROR until you re-paste the new key.")
        tk.Label(self, text=help_text, font=theme.FONT_SMALL,
                 bg=theme.BG, fg=theme.SUBTEXT,
                 wraplength=480, justify="left",
                 ).pack(padx=20, pady=(20, 0))

    # ── Behaviour ──
    def _load_existing(self) -> None:
        try:
            self._current_key = sp_security.load_key(self.key_file)
            self._show_key(self._current_key)
        except FileNotFoundError:
            self._show_key(None)
        except ValueError as e:
            messagebox.showerror(
                "Bad key file",
                f"Existing key file is corrupt: {e}\n\nGenerate a new one.",
                parent=self)
            self._show_key(None)

    def _show_key(self, key: bytes | None) -> None:
        self.txt.delete("1.0", "end")
        if key is None:
            self.txt.insert("1.0", "(no key yet — click Generate New)")
            self.txt.configure(fg=theme.SUBTEXT)
        else:
            # Insert with whitespace every 8 chars for readability — the
            # security.key_from_text helper strips whitespace on load.
            text = sp_security.key_to_text(key)
            grouped = " ".join(text[i:i+8] for i in range(0, len(text), 8))
            self.txt.insert("1.0", grouped)
            self.txt.configure(fg=theme.GREEN)

    def _generate(self) -> None:
        if self._current_key is not None:
            ok = messagebox.askyesno(
                "Replace existing key?",
                "Generating a new key will invalidate every slave's "
                "current key.\n\n"
                "You will need to re-paste the new key on every slave.\n\n"
                "Continue?",
                parent=self)
            if not ok:
                return
        new_key = sp_security.generate_key()
        sp_security.save_key(self.key_file, new_key)
        self._current_key = new_key
        self._show_key(new_key)
        if self.on_key_changed is not None:
            self.on_key_changed(new_key)
        messagebox.showinfo(
            "Key saved",
            f"New key saved to:\n{self.key_file}\n\n"
            "Copy this key to every slave.",
            parent=self)

    def _copy(self) -> None:
        if self._current_key is None:
            return
        self.clipboard_clear()
        self.clipboard_append(sp_security.key_to_text(self._current_key))

    def _export(self) -> None:
        if self._current_key is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Save key file",
            defaultextension=".key",
            initialfile="simpit.key",
            filetypes=[("Key files", "*.key"), ("All files", "*.*")])
        if path:
            sp_security.save_key(Path(path), self._current_key)
            messagebox.showinfo("Saved", f"Key saved to {path}", parent=self)
