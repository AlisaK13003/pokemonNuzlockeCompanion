"""Generation IV move catalog, live slots, and pure PP arithmetic."""

import pytest

from pokemon_ev_tracker.core.moves import PokemonMoveState, calculate_max_pp, resolve_move
from pokemon_ev_tracker.games.platinum.profile import PLATINUM_PROFILE
from pokemon_ev_tracker.pokemon.gen4.moves import get_gen4_move_definition, load_gen4_moves


@pytest.mark.parametrize(
    ("base", "ups", "expected"),
    [(10, 0, 10), (10, 1, 12), (10, 2, 14), (10, 3, 16),
     (5, 1, 6), (5, 2, 7), (5, 3, 8), (35, 1, 42), (35, 3, 56),
     (15, 1, 18), (15, 2, 21), (15, 3, 24), (15, 255, 24)],
)
def test_max_pp_uses_gen4_integer_arithmetic(base, ups, expected):
    assert calculate_max_pp(base, ups) == expected


def test_catalog_is_complete_and_gen4_specific():
    assert set(load_gen4_moves()) == set(range(1, 468))
    assert PLATINUM_PROFILE.move_definition is get_gen4_move_definition
    tackle = get_gen4_move_definition(33)
    assert (tackle.name, tackle.type_name, tackle.category, tackle.power, tackle.accuracy) == (
        "Tackle", "Normal", "Physical", 35, 95,
    )
    surf = get_gen4_move_definition(57)
    assert (surf.type_name, surf.category, surf.power, surf.base_pp) == (
        "Water", "Special", 95, 15,
    )
    defense_curl = get_gen4_move_definition(111)
    assert (defense_curl.category, defense_curl.power, defense_curl.accuracy) == (
        "Status", None, None,
    )
    assert get_gen4_move_definition(98).priority == 1
    assert get_gen4_move_definition(182).priority == 3
    assert get_gen4_move_definition(245).priority == 1
    assert get_gen4_move_definition(174).type_name == "???"
    assert get_gen4_move_definition(0) is None
    assert get_gen4_move_definition(999) is None


def test_resolved_slots_keep_live_state_and_unknown_ids_safe():
    move = resolve_move(PokemonMoveState(401, 8, 2), get_gen4_move_definition)
    assert (move.name, move.type_name, move.category) == ("Aqua Tail", "Water", "Physical")
    assert (move.power, move.accuracy, move.current_pp, move.max_pp, move.priority) == (
        90, 90, 8, 14, 0,
    )
    assert resolve_move(PokemonMoveState(0, 0, 0), get_gen4_move_definition) is None
    unknown = resolve_move(PokemonMoveState(999, 3, 1), get_gen4_move_definition)
    assert (unknown.name, unknown.move_id, unknown.current_pp, unknown.max_pp) == (
        "Unknown Move", 999, 3, None,
    )
