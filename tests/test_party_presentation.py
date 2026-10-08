"""Characterize the legacy adapter before extracting its projection logic."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_training_integration import _party, make_window

presentation_window = make_window

from pokemon_ev_tracker.core.moves import MoveDefinition
from pokemon_ev_tracker.ui.party_presentation import (
    apply_party_metadata,
    apply_party_stats,
    refresh_move_displays,
)


def test_legacy_stat_text_and_nature_roles(presentation_window):
    window, source, _ = presentation_window()
    pokemon = replace(_party((1, 183)).pokemon[0], nature_increased_stat="attack",
                      nature_decreased_stat="speed")
    source.party = replace(source.party, pokemon=(pokemon,), party_count=1)
    window._refresh_tracker_party(True, source.party)
    card = window.tracker_party_cards[1]
    assert {stat: label.text() for stat, label in card["stat_values"].items()} == {
        "hp": "53", "attack": "27", "defense": "30", "special_attack": "18",
        "special_defense": "29", "speed": "22",
    }
    assert [label.text() for label in card["iv_values"].values()] == ["0", "31", "0", "0", "0", "0"]
    assert card["stat_names"]["attack"].property("natureRole") == "up"
    assert card["stat_values"]["speed"].property("natureRole") == "down"
    assert card["stat_names"]["hp"].property("natureRole") == "neutral"
    assert card["checksum"].text() == "✓ RAM data valid"
    assert card["checksum"].property("ramState") == "valid"
    changed = replace(pokemon, checksum_valid=False, current_stats=None)
    window._refresh_tracker_party(True, replace(source.party, pokemon=(changed,)))
    assert all(label.text() == "--" for label in card["stat_values"].values())
    assert all(label.text() == "--" for label in card["iv_values"].values())
    assert card["checksum"].text() == "Warning: RAM checksum invalid"
    assert card["checksum"].property("ramState") == "invalid"


@pytest.mark.parametrize("changes, expected", [
    ({"sample_stale": True, "battle_stats_stale": True}, "Showing last valid record; level/HP may be stale"),
    ({"sample_stale": True}, "Warning: showing last valid RAM sample"),
    ({"battle_stats_stale": True, "level": None}, "Warning: invalid level/HP/stats withheld"),
    ({"battle_stats_stale": True}, "Warning: keeping last sane level/HP/stats"),
], ids=["both-stale", "sample-stale", "stats-withheld", "stats-retained"])
def test_legacy_stale_sample_messages_remain_distinct(presentation_window, changes, expected):
    window, source, _ = presentation_window()
    pokemon = replace(source.party.pokemon[0], **changes)
    window._refresh_tracker_party(True, replace(source.party, pokemon=(pokemon,), party_count=1))
    card = window.tracker_party_cards[1]
    assert card["checksum"].text() == expected
    assert card["checksum"].property("ramState") == "warning"


def test_metadata_and_moves_refresh_without_replacing_widgets(presentation_window):
    window, source, _ = presentation_window()
    pokemon = replace(source.party.pokemon[0], friendship=220, nature_name="Naive",
                      ability_name=None, ability_id=999, moves=("Tackle", "Growl"))
    card = window.tracker_party_cards[pokemon.slot]
    original = card["friendship"]
    window._refresh_tracker_stats_metadata(card, pokemon)
    window._refresh_tracker_moves(card, pokemon)
    assert card["nature"].text() == "Nature: Naive"
    assert card["ability"].text() == "Ability: Unknown #999"
    assert card["friendship"].text().startswith("Friendship: 220 / 255")
    assert card["moves_list"].text() == "Tackle\nGrowl"
    window.compact_mode = True
    window._refresh_tracker_stats_metadata(card, pokemon)
    assert card["nature"].text() == "Naive • Unknown #999"
    assert card["friendship"].text() == "Friendship 220"
    assert card["friendship"] is original
    invalid = replace(pokemon, checksum_valid=False)
    window._refresh_tracker_stats_metadata(card, invalid)
    window._refresh_tracker_moves(card, invalid)
    assert card["nature"].text() == "Nature: -- • Ability: --"
    assert card["friendship"].text() == "Friendship: -- / 255"
    assert card["moves_list"].text() == "Unavailable (checksum invalid)"


def test_equal_projection_skips_apply_but_changes_and_compact_mode_refresh(presentation_window):
    window, source, _ = presentation_window()
    pokemon = source.party.pokemon[0]
    card = window.tracker_party_cards[pokemon.slot]
    card.pop("stats_presentation", None)
    card.pop("metadata_presentation", None)
    assert apply_party_stats(card, pokemon)
    assert not apply_party_stats(card, replace(pokemon))
    assert apply_party_stats(card, replace(pokemon, battle_stats_stale=True))
    assert apply_party_metadata(card, pokemon, compact=False)
    assert not apply_party_metadata(card, replace(pokemon), compact=False)
    assert apply_party_metadata(card, pokemon, compact=True)
    assert apply_party_metadata(card, replace(pokemon, friendship=220), compact=True)


def test_move_projection_keeps_slots_and_invalidates_every_displayed_input():
    calls = []

    def catalog(move_id):
        calls.append(move_id)
        return (MoveDefinition(move_id, "Tackle", "Normal", "Physical", 35, 95, 35, 0)
                if move_id == 33 else None)

    decoded = SimpleNamespace(move_ids=(33, 0, 65535, 33),
                              move_current_pps=(12, 0, 1, 7), move_pp_ups=(0, 0, 0, 2))
    pokemon = SimpleNamespace(stable_id="one", checksum_valid=True, decoded=decoded)
    card = {}
    refresh_move_displays(card, pokemon, catalog)
    original = card["move_displays"]
    assert original[1] is None
    assert original[2].name == "Unknown Move"
    assert original[0].max_pp == 35 and original[3].max_pp == 49
    assert original[3].current_pp == 7
    calls.clear()
    refresh_move_displays(card, pokemon, catalog)
    assert card["move_displays"] is original and calls == []
    for attribute, value in (("move_current_pps", (11, 0, 1, 7)),
                             ("move_pp_ups", (1, 0, 0, 2)),
                             ("move_ids", (33, 0, 0, 33))):
        setattr(decoded, attribute, value)
        refresh_move_displays(card, pokemon, catalog)
        assert calls
        calls.clear()
    pokemon.stable_id = "two"
    refresh_move_displays(card, pokemon, catalog)
    assert calls
    calls.clear()
    refresh_move_displays(card, pokemon, lambda move_id: catalog(move_id))
    assert calls
    pokemon.checksum_valid = False
    refresh_move_displays(card, pokemon, catalog)
    assert card["move_displays"] == () and "move_projection_state" not in card
    pokemon.checksum_valid = True
    refresh_move_displays(card, pokemon, catalog)
    assert card["move_displays"][0].current_pp == 11
    refresh_move_displays(card, None, catalog)
    assert card["move_displays"] == () and "move_projection_state" not in card
    assert len(card) == 1


def test_short_move_arrays_and_absent_catalog_still_withhold_missing_fields():
    pokemon = SimpleNamespace(checksum_valid=True, stable_id="one", decoded=SimpleNamespace(
        move_ids=(33, 0, 45, 1), move_current_pps=(5,), move_pp_ups=(0,)))
    card = {}
    refresh_move_displays(card, pokemon, None)
    assert len(card["move_displays"]) == 4
    assert card["move_displays"][0].name == "Unknown Move"
    assert card["move_displays"][0].max_pp is None
    assert card["move_displays"][1:] == (None, None, None)
