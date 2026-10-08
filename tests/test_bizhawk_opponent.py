from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.core.ev_targets import EVTargetStore
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.data_sources.base import DataSourceSnapshot
from pokemon_ev_tracker.data_sources.bizhawk import BizHawkRamDataSource
from pokemon_ev_tracker.games.platinum.battle import (
    BattleValidationState,
    active_enemy_battlers,
    decode_battle_battlers,
)
from pokemon_ev_tracker.games.platinum.memory import PLATINUM_US
from pokemon_ev_tracker.games.platinum.profile import PLATINUM_PROFILE
from pokemon_ev_tracker.games.registry import default_game_provider
from pokemon_ev_tracker.ui.main_window import MainWindow
from pokemon_ev_tracker.ui.opponent_panel import CurrentOpponentPanel
from pokemon_ev_tracker.ui.sprite_loader import SpriteAsset


def _battler_record(
    species_id: int = 0,
    *,
    level: int = 0,
    current_hp: int = 0,
    max_hp: int = 0,
    stat: int = 0,
) -> bytes:
    record = bytearray(PLATINUM_US.battle_battler_record_size)
    record[0x00:0x02] = species_id.to_bytes(2, "little")
    for offset in (0x02, 0x04, 0x06, 0x08, 0x0A):
        record[offset : offset + 2] = stat.to_bytes(2, "little")
    record[0x27] = 26
    record[0x34] = level
    record[0x36:0x4C] = b"\x00" * 0x16
    record[0x4C:0x4E] = current_hp.to_bytes(2, "little")
    record[0x4E:0x50] = max_hp.to_bytes(2, "little")
    record[0x68:0x6C] = (0x12345678).to_bytes(4, "little")
    record[0x78:0x7A] = (215).to_bytes(2, "little")
    return bytes(record)


def _decode(records: dict[int, bytes]):
    return decode_battle_battlers(
        records,
        "0x022711C8",
        species_catalog=None,
    )


def test_candidate_addresses_and_record_stride_match_research_offsets() -> None:
    battlers = _decode({})

    assert PLATINUM_US.battle_battler_candidate_offsets == (
        0x54598,
        0x54658,
        0x54718,
        0x547D8,
    )
    assert all(
        right - left == PLATINUM_US.battle_battler_stride
        for left, right in zip(
            PLATINUM_US.battle_battler_candidate_offsets,
            PLATINUM_US.battle_battler_candidate_offsets[1:],
        )
    )
    assert [item.address for item in battlers] == [
        0x022711C8 + offset for offset in PLATINUM_US.battle_battler_candidate_offsets
    ]


def test_decoder_extracts_species_level_and_hp_from_candidate_record() -> None:
    battlers = _decode(
        {1: _battler_record(92, level=14, current_hp=25, max_hp=30, stat=20)}
    )

    enemy = battlers[1]
    assert enemy.species_id == 92
    assert enemy.species_name == "Gastly"
    assert enemy.level == 14
    assert (enemy.current_hp, enemy.max_hp) == (25, 30)
    assert enemy.validation_state is BattleValidationState.ACTIVE
    assert len(enemy.raw_hex.split()) == PLATINUM_US.battle_battler_record_size


def test_active_validation_uses_species_and_level_not_hp_or_stats() -> None:
    battlers = _decode(
        {
            0: _battler_record(56, level=14, current_hp=65535, max_hp=0, stat=0),
            1: _battler_record(116, level=15, current_hp=65535, max_hp=0, stat=0),
        }
    )
    horsea = battlers[1]

    assert horsea.species_name == "Horsea"
    assert horsea.level == 15
    assert horsea.current_hp == 65535
    assert horsea.max_hp == 0
    assert horsea.validation_state is BattleValidationState.ACTIVE
    assert horsea.hp_region_raw_hex
    assert [enemy.species_name for enemy in active_enemy_battlers(battlers)] == ["Horsea"]


def test_invalid_record_is_inactive_with_validation_reasons() -> None:
    invalid = _battler_record(14787, level=195, current_hp=900, max_hp=0, stat=0)

    battler = _decode({1: invalid})[1]

    assert battler.validation_state is BattleValidationState.INACTIVE
    assert "Species ID" in battler.validation_reason
    assert "Level" in battler.validation_reason
    assert battler.raw_hex


def test_all_zero_record_is_inactive() -> None:
    battler = _decode({2: bytes(PLATINUM_US.battle_battler_record_size)})[2]

    assert battler.validation_state is BattleValidationState.INACTIVE
    assert "all zeroes" in battler.validation_reason


def test_fainted_battler_can_remain_active() -> None:
    battler = _decode(
        {1: _battler_record(92, level=14, current_hp=0, max_hp=30, stat=20)}
    )[1]

    assert battler.active
    assert battler.current_hp == 0


def test_single_battle_selects_only_enemy_battler_one() -> None:
    battlers = _decode(
        {
            0: _battler_record(183, level=14, current_hp=36, max_hp=53, stat=35),
            1: _battler_record(179, level=14, current_hp=22, max_hp=33, stat=20),
        }
    )

    enemies = active_enemy_battlers(battlers)

    assert [(enemy.battler_index, enemy.species_name) for enemy in enemies] == [(1, "Mareep")]


def test_double_battle_selects_enemy_battlers_one_and_three() -> None:
    battlers = _decode(
        {
            0: _battler_record(183, level=17, current_hp=36, max_hp=53, stat=35),
            1: _battler_record(92, level=14, current_hp=25, max_hp=30, stat=20),
            2: _battler_record(183, level=17, current_hp=36, max_hp=53, stat=35),
            3: _battler_record(41, level=13, current_hp=21, max_hp=25, stat=18),
        }
    )

    enemies = active_enemy_battlers(battlers)

    assert [(enemy.battler_index, enemy.species_name) for enemy in enemies] == [
        (1, "Gastly"),
        (3, "Zubat"),
    ]


def test_trainer_replacement_replaces_species_in_same_battler_slot() -> None:
    source = BizHawkRamDataSource()
    first_payload = SimpleNamespace(
        payload={"pointer_value": "0x022711C8", "battle_battler_1_raw_hex": _battler_record(
            92, level=14, current_hp=25, max_hp=30, stat=20
        ).hex()}
    )
    replacement_payload = SimpleNamespace(
        payload={"pointer_value": "0x022711C8", "battle_battler_1_raw_hex": _battler_record(
            41, level=15, current_hp=29, max_hp=32, stat=21
        ).hex()}
    )

    initial = source._decode_battle_battlers(first_payload)[1]
    replacement = source._decode_battle_battlers(replacement_payload)[1]

    assert initial.battler_index == replacement.battler_index == 1
    assert (initial.species_name, initial.level) == ("Gastly", 14)
    assert (replacement.species_name, replacement.level) == ("Zubat", 15)
    assert (replacement.current_hp, replacement.max_hp) == (29, 32)


def test_unpaired_enemy_record_is_not_displayed_and_battle_exit_clears() -> None:
    source = BizHawkRamDataSource()
    paired_payload = SimpleNamespace(
        payload={
            "pointer_value": "0x022711C8",
            "battle_battler_0_raw_hex": _battler_record(
                56, level=14, current_hp=65535, max_hp=0, stat=0
            ).hex(),
            "battle_battler_1_raw_hex": _battler_record(
                116, level=15, current_hp=65535, max_hp=0, stat=0
            ).hex(),
        }
    )
    enemy_only_payload = SimpleNamespace(
        payload={
            "pointer_value": "0x022711C8",
            "battle_battler_1_raw_hex": _battler_record(
                116, level=15, current_hp=65535, max_hp=0, stat=0
            ).hex(),
        }
    )
    empty_payload = SimpleNamespace(payload={"pointer_value": "0x022711C8"})

    assert [
        pokemon.species_name
        for pokemon in active_enemy_battlers(source._decode_battle_battlers(paired_payload))
    ] == ["Horsea"]
    assert active_enemy_battlers(source._decode_battle_battlers(enemy_only_payload)) == ()
    assert active_enemy_battlers(source._decode_battle_battlers(empty_payload)) == ()


def test_same_payload_reuses_decoded_battler_snapshot() -> None:
    source = BizHawkRamDataSource()
    payload = SimpleNamespace(payload={})

    first = source._decode_battle_battlers(payload)
    second = source._decode_battle_battlers(payload)

    assert first is second


@pytest.mark.parametrize(
    ("payload_age", "expected_species"),
    [(0.0, ("Nidorino", "Machop")), (10.0, ())],
    ids=["fresh-file-payload", "stale-payload-clears"],
)
def test_snapshot_publishes_canonical_opponents_independent_of_heartbeat(
    payload_age, expected_species, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger="pokemon_ev_tracker.data_sources.bizhawk")
    caplog.set_level(logging.DEBUG, logger="pokemon_ev_tracker.data_sources.bizhawk")
    payload = SimpleNamespace(
        received_at=time.monotonic() - payload_age,
        payload={
            "pointer_value": "0x022711C8",
            "battle_battler_0_raw_hex": _battler_record(56, level=18, stat=25).hex(),
            "battle_battler_1_raw_hex": _battler_record(33, level=18, stat=25).hex(),
            "battle_battler_2_raw_hex": _battler_record(56, level=13, stat=20).hex(),
            "battle_battler_3_raw_hex": _battler_record(66, level=13, stat=20).hex(),
        },
    )

    class DisconnectedServer:
        host = "127.0.0.1"
        port = 46387
        fallback_file = Path("fallback.jsonl")
        stale_after_seconds = 5.0

        def latest_heartbeat(self):
            return None

        def latest_party_payload(self):
            return payload

        def is_connected(self):
            return False

    snapshot = BizHawkRamDataSource(server=DisconnectedServer()).snapshot()
    enemies = snapshot.details["active_enemy_battlers"]

    assert not snapshot.connected
    assert tuple(enemy.species for enemy in enemies) == expected_species
    expected_count = len(expected_species)
    assert f"active_enemy_battlers produced: {expected_count}" in caplog.text
    if expected_species:
        assert enemies == active_enemy_battlers(snapshot.details["battle_battlers"])


def test_ram_debug_shows_all_candidate_records_and_current_enemy() -> None:
    app = QApplication.instance() or QApplication([])
    battlers = _decode(
        {
            0: _battler_record(56, level=14, current_hp=37, max_hp=0, stat=25),
            1: _battler_record(92, level=14, current_hp=25, max_hp=30, stat=20),
        }
    )

    class DebugHarness:
        _refresh_ram_party_debug = MainWindow._refresh_ram_party_debug
        _friendship_walk_transport_debug_lines = (
            MainWindow._friendship_walk_transport_debug_lines
        )

        def __init__(self):
            self.provider = default_game_provider()
            self.ram_party_summary_label = QLabel()
            self.debug_text = ""

        def _set_ram_party_debug_text(self, text):
            self.debug_text = text

    harness = DebugHarness()
    party_state = SimpleNamespace(
        party_count=0,
        party_count_valid=True,
        error=None,
        candidate_count=0,
        pokemon=(),
    )
    payload = SimpleNamespace(
        source="file",
        payload={"pointer_value": "0x022711C8", "frame": 123, "domain": "Main RAM"},
    )

    harness._refresh_ram_party_debug(
        party_state, payload, battlers, active_enemy_battlers(battlers)
    )

    assert "Battler 0 - Player 1" in harness.debug_text
    assert "Battler 1 - Enemy 1" in harness.debug_text
    assert "Battler 2 - Player 2 / Partner" in harness.debug_text
    assert "Battler 3 - Enemy 2" in harness.debug_text
    assert "Raw record:" in harness.debug_text
    assert "Gastly, Lv. 14, HP 25 / 30" in harness.debug_text
    assert "Base EV Yield: EV yield: +1 Sp. Atk" in harness.debug_text
    assert app is not None


@pytest.mark.parametrize("compact", [False, True], ids=["normal", "compact"])
def test_main_window_binds_debug_opponents_to_visible_tracker_cards(
    monkeypatch, tmp_path, compact, caplog
) -> None:
    app = QApplication.instance() or QApplication([])
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    monkeypatch.setattr(
        "pokemon_ev_tracker.ui.opponent_panel.resolve_sprite_asset",
        lambda species_id, *, companion=False: SpriteAsset("static", Path(f"{species_id}.png")),
    )
    pixmap = QPixmap(8, 8)
    pixmap.fill()
    monkeypatch.setattr(
        "pokemon_ev_tracker.ui.opponent_panel.get_static_sprite",
        lambda _species_id, _size: pixmap,
    )
    panel_received = []
    original_set_opponents = CurrentOpponentPanel.set_opponents

    def record_panel_update(panel, opponents):
        panel_received.append(opponents)
        original_set_opponents(panel, opponents)

    monkeypatch.setattr(CurrentOpponentPanel, "set_opponents", record_panel_update)

    class SnapshotSource:
        profile = PLATINUM_PROFILE

        def __init__(self, snapshot):
            self.current_snapshot = snapshot

        def start(self):
            pass

        def stop(self):
            pass

        def snapshot(self):
            return self.current_snapshot

    party_state = SimpleNamespace(
        party_count=0,
        party_count_valid=True,
        error=None,
        candidate_count=0,
        pokemon=(),
    )
    party_payload = SimpleNamespace(
        source="file",
        payload={"pointer_value": "0x022711C8", "frame": 123, "domain": "Main RAM"},
    )

    def snapshot(records):
        battlers = _decode(records)
        enemies = active_enemy_battlers(battlers)
        return DataSourceSnapshot(
            backend_name="BizHawk RAM",
            connected=True,
            details={
                "heartbeat": None,
                "party_state": party_state,
                "display_party_state": party_state,
                "party_payload": party_payload,
                "battle_battlers": battlers,
                "active_enemy_battlers": enemies,
            },
        )

    empty_records = {}
    source = SnapshotSource(snapshot(empty_records))
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "nuzlocke_runs.json"),
    )
    window.refresh_timer.stop()
    if compact:
        window.set_compact_mode(True)
    window.show()

    double_battle = snapshot(
        {
            0: _battler_record(56, level=18, current_hp=40, stat=25),
            1: _battler_record(66, level=14, current_hp=44, stat=25),
            2: _battler_record(56, level=13, current_hp=35, stat=20),
            3: _battler_record(13, level=13, current_hp=32, stat=20),
        }
    )
    source.current_snapshot = double_battle
    window._refresh_ram_backend_debug()
    app.processEvents()

    panel = window.current_opponent_panel
    assert double_battle.connected
    assert "Enemy 1: Machop, Lv. 14" in window.ram_party_details.toPlainText()
    assert "Enemy 2: Weedle, Lv. 13" in window.ram_party_details.toPlainText()
    assert any(len(received) == 2 for received in panel_received)
    assert any(
        received is double_battle.details["active_enemy_battlers"]
        for received in panel_received
    )
    assert "MainWindow opponent update: 2 - Machop Lv14, Weedle Lv13" in caplog.text
    assert "OpponentPanel received: 2; cards rendered: 2; layout count after render: 3" in caplog.text
    assert panel.isVisible()
    assert [card.name.text() for card in panel.cards] == [
        "Machop • Lv. 14",
        "Weedle • Lv. 13",
    ]
    assert [card.ev_yield.text() for card in panel.cards] == [
        "+1 Attack EV",
        "+1 Speed EV",
    ]
    assert all(not card.isHidden() for card in panel.cards)
    assert all(panel.opponents_layout.indexOf(card) >= 0 for card in panel.cards)
    assert all(not card.sprite.pixmap().isNull() for card in panel.cards)
    assert panel.height() > 0
    assert all(card.height() > 0 for card in panel.cards)
    assert "content visible: True" in caplog.text

    source.current_snapshot = snapshot(
        {
            0: _battler_record(56, level=18, current_hp=40, stat=25),
            1: _battler_record(225, level=14, current_hp=35, stat=20),
        }
    )
    window._refresh_ram_backend_debug()
    app.processEvents()
    assert panel.cards[0].name.text() == "Delibird • Lv. 14"
    assert panel.cards[0].ev_yield.text() == "+1 Speed EV"
    assert not panel.cards[0].isHidden()
    assert panel.cards[1].isHidden()

    source.current_snapshot = snapshot(empty_records)
    window._refresh_ram_backend_debug()
    app.processEvents()
    assert panel.empty_label.text() == "Not currently battling"
    assert not panel.empty_label.isHidden()
    assert all(card.isHidden() for card in panel.cards)
    window.close()
    assert app is not None
