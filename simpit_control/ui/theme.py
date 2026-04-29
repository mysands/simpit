"""
simpit_control.ui.theme
=======================
All visual constants and the button factory.

Every color, font, and reusable visual element lives here. Widgets
import from this module so a re-skin is a one-file change. The original
codebase had these constants scattered across the main file; centralizing
them is the single biggest readability win.

The palette is dark-on-dark cockpit-styled, kept identical to the
original for continuity (existing screenshots/docs still apply).
Status colors map to :class:`simpit_control.poller.SlaveState` values
in :func:`color_for_state` so a state addition only needs one update
here, not in every widget that paints a status.

Touch guidelines applied
------------------------
Apple HIG / WCAG 2.5.5 minimum: 44×44 logical pixels per target.
Google Material: 48×48dp recommended. Microsoft WinUI: 40×40px.
We target 44px height on all interactive elements via TOUCH_PADY,
and TOUCH_SPACING (8px) between adjacent targets.

On Windows, touch drivers translate finger events to mouse events
transparently — no special tkinter event handling is required.
DPI awareness is enabled in __main__ before Tk() is created so
the app is physically the right size on high-DPI / 4K displays.
"""
from __future__ import annotations

# ── Palette ──────────────────────────────────────────────────────────────────
BG          = "#12161E"   # window background
PANEL       = "#1A202C"   # secondary background (header, footer)
BORDER      = "#2D3748"   # 1-pixel separator lines
SECTION_BG  = "#0F172A"   # section panel background
SECTION_HDR = "#162032"   # section header strip
ENTRY_BG    = "#0F172A"   # text entry background

TEXT        = "#E2E8F0"   # primary text
SUBTEXT     = "#64748B"   # captions, labels, hints

ACCENT      = "#38BDF8"   # primary accent (cyan)
ACCENT_ALT  = "#A78BFA"   # secondary accent (purple — used for slave mode)

# Status colors. The state-to-color map below is the single source of
# truth — never hardcode these in widgets.
GREEN       = "#34D399"
AMBER       = "#FBBF24"
RED         = "#F87171"
BLUE        = "#3B82F6"
GREY        = "#475569"

BTN_BG      = "#1E3A5F"   # default button background
BTN_HOV     = "#2563EB"   # button hover
BTN_DANGER  = "#3B1F1F"   # destructive (cancel, delete)
BTN_OK      = "#065F46"   # confirm (save)
BTN_AMBER   = "#7C5A00"   # toggle-active background


# ── Fonts ────────────────────────────────────────────────────────────────────
FONT_TITLE       = ("Consolas", 15, "bold")
FONT_HEADING     = ("Consolas", 12, "bold")
FONT_BODY        = ("Consolas", 11)
FONT_BODY_BOLD   = ("Consolas", 11, "bold")
FONT_SMALL       = ("Consolas", 10)
FONT_TINY        = ("Consolas", 9)
FONT_MONO        = ("Consolas", 10)


# ── Touch sizing ─────────────────────────────────────────────────────────────
# Minimum interactive target height per Apple HIG / WCAG 2.5.5: 44px.
# TOUCH_PADY is the vertical inner padding added to buttons to reach that
# height when combined with the font height (~18px for FONT_BODY_BOLD).
# TOUCH_SPACING is the minimum gap between adjacent interactive elements.
TOUCH_PADY    = 10   # ipady on buttons  →  ~18px text + 2*10 = 38px + border ≈ 44px
TOUCH_SPACING = 8    # padx/pady between buttons


# ── State -> color mapping ───────────────────────────────────────────────────
STATE_COLORS: dict[str, str] = {
    "unknown":  GREY,
    "offline":  RED,
    "online":   AMBER,
    "running":  GREEN,
    "syncing":  BLUE,
    "error":    RED,
}

STATE_LABELS: dict[str, str] = {
    "unknown":  "UNKNOWN",
    "offline":  "OFFLINE",
    "online":   "ONLINE",
    "running":  "RUNNING",
    "syncing":  "SYNCING",
    "error":    "ERROR",
}


def color_for_state(state: str) -> str:
    """Return the canonical color for a SlaveState string value."""
    return STATE_COLORS.get(state, GREY)


def label_for_state(state: str) -> str:
    """Return the canonical UPPERCASE display label for a state."""
    return STATE_LABELS.get(state, state.upper())


# ── Button factory ───────────────────────────────────────────────────────────
def make_button(parent, text: str, command,
                width: int | None = None,
                color: str = BTN_BG,
                font: tuple = FONT_BODY_BOLD):
    """Build a flat dark-themed button with hover effect.

    Vertical padding is set to TOUCH_PADY so all buttons meet the 44px
    minimum touch target height on standard-DPI displays.

    Returns a tk.Button instance. We import tkinter lazily so the rest
    of this module is import-cheap and theme constants can be used by
    pure-logic code without dragging Tk in.
    """
    import tkinter as tk
    kwargs = dict(
        text=text, font=font,
        bg=color, fg=TEXT,
        activebackground=BTN_HOV, activeforeground=TEXT,
        relief="flat", bd=0, cursor="hand2",
        padx=16, pady=TOUCH_PADY, command=command,
    )
    if width is not None:
        kwargs["width"] = width
    btn = tk.Button(parent, **kwargs)
    btn.bind("<Enter>", lambda e: btn.config(bg=BTN_HOV))
    btn.bind("<Leave>", lambda e: btn.config(bg=color))
    return btn


def enable_dpi_awareness() -> None:
    """Enable per-monitor DPI awareness on Windows.

    Must be called BEFORE Tk() is created. Without this, Windows
    renders the app blurry and scales it down on high-DPI / 4K
    displays, making touch targets physically smaller than designed.
    No-ops on non-Windows platforms.
    """
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # Fallback for older Windows versions
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

