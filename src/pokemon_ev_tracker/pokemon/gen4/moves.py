"""Offline Generation IV move definitions shared by Gen IV game profiles."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files

from pokemon_ev_tracker.core.moves import MoveDefinition


@lru_cache(maxsize=1)
def load_gen4_moves() -> dict[int, MoveDefinition]:
    raw = json.loads(files(__package__).joinpath("data/moves.json").read_text(encoding="utf-8"))
    moves = {int(move_id): MoveDefinition(move_id=int(move_id), **values)
             for move_id, values in raw.items()}
    if set(moves) != set(range(1, 468)):
        raise ValueError("Generation IV move catalog must contain IDs 1–467")
    return moves


def get_gen4_move_definition(move_id: int) -> MoveDefinition | None:
    return load_gen4_moves().get(move_id)
