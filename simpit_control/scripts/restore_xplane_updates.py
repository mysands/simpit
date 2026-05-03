#!/usr/bin/env python3
"""restore_xplane_updates.py — Remove simpit block entries from hosts file.
Requires admin/root.

Avoids ``import platform`` for the same PyInstaller-bundle reason
documented in ``block_xplane_updates.py``.
"""
import os
import sys

BLOCK_MARKER = "# simpit: block xplane updates"

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

    filtered = [l for l in lines if BLOCK_MARKER not in l]
    # Strip trailing blank lines the removal may leave.
    while filtered and not filtered[-1].strip():
        filtered.pop()

    if len(filtered) == len(lines):
        print("No simpit block entries found — nothing to remove.")
        return 0

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(filtered) + "\n")
    except PermissionError:
        print(f"ERROR: permission denied writing {path} — run as admin/root",
              file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    removed = len(lines) - len(filtered)
    print(f"Removed {removed} block entries from {path}.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
