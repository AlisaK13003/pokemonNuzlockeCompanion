"""Redesign contracts with real decoded fixtures and isolated persistence."""

import os
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication, QDialog, QProgressBar
from test_party_stats_view import _party_payload, _party_record
from test_training_integration import _party, _Source

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.config.training_preferences import EVTrainingPreferenceStore
from pokemon_ev_tracker.core.ev_training import EVTrainingPreference
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.decoder import decode_party
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.games.platinum.pc_storage import BoxedPokemon
from pokemon_ev_tracker.games.registry import default_game_provider
from pokemon_ev_tracker.pokemon.gen4.structure import decode_box_pokemon
from pokemon_ev_tracker.ui.inspection_views import BoxView, iv_quality
from pokemon_ev_tracker.ui.main_window import MainWindow
from pokemon_ev_tracker.ui.nuzlocke_dialogs import FinishRunDialog


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda self: None)
    source = _Source()
    preferences = EVTrainingPreferenceStore(tmp_path / "preferences.json")
    store = NuzlockeStore(tmp_path / "runs.json")
    widget = MainWindow(AppSettings(), data_source=source, nuzlocke_store=store,
                        training_preference_store=preferences)
    widget.refresh_timer.stop()
    widget.show()
    app.processEvents()
    yield widget, source, preferences, store, app
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_main_window_reaches_icon_navigation_breakpoint(window):
    widget, _, _, _, app = window
    widget.resize(480, 800)
    app.processEvents()
    assert widget.width() == 480
    assert widget.shell.tracker_button.text() == ""
    assert widget.shell.primary_nav.isVisible()
    assert widget.shell.primary_nav.parentWidget() is widget.shell.header
    assert widget.shell.settings_button.isVisible()


def test_direct_ev_buttons_save_by_identity_and_recalculate_without_polling(window):
    widget, source, preferences, _, _ = window
    source.opponents(66)
    widget._refresh_ram_backend_debug()
    widget.training_view.focus_chips["attack"].click()
    assert preferences.get(0).allowed_stats == {"attack"}
    assert widget.training_recommendations[1].status.value == "good"
    source.party = replace(source.party, pokemon=tuple(
        replace(p, slot=3 - p.slot) for p in reversed(source.party.pokemon)))
    widget._refresh_ram_backend_debug()
    assert widget.training_view.selected_slot == 2
    assert widget.training_view.focus_chips["attack"].isChecked()
    widget.training_view.clear_button.click()
    assert preferences.get(0) is None


@pytest.mark.parametrize("value,quality", ((0, "low"), (15, "low"), (16, "average"),
                                          (25, "average"), (26, "excellent"), (31, "excellent")))
def test_iv_quality_boundaries(value, quality):
    assert iv_quality(value) == quality


def test_style_refresh_skips_unchanged_state_but_updates_property_and_theme(monkeypatch):
    from unittest.mock import Mock

    from PySide6.QtWidgets import QLabel

    from pokemon_ev_tracker.ui.theme import apply_theme, refresh_style

    app = QApplication.instance() or QApplication([])
    label = QLabel("Status")
    label.setStyleSheet('QLabel[status="good"] { color: #00ff00; } '
                       'QLabel[status="bad"] { color: #ff0000; }')
    label.setProperty("status", "good")
    style = Mock(wraps=label.style())
    monkeypatch.setattr(label, "style", lambda: style)
    refresh_style(label)
    assert label.palette().color(label.foregroundRole()).name() == "#00ff00"
    for _ in range(20):
        refresh_style(label)
    assert style.polish.call_count == 1
    label.setProperty("status", "bad")
    refresh_style(label)
    assert style.polish.call_count == 2
    assert label.palette().color(label.foregroundRole()).name() == "#ff0000"
    apply_theme(label)
    refresh_style(label)
    assert style.polish.call_count == 3
    label.deleteLater()
    app.processEvents()


def test_inspection_follows_identity_updates_facts_and_uses_iv_scale(window):
    widget, source, _, _, app = window
    widget.set_route("stats")
    member = replace(source.party.pokemon[0], friendship=123, speed_iv=26)
    source.party = replace(source.party, pokemon=(member, source.party.pokemon[1]))
    widget._refresh_ram_backend_debug()
    card = widget.party_stats_view.cards[1]
    assert _plain_text(card.fact_values["friendship"]) == "123 / 255"
    assert card.iv_bars["speed"].maximum() == 31
    assert card.iv_bars["speed"].value() == 26
    assert card.iv_bars["speed"].property("ivQuality") == "excellent"
    assert not hasattr(widget.party_stats_view, "moves_panel")
    card_id = id(card)
    widget._refresh_ram_backend_debug()
    assert id(widget.party_stats_view.cards[1]) == card_id
    source.party = replace(source.party, pokemon=(replace(source.party.pokemon[1], slot=1),
                                                 replace(member, slot=2)))
    widget._refresh_ram_backend_debug()
    app.processEvents()
    assert widget.party_stats_view.selected_slot == 2
    assert _plain_text(widget.party_stats_view.cards[2].fact_values["friendship"]) == "123 / 255"


def test_party_selection_updates_friendship_target_sprite_and_preserves_identity(window):
    widget, source, _, _, app = window
    selected = source.party.pokemon[1]
    widget.training_view.selector.slots[2].click()
    panel = widget.friendship_walk_group
    assert panel.selected_identity == selected.stable_id
    assert panel.selected_pokemon is selected
    assert panel.sprite._asset_key[0] == selected.species_id
    assert panel.species.text() == selected.species
    assert panel.current_value.text() == str(selected.friendship)
    source.party = replace(source.party, pokemon=tuple(
        replace(p, slot=3 - p.slot) for p in reversed(source.party.pokemon)))
    widget._refresh_ram_backend_debug()
    app.processEvents()
    assert panel.selected_identity == selected.stable_id
    assert panel.sprite._asset_key[0] == selected.species_id
    assert widget.training_view.selected_slot == 1
    widget.party_stats_view._select(2)
    assert panel.selected_identity == source.party.pokemon[1].stable_id


def test_party_stats_and_training_share_companion_sprite_assets(window):
    widget, source, _, _, _ = window
    for pokemon in source.party.pokemon:
        slot = pokemon.slot
        roster_sprite = widget.training_view.selector.slots[slot].sprite
        stats_sprite = widget.party_stats_view.cards[slot].sprite
        assert roster_sprite._asset_key == stats_sprite._asset_key
        assert stats_sprite._asset_key[1].path.parent.name == "companion"
    widget.training_view.selector.slots[2].click()
    assert widget.friendship_walk_group.sprite._asset_key == widget.party_stats_view.cards[2].sprite._asset_key


@pytest.mark.parametrize("count", range(1, 7))
@pytest.mark.parametrize("width", (320, 500, 860, 1120))
def test_compact_grid_equal_width_and_matches_battle_panel(window, count, width):
    widget, source, preferences, _, app = window
    source.party = _party(*tuple((index, 1 + index) for index in range(count)))
    source.opponents(66)
    for member in source.party.pokemon:
        preferences.set(member.decoded.diagnostics.pid, EVTrainingPreference({"attack"}))
    widget._refresh_ram_backend_debug()
    widget.set_route("nuzlocke")
    widget.set_compact_mode(True)
    widget.resize(width, 450)
    app.processEvents()
    compact = widget.compact_training_view
    assert widget.route == "training"
    assert widget.shell.workspace.currentWidget() is compact
    assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert not compact.findChildren(QProgressBar)
    assert not widget.ev_change_log.isVisible()
    assert not widget.friendship_walk_group.isVisible()
    assert compact.party.width() == compact.opponent_host.width()
    tiles = [tile for tile, *_ in compact.tiles.values() if tile.isVisible()]
    assert len(tiles) == count
    assert max(tile.width() for tile in tiles) - min(tile.width() for tile in tiles) <= 1
    assert all(tile.geometry().right() < compact.party.width() for tile in tiles)
    assert all(compact.tiles[slot][4].text() == "GOOD" for slot in range(1, count + 1))


def test_disconnect_replaces_live_views_but_keeps_settings_and_diagnostics(window):
    widget, source, _, _, _ = window
    source.connected = False
    widget._refresh_ram_backend_debug()
    widget.set_route("nuzlocke")
    assert widget.shell.workspace.currentWidget() is widget.disconnected_view
    assert widget.shell.page_heading.isHidden()
    assert widget.shell.game.text() == "Waiting for BizHawk"
    assert not widget.compact_mode_button.isEnabled()
    widget.set_compact_mode(True)
    assert not widget.compact_mode
    widget.set_route("settings")
    assert widget.shell.workspace.currentWidget() is widget.preferences_view
    widget.set_route("diagnostics")
    assert widget.shell.workspace.currentWidget() is widget.diagnostics_view
    source.connected = True
    widget._refresh_ram_backend_debug()
    widget.set_route("training")
    assert widget.shell.workspace.currentWidget() is widget.training_view
    assert not widget.shell.page_heading.isHidden()
    assert "Platinum" in widget.shell.game.text()


def test_settings_wire_window_diagnostics_run_and_persistence(window, tmp_path):
    widget, _, _, store, _ = window
    run = store.create_run("Settings", PLATINUM_NUZLOCKE_PROFILE)
    widget.nuzlocke_view._refresh_all()
    widget.preferences_view.refresh()
    for key in ("always_on_top", "advanced_diagnostics", "auto_record", "auto_deaths"):
        widget.preferences_view.inputs[key].click()
    assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert not widget.diagnostics_view.section_buttons["advanced"].isHidden()
    assert run.auto_record_unambiguous_encounters
    assert run.automatically_confirm_deaths
    widget.settings.save(tmp_path / "settings.json")
    loaded = AppSettings.load(tmp_path / "settings.json")
    assert loaded.always_on_top and loaded.advanced_diagnostics


def test_box_inspection_decodes_only_box_facts_and_never_uses_met_level(window):
    _, _, _, _, app = window
    raw = _party_record(7, 1, 65, attack=40, friendship=142, met_level=5)
    party = decode_party(_party_payload((raw,))).pokemon[0]
    decoded = decode_box_pokemon(raw[:136])
    assert decoded.ivs.attack == party.attack_iv
    assert decoded.friendship == party.friendship
    assert decoded.ability_id == party.ability_id
    assert decoded.nature_name == party.nature_name
    boxed = BoxedPokemon(2, 3, "Bulbasaur", decoded)
    view = BoxView(default_game_provider())
    view.refresh_storage(SimpleNamespace(box_count=18, slots_per_box=30,
                                         valid_pokemon=(boxed,)), True)
    assert view.empty.text() == "This box is empty."
    view.change_box(1)
    app.processEvents()
    assert view.cards[3].meta.text().startswith("Lv. —")
    assert view.cards[3].hp.text() == "HP unavailable"
    assert _plain_text(view.cards[3].fact_values["friendship"]) == "142 / 255"
    view.refresh_storage(None, False)
    assert not view.cards
    assert "validated" in view.empty.text()


@pytest.mark.parametrize("outcome", ("WON", "WIPED", "ABANDONED"))
def test_finish_run_preserves_records_notes_and_persists_outcome(window, monkeypatch, outcome):
    widget, _, _, store, _ = window
    run = store.create_run("Finish", PLATINUM_NUZLOCKE_PROFILE)
    store.update_run_details(run.run_id, "Existing notes", False)
    encounters = dict(run.encounters)
    widget.nuzlocke_view._refresh_all()
    def finish(dialog):
        dialog.outcome.setCurrentIndex(dialog.outcome.findData(outcome))
        dialog.notes.setPlainText("Final notes")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(FinishRunDialog, "exec", finish)
    widget.nuzlocke_view.finish_run_button.click()
    restored = NuzlockeStore(store.path).get_run(run.run_id)
    assert restored.status == outcome
    assert restored.notes == "Existing notes"
    assert restored.outcome_note == "Final notes"
    assert restored.encounters == encounters
    assert store.active_run is None


def test_dashboard_has_all_profile_locations_and_only_supported_filters(window):
    widget, _, _, store, _ = window
    store.create_run("Ledger", PLATINUM_NUZLOCKE_PROFILE)
    view = widget.nuzlocke_view
    view._refresh_all()
    assert view.ledger.rows.count() == 10
    view.ledger.expand_button.click()
    assert len(view.ledger.visible_records) == len(PLATINUM_NUZLOCKE_PROFILE.locations)
    assert set(view.ledger.filters) == {"", "NOT_ENCOUNTERED", "CAUGHT", "FAILED", "DEAD"}
    assert len(view.fight_line.nodes) <= 3
    assert view.readiness.count.text() == "2 / 6 Pokémon"


def test_settings_clears_primary_navigation_selection(window):
    widget, _, _, _, app = window
    widget.set_route("nuzlocke", persist=False)
    widget.set_route("settings", persist=False)
    app.processEvents()
    assert not widget.shell.tracker_button.isChecked()
    assert not any(button.isChecked() for button in widget.shell.nav_buttons.values())


def test_compact_geometry_survives_expand_and_reconnect(window):
    widget, source, _, _, app = window
    widget.set_compact_mode(True)
    widget.resize(520, 430)
    app.processEvents()
    geometry = widget._current_window_geometry()
    widget.set_compact_mode(False)
    widget.set_compact_mode(True)
    app.processEvents()
    assert widget._current_window_geometry() == geometry
    source.connected = False
    widget._refresh_ram_backend_debug()
    source.connected = True
    widget._refresh_ram_backend_debug()
    app.processEvents()
    assert widget.compact_mode
    assert widget._current_window_geometry() == geometry


def test_history_preserves_ages_after_front_truncation_and_updates_without_rebuilding(window, monkeypatch):
    from pokemon_ev_tracker.ui.ev_change_log import EvChangeLogWidget
    _, _, _, _, app = window
    clock = [100.0]
    monkeypatch.setattr("pokemon_ev_tracker.ui.ev_change_log.time.monotonic", lambda: clock[0])
    log = EvChangeLogWidget(lambda: None)
    first = ("first", "+1 Attack — First")
    second = ("second", "+2 Speed — Second")
    third = ("third", "+1 Defense — Third")
    log.render([first, second], False)
    clock[0] = 160
    log.render([second, third], False)
    log.resize(360, 160)
    log.show()
    app.processEvents()
    row = log.list.itemWidget(log.list.item(0))
    clock[0] = 190
    log._update_ages()
    assert log.list.item(0).text().endswith("30s")
    assert log.list.item(1).text().endswith("1m")
    assert log.list.itemWidget(log.list.item(0)) is row
    assert row.width() == log.list.viewport().width()
    log.close()
    log.deleteLater()
    app.processEvents()


def test_friendship_stop_remains_available_during_pending_and_paused_states(window):
    widget, _, _, _, _ = window
    panel = widget.friendship_walk_group
    panel.stop.setEnabled(True)
    for status, visible in (("Idle", False), ("Starting — waiting for Lua acknowledgement", True),
                            ("Paused — RAM connection lost", True)):
        panel.render_training(status=status, current=100, target=220, eta=None, session=None)
        assert panel.stop.isHidden() is not visible


def _plain_text(label):
    document = QTextDocument()
    document.setHtml(label.text())
    return document.toPlainText()
