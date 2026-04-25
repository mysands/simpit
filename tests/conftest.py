"""Shared pytest fixtures and path setup."""
import sys
from pathlib import Path

# Make `simpit_common`, `simpit_slave`, `simpit_control` importable when
# tests are run from the repo root via `pytest`.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
