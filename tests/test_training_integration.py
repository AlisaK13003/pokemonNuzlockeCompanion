"""Training preferences through real widgets and existing decoded snapshot interfaces."""

from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from test_bizhawk_opponent import _battler_record, _decode
from test_party_stats_view import _party_payload, _party_record

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.config.training_preferences import EVTrainingPreferenceStore
from pokemon_ev_tracker.core.ev_targets import EVTarget, EVTargetStore
from pokemon_ev_tracker.core.ev_training import EVTrainingPreference, RecommendationStatus
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.data_sources.base import DataSourceSnapshot
from pokemon_ev_tracker.games.platinum.battle import active_enemy_battlers
from pokemon_ev_tracker.games.platinum.decoder import decode_party
from pokemon_ev_tracker.games.platinum.ev_yields import get_training_ev_yield
from pokemon_ev_tracker.games.platinum.profile import PLATINUM_PROFILE
from pokemon_ev_tracker.ui.main_window import MainWindow
from pokemon_ev_tracker.ui.training_focus_dialog import TrainingFocusDialog


def _party(*members):
    return decode_party(_party_payload(tuple(
        _party_record(pid, species, 47, attack=27) for pid, species in members
    )))


class _Source:
    profile = PLATINUM_PROFILE

    def __init__(self):
        self.party = _party((0, 183), (2, 443))
        self.connected = True
        self.battlers = _decode({})

    def start(self):
        pass

    def stop(self):
        pass

    def opponents(self, *species):
        records = {}
        for index, value in zip((1, 3), species):
            records[index - 1] = _battler_record(183, level=17, current_hp=30, max_hp=53, stat=27)
            records[index] = _battler_record(value, level=14, current_hp=20, max_hp=30, stat=15)
        self.battlers = _decode(records)

    def snapshot(self):
        return DataSourceSnapshot("BizHawk RAM", self.connected, {
            "heartbeat": None, "party_payload": None, "party_payload_fresh": True,
            "party_state": self.party, "display_party_state": self.party,
            "battle_battlers": self.battlers,
            "active_enemy_battlers": active_enemy_battlers(self.battlers),
        })


@pytest.fixture
def make_window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda self: None)
    windows = []

    def create(*, legacy=None, source=None):
        source = source or _Source()
        store = EVTrainingPreferenceStore(tmp_path / "ev_training_preferences.json")
        window = MainWindow(
            AppSettings(), data_source=source, target_store=legacy,
            nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
            training_preference_store=store,
        )
        window.refresh_timer.stop()
        windows.append(window)
        return window, source, store

    yield create
    for window in windows:
        window.close()
        window.deleteLater()
    app.processEvents()


def _edit(monkeypatch, window, stats, *, accept=True, during_dialog=None):
    def execute(dialog):
        for stat, button in dialog.stat_buttons.items():
            button.setChecked(stat in stats)
        if during_dialog is not None:
            during_dialog()
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(TrainingFocusDialog, "exec", execute)
    view = window.compact_training_view if window.compact_mode else window.training_view
    view.edit_button.click()


def _status(window, slot=1):
    return window.recommendation_panel.rows[slot][3].property("status")


def test_save_cancel_clear_and_restart_use_preferences_only(make_window, monkeypatch, tmp_path):
    legacy = EVTargetStore(tmp_path / "ev_targets.json")
    legacy.set(0, EVTarget(special_attack_target=252))
    old_contents = legacy.path.read_bytes()
    window, source, store = make_window(legacy=legacy)
    source.opponents(66)  # Machop: Attack +1.
    window._refresh_ram_backend_debug()
    assert _status(window) == "no-focus"
    assert window.training_view.focus_summary.text() == "No focus set"

    _edit(monkeypatch, window, {"hp", "attack"})

    expected = EVTrainingPreference(frozenset({"hp", "attack"}))
    assert store.get(0) == expected  # PID zero is valid.
    assert _status(window) == "good"  # Updated immediately, without polling again.
    assert window.training_recommendations[1].allowed_yields == {"attack": 1}
    assert window.training_view.focus_chips["hp"].property("evFocused")
    assert not window.training_view.focus_chips["special_attack"].property("evFocused")
    assert legacy.path.read_bytes() == old_contents
    _edit(monkeypatch, window, {"speed"}, accept=False)
    assert store.get(0) == expected
    assert _status(window) == "good"
    window.close()

    restored, _source, restored_store = make_window(legacy=legacy)
    assert restored_store.get(0) == expected
    assert restored.training_view.focus_chips["attack"].property("evFocused")
    restored.training_view.clear_button.click()
    assert restored_store.get(0) is None
    assert EVTrainingPreferenceStore(store.path).get(0) is None
    assert restored.training_view.focus_summary.text() == "No focus set"
    assert _status(restored) == "no-focus"
    assert legacy.path.read_bytes() == old_contents


def test_live_opponents_double_battle_and_end_recalculate_every_member(make_window):
    window, source, store = make_window()
    store.set(0, EVTrainingPreference(frozenset({"attack"})))
    store.set(2, EVTrainingPreference(frozenset({"special_attack", "defense"})))
    for opponents, expected in (
        ((66,), ("good", "avoid")),
        ((63,), ("avoid", "good")),
        ((66, 74), ("mixed", "mixed")),
        ((63, 74), ("avoid", "good")),
        ((74,), ("avoid", "good")),
    ):
        source.opponents(*opponents)
        window._refresh_ram_backend_debug()
        assert (_status(window, 1), _status(window, 2)) == expected
    assert window.training_recommendations[2].allowed_yields == {"defense": 1}
    source.opponents()
    window._refresh_ram_backend_debug()
    assert _status(window) == "idle"
    assert _status(window, 2) == "idle"
    assert not window.training_recommendations
    assert "+" not in window.recommendation_panel.rows[2][4].text()


def test_reorder_evolution_and_replacement_keep_preferences_on_pid(make_window):
    window, source, store = make_window()
    preference = EVTrainingPreference(frozenset({"attack"}))
    store.set(0, preference)
    source.opponents(66)
    window._refresh_ram_backend_debug()
    source.party = _party((2, 443), (0, 183))
    window._refresh_ram_backend_debug()
    assert window.training_view.selected_slot == 2
    assert _status(window, 2) == "good"
    assert _status(window, 1) == "no-focus"
    source.party = _party((2, 443), (0, 184))
    window._refresh_ram_backend_debug()
    assert window.tracker_party_cards[2]["pokemon"].species == "Azumarill"
    assert window.tracker_party_cards[2]["training_preference"] == preference
    assert _status(window, 2) == "good"
    source.party = _party((2, 443), (3, 184))
    window._refresh_ram_backend_debug()
    assert _status(window, 2) == "no-focus"
    assert window.tracker_party_cards[2]["training_preference"] is None
    assert store.get(0) == preference


def test_modal_reorder_saves_captured_pid_and_refreshes_current_party(make_window, monkeypatch):
    window, source, store = make_window()
    source.opponents(66)
    window._refresh_ram_backend_debug()

    def reorder():
        source.party = _party((2, 443), (0, 184))
        window._refresh_ram_backend_debug()

    _edit(monkeypatch, window, {"attack"}, during_dialog=reorder)

    assert store.get(0) == EVTrainingPreference(frozenset({"attack"}))
    assert store.get(2) is None
    assert window.tracker_party_cards[1]["training_preference"] is None
    assert window.training_view.selected_slot == 2
    assert window.training_view.identity.species.text().startswith("Azumarill")
    assert _status(window, 2) == "good"
    assert _status(window, 1) == "no-focus"


def test_compact_uses_same_result_and_updates_focus_without_another_poll(make_window, monkeypatch):
    window, source, store = make_window()
    source.opponents(66, 63)
    window._refresh_ram_backend_debug()
    _edit(monkeypatch, window, {"attack"})
    results = window.training_recommendations
    assert results[1].status is RecommendationStatus.MIXED
    shared_panel = window.recommendation_panel

    window.set_compact_mode(True)

    assert window.recommendation_panel is shared_panel
    assert window.training_recommendations is results
    assert _status(window) == "mixed"
    assert not shared_panel.rows[1][2].isHidden()
    _edit(monkeypatch, window, {"attack", "special_attack"})
    assert _status(window) == "good"
    assert store.get(0).allowed_stats == {"attack", "special_attack"}
    window.compact_training_view.clear_button.click()
    assert _status(window) == "no-focus"
    window.set_compact_mode(False)
    assert window.training_view.focus_summary.text() == "No focus set"


def test_actual_evs_are_independent_of_focus_and_legacy_targets(make_window, monkeypatch):
    window, source, _store = make_window()
    evs = dict(zip(("hp", "attack", "defense", "special_attack", "special_defense", "speed"),
                   (74, 102, 8, 0, 4, 55)))
    mon = replace(source.party.pokemon[0], evs=evs, ev_total=243)
    source.party = replace(source.party, pokemon=(mon, source.party.pokemon[1]))
    window._refresh_ram_backend_debug()
    _edit(monkeypatch, window, {"hp", "attack"})
    for view in (window.training_view, window.compact_training_view):
        assert [label.text() for label in view.ev_values.values()] == [str(v) for v in evs.values()]
        assert view.total.text() == "243 / 510 total"
        assert not hasattr(view, "ev_bars")


def test_failed_save_or_clear_keeps_rendered_saved_preference(make_window, monkeypatch):
    window, source, store = make_window()
    preference = EVTrainingPreference(frozenset({"attack"}))
    store.set(0, preference)
    source.opponents(66)
    window._refresh_ram_backend_debug()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1]))

    def fail():
        raise OSError("Disk unavailable")

    monkeypatch.setattr(store, "_save", fail)
    _edit(monkeypatch, window, {"speed"})
    assert store.get(0) == preference
    assert _status(window) == "good"
    window.training_view.clear_button.click()
    assert store.get(0) == preference
    assert len(warnings) == 2
    assert _status(window) == "good"


def test_unavailable_yield_and_disconnect_remove_previous_battle_result(make_window):
    window, source, store = make_window()
    store.set(0, EVTrainingPreference(frozenset({"attack"})))
    source.opponents(66)
    window._refresh_ram_backend_debug()
    assert _status(window) == "good"
    assert get_training_ev_yield(66).attack == 1
    assert get_training_ev_yield(99999) is None
    window._refresh_training_recommendations((SimpleNamespace(species_id=99999),))
    assert _status(window) == "unavailable"
    assert not window.training_recommendations
    source.connected = False
    window._refresh_ram_backend_debug()
    assert not window.training_recommendations
    assert all(row[0].isHidden() for row in window.recommendation_panel.rows.values())
