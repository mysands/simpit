"""
simpit_control.ui.net_utils
===========================
Lightweight network helpers used by the UI layer.

Kept in a dedicated module so both app.py and dialogs/security_setup.py
can import from here without creating a circular dependency.
"""
from __future__ import annotations

import socket


def local_ips() -> list[str]:
    """Return non-loopback IPv4 addresses for this machine.

    Tries two methods so we get a useful result on machines with unusual
    network configs (no resolvable hostname, VPNs, etc.).
    """
    seen: list[str] = []
    # Method 1: hostname resolution — picks up all bound interfaces.
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = info[4][0]
            if ":" not in addr and not addr.startswith("127.") and addr not in seen:
                seen.append(addr)
    except Exception:
        pass
    # Method 2: UDP trick — finds the outbound interface to the LAN gateway.
    # Useful when the hostname doesn't resolve to a routable address.
    if not seen:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            addr = s.getsockname()[0]
            s.close()
            if addr not in seen:
                seen.append(addr)
        except Exception:
            pass
    return seen
