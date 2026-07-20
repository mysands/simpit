"""
simpit_common
=============
Shared primitives used by both simpit_control and simpit_slave.

Modules:
    protocol     - Wire format (JSON-over-{UDP,TCP}), command names, envelope
    security     - HMAC-SHA256 signing/verification, key management
    platform     - OS abstraction (paths, processes, script invocation)
    probes       - Extensible state-query primitives
    xp_rref      - X-Plane RREF UDP dataref wire helpers
    tilemath     - Slippy-tile / Ortho4XP-atlas math (pure)
    ortho_config - Ortho cache agent config: typed load/save + fleet merge

Public API is re-exported here for convenience.
"""
from . import ortho_config, platform, probes, protocol, security, tilemath, xp_rref

__version__ = "0.1.0"

__all__ = ["protocol", "security", "platform", "probes", "xp_rref",
           "tilemath", "ortho_config", "__version__"]
