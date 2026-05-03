"""simpit_slave._bundle_hints — keep PyInstaller static analysis honest.

PyInstaller bundles only stdlib modules that the frozen package's own
code imports. Cascaded scripts run in-process via ``runpy.run_path``
and therefore share that import space — anything *they* need but the
slave itself doesn't reach for is silently absent from the bundle and
fails at runtime with ``No module named 'X'``.

This module exists to make those imports visible to PyInstaller's
static analysis. It is imported once by ``simpit_slave/__init__.py``
at package load time. None of the names below are referenced at
runtime; the imports themselves are the entire point.

To support a new cascaded script that uses module ``foo``: add
``import foo`` here. No other code change is required.
"""
# Standard library modules cascaded scripts may need.
# Listed alphabetically so additions are easy to merge.
import datetime           # noqa: F401  -- backup_xplane filename stamping
import platform           # noqa: F401  -- block/restore_xplane_updates (legacy)
import shutil             # noqa: F401  -- general file-tree manipulation
import socket             # noqa: F401  -- backup_xplane hostname (also slave)
import subprocess         # noqa: F401  -- launch-style scripts (also slave)
import tarfile            # noqa: F401  -- backup_xplane / restore_xplane on POSIX
import zipfile            # noqa: F401  -- backup_xplane / restore_xplane on Windows
