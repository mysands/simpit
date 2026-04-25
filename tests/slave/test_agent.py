"""Tests for simpit_slave.agent — real network round-trips on loopback.

These tests boot a real Agent on an OS-assigned port, drive it from the
test process, and tear it down. They verify the protocol works end to
end: signing, parsing, dispatch, response.
"""
import os
import socket
import time

import pytest

from simpit_common import platform as sp_platform
from simpit_common import protocol as sp_protocol
from simpit_common import security as sp_security
from simpit_slave import agent as sp_agent
from simpit_slave import data as sp_data


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def free_port_pair():
    """Return (udp_port, tcp_port). OS picks them; we close immediately."""
    s_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s_udp.bind(("127.0.0.1", 0))
    udp_port = s_udp.getsockname()[1]
    s_udp.close()
    s_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s_tcp.bind(("127.0.0.1", 0))
    tcp_port = s_tcp.getsockname()[1]
    s_tcp.close()
    return udp_port, tcp_port


@pytest.fixture
def running_agent(tmp_path, free_port_pair):
    """Boot an Agent on free ports, tear it down after the test."""
    udp_port, tcp_port = free_port_pair
    paths = sp_data.SlavePaths.under(tmp_path)
    key = sp_security.generate_key()
    cfg = sp_agent.AgentConfig(
        bind_host="127.0.0.1",
        udp_port=udp_port, tcp_port=tcp_port,
        broadcast=False,
    )
    a = sp_agent.Agent(paths=paths, key=key, config=cfg)
    a.start()
    # Give the listeners a moment to actually bind.
    time.sleep(0.05)
    try:
        yield a, paths, key, udp_port, tcp_port
    finally:
        a.stop(join_timeout=2.0)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _udp_call(env, key, port, timeout=2.0):
    """Send a signed envelope over UDP and parse the verified reply."""
    wire = sp_security.sign_envelope(env, key).to_json_bytes()
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.settimeout(timeout)
    try:
        sk.sendto(wire, ("127.0.0.1", port))
        data, _ = sk.recvfrom(65536)
    finally:
        sk.close()
    return sp_security.verify_and_parse(data, key)


def _tcp_call(env, key, port, timeout=10.0):
    """Send a signed envelope over TCP, read back the framed reply."""
    wire = sp_security.sign_envelope(env, key).to_json_bytes()
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sk.settimeout(timeout)
    try:
        sk.connect(("127.0.0.1", port))
        sk.sendall(len(wire).to_bytes(4, "big") + wire)
        # Read length prefix
        hdr = b""
        while len(hdr) < 4:
            chunk = sk.recv(4 - len(hdr))
            if not chunk:
                raise RuntimeError("short read")
            hdr += chunk
        n = int.from_bytes(hdr, "big")
        body = b""
        while len(body) < n:
            chunk = sk.recv(min(65536, n - len(body)))
            if not chunk:
                raise RuntimeError("short body read")
            body += chunk
    finally:
        sk.close()
    return sp_security.verify_and_parse(body, key)


# ── PING ─────────────────────────────────────────────────────────────────────
class TestPing:
    def test_responds_with_ping_result(self, running_agent):
        _, _, key, udp_port, _ = running_agent
        env = sp_protocol.make_envelope("PING")
        resp = _udp_call(env, key, udp_port)
        assert resp.cmd == "PING_RESULT"

    def test_unsigned_message_dropped(self, running_agent):
        _, _, key, udp_port, _ = running_agent
        # Send a perfectly-shaped envelope but with a bogus signature.
        # We expect no reply (drop), so the recv times out.
        bad_env = sp_protocol.Envelope(
            v=1, ts=time.time(), cmd="PING", body=None, sig="deadbeef" * 8)
        wire = bad_env.to_json_bytes()
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.settimeout(0.5)
        try:
            sk.sendto(wire, ("127.0.0.1", udp_port))
            with pytest.raises(socket.timeout):
                sk.recvfrom(65536)
        finally:
            sk.close()


# ── STATUS ───────────────────────────────────────────────────────────────────
class TestStatus:
    def test_returns_snapshot(self, running_agent):
        _, _, key, udp_port, _ = running_agent
        env = sp_protocol.make_envelope("STATUS", body={"probes": []})
        resp = _udp_call(env, key, udp_port)
        assert resp.cmd == "STATUS_RESULT"
        assert "hostname" in resp.body
        assert "os" in resp.body
        assert "uptime_sec" in resp.body

    def test_evaluates_probes(self, running_agent, tmp_path):
        _, _, key, udp_port, _ = running_agent
        # Use a probe that asks 'does this temp dir exist?'.
        env = sp_protocol.make_envelope("STATUS", body={
            "probes": [{
                "name": "tmp_dir", "type": "folder_exists",
                "params": {"path": str(tmp_path)},
            }],
        })
        resp = _udp_call(env, key, udp_port)
        assert len(resp.body["probes"]) == 1
        assert resp.body["probes"][0]["value"] == "present"


# ── EXEC_SCRIPT (TCP) ────────────────────────────────────────────────────────
class TestExecScriptTCP:
    def test_runs_script_returns_full_output(self, running_agent):
        _, paths, key, _, tcp_port = running_agent
        ext = sp_platform.script_extension()
        if ext == ".sh":
            (paths.cascaded / "echo_test.sh").write_text(
                "#!/bin/sh\necho line 1\necho line 2\n")
            os.chmod(paths.cascaded / "echo_test.sh", 0o755)
        else:
            (paths.cascaded / "echo_test.bat").write_text(
                "@echo off\necho line 1\necho line 2\n")

        env = sp_protocol.make_envelope("EXEC_SCRIPT", body={
            "script_name": "echo_test",
            "env": {}, "args": [],
        })
        resp = _tcp_call(env, key, tcp_port)
        assert resp.cmd == "EXEC_SCRIPT_RESULT"
        assert resp.body["found"] is True
        assert resp.body["exit_code"] == 0
        assert "line 1" in resp.body["stdout"]
        assert "line 2" in resp.body["stdout"]

    def test_missing_script(self, running_agent):
        _, _, key, _, tcp_port = running_agent
        env = sp_protocol.make_envelope("EXEC_SCRIPT",
                                          body={"script_name": "ghost"})
        resp = _tcp_call(env, key, tcp_port)
        assert resp.body["found"] is False
        assert resp.body["exit_code"] == -1


# ── SYNC_PUSH (TCP) ──────────────────────────────────────────────────────────
class TestSyncPushTCP:
    def test_writes_cascaded_scripts(self, running_agent):
        _, paths, key, _, tcp_port = running_agent
        env = sp_protocol.make_envelope("SYNC_PUSH", body={
            "scripts": [
                {"name": "alpha", "content": "echo A\n"},
                {"name": "beta",  "content": "echo B\n"},
            ],
        })
        resp = _tcp_call(env, key, tcp_port)
        assert resp.cmd == "SYNC_ACK"
        assert resp.body["count"] == 2
        ext = sp_platform.script_extension()
        assert (paths.cascaded / ("alpha" + ext)).exists()
        assert (paths.cascaded / ("beta"  + ext)).exists()

    def test_replaces_old_scripts(self, running_agent):
        _, paths, key, _, tcp_port = running_agent
        ext = sp_platform.script_extension()
        # Pre-existing cascaded script
        paths.ensure()
        (paths.cascaded / ("old" + ext)).write_text("ancient")
        env = sp_protocol.make_envelope("SYNC_PUSH", body={
            "scripts": [{"name": "fresh", "content": "new\n"}],
        })
        resp = _tcp_call(env, key, tcp_port)
        assert resp.cmd == "SYNC_ACK"
        assert not (paths.cascaded / ("old" + ext)).exists()
        assert (paths.cascaded / ("fresh" + ext)).exists()
