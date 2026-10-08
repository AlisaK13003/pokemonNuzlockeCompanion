"""Desktop lifecycle actions without a live emulator."""

import os
from typing import ClassVar

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.core.ev_targets import EVTargetStore
from pokemon_ev_tracker.core.nuzlocke.acquisition import AcquisitionCandidate
from pokemon_ev_tracker.core.nuzlocke.death_detection import PartyHpSample
from pokemon_ev_tracker.core.nuzlocke.models import EncounterRecord
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.core.nuzlocke.wipe_detection import PartyWipeCandidate, WipeMember
from pokemon_ev_tracker.data_sources.bizhawk import BizHawkRamDataSource
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.ui.main_window import MainWindow
from pokemon_ev_tracker.ui.nuzlocke_dialogs import NewRunDialog


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BizHawkRamDataSource, "start", lambda self: None)
    monkeypatch.setattr(BizHawkRamDataSource, "stop", lambda self: None)
    monkeypatch.setattr(AppSettings, "save_default", lambda self: None)
    settings = AppSettings()
    result = MainWindow(
        settings, target_store=EVTargetStore(tmp_path / "ev.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"))
    result.refresh_timer.stop()
    yield result
    result.close()
    assert app is not None


def wipe(run_id):
    return PartyWipeCandidate((
        WipeMember("stable", "Shinx", "Sparky", 12, 0, 1, "Route 202", 4),
    ), "2026-10-05T12:00:00Z", run_id)


class FakeMessageBox:
    class ButtonRole:
        AcceptRole = "accept"
        RejectRole = "reject"
        DestructiveRole = "destructive"

    choices: ClassVar[list[str]] = []

    def __init__(self, _parent):
        self._buttons = {}
        self._clicked = None

    def setWindowTitle(self, _title):
        pass

    def setText(self, _text):
        pass

    def setInformativeText(self, _text):
        pass

    def addButton(self, label, _role):
        button = object()
        self._buttons[label] = button
        return button

    def exec(self):
        self._clicked = self._buttons[self.choices.pop(0)]

    def clickedButton(self):
        return self._clicked


def test_wipe_end_preserves_history_and_deduplicates_death(window):
    store = window.nuzlocke_view.store
    run = store.create_run("Platinum Run 04", PLATINUM_NUZLOCKE_PROFILE)
    window.nuzlocke_view._refresh_all()
    candidate = wipe(run.run_id)
    assert window.nuzlocke_view.end_run_wiped(candidate)
    assert run.status == "WIPED"
    assert run.ended_at and store.active_run is None
    assert len(run.deaths) == 1
    assert run.deaths[0].stable_id == "stable"
    assert not window.nuzlocke_view.end_run_wiped(candidate)
    assert len(run.deaths) == 1
    assert window.training_view.next_fight_name.text() == "No active run"
    assert store.suggest_next_run_name(PLATINUM_NUZLOCKE_PROFILE) == "Platinum Run 05"


def test_wipe_ignored_and_no_run_prompt_setting(window, monkeypatch):
    store = window.nuzlocke_view.store
    run = store.create_run("Keep", PLATINUM_NUZLOCKE_PROFILE)
    window.settings.nuzlocke_wipe_action = "IGNORE"
    window._handle_party_wipe(wipe(run.run_id))
    assert run.status == "ACTIVE"
    window.nuzlocke_view.no_run_prompt_input.setChecked(False)
    assert not window.settings.prompt_for_nuzlocke_run
    window.nuzlocke_view.no_run_prompt_input.setChecked(True)
    assert window.settings.prompt_for_nuzlocke_run


def test_run_library_filters_and_abandon_does_not_delete(window, monkeypatch):
    store = window.nuzlocke_view.store
    first = store.create_run("First", PLATINUM_NUZLOCKE_PROFILE)
    second = store.create_run("Second", PLATINUM_NUZLOCKE_PROFILE)
    window.nuzlocke_view._refresh_all()
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    table = window.nuzlocke_view.run_library_table
    table.selectRow(0)
    window.nuzlocke_view._abandon_run()
    assert first.status == "ABANDONED"
    assert first in store.runs
    assert store.active_run is second
    window.nuzlocke_view.run_status_filter.setCurrentIndex(
        window.nuzlocke_view.run_status_filter.findData("ABANDONED"))
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "First"


@pytest.mark.parametrize("choice", ["Keep Current Run", "Dismiss"])
def test_wipe_prompt_can_leave_run_active(window, monkeypatch, choice):
    from pokemon_ev_tracker.ui import main_window

    store = window.nuzlocke_view.store
    run = store.create_run("Continue", PLATINUM_NUZLOCKE_PROFILE)
    FakeMessageBox.choices = [choice]
    monkeypatch.setattr(main_window, "QMessageBox", FakeMessageBox)
    window._handle_party_wipe(wipe(run.run_id))
    assert store.active_run is run
    assert run.status == "ACTIVE"
    assert not run.deaths


def test_auto_wipe_offers_next_run_and_rebaselines_existing_party(window, monkeypatch):
    from pokemon_ev_tracker.ui import main_window

    store = window.nuzlocke_view.store
    old = store.create_run("Platinum Run 04", PLATINUM_NUZLOCKE_PROFILE)
    window.settings.nuzlocke_wipe_action = "END RUN AS WIPED"
    FakeMessageBox.choices = ["Create New Run"]
    monkeypatch.setattr(main_window, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(NewRunDialog, "exec", lambda self: self.DialogCode.Accepted)
    window._handle_party_wipe(wipe(old.run_id))
    new = store.active_run
    assert old.status == "WIPED"
    assert new is not None and new.run_id != old.run_id
    assert new.name == "Platinum Run 05"
    party = (AcquisitionCandidate(
        "stable", 403, "Shinx", "Sparky", 12, 4, 1, "Route 202", 0, 12, False),)
    assert window.acquisitions._party.observe(
        party, connected=True, valid_snapshot=True, run=new, store=store,
        classify=lambda _candidate, _run: ("WILD", "HIGH", None)) == ()
    hp = (PartyHpSample("stable", "Shinx", "Sparky", 12, 0, 1, "Route 202"),)
    assert window.party_lifecycle._hp.observe(
        hp, connected=True, valid_snapshot=True, run_id=new.run_id,
        stream_identity="stream", frame=10) == ()
    assert window.party_lifecycle._wipe.observe(
        hp, connected=True, valid_snapshot=True, run_id=new.run_id,
        stream_identity="stream", frame=10) is None
    assert new.acquisition_events == []


def test_no_run_prompt_can_be_disabled_persistently(window, monkeypatch):
    from pokemon_ev_tracker.ui import main_window

    FakeMessageBox.choices = ["Don't Ask Again"]
    monkeypatch.setattr(main_window, "QMessageBox", FakeMessageBox)
    window._prompt_create_run_without_active()
    assert not window.settings.prompt_for_nuzlocke_run
    assert not window.nuzlocke_view.no_run_prompt_input.isChecked()


def test_mark_won_requires_all_fights_and_delete_active_clears_selection(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    store = window.nuzlocke_view.store
    run = store.create_run("Champion", PLATINUM_NUZLOCKE_PROFILE)
    window.nuzlocke_view._mark_run_won()
    assert run.status == "ACTIVE"
    for cap in PLATINUM_NUZLOCKE_PROFILE.level_caps:
        store.set_cap_completed(run.run_id, cap.cap_id, True)
    window.nuzlocke_view._refresh_all()
    assert window.nuzlocke_view.mark_won_button.isVisible() or not window.nuzlocke_view.mark_won_button.isHidden()
    window.nuzlocke_view._mark_run_won()
    assert run.status == "WON"
    assert run.ended_at and store.active_run is None
    store.create_run("Delete me", PLATINUM_NUZLOCKE_PROFILE)
    window.nuzlocke_view._refresh_all()
    table = window.nuzlocke_view.run_library_table
    table.selectRow(next(row for row in range(table.rowCount())
                         if table.item(row, 0).text() == "Delete me"))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    window.nuzlocke_view._delete_run()
    assert store.active_run is not None
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    window.nuzlocke_view._delete_run()
    assert store.active_run is None
    assert len(store.runs) == 1


def test_wipe_leaves_ambiguous_encounters_unchanged(window):
    store = window.nuzlocke_view.store
    run = store.create_run("Ambiguous", PLATINUM_NUZLOCKE_PROFILE)
    locations = PLATINUM_NUZLOCKE_PROFILE.locations[:2]
    for location in locations:
        store.update_encounter(run.run_id, EncounterRecord(
            location.location_id, location.name, "CAUGHT", "Shinx", "Sparky", 4,
            met_location_id=1, met_location_name="Route 202"))
    assert window.nuzlocke_view.end_run_wiped(wipe(run.run_id))
    assert len(run.deaths) == 1
    assert run.deaths[0].encounter_location_id is None
    assert all(run.encounters[location.location_id].status == "CAUGHT" for location in locations)
