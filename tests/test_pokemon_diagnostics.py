"""Characterize existing party, boxed and anchor diagnostic text before extraction."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from test_gen4_party_ram import _party_record
from test_party_stats_view import _party_payload
from test_pc_storage_discovery import _boxed_record
from test_training_integration import make_window

from pokemon_ev_tracker.games.platinum.decoder import decode_party
from pokemon_ev_tracker.games.platinum.pc_storage import BoxedPokemon
from pokemon_ev_tracker.games.registry import default_game_provider
from pokemon_ev_tracker.pokemon.gen4.structure import decode_box_pokemon
from pokemon_ev_tracker.ui.main_window import MainWindow
from pokemon_ev_tracker.ui.party_presentation import refresh_move_displays
from pokemon_ev_tracker.ui.pc_inspection_presentation import format_pc_pokemon_inspection
from pokemon_ev_tracker.ui.pokemon_diagnostics import (
    PartyPokemonDiagnosticObservation,
    format_boxed_change_diagnostics,
    format_boxed_record_diagnostics,
    format_party_pokemon_diagnostics,
)

diagnostic_window = make_window


class TextSink:
    def setText(self, text):
        self.text = text


class DiagnosticHarness:
    _refresh_ram_party_debug = MainWindow._refresh_ram_party_debug
    _friendship_walk_transport_debug_lines = MainWindow._friendship_walk_transport_debug_lines
    _pc_storage_debug_text = MainWindow._pc_storage_debug_text
    _pc_pokemon_inspection_lines = MainWindow._pc_pokemon_inspection_lines

    def __init__(self):
        self.provider = default_game_provider()
        self.ram_party_summary_label = TextSink()
        self._pc_resolver_lifecycle = "UNRESOLVED"
        self.acquisitions = SimpleNamespace(
            pc_baseline_count=0, pc_addition_count=0, pc_emitted_count=0, pc_tracking=False)
        self._pc_monitoring_ready = False
        self.observed = False
        self.nuzlocke_view = SimpleNamespace(store=SimpleNamespace(
            has_observed_pokemon=lambda *_: self.observed))

    def _set_ram_party_debug_text(self, text):
        self.text = text


def party_cases():
    raw = _party_record(0x12345678, 387, (1, 2, 3, 4, 5, 6), nickname="Leaf",
                        held_item_id=213, friendship=142, ivs=(31, 20, 12, 9, 0, 29),
                        moves=(33, 0, 45, 999), move_pps=(12, 0, 7, 2), move_pp_ups=(1, 0, 2, 0))
    good = decode_party(_party_payload((raw,)), base_address=0x0227E20C)
    invalid = bytearray(raw)
    invalid[8] ^= 1
    bad = decode_party(_party_payload((bytes(invalid),)), base_address=0x0227E20C)
    mon = good.pokemon[0]
    unknown = replace(mon, species_id=999, species="Unknown #999", ability_id=999,
                      ability_name=None, held_item_name=None, met_location_name=None,
                      nature_increased_stat=None, nature_decreased_stat=None, level=None,
                      current_hp=None, max_hp=None)
    no_address = replace(mon, decoded=replace(mon.decoded, diagnostics=replace(
        mon.decoded.diagnostics, address=None), nickname_diagnostics=replace(
        mon.decoded.nickname_diagnostics, absolute_address=None, terminator_unit_index=None)))
    invalid_tail = decode_party(_party_payload((_party_record(
        1, 387, (0, 0, 0, 0, 0, 0), current_hp=65535),)))
    return {"valid": good, "invalid-checksum": bad,
            "unknown-metadata": replace(good, pokemon=(unknown,)),
            "missing-address-nickname-terminator": replace(good, pokemon=(no_address,)),
            "stale-display": replace(good, pokemon=(replace(mon, sample_stale=True, battle_stats_stale=True),),
                                     live_read_warning="showing last good sample"),
            "invalid-battle-tail": invalid_tail,
            "empty": decode_party(_party_payload(())), "missing": None,
            "invalid-count": decode_party((7).to_bytes(4, "little"))}


def box_cases():
    raw = _boxed_record(298, nickname="Tiny")
    decoded = decode_box_pokemon(raw, 0x02280004)
    valid = BoxedPokemon(2, 3, "Azurill", decoded, raw.hex())
    invalid = bytearray(raw)
    invalid[8] ^= 1
    return {"valid": valid, "invalid-checksum": replace(valid, decoded=decode_box_pokemon(bytes(invalid))),
            "unknown-metadata": replace(valid, species_name="Unknown #999",
                decoded=replace(decoded, species_id=999, nickname="", met_location_id=65535, met_level=None)),
            "missing-raw": replace(valid, raw_hex="")}


def inspection_cases():
    record = {"address": "0x02280004", "species_id": "298", "nickname": "Tiny",
              "pid": "0x12345678", "stable_id": "pid:12345678:ot:0000:0000",
              "checksum_valid": True, "party_overlap": False, "delta_hex": "+0x88"}
    return {"empty": {}, "valid": {
        "pc_storage_inspection_anchor_address": "0x02280004",
        "pc_storage_inspection_anchor_record": record,
        "pc_storage_inspection_nearby_records": [record, None, {"species_id": "999", "checksum_valid": False}],
        "pc_storage_inspection_spacing_summary": {"adjacent_record_pairs": 1,
            "pairs_multiple_of_136": 1, "repeated_neighbor_spacings": [{"delta_bytes": 136, "occurrences": 1}]},
        "pc_storage_discovery_identity_matches": [{**record, "surrounding_valid_pokemon": [record],
            "pointer_references": [{"reference_address": "0x02000000", "target_address": "0x02280004"}]}],
        "pc_storage_inspection_adjacent_start": "0x02280000",
        "pc_storage_inspection_adjacent_hex": "00112233", "pc_storage_inspection_adjacent_u32": [{}],
        "pc_storage_inspection_pointer_references": [{"target": ["anchor"]}],
        "pc_storage_movement_comparison": {"status": "baseline captured", "baseline_addresses": ["0x02280004"]},
    }, "malformed": {"pc_storage_inspection_anchor_record": {"species_id": True},
        "pc_storage_inspection_adjacent_hex": "garbage",
        "pc_storage_inspection_region_hex": "garbage", "pc_storage_inspection_region_start": "0x02280000"},
    "movement": {"pc_storage_movement_comparison": {"status": "moved", "stable_id": "pid:1",
        "baseline_addresses": ["0x02280004"], "current_addresses": ["0x02280114"],
        "movement_candidates": [{}], "changed_ram": {"changed_bytes": 4, "focus_changed_regions": [{}]}}}}


def fixed_item_assets():
    return (patch("pokemon_ev_tracker.ui.main_window.item_sprite_slug", return_value="shell-bell"),
            patch("pokemon_ev_tracker.ui.main_window.item_sprite_path", return_value="fixture/shell-bell.png"),
            patch("pokemon_ev_tracker.ui.main_window.item_sprite_exists", return_value=True))


def characterize():
    result = {"party": {}, "box": {}, "inspection": {}}
    harness = DiagnosticHarness()
    slug, path, exists = fixed_item_assets()
    with slug, path, exists:
        for name, party in party_cases().items():
            harness._refresh_ram_party_debug(party, None)
            result["party"][name] = {"text": harness.text, "summary": harness.ram_party_summary_label.text}
    for name, mon in box_cases().items():
        state = SimpleNamespace(records=(mon,), available=True, records_address=0x02280004,
            valid_pokemon=(mon,) if mon.checksum_valid else (), empty_records=539, checksum_failures=not mon.checksum_valid)
        result["box"][name] = harness._pc_storage_debug_text(state, None, False, changes=(mon,))
    result["box"]["empty"] = harness._pc_storage_debug_text(
        SimpleNamespace(records=(), available=True, valid_pokemon=(), empty_records=540, checksum_failures=0), None, True)
    result["box"]["missing"] = harness._pc_storage_debug_text(None, None, False)
    for name, payload in inspection_cases().items():
        result["inspection"][name] = harness._pc_pokemon_inspection_lines(payload)
    return result


@pytest.mark.parametrize("section,name", [
    *(("party", name) for name in party_cases()),
    *(("box", name) for name in (*box_cases(), "empty", "missing")),
    *(("inspection", name) for name in inspection_cases()),
])
def test_existing_diagnostic_text(section, name):
    expected = json.loads((Path(__file__).parent / "fixtures/pokemon-diagnostics.json").read_text(encoding="utf-8"))
    assert characterize()[section][name] == expected[section][name]


def test_live_acquisition_observation_not_hidden_by_formatting_reuse():
    harness = DiagnosticHarness()
    run = SimpleNamespace(run_id="fixture", game="pokemon-platinum", encounters={})
    party = party_cases()["valid"]
    harness._refresh_ram_party_debug(party, None, acquisition_run=run)
    assert "Already observed in active run: false" in harness.text
    harness.observed = True
    harness._refresh_ram_party_debug(party, None, acquisition_run=run)
    assert "Already observed in active run: true" in harness.text


def test_existing_move_projection_preserves_slots_and_pp():
    pokemon = party_cases()["valid"].pokemon[0]
    card = {}
    refresh_move_displays(card, pokemon, default_game_provider().move_catalog)
    moves = card["move_displays"]
    assert len(moves) == 4
    assert moves[0].current_pp == 12
    assert moves[1] is None
    assert moves[2].current_pp == 7
    assert moves[3].current_pp == 2
    refresh_move_displays(card, replace(pokemon, checksum_valid=False), default_game_provider().move_catalog)
    assert card["move_displays"] == ()


def observation():
    return PartyPokemonDiagnosticObservation(
        acquisition_source="UNAVAILABLE", acquisition_confidence="LOW", suggested_id=None,
        suggested_name=None, already_observed=False, item_slug=None, item_path=None,
        item_exists=False)


def test_pure_formatters_consume_decoded_models_without_decoding_or_mutating(monkeypatch):
    party = party_cases()["valid"].pokemon[0]
    box = box_cases()["valid"]
    before_evs = dict(party.evs)
    decode = Mock(side_effect=AssertionError("presentation must not decode"))
    monkeypatch.setattr("pokemon_ev_tracker.pokemon.gen4.structure.decode_party_pokemon", decode)
    monkeypatch.setattr("pokemon_ev_tracker.pokemon.gen4.structure.decode_box_pokemon", decode)
    lines = tuple(format_party_pokemon_diagnostics(party, observation()))
    assert "PID: 0x12345678" in lines
    assert f"Stable Pokémon ID: {party.stable_id}" in lines
    assert "Held item sprite path: --" in lines
    assert "Held item sprite exists: false" in lines
    assert "EVs withheld because checksum is invalid" not in lines
    assert "  Address: --" in format_boxed_record_diagnostics(box, None)
    assert "NEW: Box 2 Slot 3" in format_boxed_change_diagnostics(box)
    assert "  Nickname: Tiny" in format_boxed_record_diagnostics(box, None)
    assert "Current stats from party tail:" not in format_boxed_record_diagnostics(box, None)
    assert dict(party.evs) == before_evs
    decode.assert_not_called()


def test_unsupported_acquisition_capabilities_do_not_call_classifier():
    harness = DiagnosticHarness()
    harness.provider.capabilities = replace(harness.provider.capabilities, nuzlocke=False)
    classifier = Mock(side_effect=AssertionError("unsupported classification"))
    harness.provider.classify_acquisition = classifier
    harness._refresh_ram_party_debug(party_cases()["valid"], None)
    assert "Acquisition classification: UNAVAILABLE (low confidence)" in harness.text
    classifier.assert_not_called()


def test_anchor_formatter_does_not_modify_observations_and_limits_dump_rows():
    payload = inspection_cases()["valid"]
    before = json.dumps(payload, sort_keys=True)
    names = default_game_provider().species_names()
    assert format_pc_pokemon_inspection(payload, names) == DiagnosticHarness()._pc_pokemon_inspection_lines(payload)
    assert json.dumps(payload, sort_keys=True) == before
    dump = format_pc_pokemon_inspection({"pc_storage_inspection_adjacent_hex": bytes(range(32)).hex(),
                                      "pc_storage_inspection_adjacent_start": "0x02280000"}, names)
    assert "    0x02280000: " + bytes(range(16)).hex(" ").upper() in dump
    assert "    0x02280010: " + bytes(range(16, 32)).hex(" ").upper() in dump


def test_diagnostic_widgets_skip_equivalent_text_and_preserve_missing_state(diagnostic_window, monkeypatch):
    window, _source, _ = diagnostic_window()
    party = party_cases()["valid"]
    write = Mock(wraps=window.ram_party_details.setPlainText)
    monkeypatch.setattr(window.ram_party_details, "setPlainText", write)
    window._refresh_ram_party_debug(party, None)
    original = window.ram_party_details.toPlainText()
    cursor = window.ram_party_details.textCursor()
    cursor.setPosition(12)
    window.ram_party_details.setTextCursor(cursor)
    window._refresh_ram_party_debug(party, None)
    assert write.call_count == 1
    assert window.ram_party_details.textCursor().position() == 12
    assert window.ram_party_details.toPlainText() == original
    window._refresh_ram_party_debug(None, None, ram_fresh=False)
    assert window.ram_party_summary_label.text() == "Party count: --"
    assert "STALE / DISCONNECTED" in window.ram_party_details.toPlainText()
    assert write.call_count == 2


def test_health_rows_preserve_stale_identity_and_invalid_hp(diagnostic_window):
    window, _source, _ = diagnostic_window()
    view = window.diagnostics_view
    mon = party_cases()["stale-display"].pokemon[0]
    view._refresh_party((mon,), fresh=False, valid=True)
    assert view.party_state_label.text() == "LAST SNAPSHOT"
    assert view.party_rows[0][2].toolTip() == mon.stable_id
    assert view.party_rows[0][3].toolTip() == "Last received snapshot"
    assert view.party_rows[0][3].text() == "HP 30 / 60"
    view._refresh_party((replace(mon, checksum_valid=False),), fresh=True, valid=False)
    assert view.party_rows[0][3].text() == "HP —"
    assert view.party_meta[0].text() == "Slot 1 · HP unavailable"
    view._refresh_party((), fresh=True, valid=True)
    assert view.party_empty.text() == "Party is empty."
    assert view.party_rows[0][0].isHidden()
