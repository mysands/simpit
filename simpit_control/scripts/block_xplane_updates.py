#!/usr/bin/env python3
"""block_xplane_updates.py — Add xplane.com to hosts to block update checks.
Requires admin/root. Called by SimPit Control via EXEC_SCRIPT (needs_admin=True).

Why no ``import platform``:
    The slave is shipped as a PyInstaller bundle. PyInstaller's static
    analysis only bundles stdlib modules the slave's own code imports.
    Cascaded user scripts run in-process via ``runpy.run_path``, so
    they share that bundle's import space — meaning anything the slave
    doesn't already import is unavailable. The slave imports
    ``simpit_common.platform`` but never stdlib ``platform``, so
    ``import platform`` in this script would fail with
    ``No module named 'platform'``. ``os.name`` answers the same
    question and is already pulled in by the slave.
"""
import os
import sys

BLOCK_MARKER = "# simpit: block xplane updates"
BLOCK_ENTRIES = [
    f"0.0.0.0 updater.x-plane.com  {BLOCK_MARKER}",
    f"0.0.0.0 store.x-plane.com    {BLOCK_MARKER}",
]

def hosts_path():
    if os.name == "nt":
        return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                            "System32", "drivers", "etc", "hosts")
    return "/etc/hosts"

def main():
    path = hosts_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        print(f"ERROR: cannot read {path}: {e}", file=sys.stderr)
        return 1

    existing = {l.strip() for l in lines}
    to_add = [e for e in BLOCK_ENTRIES if e.strip() not in existing]
    if not to_add:
        print("X-Plane update hosts entries already present.")
        return 0

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(to_add) + "\n")
    except PermissionError:
        print(f"ERROR: permission denied writing {path} — run as admin/root",
              file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Added {len(to_add)} block entries to {path}.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
