"""Tests for simpit_control.slave_link — exercises against a real agent.

We boot a real slave Agent on loopback and drive it from a SlaveLink.
This catches any wire-format drift between the two sides.
"""
import os
import socket
import time

import pytest

from simpit_common import platform as sp_platform
from simpit_common import security as sp_security
from simpit_control import data as sp_control_data
from simpit_control import slave_link as sp_link
from simpit_slave import agent as sp_agent
from simpit_slave import data as sp_slave_data


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def free_port_pair():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    udp = s.getsockname()[1]; s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    tcp = s.getsockname()[1]; s.close()
    return udp, tcp


@pytest.fixture
def real_slave(tmp_path, free_port_pair):
    udp_port, tcp_port = free_port_pair
    paths = sp_slave_data.SlavePaths.under(tmp_path)
    key = sp_security.generate_key()
    cfg = sp_agent.AgentConfig(
        bind_host="127.0.0.1", udp_port=udp_port, tcp_port=tcp_port,
        broadcast=False)
    agent = sp_agent.Agent(paths=paths, key=key, config=cfg)
    agent.start()
    time.sleep(0.05)

    slave = sp_control_data.Slave(
        id="slave_test", name="t", host="127.0.0.1",
        udp_port=udp_port, tcp_port=tcp_port)
    link = sp_link.SlaveLink(slave=slave, key=key)
    try:
        yield link, paths, key
    finally:
        agent.stop(join_timeout=2.0)


# ── PING ─────────────────────────────────────────────────────────────────────
class TestPing:
    def test_returns_dict(self, real_slave):
        link, _, _ = real_slave
        body = link.ping()
        assert "ts" in body

    def test_unreachable_raises(self):
        # Random unused port, no agent there.
        slave = sp_control_data.Slave(
            id="x", name="x", host="127.0.0.1",
            udp_port=1, tcp_port=2)
        link = sp_link.SlaveLink(slave=slave, key=sp_security.generate_key())
        with pytest.raises(sp_link.SlaveError):
            link.ping(timeout=0.5)

    def test_wrong_key_raises_bad_response(self, real_slave):
        link, _, _ = real_slave
        # Replace key with a different one — agent will reject any
        # message we send and we'll time out.
        link.key = sp_security.generate_key()
        with pytest.raises(sp_link.SlaveError):
            link.ping(timeout=0.5)


# ── STATUS ───────────────────────────────────────────────────────────────────
class TestStatus:
    def test_returns_snapshot_shape(self, real_slave):
        link, _, _ = real_slave
        body = link.status()
        assert "hostname" in body
        assert "os" in body

    def test_evaluates_probes(self, real_slave, tmp_path):
        link, _, _ = real_slave
        body = link.status(probes=[{
            "name": "tmp_check", "type": "folder_exists",
            "params": {"path": str(tmp_path)},
        }])
        assert any(p["name"] == "tmp_check" and p["value"] == "present"
                   for p in body["probes"])


# ── EXEC_SCRIPT (TCP) ────────────────────────────────────────────────────────
class TestExecScript:
    def test_runs_and_returns_full_output(self, real_slave):
        link, paths, _ = real_slave
        ext = sp_platform.script_extension()
        body_text = ("#!/bin/sh\necho hello\n" if ext == ".sh"
                     else "@echo off\necho hello\n")
        path = paths.cascaded / ("greet" + ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body_text)
        if ext == ".sh":
            os.chmod(path, 0o755)

        body = link.exec_script("greet")
        assert body["found"] is True
        assert body["exit_code"] == 0
        assert "hello" in body["stdout"]


# ── SYNC_PUSH (TCP) ──────────────────────────────────────────────────────────
class TestSyncPush:
    def test_pushes_scripts(self, real_slave):
        link, paths, _ = real_slave
        body = link.sync_push([
            {"name": "alpha", "content": "echo A\n"},
            {"name": "beta",  "content": "echo B\n"},
        ])
        assert body["count"] == 2
        ext = sp_platform.script_extension()
        assert (paths.cascaded / ("alpha" + ext)).exists()
