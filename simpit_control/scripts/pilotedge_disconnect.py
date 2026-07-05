"""Send PilotEdge/Disconnect command to X-Plane via UDP CMND packet."""
import socket

_HOST = "127.0.0.1"
_PORT = 49000
_CMD  = "PilotEdge/Disconnect"

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    s.sendto(b"CMND\x00" + _CMD.encode("utf-8") + b"\x00", (_HOST, _PORT))

print(f"Sent {_CMD} to {_HOST}:{_PORT}")
