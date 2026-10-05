"""Offline EV yields for the first 493 Pokémon species."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pokemon_ev_tracker.core.ev_training import EVYield

EV_STAT_LABELS = (
    ("hp", "HP"),
    ("attack", "Attack"),
    ("defense", "Defense"),
    ("special_attack", "Sp. Atk"),
    ("special_defense", "Sp. Def"),
    ("speed", "Speed"),
)


@lru_cache(maxsize=1)
def _load_ev_yields() -> dict[str, dict[str, int]]:
    path = Path(__file__).resolve().parent / "data" / "pokemon_ev_yields.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or len(data) != 493:
        raise ValueError("Platinum EV-yield data must contain National Dex IDs 1-493.")
    return data


def get_ev_yield(species_id: int) -> dict[str, int] | None:
    """Return the species' base yield by stat, or None when it is unknown."""
    try:
        values = _load_ev_yields().get(str(int(species_id)))
    except (TypeError, ValueError):
        return None
    if values is None:
        return None
    return {stat: int(values[stat]) for stat, _label in EV_STAT_LABELS}


def get_training_ev_yield(species_id: int) -> EVYield | None:
    """Adapt the existing Platinum catalog to the game-neutral training engine."""
    values = get_ev_yield(species_id)
    return EVYield(**values) if values is not None else None


def format_ev_yield(species_id: int) -> str:
    values = get_ev_yield(species_id)
    if values is None:
        return "EV yield unavailable"
    parts = [
        f"+{values[stat]} {label}"
        for stat, label in EV_STAT_LABELS
        if values[stat]
    ]
    return "EV yield: " + (", ".join(parts) if parts else "none")


def format_ev_yield_summary(species_id: int) -> str:
    """Format base battle yield compactly for the opponent display."""
    values = get_ev_yield(species_id)
    if values is None:
        return "EV yield unavailable"
    parts = [
        f"+{values[stat]} {label}"
        for stat, label in EV_STAT_LABELS
        if values[stat]
    ]
    if not parts:
        return "No base EV yield"
    suffix = " EV" if len(parts) == 1 else ""
    return " · ".join(parts) + suffix
