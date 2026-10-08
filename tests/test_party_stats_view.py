from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
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






def test_main_window_backfills_party_baseline_and_surfaces_conflicting_acquisition(monkeypatch, tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    run_store = NuzlockeStore(tmp_path / "runs.json")
    run = run_store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    original = _party_record(0x12345678, 21, 0, attack=20, met_location_id=0x11, met_level=3)
    # This fixture represents an existing mid-game party, after the starter.
    run.starter_observation_complete = True

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
        route_id = next(
            item.location_id for item in PLATINUM_NUZLOCKE_PROFILE.locations
            if item.name == "Route 202"
        )
        initial_encounter = run.encounters[route_id]
        assert initial_encounter.status == "CAUGHT"
        assert initial_encounter.species == "Spearow"
        assert initial_encounter.level == 3

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
        assert run.encounters[route_id] == initial_encounter
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
