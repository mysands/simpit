#!/usr/bin/env python3
"""
quit_xplane.py — SimPit standard script

Sends a single X-Plane CMND UDP packet to the locally running X-Plane
instance asking it to quit. Cascaded to every slave; each slave fires
the packet at its own loopback, so no IPs need tracking.

X-Plane CMND wire format
------------------------
Header : the four ASCII bytes "CMND" followed by one null byte (5 total)
Body   : the command string followed by one null byte

X-Plane listens on UDP port 49000 by default. The "Accept UDP commands"
toggle in Settings → Network must be on, otherwise the packet is
silently dropped.

Exits 0 once the packet is sent. UDP is fire-and-forget — there's no
reply to wait for and no way to confirm receipt at this layer. If
X-Plane wasn't running, the OS will swallow the packet and we still
exit 0; that's harmless.
"""
from __future__ import annotations

import socket
import sys

XP_HOST = "127.0.0.1"
XP_PORT = 49000
COMMAND = "sim/operation/quit"


def main() -> int:
    packet = b"CMND\x00" + COMMAND.encode("ascii") + b"\x00"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(packet, (XP_HOST, XP_PORT))
    except OSError as exc:
        print(f"ERROR: send failed: {exc}", file=sys.stderr)
        return 1
    print(f"Sent CMND '{COMMAND}' to {XP_HOST}:{XP_PORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
