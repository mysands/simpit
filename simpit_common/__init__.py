"""
simpit_common
=============
Shared primitives used by both simpit_control and simpit_slave.

Modules:
    protocol  - Wire format (JSON-over-{UDP,TCP}), command names, envelope
    security  - HMAC-SHA256 signing/verification, key management
    platform  - OS abstraction (paths, processes, script invocation)
    probes    - Extensible state-query primitives

Public API is re-exported here for convenience.
"""
from . import platform, probes, protocol, security

__version__ = "0.1.0"

__all__ = ["protocol", "security", "platform", "probes", "__version__"]
