"""End-to-end: Control's Store + Poller + SlaveLink against a real Agent.

This is the highest-level test we have. It boots a real slave Agent on
loopback, registers it in a Control Store, drives a Poller against it,
runs a real EXEC_SCRIPT, and verifies the cache reflects everything
correctly.

If this passes, the wire format, security, store, slave_link, poller
and slave agent are all in agreement. If something breaks here but the
unit tests pass, the bug is at a layer boundary.
"""
import socket
import time

import pytest

from simpit_common import platform as sp_platform
from simpit_common import security as sp_security
from simpit_control import data as sp_control_data
from simpit_control import poller as sp_poller
from simpit_control import slave_link as sp_link
from simpit_slave import agent as sp_agent
from simpit_slave import data as sp_slave_data


@pytest.fixture
def free_ports():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    udp = s.getsockname()[1]; s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    tcp = s.getsockname()[1]; s.close()
    return udp, tcp


@pytest.fixture
def fleet(tmp_path, free_ports):
    """A real slave + a Control Store wired together by a shared key."""
    udp_port, tcp_port = free_ports
    key = sp_security.generate_key()

    # ── Slave side ──
    slave_paths = sp_slave_data.SlavePaths.under(tmp_path / "slave")
    agent = sp_agent.Agent(
        paths=slave_paths, key=key,
        config=sp_agent.AgentConfig(
            bind_host="127.0.0.1", udp_port=udp_port, tcp_port=tcp_port,
            broadcast=False))
    agent.start()
    time.sleep(0.05)

    # ── Control side ──
    control_paths = sp_control_data.ControlPaths.under(tmp_path / "control")
    store = sp_control_data.Store(control_paths)
    slave = store.add_slave(name="LOOP", host="127.0.0.1",
                            udp_port=udp_port, tcp_port=tcp_port)

    try:
        yield store, slave, key, slave_paths
    finally:
        agent.stop(join_timeout=2.0)


# ── Tests ────────────────────────────────────────────────────────────────────
class TestEndToEnd:
    def test_poller_sees_real_slave_online(self, fleet):
        store, slave, key, _ = fleet
        provider = sp_poller.RealLinkProvider(key=key)
        p = sp_poller.Poller(store, provider,
                             cadence=sp_poller.PollCadence(ping_interval=0.1))

        import threading
        seen = threading.Event()
        def on_update(snap):
            if snap.state == sp_poller.SlaveState.ONLINE:
                seen.set()
        p.subscribe(on_update)

        p.start()
        try:
            assert seen.wait(2.0), "slave never reached ONLINE"
        finally:
            p.stop()

    def test_sync_push_then_exec_script(self, fleet):
        store, slave, key, slave_paths = fleet

        # Register a cascading bat file with content.
        ext = sp_platform.script_extension()
        body = ("#!/bin/sh\necho hello-from-end-to-end\n" if ext == ".sh"
                else "@echo off\necho hello-from-end-to-end\n")
        bat = store.add_batfile(name="Greet", script_name="greet",
                                cascade=True, content=body)
        assert bat.cascade  # sanity check the registration

        # Push the cascaded set.
        link = sp_link.SlaveLink(slave=slave, key=key)
        cascaded = store.cascaded_for_slave(slave.id)
        scripts = [{"name": b.script_name, "content": b.content}
                   for b in cascaded]
        ack = link.sync_push(scripts)
        assert ack["count"] == 1

        # Verify on the slave's filesystem that it landed.
        assert (slave_paths.cascaded / ("greet" + ext)).is_file()

        # Now run it.
        result = link.exec_script("greet")
        assert result["found"] is True
        assert result["exit_code"] == 0
        assert "hello-from-end-to-end" in result["stdout"]

    def test_status_after_sync_reflects_inventory(self, fleet):
        store, slave, key, _ = fleet
        link = sp_link.SlaveLink(slave=slave, key=key)
        link.sync_push([
            {"name": "a", "content": "echo a\n"},
            {"name": "b", "content": "echo b\n"},
        ])
        body = link.status()
        inv = body["script_inventory"]["cascaded"]
        assert "a" in inv and "b" in inv

    def test_offline_when_agent_stops(self, fleet):
        # Kill the agent mid-test and confirm the poller detects it.
        store, slave, key, _ = fleet
        provider = sp_poller.RealLinkProvider(key=key)
        # We can't stop the agent from here easily because it's owned by
        # the fixture. Instead, just point the slave at a dead port.
        slave.tcp_port = 1   # privileged, will fail to connect
        slave.udp_port = 1
        store.update_slave(slave)
        p = sp_poller.Poller(store, provider,
                             cadence=sp_poller.PollCadence(ping_interval=0.1))
        p._poll_one(slave)
        assert p.get(slave.id).state == sp_poller.SlaveState.OFFLINE
