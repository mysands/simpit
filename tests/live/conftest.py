"""Fixtures for the live ortho-chain suite (thin pytest layer).

All check logic lives in `ortho_checks.py`, which is pytest-free so it can
be copied to a slave and run via `verify_live.py`. These fixtures just
share the config and one RREF position sample across tests.
"""
from __future__ import annotations

import pytest

from tests.live import ortho_checks as oc


@pytest.fixture(scope="session")
def cfg() -> dict:
    """Verifier config: ortho_agent.json (ORTHO_AGENT_CONFIG) over defaults."""
    return oc.load_config()


@pytest.fixture(scope="session")
def xp_pos(cfg: dict) -> dict | None:
    """One live RREF position sample, or None if X-Plane is unreachable."""
    result = oc.check_rref(cfg)
    return result.data.get("pos")
