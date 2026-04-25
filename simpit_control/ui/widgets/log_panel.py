"""
Activity log panel.

Shows a stream of user-facing events with timestamp + tag. Used by the
controller's OpResult callbacks and the poller's state-change events
to give the operator a running narrative of what's happening across
the fleet.

Tags map to colors so 'error' lines stand out red, 'ok' lines green,
etc. The panel keeps its own state (lines added) and offers clear() so
the user can reset between sessions without restarting the app.

The panel is intentionally a Frame, not a Toplevel — the main window
embeds it. A separate :meth:`dump_to_file` helper lets the user save
the current log for sharing in a bug report.
"""
from __future__ import annotations

import datetime
import tkinter as tk
from pathlib import Path

from .. import theme


class LogPanel(tk.Frame):
    """Scrollable, timestamped, color-tagged log."""

    def __init__(self, parent: tk.Widget, height: int = 8):
        super().__init__(parent, bg=theme.BG)
        self._error_count = 0
        self._build(height)

    def _build(self, height: int) -> None:
        # Header strip: title + clear button + error counter
        hdr = tk.Frame(self, bg=theme.BG)
        hdr.pack(fill="x")
        tk.Label(hdr, text="ACTIVITY LOG", font=theme.FONT_TINY,
                 bg=theme.BG, fg=theme.SUBTEXT).pack(side="left")
        self._lbl_errors = tk.Label(hdr, text="", font=theme.FONT_TINY,
                                      bg=theme.BG, fg=theme.RED)
        self._lbl_errors.pack(side="left", padx=(8, 0))
        tk.Button(hdr, text="Clear", font=theme.FONT_TINY,
                  bg=theme.BTN_DANGER, fg=theme.TEXT, relief="flat",
                  bd=0, cursor="hand2", padx=8, pady=2,
                  command=self.clear).pack(side="right")

        # Text + scrollbar
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, pady=(4, 0))
        self.txt = tk.Text(
            body, height=height, font=theme.FONT_MONO,
            bg=theme.ENTRY_BG, fg=theme.GREEN,
            insertbackground=theme.GREEN, relief="flat", bd=0,
            state="disabled", wrap="word",
        )
        scroll = tk.Scrollbar(body, command=self.txt.yview)
        self.txt.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)

        # Color tags
        for tag, color in [("ok", theme.GREEN), ("error", theme.RED),
                           ("warn", theme.AMBER), ("accent", theme.ACCENT),
                           ("muted", theme.SUBTEXT)]:
            self.txt.tag_config(tag, foreground=color)

    # ── Public API ──
    def append(self, msg: str, tag: str = "ok") -> None:
        """Add one line to the log. Safe to call from any thread.

        When called from the main thread, we update the widget directly
        — that's the common case (most append calls come from controller
        callbacks invoked synchronously inside event handlers). When
        called from a worker thread, we defer via ``after(0, ...)`` so
        Tk widget access stays main-thread-only.

        The synchronous main-thread path is also what makes this widget
        testable without a mainloop: tests can call append() and read
        back contents on the next line without spinning the event loop.
        """
        import threading
        if threading.current_thread() is threading.main_thread():
            self._do_append(msg, tag)
            return
        try:
            self.after(0, self._do_append, msg, tag)
        except RuntimeError:
            # Widget destroyed mid-callback; ignore.
            pass

    def _do_append(self, msg: str, tag: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.txt.config(state="normal")
        self.txt.insert("end", f"[{ts}] ", "muted")
        self.txt.insert("end", msg + "\n", tag)
        self.txt.see("end")
        self.txt.config(state="disabled")
        if tag == "error":
            self._error_count += 1
            self._lbl_errors.config(text=f"● {self._error_count} errors")

    def clear(self) -> None:
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.config(state="disabled")
        self._error_count = 0
        self._lbl_errors.config(text="")

    def dump_to_file(self, path: Path) -> None:
        """Write the current log contents to a file (for bug reports)."""
        text = self.txt.get("1.0", "end-1c")
        Path(path).write_text(text, encoding="utf-8")

    @property
    def error_count(self) -> int:
        return self._error_count
