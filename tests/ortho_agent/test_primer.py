"""Tests for simpit_ortho_agent.primer (worker thread, tmp_path files).

The read order/kind is observed by wrapping Primer._read with a
recorder, so the tests see exactly what the worker did without
touching its threading.
"""
from __future__ import annotations

import time
from pathlib import Path

from simpit_ortho_agent.primer import Primer


def _make_atlases(root: Path, names: list[str]) -> None:
    tex = root / "zOrtho4XP_Z18_+42-073" / "textures"
    tex.mkdir(parents=True, exist_ok=True)
    for name in names:
        (tex / name).write_bytes(b"\x00" * 1024)


def _rel(name: str) -> str:
    return f"zOrtho4XP_Z18_+42-073/textures/{name}"


def _recording(primer: Primer) -> list[tuple[str, bool]]:
    """Wrap primer._read to record (rel_path, full) per executed job."""
    reads: list[tuple[str, bool]] = []
    real = primer._read

    def wrapped(rel_path: str, full: bool) -> bool:
        reads.append((rel_path, full))
        return real(rel_path, full)

    primer._read = wrapped
    return reads


def _wait_idle(primer: Primer, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if primer.idle():
            return
        time.sleep(0.01)
    raise AssertionError("primer never drained its queue")


def test_priming_order_is_the_submitted_nearest_first_order(tmp_path):
    """The worker reads atlases exactly in the order schedule() gave."""
    names = [f"{16 * i}_0_BI16.dds" for i in range(4)]
    _make_atlases(tmp_path, names)
    primer = Primer(tmp_path, touch_interval_seconds=3600.0)
    reads = _recording(primer)
    primer.schedule([_rel(n) for n in names])
    primer.start()
    _wait_idle(primer)
    primer.stop()
    assert [r for r, _ in reads] == [_rel(n) for n in names]
    assert all(full for _, full in reads)          # first pass = full primes


def test_keep_warm_retouches_after_interval_only(tmp_path):
    """Warm atlases are re-touched once touch_interval elapses — not
    before — and the re-touch is a cheap touch, not a full prime."""
    _make_atlases(tmp_path, ["0_0_BI16.dds"])
    primer = Primer(tmp_path, touch_interval_seconds=0.2)
    reads = _recording(primer)
    primer.start()
    assert primer.schedule([_rel("0_0_BI16.dds")]) == 1
    _wait_idle(primer)
    assert primer.is_warm(_rel("0_0_BI16.dds"))
    assert primer.schedule([_rel("0_0_BI16.dds")]) == 0    # too soon
    time.sleep(0.25)
    assert primer.schedule([_rel("0_0_BI16.dds")]) == 1    # touch due
    _wait_idle(primer)
    primer.stop()
    assert [full for _, full in reads] == [True, False]    # prime, then touch


def test_leaving_the_keep_set_stops_touches_and_forgets_warmth(tmp_path):
    """An atlas that leaves the keep set is never touched again, and a
    re-entry gets a FULL prime (it may have been evicted meanwhile)."""
    _make_atlases(tmp_path, ["0_0_BI16.dds", "16_0_BI16.dds"])
    a, b = _rel("0_0_BI16.dds"), _rel("16_0_BI16.dds")
    primer = Primer(tmp_path, touch_interval_seconds=0.05)
    reads = _recording(primer)
    primer.start()
    primer.schedule([a, b])
    _wait_idle(primer)
    time.sleep(0.1)
    primer.schedule([b])                    # `a` left the keep set
    _wait_idle(primer)
    assert not primer.is_warm(a)
    time.sleep(0.1)
    primer.schedule([a, b])                 # `a` re-enters
    _wait_idle(primer)
    primer.stop()
    a_reads = [full for rel, full in reads if rel == a]
    assert a_reads == [True, True]          # full prime both times, no touch
    b_reads = [full for rel, full in reads if rel == b]
    assert b_reads[0] is True and not any(b_reads[1:])   # then touches only


def test_missing_file_is_skipped_and_retried_later(tmp_path):
    """Water/unbuilt (or NAS hiccup): debug-skip, never warm, no crash."""
    primer = Primer(tmp_path, touch_interval_seconds=0.05)
    primer.start()
    primer.schedule([_rel("64_64_BI16.dds")])       # file doesn't exist
    _wait_idle(primer)
    assert not primer.is_warm(_rel("64_64_BI16.dds"))
    # Once the file appears (transient error cleared), priming works.
    _make_atlases(tmp_path, ["64_64_BI16.dds"])
    time.sleep(0.06)
    primer.schedule([_rel("64_64_BI16.dds")])
    _wait_idle(primer)
    primer.stop()
    assert primer.is_warm(_rel("64_64_BI16.dds"))


def test_reschedule_replaces_pending_queue(tmp_path):
    """A new schedule() replaces stale pending work with the new order."""
    names = [f"{16 * i}_0_BI16.dds" for i in range(3)]
    _make_atlases(tmp_path, names)
    primer = Primer(tmp_path, touch_interval_seconds=3600.0)
    reads = _recording(primer)
    primer.schedule([_rel(n) for n in names])           # worker not started
    primer.schedule([_rel(names[2])])                   # turn: new priority
    primer.start()
    _wait_idle(primer)
    primer.stop()
    assert [r for r, _ in reads] == [_rel(names[2])]


def test_clear_pending_pauses_without_forgetting_warmth(tmp_path):
    """SIM_OFFLINE pause: queue drops, warm map survives."""
    _make_atlases(tmp_path, ["0_0_BI16.dds", "16_0_BI16.dds"])
    a, b = _rel("0_0_BI16.dds"), _rel("16_0_BI16.dds")
    primer = Primer(tmp_path, touch_interval_seconds=3600.0)
    primer.start()
    primer.schedule([a])
    _wait_idle(primer)
    primer.stop()                          # freeze the worker first —
    primer.schedule([a, b])                # deterministic queue state
    assert not primer.idle()               # b is pending (a still warm)
    primer.clear_pending()
    assert primer.idle()                   # pause dropped the queue...
    assert primer.is_warm(a)               # ...but warmth survives


def test_primer_never_writes_into_the_scenery_tree(tmp_path):
    """Priming is reads only — the cache/mount is never written to."""
    _make_atlases(tmp_path, ["0_0_BI16.dds"])
    before = sorted(p for p in tmp_path.rglob("*"))
    mtimes = {p: p.stat().st_mtime_ns for p in before if p.is_file()}
    primer = Primer(tmp_path, touch_interval_seconds=3600.0)
    primer.start()
    primer.schedule([_rel("0_0_BI16.dds")])
    _wait_idle(primer)
    primer.stop()
    assert sorted(p for p in tmp_path.rglob("*")) == before
    assert {p: p.stat().st_mtime_ns
            for p in before if p.is_file()} == mtimes
