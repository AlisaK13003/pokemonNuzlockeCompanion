from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.core.ev_targets import EVTargetStore
from pokemon_ev_tracker.core.friendship_training import FriendshipWalkSessionStats
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.data_sources.base import DataSourceSnapshot
from pokemon_ev_tracker.data_sources.bizhawk import BizHawkRamDataSource
from pokemon_ev_tracker.games.platinum.decoder import decode_party
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.games.platinum.profile import PLATINUM_PROFILE
from pokemon_ev_tracker.pokemon.gen4.crypto import (
    BOX_DATA_SIZE,
    calculate_checksum,
    encrypt_box_data,
    xor_words,
)
from pokemon_ev_tracker.pokemon.gen4.structure import PARTY_POKEMON_SIZE
from pokemon_ev_tracker.transport.bizhawk_server import FriendshipWalkCommandReceipt
from pokemon_ev_tracker.ui.main_window import MainWindow


@pytest.fixture
def make_window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BizHawkRamDataSource, "start", lambda self: None)
    monkeypatch.setattr(BizHawkRamDataSource, "stop", lambda self: None)
    monkeypatch.setattr(AppSettings, "save_default", lambda self: None)

    def create(settings: AppSettings | None = None) -> MainWindow:
        window = MainWindow(
            settings or AppSettings(),
            target_store=EVTargetStore(tmp_path / "targets.json"),
            nuzlocke_store=NuzlockeStore(tmp_path / "nuzlocke_runs.json"),
        )
        window.refresh_timer.stop()
        return window

    yield create
    assert app is not None


@pytest.mark.parametrize("compact", [False, True])
def test_ram_warning_footer_never_reflows_tracker_controls(make_window, compact) -> None:
    app = QApplication.instance()
    window = make_window()
    window.resize(640, 520)
    if compact:
        window.set_compact_mode(True)
    window.show()
    app.processEvents()

    valid_state = decode_party(_party_payload((_party_record(0x12345678, 183, 47, attack=27),)))
    warning_text = (
        "RAM checksum failed for slot(s) 3; showing the last valid matching-PID sample. "
        "This full message remains available in the status tooltip."
    )
    warning_state = replace(valid_state, live_read_warning=warning_text)
    window._refresh_tracker_party(True, valid_state)
    for _ in range(3):
        app.processEvents()
    start_button = window.start_friendship_walk_button
    original_y = start_button.mapToGlobal(QPoint(0, 0)).y()

    window._refresh_tracker_party(True, warning_state)
    for _ in range(3):
        app.processEvents()
    assert start_button.mapToGlobal(QPoint(0, 0)).y() == original_y
    assert window.statusBar().isAncestorOf(window.tracker_status_message)
    assert not window.training_view.isAncestorOf(window.tracker_status_message)
    assert not window.tracker_status_message.wordWrap()
    assert window.tracker_status_message.toolTip() == warning_text
    assert window.statusBar().height() == 26
    if compact:
        assert window.tracker_status_message.text() == "⚠ RAM checksum warning"

    window._refresh_tracker_party(True, valid_state)
    for _ in range(3):
        app.processEvents()
    assert start_button.mapToGlobal(QPoint(0, 0)).y() == original_y
    assert window.tracker_status_message.text() == "BizHawk RAM • CONNECTED"
    assert window.tracker_status_message.toolTip() == ""
    window.close()


def test_training_selector_and_single_workspace_fit_desktop_breakpoints(make_window) -> None:
    app = QApplication.instance()
    window = make_window()
    window.show()
    party = decode_party(_party_payload(tuple(
        _party_record(0x1000 + slot, 183 + slot, 47, attack=20 + slot)
        for slot in range(6))))
    window._refresh_tracker_party(True, party)
    view = window.training_view
    for width in (1400, 1000, 800, 620):
        window.resize(width, 500)
        app.processEvents()
        slots = list(view.selector.slots.values())
        assert len(slots) == 6
        assert all(slot.isVisible() and slot.isEnabled() for slot in slots)
        assert sum(slot.isChecked() for slot in slots) == 1
        assert max(slot.geometry().right() for slot in slots) < view.selector.width()
        assert view.scroll.widget().width() <= view.scroll.viewport().width()
        assert view.scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert window.shell.workspace.currentWidget() is view
        assert all(not card["widget"].isVisible() for card in window.tracker_party_cards.values())
    assert view.scroll.verticalScrollBar().maximum() > 0
    window.close()


def test_narrow_workspaces_keep_ev_summary_and_inspection_fields(make_window) -> None:
    app = QApplication.instance()
    window = make_window()
    window.resize(620, 500)
    window.show()
    party = decode_party(_party_payload((_party_record(0x1234, 183, 47, attack=20),)))
    window._refresh_tracker_party(True, party)
    app.processEvents()
    assert window.training_view.total.text() == "0 / 510 total"
    assert all(label.isVisible() for label in window.training_view.ev_values.values())
    window.set_tracker_view("stats")
    app.processEvents()
    view = window.party_stats_view
    assert window.shell.workspace.currentWidget() is view
    assert view.stat_values["attack"].text() == "20"
    assert view.friendship.isVisible()
    assert view.moves_panel.isVisible()
    assert view.scroll.widget().width() <= view.scroll.viewport().width()
    window.close()


def test_training_ev_log_scrolls_without_expanding_workspace(make_window) -> None:
    app = QApplication.instance()
    window = make_window()
    window.resize(900, 650)
    window.show()
    window._ev_history_records = [
        (f"15:42:{index:02d}   sheepy   Attack 0 -> 1   +1", "+1 Attack")
        for index in range(80)]
    window._render_ev_history()
    app.processEvents()
    assert window.ev_change_list.maximumHeight() <= 230
    assert window.ev_change_list.verticalScrollBar().maximum() > 0
    assert window.training_view.runtime_hosts["history"].isAncestorOf(window.ev_change_log)
    window.close()


@pytest.mark.parametrize("width", (700, 900, 1400))
def test_party_stats_show_all_fields_and_four_move_slots_at_each_width(make_window, width: int) -> None:
    app = QApplication.instance()
    window = make_window()
    window.resize(width, 520)
    window.show()
    party = decode_party(_party_payload((_party_record(
        0x12345678, 183, 47, attack=27, friendship=164, moves=(98, 45, 0, 1)),)))
    window._refresh_tracker_party(True, party)
    window.set_tracker_view("stats")
    app.processEvents()
    view = window.party_stats_view
    assert view.stat_values["attack"].text() == "27"
    assert all(label.isVisible() for label in view.stat_names.values())
    assert all(label.isVisible() for label in view.iv_values.values())
    assert [label.text() for label in view.move_names] == [
        "01  Quick Attack", "02  Growl", "03  Empty move slot", "04  Pound"]
    assert all(label.isVisible() for label in view.move_names)
    assert view.friendship_bar.isVisible()
    assert view.friendship_bar.value() == 164
    assert view.scroll.widget().width() <= view.scroll.viewport().width()
    assert view.scroll.verticalScrollBar().maximum() > 0
    window.close()


def test_party_selector_height_stays_compact_when_window_grows(make_window) -> None:
    app = QApplication.instance()
    window = make_window()
    window.resize(900, 520)
    window.show()
    party = decode_party(_party_payload((_party_record(0x12345678, 183, 47, attack=27),)))
    window._refresh_tracker_party(True, party)
    window.set_tracker_view("stats")
    app.processEvents()
    view = window.party_stats_view
    assert view.item_name.text() == "No held item"
    selector_height = view.selector.height()
    window.resize(900, 820)
    app.processEvents()
    assert view.selector.height() == selector_height
    assert view.selector.height() <= 100
    window.close()


def test_party_selection_synchronizes_all_workspaces_and_follows_identity(make_window) -> None:
    app = QApplication.instance()
    window = make_window()
    records = (_party_record(0x1234, 183, 47, attack=27),
               _party_record(0x5678, 443, 8, attack=41))
    window._refresh_tracker_party(True, decode_party(_party_payload(records)))
    window.training_view.selector.slots[2].click()
    assert all(view.selected_slot == 2 for view in window._party_views)
    assert window.party_stats_view.stat_values["attack"].text() == "41"
    window._refresh_tracker_party(True, decode_party(_party_payload(tuple(reversed(records)))))
    app.processEvents()
    assert all(view.selected_slot == 1 for view in window._party_views)
    assert window.party_stats_view.stat_values["attack"].text() == "41"
    assert window.party_stats_view.identity.species.text().startswith("Gible")
    window.close()


def test_nuzlocke_tables_remain_locally_scrollable_at_narrow_width(make_window) -> None:
    app = QApplication.instance()
    window = make_window()
    window.nuzlocke_view.store.create_run("Narrow layout", PLATINUM_NUZLOCKE_PROFILE)
    window.nuzlocke_view._refresh_all()
    window.resize(620, 500)
    window.show()
    window.set_route("nuzlocke")
    window.nuzlocke_view.show_section("encounters")
    app.processEvents()

    assert window.nuzlocke_scroll_area.widgetResizable()
    assert window.nuzlocke_scroll_area.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert window.nuzlocke_view.encounters_table.horizontalScrollBar().maximum() > 0
    window._geometry_save_timer.stop()
    window.close()


def test_training_stats_toggle_hides_log_without_clearing_history(make_window) -> None:
    window = make_window()
    window._ev_history_records = [("15:42:11  Marill Attack 0 -> 1  +1", "+1 Attack")]
    window._render_ev_history()
    window.set_tracker_view("stats")
    assert window.shell.workspace.currentWidget() is window.party_stats_view
    assert window.ev_change_log.isHidden()
    assert window.ev_change_list.count() == 1
    assert window.settings.tracker_view == "stats"
    window.set_tracker_view("training")
    assert window.shell.workspace.currentWidget() is window.training_view
    assert not window.ev_change_log.isHidden()
    assert window.ev_change_list.item(0).text().startswith("15:42:11")
    window.close()


def test_stats_view_is_restored_from_settings(make_window) -> None:
    window = make_window(AppSettings(tracker_view="stats"))
    assert window.tracker_view == "stats"
    assert window.tracker_view_buttons["stats"].isChecked()
    assert window.shell.workspace.currentWidget() is window.party_stats_view
    assert window.ev_change_log.isHidden()
    window.close()


def test_stats_view_works_in_compact_mode(make_window) -> None:
    window = make_window()
    window.set_tracker_view("stats")
    window.set_compact_mode(True)
    assert window.compact_mode
    assert window.shell.workspace.currentWidget() is window.compact_party_stats_view
    assert window.ev_change_log.isHidden()
    window.close()


def test_party_reorder_keeps_stats_attached_to_each_ram_record(make_window) -> None:
    window = make_window()
    window.set_tracker_view("stats")
    first = _party_payload(
        (
            _party_record(0x12345678, 183, 47, attack=27, moves=(401, 0, 0, 0),
                          move_pps=(8, 0, 0, 0), move_pp_ups=(2, 0, 0, 0)),
            _party_record(0x12345679, 443, 8, attack=41, moves=(85, 0, 0, 0),
                          move_pps=(12, 0, 0, 0)),
        )
    )
    second = _party_payload(
        (
            _party_record(0x12345679, 443, 8, attack=41, moves=(85, 0, 0, 0),
                          move_pps=(12, 0, 0, 0)),
            _party_record(0x12345678, 183, 47, attack=27, moves=(401, 0, 0, 0),
                          move_pps=(8, 0, 0, 0), move_pp_ups=(2, 0, 0, 0)),
        )
    )

    window._refresh_tracker_party(True, decode_party(first))
    assert window.tracker_party_cards[1]["stat_values"]["attack"].text() == "27"
    assert window.tracker_party_cards[2]["stat_values"]["attack"].text() == "41"
    assert window.party_stats_view.stat_values["attack"].text() == "27"
    assert window.party_stats_view.move_names[0].text() == "01  Aqua Tail"
    assert window.party_stats_view.move_pp[0].text() == "8 / 14 PP"

    window._refresh_tracker_party(True, decode_party(second))
    assert window.tracker_party_cards[1]["stat_values"]["attack"].text() == "41"
    assert window.tracker_party_cards[1]["ability"].text() == "Ability: Sand Veil"
    assert window.tracker_party_cards[2]["stat_values"]["attack"].text() == "27"
    assert window.tracker_party_cards[2]["ability"].text() == "Ability: Thick Fat"
    assert window.party_stats_view.selected_slot == 2
    assert window.party_stats_view.stat_values["attack"].text() == "27"
    assert "Thick Fat" in window.party_stats_view.ability.text()
    assert window.party_stats_view.move_names[0].text() == "01  Aqua Tail"
    assert window.party_stats_view.move_pp[0].text() == "8 / 14 PP"
    window.party_stats_view.set_selected_slot(1)
    assert window.party_stats_view.move_names[0].text() == "01  Thunderbolt"
    assert window.party_stats_view.move_pp[0].text() == "12 / 15 PP"
    window.close()


def test_nature_highlights_only_boosted_and_lowered_non_hp_stats(make_window) -> None:
    window = make_window()
    window.set_tracker_view("stats")
    record = _party_record(3, 183, 47, attack=27)
    window._refresh_tracker_party(True, decode_party(_party_payload((record,))))
    view = window.party_stats_view
    assert "Adamant" in view.nature.text()
    assert view.stat_names["attack"].property("natureRole") == "up"
    assert view.stat_names["special_attack"].property("natureRole") == "down"
    assert view.stat_names["hp"].property("natureRole") == "neutral"
    window.close()


def test_party_stats_renders_friendship_and_debug_value(make_window) -> None:
    window = make_window()
    window.set_tracker_view("stats")
    party_state = decode_party(
        _party_payload(
            (
                _party_record(
                    3,
                    183,
                    47,
                    attack=27,
                    friendship=164,
                    moves=(98, 45, 0, 1),
                ),
            )
        )
    )

    window._refresh_tracker_party(True, party_state)
    window._refresh_ram_party_debug(party_state, None)
    view = window.party_stats_view
    assert window.shell.workspace.currentWidget() is view
    assert view.friendship.text() == "Friendship 164 / 255"
    assert view.friendship_bar.maximum() == 255
    assert view.friendship_bar.value() == 164
    assert [label.text() for label in view.move_names] == [
        "01  Quick Attack", "02  Growl", "03  Empty move slot", "04  Pound"]
    assert view.move_types[0].text() == "Normal"
    assert view.move_categories[0].text() == "Physical"
    assert "Priority +1" in view.move_details[0].text()
    assert view.move_categories[1].text() == "Status"
    assert "Power —" in view.move_details[1].text()
    assert "Friendship: 164" in window.ram_party_details.toPlainText()
    window.close()


def test_party_stats_moves_update_live_from_ram(make_window) -> None:
    window = make_window()
    window.set_tracker_view("stats")
    first = decode_party(
        _party_payload(
            (_party_record(3, 183, 47, attack=27, moves=(98, 45, 0, 1),
                           move_pps=(22, 30, 0, 35)),)
        )
    )
    updated = decode_party(
        _party_payload(
            (_party_record(3, 183, 47, attack=27, moves=(85, 111, 0, 1),
                           move_pps=(10, 35, 0, 34), move_pp_ups=(1, 0, 0, 0)),)
        )
    )

    window._refresh_tracker_party(True, first)
    view = window.party_stats_view
    original_labels = tuple(view.move_names)
    assert view.move_pp[0].text() == "22 / 30 PP"
    window._refresh_tracker_party(True, updated)

    assert tuple(view.move_names) == original_labels
    assert [label.text() for label in view.move_names] == [
        "01  Thunderbolt", "02  Defense Curl", "03  Empty move slot", "04  Pound"]
    assert view.move_pp[0].text() == "10 / 18 PP"
    assert view.move_pp[1].text() == "35 / 40 PP"
    assert view.move_details[1].text() == "Power —  ·  Accuracy —"
    assert view.move_types[1].text() == "Normal"
    assert view.move_categories[1].text() == "Status"
    assert not view.move_types[2].isVisible()
    compact = window.compact_party_stats_view
    assert compact.move_pp[0].text() == "10 / 18 PP"
    assert compact.move_details[0].isHidden()
    window.close()


def test_friendship_goal_selection_and_live_auto_stop_use_safe_stop(make_window) -> None:
    window = make_window()
    first = decode_party(_party_payload((
        _party_record(3, 183, 47, attack=27, friendship=74),
        _party_record(4, 443, 8, attack=41, friendship=90),
    )))
    window._refresh_tracker_party(True, first)
    panel = window.friendship_walk_group
    assert panel.selector.count() == 2
    assert "Friendship 74/255" in panel.selector.itemText(0)
    assert "Marill" in panel.selector.itemText(0)
    assert panel.goal_picker.itemData(0) == 160
    assert panel.goal_picker.itemData(1) == 220
    assert panel.goal_picker.itemData(2) == 255
    panel.goal_picker.setCurrentIndex(1)
    first_identity = panel.selected_identity
    assert window.settings.friendship_goals[first_identity] == 220
    panel.selector.setCurrentIndex(1)
    assert panel.friendship.text() == "Friendship 90 / 255"
    panel.selector.setCurrentIndex(0)
    assert panel.goal == 220
    panel.goal_picker.setCurrentIndex(0)

    base_payload = {"frame": 100, "player_x": 4, "player_y": 8,
                    "player_coordinates_validated": True}
    payload = SimpleNamespace(payload=base_payload, source="file")
    commands = []

    def send(action, value=None):
        commands.append((action, value))
        return FriendshipWalkCommandReceipt(len(commands), "file")

    window.ram_data_source.send_friendship_walk_command = send
    window.ram_data_source.snapshot = Mock(return_value=SimpleNamespace(
        connected=True, details={"party_payload": payload, "party_payload_fresh": True,
                                 "party_state": first, "active_enemy_battlers": ()},
    ))
    window._start_friendship_walk()
    assert commands == [("START", "horizontal")]
    assert panel.telemetry.text() == "Calibrating…"
    ack_payload = SimpleNamespace(payload={**base_payload, "frame": 101,
        "friendship_walk_enabled": True, "friendship_walk_ack_sequence": 1}, source="file")
    window._refresh_friendship_walk(SimpleNamespace(details={
        "party_payload_fresh": True, "party_state": first,
    }), ack_payload, ())
    assert window.friendship_walk.active

    reached = decode_party(_party_payload((
        _party_record(3, 183, 47, attack=27, friendship=160),
        _party_record(4, 443, 8, attack=41, friendship=90),
    )))
    window._refresh_tracker_party(True, reached)
    snapshot = SimpleNamespace(details={"party_payload_fresh": True, "party_state": reached})
    window._refresh_friendship_walk(snapshot, ack_payload, ())
    assert not window.friendship_walk.active
    assert commands[-1] == ("STOP", None)
    assert commands.count(("STOP", None)) == 1
    assert panel.status.text() == "Goal reached"
    assert panel.session_gain.text() == "+86 friendship this session"
    window._refresh_friendship_walk(snapshot, ack_payload, ())
    assert commands.count(("STOP", None)) == 1
    window.close()


def test_friendship_tracked_departure_pauses_without_switching(make_window) -> None:
    window = make_window()
    first = decode_party(_party_payload((
        _party_record(3, 183, 47, attack=27, friendship=74),
        _party_record(4, 443, 8, attack=41, friendship=90),
    )))
    window._refresh_tracker_party(True, first)
    identity = window.friendship_walk_group.selected_identity
    commands = []
    window.ram_data_source.send_friendship_walk_command = lambda action, value=None: (
        commands.append((action, value)) or FriendshipWalkCommandReceipt(len(commands), "file")
    )
    window.friendship_walk.start("horizontal", 100, 4, 8)
    window._walk_session = FriendshipWalkSessionStats(0, 74, 74)
    window._walk_session_identity = identity
    remaining = decode_party(_party_payload((
        _party_record(4, 443, 8, attack=41, friendship=90),
    )))
    window._refresh_tracker_party(True, remaining)
    payload = SimpleNamespace(payload={"frame": 101, "player_x": 4, "player_y": 8,
                                       "player_coordinates_validated": True}, source="file")
    window._refresh_friendship_walk(SimpleNamespace(details={
        "party_payload_fresh": True, "party_state": remaining,
    }), payload, ())
    assert not window.friendship_walk.active
    assert commands[-1] == ("STOP", None)
    assert window.friendship_walk_group.selected_identity == identity
    assert window.friendship_walk_group.status.text() == "Tracked Pokémon left party"
    window.friendship_walk_group.selector.setCurrentIndex(1)
    assert window.friendship_walk_group.selected_pokemon.species == "Gible"
    window.close()


def test_stale_friendship_ram_does_not_complete_goal(make_window) -> None:
    window = make_window()
    first = decode_party(_party_payload((
        _party_record(3, 183, 47, attack=27, friendship=74),
    )))
    window._refresh_tracker_party(True, first)
    window.friendship_walk.start("horizontal", 100, 4, 8)
    window._walk_session = FriendshipWalkSessionStats(0, 74, 74)
    window._walk_session_identity = window.friendship_walk_group.selected_identity
    commands = []
    window.ram_data_source.send_friendship_walk_command = lambda action, value=None: (
        commands.append((action, value)) or FriendshipWalkCommandReceipt(len(commands), "file")
    )
    reached = decode_party(_party_payload((
        _party_record(3, 183, 47, attack=27, friendship=160),
    )))
    payload = SimpleNamespace(payload={"frame": 101, "player_x": 4, "player_y": 8,
                                       "player_coordinates_validated": True}, source="file")
    window._refresh_friendship_walk(SimpleNamespace(details={
        "party_payload_fresh": False, "party_state": reached,
    }), payload, ())
    assert not window._walk_target_reached
    assert window.friendship_walk_group.status.text() != "Goal reached"
    assert not window.friendship_walk.active
    assert commands[-1] == ("STOP", None)
    window.close()


def test_invalid_tracked_record_pauses_as_ram_issue_not_departure(make_window) -> None:
    window = make_window()
    first = decode_party(_party_payload((
        _party_record(3, 183, 47, attack=27, friendship=74),
    )))
    window._refresh_tracker_party(True, first)
    identity = window.friendship_walk_group.selected_identity
    window.friendship_walk.start("horizontal", 100, 4, 8)
    window._walk_session = FriendshipWalkSessionStats(0, 74, 74)
    window._walk_session_identity = identity
    commands = []
    window.ram_data_source.send_friendship_walk_command = lambda action, value=None: (
        commands.append((action, value)) or FriendshipWalkCommandReceipt(len(commands), "file")
    )
    corrupt = SimpleNamespace(party_count_valid=True, pokemon=(
        SimpleNamespace(stable_id=identity, checksum_valid=False),
    ))
    payload = SimpleNamespace(payload={"frame": 101, "player_x": 4, "player_y": 8,
                                       "player_coordinates_validated": True}, source="file")
    snapshot = SimpleNamespace(details={"party_payload_fresh": True, "party_state": corrupt})
    window._refresh_friendship_walk(snapshot, payload, ())
    window._refresh_friendship_walk(snapshot, payload, ())
    assert window.friendship_walk_group.status.text() == "Paused — RAM connection lost"
    assert not window._walk_tracked_missing
    assert commands.count(("STOP", None)) == 1
    window.close()


def test_already_reached_friendship_goal_never_starts_commands(make_window) -> None:
    window = make_window()
    party = decode_party(_party_payload((
        _party_record(3, 183, 47, attack=27, friendship=170),
    )))
    window._refresh_tracker_party(True, party)
    sender = Mock()
    window.ram_data_source.send_friendship_walk_command = sender
    window._start_friendship_walk()
    sender.assert_not_called()
    assert window.friendship_walk_group.status.text() == "Goal reached"
    assert not window.friendship_walk.active
    window.close()


def test_friendship_updates_live_when_ram_value_changes(make_window) -> None:
    window = make_window()
    window.set_tracker_view("stats")

    first = decode_party(
        _party_payload((_party_record(3, 183, 47, attack=27, friendship=164),))
    )
    updated = decode_party(
        _party_payload((_party_record(3, 183, 47, attack=27, friendship=165),))
    )
    window._refresh_tracker_party(True, first)
    bar = window.party_stats_view.friendship_bar
    original_bar_id = id(bar)

    window._refresh_tracker_party(True, updated)

    view = window.party_stats_view
    assert view.friendship.text() == "Friendship 165 / 255"
    assert view.friendship_bar.value() == 165
    assert id(view.friendship_bar) == original_bar_id
    window.close()


def test_compact_party_stats_shows_minimal_friendship_without_bar(make_window) -> None:
    window = make_window()
    window.set_tracker_view("stats")
    window.set_compact_mode(True)
    party_state = decode_party(
        _party_payload((_party_record(3, 183, 47, attack=27, friendship=164),))
    )

    window._refresh_tracker_party(True, party_state)
    view = window.compact_party_stats_view
    assert view.nature.text().startswith("Adamant")
    assert "Thick Fat" in view.ability.text()
    assert view.friendship.text() == "Friendship 164 / 255"
    assert view.friendship_bar.isHidden()
    window.close()


def test_main_window_baselines_then_surfaces_new_party_acquisition(monkeypatch, tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    run_store = NuzlockeStore(tmp_path / "runs.json")
    run = run_store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    original = _party_record(0x12345678, 21, 0, attack=20, met_location_id=0x11, met_level=3)

    class SnapshotSource:
        profile = PLATINUM_PROFILE

        def __init__(self):
            self.state = decode_party(_party_payload((original,)))

        def start(self):
            pass

        def stop(self):
            pass

        def snapshot(self):
            return DataSourceSnapshot(
                "BizHawk RAM",
                True,
                {
                    "heartbeat": None,
                    "party_state": self.state,
                    "display_party_state": self.state,
                    "party_payload": None,
                    "battle_battlers": (),
                    "active_enemy_battlers": (),
                },
            )

    source = SnapshotSource()
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=run_store,
    )
    try:
        assert run.acquisition_events == []
        assert run.observed_pokemon_ids == ["pid:12345678:ot:0000:0000"]

        newly_seen = _party_record(
            0x87654321,
            403,
            0,
            attack=23,
            met_location_id=0x11,
            met_level=4,
        )
        source.state = decode_party(_party_payload((original, newly_seen)))
        window._refresh_ram_backend_debug()
        app.processEvents()

        assert len(run.acquisition_events) == 1
        assert run.acquisition_events[0].species_name == "Shinx"
        assert run.acquisition_events[0].suggested_location_name == "Route 202"
        assert window.nuzlocke_view.acquisition_selector.currentData() == (
            "pid:87654321:ot:0000:0000"
        )
        debug_text = window.ram_party_details.toPlainText()
        assert "Met location: ID 17 (Route 202)" in debug_text
        assert "decrypted box +0x3E" in debug_text
        assert "Acquisition classification: WILD" in debug_text
        assert "Already observed in active run: true" in debug_text
        assert (
            run.encounters[
                next(
                    item.location_id
                    for item in PLATINUM_NUZLOCKE_PROFILE.locations
                    if item.name == "Route 202"
                )
            ].species
            == ""
        )
    finally:
        window.close()


def _party_payload(records: tuple[bytes, ...]) -> bytes:
    return (
        len(records).to_bytes(4, "little")
        + b"".join(records)
        + bytes(PARTY_POKEMON_SIZE * (6 - len(records)))
    )


def _party_record(
    pid: int,
    species_id: int,
    ability_id: int,
    *,
    attack: int,
    met_location_id: int = 0,
    met_level: int = 0,
    friendship: int = 0,
    moves: tuple[int, int, int, int] = (0, 0, 0, 0),
    move_pps: tuple[int, int, int, int] = (0, 0, 0, 0),
    move_pp_ups: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> bytes:
    box = bytearray(BOX_DATA_SIZE)
    box[0:2] = species_id.to_bytes(2, "little")
    box[0x0C] = friendship
    box[0x0D] = ability_id
    for index, move_id in enumerate(moves):
        offset = 0x20 + index * 2
        box[offset : offset + 2] = move_id.to_bytes(2, "little")
    box[0x28:0x2C] = bytes(move_pps)
    box[0x2C:0x30] = bytes(move_pp_ups)
    box[0x3E:0x40] = met_location_id.to_bytes(2, "little")
    box[0x57] = 12
    box[0x7C] = met_level
    box[0x30:0x34] = (31 << 5).to_bytes(4, "little")
    checksum = calculate_checksum(bytes(box))

    record = bytearray(PARTY_POKEMON_SIZE)
    record[0:4] = pid.to_bytes(4, "little")
    record[6:8] = checksum.to_bytes(2, "little")
    record[8 : 8 + BOX_DATA_SIZE] = encrypt_box_data(bytes(box), pid, checksum)
    stats = bytearray(0x14)
    stats[4] = 17
    stats[6:8] = (30).to_bytes(2, "little")
    stats[8:10] = (53).to_bytes(2, "little")
    stats[10:12] = attack.to_bytes(2, "little")
    stats[12:14] = (30).to_bytes(2, "little")
    stats[14:16] = (22).to_bytes(2, "little")
    stats[16:18] = (18).to_bytes(2, "little")
    stats[18:20] = (29).to_bytes(2, "little")
    record[0x88:0x9C] = xor_words(bytes(stats), pid)
    return bytes(record)
