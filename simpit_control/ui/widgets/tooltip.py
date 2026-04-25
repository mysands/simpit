"""Lightweight tooltip — hover over a widget to see help text."""
from __future__ import annotations

import tkinter as tk

from .. import theme


class Tooltip:
    """A delayed tooltip that pops up below the bound widget on hover."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None) -> None:
        if not self.text or self.tip is not None:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.tip, text=self.text, font=theme.FONT_SMALL,
            bg=theme.BTN_BG, fg=theme.TEXT, relief="flat",
            padx=8, pady=4,
        ).pack()

    def _hide(self, _event=None) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
