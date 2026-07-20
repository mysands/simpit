"""Tests for simpit_common.ortho_config (pure logic, no UI, no network)."""
from __future__ import annotations

import json

import pytest

from simpit_common import ortho_config
from simpit_common.ortho_config import OrthoAgentConfig


def test_defaults_are_valid():
    """The out-of-box config passes validation (fresh install works)."""
    OrthoAgentConfig().validate()


def test_rclone_cmd_derivation():
    """Mount command reflects the fields and carries the critical flags."""
    cfg = OrthoAgentConfig(remote_target="randhawanas:XPlane12/Custom Scenery",
                           mount_root="X:/", cache_max_gb=50,
                           cache_max_age="8760h", rc_addr="127.0.0.1:5572")
    cmd = cfg.build_rclone_cmd()
    assert cmd[:4] == ["rclone", "mount",
                       "randhawanas:XPlane12/Custom Scenery", "X:"]
    assert "--vfs-cache-mode" in cmd and "full" in cmd
    assert "50G" in cmd
    assert "8760h" in cmd          # never 0 — that purges the cache
    assert "--rc-addr" in cmd and "127.0.0.1:5572" in cmd


def test_save_load_round_trip(tmp_path):
    """Saved config loads back field-identical, with rclone_cmd embedded."""
    path = tmp_path / "ortho_agent.json"
    cfg = OrthoAgentConfig(master_ip="192.168.10.5", cache_max_gb=80,
                           active_zoom=16, touch_interval_seconds=120.0)
    ortho_config.save(cfg, path)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["rclone_cmd"] == cfg.build_rclone_cmd()
    assert on_disk["schema_version"] == ortho_config.SCHEMA_VERSION
    loaded = ortho_config.load_or_default(path)
    assert loaded == cfg


def test_load_or_default_missing_and_corrupt(tmp_path):
    """Missing or corrupt files yield defaults instead of raising."""
    assert ortho_config.load_or_default(tmp_path / "nope.json") == \
        OrthoAgentConfig()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ortho_config.load_or_default(bad) == OrthoAgentConfig()


def test_from_dict_ignores_unknown_and_stale_rclone_cmd():
    """Unknown keys are dropped; a stale stored rclone_cmd is re-derived."""
    cfg = OrthoAgentConfig.from_dict({
        "cache_max_gb": 25,
        "rclone_cmd": ["rclone", "mount", "old:share", "Y:"],
        "someday_a_new_field": True,
    })
    assert cfg.cache_max_gb == 25
    assert cfg.build_rclone_cmd()[2] == cfg.remote_target != "old:share"


@pytest.mark.parametrize("bad_field, bad_value, fragment", [
    ("remote_target", "no-colon-here", "rclone remote"),
    ("mount_root", "not-a-drive", "drive letter"),
    ("cache_max_gb", 0, "1-2000"),
    ("cache_max_age", "0", "purges the cache"),
    ("rc_addr", "no-port", "host:port"),
    ("master_ip", "  ", "Master IP"),
    ("xp_udp_port", 70000, "1-65535"),
    ("active_zoom", 17, "16 or 18"),
    ("n_rings", 0, "1-16"),
    ("lookahead_seconds", 1000.0, "0-600"),
    ("poll_hz", 0.0, "0.1-10"),
    ("touch_interval_seconds", 5.0, "10-3600"),
    ("heading_offset_deg", 270.0, "-180..180"),
])
def test_validation_rejects(bad_field, bad_value, fragment):
    """Each invalid field fails validation with a message naming it."""
    cfg = OrthoAgentConfig(**{bad_field: bad_value})
    with pytest.raises(ValueError) as excinfo:
        cfg.validate()
    assert fragment in str(excinfo.value)


def test_validation_reports_all_errors_at_once():
    """Multiple bad fields produce one combined message (dialog UX)."""
    cfg = OrthoAgentConfig(active_zoom=17, n_rings=0, cache_max_gb=0)
    try:
        cfg.validate()
        raise AssertionError("validate() should have raised")
    except ValueError as exc:
        text = str(exc)
        assert "16 or 18" in text and "1-16" in text and "1-2000" in text


def test_cache_dir_flag_derivation():
    """--cache-dir appears only when a custom cache folder is set."""
    assert "--cache-dir" not in OrthoAgentConfig().build_rclone_cmd()
    cmd = OrthoAgentConfig(cache_dir=r"D:\rclone-cache").build_rclone_cmd()
    i = cmd.index("--cache-dir")
    assert cmd[i + 1] == r"D:\rclone-cache"


def test_default_fleet_dir_is_empty_and_local_only(tmp_path):
    """No baked-in site path: out of the box, fleet distribution is off
    and everything works purely on the local file (no network probes)."""
    cfg = OrthoAgentConfig()
    assert cfg.fleet_config_dir == ""
    assert ortho_config.fleet_path(cfg) is None
    local = tmp_path / "ortho_agent.json"
    assert ortho_config.save_fleet(cfg, local) is None      # no warning
    assert ortho_config.load_or_default(local) == cfg
    assert ortho_config.load_effective(local, hostname="RIGHT") == cfg


def test_save_fleet_writes_local_and_nas_copy(tmp_path):
    """save_fleet lands both copies and returns no warning on success."""
    local = tmp_path / "local" / "ortho_agent.json"
    fleet_dir = tmp_path / "nas"
    cfg = OrthoAgentConfig(fleet_config_dir=str(fleet_dir), n_rings=6)
    assert ortho_config.save_fleet(cfg, local) is None
    assert ortho_config.load_or_default(local) == cfg
    assert ortho_config.load_or_default(fleet_dir / "ortho_agent.json") == cfg


def test_save_fleet_unreachable_nas_keeps_local(tmp_path, monkeypatch):
    """NAS write failure returns a warning but the local copy is saved."""
    local = tmp_path / "ortho_agent.json"
    cfg = OrthoAgentConfig(fleet_config_dir=str(tmp_path / "nas"))
    real_save = ortho_config.save

    def failing_save(config, path):
        if "nas" in str(path):
            raise OSError("share unreachable")
        real_save(config, path)

    monkeypatch.setattr(ortho_config, "save", failing_save)
    warning = ortho_config.save_fleet(cfg, local)
    assert warning and "fleet copy" in warning
    assert local.is_file()


def test_load_effective_prefers_fleet_then_overlay(tmp_path):
    """Fleet base overrides local; hostname overlay overrides fleet —
    but only for the keys the overlay actually contains."""
    fleet_dir = tmp_path / "nas"
    local = tmp_path / "ortho_agent.json"
    base = OrthoAgentConfig(fleet_config_dir=str(fleet_dir),
                            n_rings=6, lookahead_seconds=90.0)
    ortho_config.save_fleet(base, local)
    # Stale local copy: the fleet base should win over it.
    ortho_config.save(
        OrthoAgentConfig(fleet_config_dir=str(fleet_dir), n_rings=2), local)
    # Per-machine overlay: only heading offset differs on CENTERLEFT.
    (fleet_dir / "ortho_agent.centerleft.json").write_text(
        '{"heading_offset_deg": -45}', encoding="utf-8")

    eff = ortho_config.load_effective(local, hostname="CENTERLEFT")
    assert eff.n_rings == 6                    # fleet beat stale local
    assert eff.lookahead_seconds == 90.0       # inherited from fleet
    assert eff.heading_offset_deg == -45.0     # overlay applied


def test_load_effective_offline_falls_back_to_local(tmp_path):
    """Unreachable fleet dir → the cached local copy runs the machine."""
    local = tmp_path / "ortho_agent.json"
    cfg = OrthoAgentConfig(fleet_config_dir=str(tmp_path / "gone"),
                           n_rings=5)
    ortho_config.save(cfg, local)
    assert ortho_config.load_effective(local, hostname="RIGHT") == cfg


def test_save_invalid_writes_nothing(tmp_path):
    """A failed validation must not clobber the existing file."""
    path = tmp_path / "ortho_agent.json"
    ortho_config.save(OrthoAgentConfig(), path)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        ortho_config.save(OrthoAgentConfig(active_zoom=17), path)
    assert path.read_text(encoding="utf-8") == before
