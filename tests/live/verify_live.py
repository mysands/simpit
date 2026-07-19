#!/usr/bin/env python
"""Standalone ortho-chain verifier for live X-Plane machines.

Deploy by copying TWO files to the target machine (no repo, no pytest,
no packages needed - stdlib only, Python 3.10+):

    ortho_checks.py
    verify_live.py

Run with X-Plane up and the rclone mount running:

    python verify_live.py [path\\to\\ortho_agent.json]

Checks run in dependency order and print PASS / FAIL / SKIP with a
reason each. Exit code 0 = no failures, 1 = at least one FAIL, so the
script can be called from a deploy batch file.

The agent checks SKIP (not FAIL) when no ortho agent process is running,
so this same script verifies a machine both before agent deployment
(mount + cache + X-Plane feed) and after (priming acceptance).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ortho_checks as oc  # noqa: E402


def main(argv: list[str]) -> int:
    """Run the live check sequence and print a summary.

    Args:
        argv: process argv; argv[1] may name an ortho_agent.json.

    Returns:
        0 if no check failed, 1 otherwise.
    """
    cfg = oc.load_config(argv[1] if len(argv) > 1 else None)
    print(f"ortho live verification - mount {cfg['mount_root']}, "
          f"rc {cfg['rc_addr']}, master {cfg['master_ip']}:"
          f"{cfg['xp_udp_port']}\n")

    results: list[oc.CheckResult] = []

    def run(fn, *args) -> oc.CheckResult:
        start = time.perf_counter()
        result = fn(*args)
        elapsed = time.perf_counter() - start
        print(f"[{result.status:4}] {result.name:16} {result.message} "
              f"({elapsed:.1f}s)")
        results.append(result)
        return result

    run(oc.check_mount, cfg)
    run(oc.check_atlas_naming, cfg)
    run(oc.check_scenery_link, cfg)
    run(oc.check_rc_cache, cfg)
    run(oc.check_cache_write, cfg)
    rref = run(oc.check_rref, cfg)
    pos = rref.data.get("pos")
    run(oc.check_agent_current, cfg, pos)
    run(oc.check_agent_lookahead, cfg, pos)

    counts = {s: sum(1 for r in results if r.status == s)
              for s in (oc.PASS, oc.FAIL, oc.SKIP)}
    print(f"\n{counts[oc.PASS]} passed, {counts[oc.FAIL]} failed, "
          f"{counts[oc.SKIP]} skipped")
    return 1 if counts[oc.FAIL] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
