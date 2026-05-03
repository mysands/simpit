"""Tests for SlaveDialog — focused on the backup configuration fields.

Originally there were no SlaveDialog tests at all; this file is the
seed. Every test here uses ``tk_session_root`` from the shared
conftest because the dialog is a Toplevel and needs a parent root.
We exercise ``_build_env`` directly rather than driving the Save
button so we don't have to mock the Controller's persistence layer
just to assert how StringVars get translated into env dicts.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simpit_control import data as sp_data
from simpit_control.ui.controller import Controller
from simpit_control.ui.dialogs.slave_dialog import SlaveDialog


@pytest.fixture
def controller(tmp_path):
    """Real Controller backed by a tmp Store. The link_factory is a
    no-op since we never actually wire to slaves in these tests."""
    paths = sp_data.ControlPaths.under(tmp_path)
    store = sp_data.Store(paths)
    return Controller(store=store, link_factory=lambda *a, **kw: None)


@pytest.fixture
def dialog(tk_session_root, controller):
    """Fresh dialog per test. The teardown destroys the Toplevel; the
    session root persists across tests (see ui/conftest.py)."""
    dlg = SlaveDialog(tk_session_root, controller, existing=None)
    yield dlg
    try:
        dlg.destroy()
    except Exception:
        pass


# ── BACKUP_FOLDER & BACKUP_KEEP fields ───────────────────────────────────────

class TestBackupFields:

    def test_fields_default_to_blank(self, dialog):
        """A brand-new slave has no backup config — both fields blank,
        not pre-filled with a guessed default."""
        assert dialog.var_backup_folder.get() == ""
        assert dialog.var_backup_keep.get() == ""

    def test_blank_fields_omitted_from_env(self, dialog):
        """Empty BACKUP_FOLDER means 'this slave doesn't back up' —
        the key must be absent so the script's preflight catches it
        with a clear error rather than the slave silently defaulting."""
        env = dialog._build_env()
        assert "BACKUP_FOLDER" not in env
        assert "BACKUP_KEEP" not in env

    def test_filled_fields_propagate_to_env(self, dialog):
        dialog.var_backup_folder.set("\\\\NAS\\backups\\simpit")
        dialog.var_backup_keep.set("3")
        env = dialog._build_env()
        assert env["BACKUP_FOLDER"] == "\\\\NAS\\backups\\simpit"
        assert env["BACKUP_KEEP"] == "3"

    def test_unc_path_not_double_slashed(self, dialog):
        """The XPLANE_FOLDER field auto-appends a trailing slash; the
        BACKUP_FOLDER field must NOT — UNC paths and most NAS shares
        work without one and adding it makes downstream paths ugly."""
        dialog.var_backup_folder.set("\\\\NAS\\backups")
        env = dialog._build_env()
        assert env["BACKUP_FOLDER"] == "\\\\NAS\\backups"
        assert not env["BACKUP_FOLDER"].endswith("\\\\")

    def test_backup_folder_whitespace_stripped(self, dialog):
        """Users sometimes paste with surrounding whitespace; strip it
        rather than producing a path that fails on the slave."""
        dialog.var_backup_folder.set("  /mnt/nas/backups  ")
        env = dialog._build_env()
        assert env["BACKUP_FOLDER"] == "/mnt/nas/backups"

    def test_backup_keep_only_in_env_when_filled(self, dialog):
        """If BACKUP_KEEP is blank, the key is absent so the script's
        own default (2) takes effect. Filling it overrides."""
        dialog.var_backup_folder.set("/tmp/b")
        env = dialog._build_env()
        assert "BACKUP_KEEP" not in env

        dialog.var_backup_keep.set("7")
        env = dialog._build_env()
        assert env["BACKUP_KEEP"] == "7"


# ── BACKUP_KEEP validation ───────────────────────────────────────────────────

class TestBackupKeepValidation:

    def test_non_integer_rejected(self, dialog):
        dialog.var_backup_keep.set("abc")
        with pytest.raises(ValueError, match="BACKUP_KEEP"):
            dialog._build_env()

    def test_negative_rejected(self, dialog):
        dialog.var_backup_keep.set("-1")
        with pytest.raises(ValueError, match=">= 1"):
            dialog._build_env()

    def test_zero_rejected(self, dialog):
        dialog.var_backup_keep.set("0")
        with pytest.raises(ValueError, match=">= 1"):
            dialog._build_env()

    def test_one_accepted(self, dialog):
        """Edge: keep=1 is valid (keep just the newest, prune the
        rest). The script enforces the same lower bound; this is the
        UI-side mirror so the user gets immediate feedback."""
        dialog.var_backup_keep.set("1")
        env = dialog._build_env()
        assert env["BACKUP_KEEP"] == "1"

    def test_whitespace_around_number_ok(self, dialog):
        dialog.var_backup_keep.set("  3  ")
        env = dialog._build_env()
        assert env["BACKUP_KEEP"] == "3"


# ── Edit-existing slave round-trip ───────────────────────────────────────────

class TestExistingSlavePopulation:
    """Editing an existing slave must surface its env values into the
    dialog fields, otherwise the user can't see/change them."""

    def test_existing_backup_fields_loaded(self, tk_session_root, controller):
        existing = sp_data.Slave(
            id="abc", name="RIGHT", host="192.168.1.51",
            env={
                "XPLANE_FOLDER": "C:\\X-Plane 12.1\\",
                "BACKUP_FOLDER": "\\\\NAS\\backups\\simpit",
                "BACKUP_KEEP":   "5",
            },
        )
        dlg = SlaveDialog(tk_session_root, controller, existing=existing)
        try:
            assert dlg.var_backup_folder.get() == "\\\\NAS\\backups\\simpit"
            assert dlg.var_backup_keep.get() == "5"
        finally:
            dlg.destroy()

    def test_existing_without_backup_fields_blank(
        self, tk_session_root, controller
    ):
        existing = sp_data.Slave(
            id="abc", name="OLD", host="1.2.3.4",
            env={"XPLANE_FOLDER": "C:\\X\\"},
        )
        dlg = SlaveDialog(tk_session_root, controller, existing=existing)
        try:
            assert dlg.var_backup_folder.get() == ""
            assert dlg.var_backup_keep.get() == ""
        finally:
            dlg.destroy()

    def test_round_trip_preserves_backup_env(
        self, tk_session_root, controller
    ):
        """Open dialog on existing slave, change nothing, save: the
        env dict that comes back must still carry both backup keys."""
        existing = sp_data.Slave(
            id="abc", name="x", host="1.2.3.4",
            env={
                "XPLANE_FOLDER": "C:\\X\\",
                "SIM_EXE_NAME":  "X-Plane.exe",
                "BACKUP_FOLDER": "/mnt/nas/backups",
                "BACKUP_KEEP":   "4",
            },
        )
        dlg = SlaveDialog(tk_session_root, controller, existing=existing)
        try:
            env = dlg._build_env()
            assert env["BACKUP_FOLDER"] == "/mnt/nas/backups"
            assert env["BACKUP_KEEP"]   == "4"
        finally:
            dlg.destroy()
