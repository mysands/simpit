"""Tests for simpit_control.poller — using mock slaves (no network)."""
import threading
import time

import pytest

from simpit_control import data as sp_data
from simpit_control import mock_slave as sp_mock
from simpit_control import poller as sp_poller


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path):
    return sp_data.Store(sp_data.ControlPaths.under(tmp_path))


@pytest.fixture
def provider():
    return sp_mock.MockLinkProvider()


# ── Single-poll behaviour (run _poll_one directly, no threading) ─────────────
class TestPollOneBehaviour:
    def test_normal_slave_becomes_online(self, store, provider):
        s = store.add_slave(name="X", host="h")
        provider.add(s.id, sp_mock.MockSlaveState(mode=sp_mock.MockMode.NORMAL))
        p = sp_poller.Poller(store, provider)
        p._poll_one(s)
        assert p.get(s.id).state == sp_poller.SlaveState.ONLINE

    def test_offline_slave_becomes_offline(self, store, provider):
        s = store.add_slave(name="X", host="h")
        provider.add(s.id, sp_mock.MockSlaveState(mode=sp_mock.MockMode.OFFLINE))
        p = sp_poller.Poller(store, provider)
        p._poll_one(s)
        assert p.get(s.id).state == sp_poller.SlaveState.OFFLINE

    def test_bad_key_becomes_error(self, store, provider):
        s = store.add_slave(name="X", host="h")
        provider.add(s.id, sp_mock.MockSlaveState(mode=sp_mock.MockMode.BAD_KEY))
        p = sp_poller.Poller(store, provider)
        p._poll_one(s)
        status = p.get(s.id)
        assert status.state == sp_poller.SlaveState.ERROR
        assert "key" in status.error.lower() or "format" in status.error.lower()

    def test_running_state_when_probe_says_running(self, store, provider):
        # Register a batfile with a state probe; mock returns 'running'
        # for that probe; expect poller to flip to RUNNING.
        s = store.add_slave(name="X", host="h")
        bat = store.add_batfile(name="X-Plane", script_name="x",
                                cascade=True,
                                state_probe={"type": "process_running",
                                             "params": {"name": "X-Plane"}})
        state = provider.add(s.id, sp_mock.MockSlaveState())
        state.probe_overrides[bat.id] = "running"
        p = sp_poller.Poller(store, provider)
        p._poll_one(s)
        assert p.get(s.id).state == sp_poller.SlaveState.RUNNING

    def test_probe_results_stored_by_id(self, store, provider):
        s = store.add_slave(name="X", host="h")
        bat = store.add_batfile(name="Hosts", script_name="h",
                                cascade=True,
                                state_probe={"type": "file_contains",
                                             "params": {"path": "/etc/hosts",
                                                        "contains": "x"}})
        state = provider.add(s.id, sp_mock.MockSlaveState())
        state.probe_overrides[bat.id] = "absent"
        p = sp_poller.Poller(store, provider)
        p._poll_one(s)
        cached = p.get(s.id)
        assert cached.probe_results.get(bat.id) == "absent"

    def test_probe_params_resolved_with_slave_env_before_send(
            self, store, provider, monkeypatch):
        """Poller must substitute ${VAR} from slave.env into probe params
        Control-side, so the slave receives literal paths and we don't
        ship the env block on every STATUS poll."""
        env = {"XPLANE_FOLDER": "/opt/xp", "SIM_EXE_NAME": "X-Plane"}
        s = store.add_slave(name="X", host="h", env=env)
        store.add_batfile(
            name="scenery", script_name="x", cascade=True,
            state_probe={"type": "folder_exists",
                         "params": {"path": "${XPLANE_FOLDER}/Custom Scenery"}})
        state = provider.add(s.id, sp_mock.MockSlaveState())

        # Capture the args the link receives.
        captured = {}
        link = provider.link_for(s)
        orig_status = link.status

        def spy_status(probes=None, timeout=2.0):
            captured["probes"] = probes
            captured["kwargs"] = {"timeout": timeout}
            return orig_status(probes=probes, timeout=timeout)

        monkeypatch.setattr(link, "status", spy_status)
        # Pin the provider to return our spied link
        monkeypatch.setattr(provider, "link_for", lambda slave: link)

        p = sp_poller.Poller(store, provider)
        p._poll_one(s)

        assert "probes" in captured
        assert len(captured["probes"]) == 1
        # ${XPLANE_FOLDER} resolved to literal path before sending
        assert captured["probes"][0]["params"]["path"] == "/opt/xp/Custom Scenery"
        # No env field anywhere in what we sent
        assert "env" not in captured.get("kwargs", {})

    def test_status_called_without_env_kwarg(self, store, provider, monkeypatch):
        """Sanity: link.status() no longer accepts/receives env."""
        s = store.add_slave(name="X", host="h", env={"FOO": "bar"})
        provider.add(s.id, sp_mock.MockSlaveState())
        link = provider.link_for(s)

        seen_kwargs = {}
        orig = link.status

        def spy(**kwargs):
            seen_kwargs.update(kwargs)
            return orig(**kwargs)

        monkeypatch.setattr(link, "status", spy)
        monkeypatch.setattr(provider, "link_for", lambda slave: link)

        p = sp_poller.Poller(store, provider)
        p._poll_one(s)
        assert "env" not in seen_kwargs


# ── State recovery ───────────────────────────────────────────────────────────
class TestRecovery:
    def test_offline_then_online(self, store, provider):
        s = store.add_slave(name="X", host="h")
        state = provider.add(s.id, sp_mock.MockSlaveState(mode=sp_mock.MockMode.OFFLINE))
        p = sp_poller.Poller(store, provider)
        p._poll_one(s)
        assert p.get(s.id).state == sp_poller.SlaveState.OFFLINE

        # Recovery: flip to NORMAL, poll again, expect ONLINE.
        state.mode = sp_mock.MockMode.NORMAL
        p._poll_one(s)
        assert p.get(s.id).state == sp_poller.SlaveState.ONLINE


# ── Subscribers ──────────────────────────────────────────────────────────────
class TestSubscribers:
    def test_called_on_state_change(self, store, provider):
        s = store.add_slave(name="X", host="h")
        provider.add(s.id, sp_mock.MockSlaveState())
        p = sp_poller.Poller(store, provider)

        events = []
        p.subscribe(lambda snap: events.append(snap.state))

        p._poll_one(s)
        assert events  # at least one event
        assert events[-1] == sp_poller.SlaveState.ONLINE

    def test_not_called_when_state_unchanged(self, store, provider):
        s = store.add_slave(name="X", host="h")
        provider.add(s.id, sp_mock.MockSlaveState())
        p = sp_poller.Poller(store, provider)

        events = []
        p.subscribe(lambda snap: events.append(snap))

        p._poll_one(s)
        n1 = len(events)
        p._poll_one(s)   # same state again
        n2 = len(events)
        # No new events because nothing changed.
        assert n2 == n1

    def test_unsubscribe_works(self, store, provider):
        s = store.add_slave(name="X", host="h")
        provider.add(s.id, sp_mock.MockSlaveState())
        p = sp_poller.Poller(store, provider)

        events = []
        unsub = p.subscribe(lambda snap: events.append(snap))
        unsub()
        p._poll_one(s)
        assert events == []

    def test_subscriber_exception_does_not_break_poller(self, store, provider):
        s = store.add_slave(name="X", host="h")
        provider.add(s.id, sp_mock.MockSlaveState())
        p = sp_poller.Poller(store, provider)

        def bad(snap):
            raise RuntimeError("kaboom")

        events = []
        p.subscribe(bad)
        p.subscribe(lambda snap: events.append(snap))
        p._poll_one(s)   # must not raise
        assert events  # the well-behaved subscriber still got the event


# ── Full thread lifecycle ────────────────────────────────────────────────────
class TestPollerThread:
    def test_start_stop(self, store, provider):
        # Just verify start/stop doesn't deadlock or leak threads.
        p = sp_poller.Poller(store, provider,
                             cadence=sp_poller.PollCadence(ping_interval=0.05))
        p.start()
        time.sleep(0.1)
        p.stop()
        # Calling stop again must be safe.
        p.stop()

    def test_poll_loop_updates_cache(self, store, provider):
        s = store.add_slave(name="X", host="h")
        provider.add(s.id, sp_mock.MockSlaveState())
        p = sp_poller.Poller(store, provider,
                             cadence=sp_poller.PollCadence(ping_interval=0.05))

        seen = threading.Event()
        def on_update(snap):
            if snap.state == sp_poller.SlaveState.ONLINE:
                seen.set()
        p.subscribe(on_update)

        p.start()
        try:
            assert seen.wait(2.0), "poller never reached ONLINE"
        finally:
            p.stop()


# ── Manual SYNCING ───────────────────────────────────────────────────────────
class TestMarkSyncing:
    def test_mark_syncing_flips_state(self, store, provider):
        s = store.add_slave(name="X", host="h")
        provider.add(s.id, sp_mock.MockSlaveState())
        p = sp_poller.Poller(store, provider)
        p.mark_syncing(s.id)
        assert p.get(s.id).state == sp_poller.SlaveState.SYNCING
