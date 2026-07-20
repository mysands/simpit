"""Tests for UI widgets — instantiate under Xvfb and verify they render.

These are smoke tests: we're checking the widget can be built from a
view-model without crashing, that it contains the expected text, and
that callbacks fire when buttons are clicked. We don't pixel-test the
rendering — that's a Tk version pain trap.

On Linux, set DISPLAY=:99 (or any other X server) to run; tests skip
cleanly if no display is available. Windows and macOS have native
display servers and don't use DISPLAY, so the guard is Linux-only.
"""
import os
import sys
import time

import pytest

# Skip the whole module if we're on Linux without a display. Windows
# and macOS have native display servers; tkinter works there without
# DISPLAY being set, so we don't gate them on it.
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    pytest.skip("no DISPLAY available", allow_module_level=True)

import tkinter as tk

from simpit_control import data as sp_data
from simpit_control import poller as sp_poller
from simpit_control.ui import viewmodels as vm
from simpit_control.ui.widgets import (
    BatFileListWidget,
    LogPanel,
    SlaveCardWidget,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def root(tk_session_root):
    """A hidden Toplevel under the session-scoped Tk root.

    We use Toplevel rather than constructing a fresh Tk() because
    multiple Tk roots in the same process is unsupported and the
    destroy-then-recreate cycle fails on Python 3.14 + Windows. The
    session_root fixture (in conftest.py) keeps a single Tk alive for
    the whole session; each test gets its own throwaway Toplevel as
    a parent for its widgets.
    """
    top = tk.Toplevel(tk_session_root)
    top.withdraw()
    yield top
    try:
        top.update_idletasks()
        top.destroy()
    except tk.TclError:
        pass


# ── SlaveCardWidget ──────────────────────────────────────────────────────────
class TestSlaveCardWidget:
    def _make_vm(self, state=sp_poller.SlaveState.ONLINE):
        slave = sp_data.Slave(id="s1", name="CENTERLEFT", host="10.0.0.5")
        status = sp_poller.SlaveStatus(
            slave_id="s1", state=state, last_seen=time.time())
        return vm.SlaveCardVM.build(slave, status)

    def test_renders_without_error(self, root):
        card_vm = self._make_vm()
        widget = SlaveCardWidget(root, card_vm)
        widget.pack()
        root.update_idletasks()
        # If we got here without exception, success.
        assert widget.vm == card_vm

    def test_shows_slave_name(self, root):
        card_vm = self._make_vm()
        widget = SlaveCardWidget(root, card_vm)
        # Find the label containing the name in the widget tree.
        labels = self._all_label_text(widget)
        assert "CENTERLEFT" in labels

    def test_offline_shows_offline_label(self, root):
        card_vm = self._make_vm(state=sp_poller.SlaveState.OFFLINE)
        widget = SlaveCardWidget(root, card_vm)
        labels = self._all_label_text(widget)
        assert "OFFLINE" in " ".join(labels)

    def test_sync_callback_fires(self, root):
        called = []
        widget = SlaveCardWidget(root, self._make_vm(),
                                    on_sync=lambda sid: called.append(sid))
        widget.pack()
        root.update_idletasks()
        # Find the Sync button and invoke its command directly.
        sync_btn = self._find_button(widget, "Sync")
        assert sync_btn is not None
        sync_btn.invoke()
        assert called == ["s1"]

    def test_offline_disables_sync(self, root):
        called = []
        vm_off = self._make_vm(state=sp_poller.SlaveState.OFFLINE)
        widget = SlaveCardWidget(root, vm_off,
                                    on_sync=lambda sid: called.append(sid))
        widget.pack()
        root.update_idletasks()
        sync_btn = self._find_button(widget, "Sync")
        sync_btn.invoke()
        # Disabled button's command is a no-op lambda — callback shouldn't fire.
        assert called == []

    def test_update_vm_changes_display(self, root):
        widget = SlaveCardWidget(root, self._make_vm())
        widget.pack()
        new_vm = self._make_vm(state=sp_poller.SlaveState.RUNNING)
        widget.update_vm(new_vm)
        root.update_idletasks()
        labels = " ".join(self._all_label_text(widget))
        assert "RUNNING" in labels

    # ── Helpers ──
    def _all_label_text(self, widget: tk.Widget) -> list[str]:
        out = []
        for w in self._walk(widget):
            if isinstance(w, tk.Label):
                txt = w.cget("text")
                if txt:
                    out.append(str(txt))
        return out

    def _find_button(self, widget: tk.Widget,
                       text: str) -> tk.Button | None:
        for w in self._walk(widget):
            if isinstance(w, tk.Button):
                if w.cget("text") == text:
                    return w
        return None

    def _walk(self, w: tk.Widget):
        yield w
        for child in w.winfo_children():
            yield from self._walk(child)


# ── LogPanel ─────────────────────────────────────────────────────────────────
class TestLogPanel:
    def test_renders(self, root):
        panel = LogPanel(root)
        panel.pack()
        root.update_idletasks()
        assert panel.error_count == 0

    def test_append_and_clear(self, root):
        panel = LogPanel(root)
        panel.pack()
        panel.append("hello", tag="ok")
        # append goes via after(0); flush the queue so the text actually lands.
        root.update_idletasks()
        contents = panel.txt.get("1.0", "end")
        assert "hello" in contents
        panel.clear()
        contents = panel.txt.get("1.0", "end").strip()
        assert contents == ""

    def test_error_count_increments(self, root):
        panel = LogPanel(root)
        panel.pack()
        panel.append("oops", tag="error")
        panel.append("bad", tag="error")
        root.update_idletasks()
        assert panel.error_count == 2


# ── BatFileListWidget ────────────────────────────────────────────────────────
class TestBatFileListWidget:
    def test_empty_renders_placeholder(self, root):
        widget = BatFileListWidget(root, slaves=[], rows=[])
        widget.pack()
        root.update_idletasks()
        # Find a label that mentions "No scripts"
        all_labels = []
        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)
        for w in walk(widget):
            if isinstance(w, tk.Label):
                all_labels.append(w.cget("text"))
        assert any("No scripts" in str(t) for t in all_labels)

    def test_renders_rows(self, root):
        slave_vm = vm.SlaveCardVM.build(
            sp_data.Slave(id="s1", name="X", host="h"),
            sp_poller.SlaveStatus(slave_id="s1",
                                    state=sp_poller.SlaveState.ONLINE,
                                    last_seen=time.time()),
        )
        bat_vm = vm.BatFileRowVM.build(
            sp_data.BatFile(id="b1", name="Launch X-Plane",
                              script_name="launch_xplane",
                              cascade=True, content="echo"),
            ["s1"], {},
        )
        widget = BatFileListWidget(root, slaves=[slave_vm],
                                       rows=[bat_vm])
        widget.pack()
        root.update_idletasks()
        text_blob = " ".join(self._all_text(widget))
        assert "Launch X-Plane" in text_blob

    def test_run_callback_fires(self, root):
        slave_vm = vm.SlaveCardVM.build(
            sp_data.Slave(id="s1", name="X", host="h"),
            sp_poller.SlaveStatus(slave_id="s1",
                                    state=sp_poller.SlaveState.ONLINE,
                                    last_seen=time.time()),
        )
        bat_vm = vm.BatFileRowVM.build(
            sp_data.BatFile(id="b1", name="Run", script_name="r",
                              cascade=True, content="x"),
            ["s1"], {},
        )
        called = []
        widget = BatFileListWidget(
            root, slaves=[slave_vm], rows=[bat_vm],
            on_run=lambda sid, bid: called.append((sid, bid)))
        widget.pack()
        root.update_idletasks()
        # Find the ▶ button
        btn = None
        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)
        for w in walk(widget):
            if isinstance(w, tk.Button) and w.cget("text") == "▶":
                btn = w; break
        assert btn is not None
        btn.invoke()
        assert called == [("s1", "b1")]

    def _make_rows(self, n):
        return [vm.BatFileRowVM.build(
                    sp_data.BatFile(id=f"b{i}", name=f"Script {i}",
                                    script_name=f"s{i}", cascade=False,
                                    content="echo"),
                    [], {})
                for i in range(n)]

    def _show(self, root, widget, geometry):
        """Map the window for real — scrollbar visibility and canvas
        heights are geometry facts that don't exist while withdrawn."""
        widget.pack(fill="both", expand=True)
        root.geometry(geometry)
        root.deiconify()
        root.update()

    def test_many_rows_scroll_into_view(self, root):
        """Rows beyond the visible height are reachable by scrolling —
        the panel used to clip everything below the window edge."""
        widget = BatFileListWidget(root, slaves=[], rows=self._make_rows(30))
        self._show(root, widget, "500x300")          # too short for 30 rows
        canvas = widget._canvas
        assert canvas is not None
        assert widget._vsb.winfo_ismapped()          # scrollbar appeared
        assert canvas.yview()[1] < 1.0               # content overflows
        canvas.yview_moveto(1.0)
        root.update()
        assert canvas.yview()[1] == pytest.approx(1.0)   # bottom reachable

    def test_scrollbar_hidden_when_content_fits(self, root):
        widget = BatFileListWidget(root, slaves=[], rows=self._make_rows(2))
        self._show(root, widget, "500x600")
        assert not widget._vsb.winfo_ismapped()

    def test_update_data_keeps_scroll_position(self, root):
        """The dashboard rebuilds this list every poll tick; the view
        must not snap back to the top each time."""
        rows = self._make_rows(30)
        widget = BatFileListWidget(root, slaves=[], rows=rows)
        self._show(root, widget, "500x300")
        widget._canvas.yview_moveto(0.5)
        root.update()
        before = widget._canvas.yview()[0]
        assert before > 0.0
        widget.update_data([], rows)
        root.update()
        assert widget._canvas.yview()[0] == pytest.approx(before, abs=0.05)

    def _all_text(self, widget):
        out = []
        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)
        for w in walk(widget):
            if isinstance(w, tk.Label):
                t = w.cget("text")
                if t:
                    out.append(str(t))
        return out
