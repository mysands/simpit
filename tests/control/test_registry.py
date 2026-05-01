"""Tests for simpit_control.registry — script definitions and seeder."""
import pytest

from simpit_control import data as sp_data
from simpit_control import registry as sp_registry


# ── ScriptDef ─────────────────────────────────────────────────────────────────
class TestScriptDef:
    def test_registry_not_empty(self):
        assert len(sp_registry.REGISTRY) > 0

    def test_all_entries_have_required_fields(self):
        for defn in sp_registry.REGISTRY:
            assert defn.name, f"{defn.script_name} missing name"
            assert defn.script_name, f"{defn.name} missing script_name"

    def test_registry_by_name_covers_all(self):
        for defn in sp_registry.REGISTRY:
            assert defn.script_name in sp_registry.REGISTRY_BY_NAME

    def test_custom_scenery_scripts_present(self):
        assert "enable_custom_scenery" in sp_registry.REGISTRY_BY_NAME
        assert "disable_custom_scenery" in sp_registry.REGISTRY_BY_NAME

    def test_update_block_scripts_present(self):
        assert "block_xplane_updates" in sp_registry.REGISTRY_BY_NAME
        assert "restore_xplane_updates" in sp_registry.REGISTRY_BY_NAME

    def test_update_scripts_need_admin(self):
        for name in ("block_xplane_updates", "restore_xplane_updates"):
            defn = sp_registry.REGISTRY_BY_NAME[name]
            assert defn.needs_admin, f"{name} should require admin"

    def test_custom_scenery_has_folder_probe(self):
        for name in ("enable_custom_scenery", "disable_custom_scenery"):
            defn = sp_registry.REGISTRY_BY_NAME[name]
            assert defn.state_probe is not None
            assert defn.state_probe["type"] == "folder_exists"

    def test_all_cascade(self):
        for defn in sp_registry.REGISTRY:
            assert defn.cascade, f"{defn.script_name} should be cascade=True"

    def test_bat_content_not_empty_for_standard_scripts(self):
        for name in ("enable_custom_scenery", "disable_custom_scenery"):
            defn = sp_registry.REGISTRY_BY_NAME[name]
            assert defn.content_bat, f"{name} missing .bat content"

    def test_sh_content_for_standard_scripts(self):
        for name in ("enable_custom_scenery", "disable_custom_scenery"):
            defn = sp_registry.REGISTRY_BY_NAME[name]
            assert defn.content_sh, f"{name} missing .sh content"

    def test_update_scripts_use_python_content(self):
        for name in ("block_xplane_updates", "restore_xplane_updates"):
            defn = sp_registry.REGISTRY_BY_NAME[name]
            # Python content stored in content_bat (cross-platform .py)
            assert "import sys" in defn.content_bat, \
                f"{name} content_bat should be Python"


# ── seed_registry ─────────────────────────────────────────────────────────────
class TestSeedRegistry:
    def test_seed_on_empty_store_adds_all(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        assert store.batfiles() == []
        n = sp_registry.seed_registry(store)
        assert n == len(sp_registry.REGISTRY)
        assert len(store.batfiles()) == len(sp_registry.REGISTRY)

    def test_seed_is_idempotent(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        n1 = sp_registry.seed_registry(store)
        n2 = sp_registry.seed_registry(store)
        assert n1 == len(sp_registry.REGISTRY)
        assert n2 == 0  # nothing new to add

    def test_seed_skips_existing_script_names(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        store.add_batfile(name="My Custom", script_name="enable_custom_scenery",
                          cascade=True, content="custom content")
        n = sp_registry.seed_registry(store)
        # enable_custom_scenery already exists — should be skipped
        assert n == len(sp_registry.REGISTRY) - 1
        # The existing entry should be unchanged
        bats = {b.script_name: b for b in store.batfiles()}
        assert bats["enable_custom_scenery"].content == "custom content"

    def test_seeded_scripts_persist_on_reload(self, tmp_path):
        store1 = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        sp_registry.seed_registry(store1)
        store2 = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        names = {b.script_name for b in store2.batfiles()}
        expected = {d.script_name for d in sp_registry.REGISTRY}
        assert expected.issubset(names)

    def test_seeded_update_scripts_need_admin(self, tmp_path):
        store = sp_data.Store(sp_data.ControlPaths.under(tmp_path))
        sp_registry.seed_registry(store)
        bats = {b.script_name: b for b in store.batfiles()}
        for name in ("block_xplane_updates", "restore_xplane_updates"):
            assert bats[name].needs_admin
