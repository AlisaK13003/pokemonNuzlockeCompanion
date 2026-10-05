"""Game-neutral move definitions and live-slot presentation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MoveDefinition:
    move_id: int
    name: str
    type_name: str
    category: str
    power: int | None
    accuracy: int | None
    base_pp: int
    priority: int


@dataclass(frozen=True)
class PokemonMoveState:
    move_id: int
    current_pp: int
    pp_ups: int


@dataclass(frozen=True)
class MoveDisplayData:
    move_id: int
    name: str
    type_name: str | None
    category: str | None
    power: int | None
    accuracy: int | None
    current_pp: int
    max_pp: int | None
    priority: int | None
    pp_ups: int


def calculate_max_pp(base_pp: int, pp_ups: int) -> int:
    """Use the Gen IV integer calculation, clamping corrupt PP Up bytes."""
    if base_pp < 0 or pp_ups < 0:
        raise ValueError("Base PP and PP Ups must be nonnegative")
    return base_pp + (base_pp * min(pp_ups, 3) * 20) // 100


def resolve_move(
    state: PokemonMoveState,
    definition_for_id: Callable[[int], MoveDefinition | None],
) -> MoveDisplayData | None:
    if state.move_id == 0:
        return None
    definition = definition_for_id(state.move_id)
    return MoveDisplayData(
        move_id=state.move_id,
        name=definition.name if definition else "Unknown Move",
        type_name=definition.type_name if definition else None,
        category=definition.category if definition else None,
        power=definition.power if definition else None,
        accuracy=definition.accuracy if definition else None,
        current_pp=state.current_pp,
        max_pp=calculate_max_pp(definition.base_pp, state.pp_ups) if definition else None,
        priority=definition.priority if definition else None,
        pp_ups=state.pp_ups,
    )
