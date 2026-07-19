"""Live verification of the ortho scenery cache chain (pytest wrappers).

The real check logic is in `ortho_checks.py`; on a slave machine run it
via `verify_live.py` instead (no pytest needed). On a dev machine:

    pytest -m live tests/live -v

Each wrapper maps a CheckResult onto pytest semantics: SKIP results skip
with the check's reason, FAIL results fail with it. The suite doubles as
a deployment checklist:

    mount reachable → atlas scheme intact → Custom Scenery link OK →
    rc API up → rclone actually caches reads → X-Plane feed OK →
    agent primes current + lookahead atlases
"""
from __future__ import annotations

import pytest

from tests.live import ortho_checks as oc

pytestmark = pytest.mark.live


def _apply(result: oc.CheckResult) -> None:
    """Translate a CheckResult into pytest pass/fail/skip."""
    if result.status == oc.SKIP:
        pytest.skip(result.message)
    assert result.status == oc.PASS, f"{result.name}: {result.message}"
    print(f"\n{result.name}: {result.message}")


def test_mount_accessible(cfg: dict):
    """PASS: mount lists zOrtho4XP folders. FAIL: mount down/empty."""
    _apply(oc.check_mount(cfg))


def test_atlas_naming_scheme(cfg: dict):
    """PASS: real filenames match the verified atlas scheme. FAIL: the
    scheme changed and agent tilemath assumptions are stale."""
    _apply(oc.check_atlas_naming(cfg))


def test_custom_scenery_link_resolves_to_mount(cfg: dict):
    """PASS: Custom Scenery junction/symlink resolves onto the mount.
    FAIL: link broken. SKIP: no links created yet."""
    _apply(oc.check_scenery_link(cfg))


def test_rc_stats_and_cache_under_cap(cfg: dict):
    """PASS: rc answers and cache is within the cap. FAIL: rc down or
    cache runaway (mount flags wrong)."""
    _apply(oc.check_rc_cache(cfg))


def test_read_through_mount_populates_cache(cfg: dict):
    """PASS: a full read of an uncached atlas lands it in the VFS cache —
    the exact mechanism priming relies on. FAIL: cache mode not 'full' or
    cache being purged (--vfs-cache-max-age misconfigured)."""
    _apply(oc.check_cache_write(cfg))


def test_xplane_position_feed_sane(cfg: dict):
    """PASS: RREF delivers a plausible position sample. FAIL: X-Plane not
    running, port blocked, or garbled decode."""
    _apply(oc.check_rref(cfg))


def test_agent_primed_current_atlas(cfg: dict, xp_pos: dict | None):
    """PASS: atlas under the aircraft already cached (agent primed it).
    FAIL: agent running but not priming. SKIP: agent not running."""
    _apply(oc.check_agent_current(cfg, xp_pos))


def test_agent_primed_lookahead_atlas(cfg: dict, xp_pos: dict | None):
    """PASS: atlas at the ground-track lookahead position cached ahead of
    arrival. FAIL: lookahead priming broken. SKIP: agent not running or
    aircraft stationary."""
    _apply(oc.check_agent_lookahead(cfg, xp_pos))
