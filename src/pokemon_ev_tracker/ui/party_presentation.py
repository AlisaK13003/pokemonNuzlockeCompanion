"""Legacy party-card projections, separate from application orchestration.

The frozen values and move formatter are pure projections of decoded data.
The small apply functions own only the existing card's display fields. They do
not validate RAM, persist preferences, observe events, or manage sprite movies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pokemon_ev_tracker.core.moves import MoveDefinition, PokemonMoveState, resolve_move
from pokemon_ev_tracker.pokemon.gen4.friendship import friendship_label
from pokemon_ev_tracker.ui.theme import refresh_style

STAT_KEYS = ("hp", "attack", "defense", "special_attack", "special_defense", "speed")


@dataclass(frozen=True)
class StatDisplay:
    stat: str
    value: str
    iv: str
    nature_role: str


@dataclass(frozen=True)
class PartyStatsPresentation:
    stats: tuple[StatDisplay, ...]
    checksum_text: str
    ram_state: str


@dataclass(frozen=True)
class PartyMetadataPresentation:
    nature_text: str
    ability_text: str
    friendship_text: str
    friendship: int | None
    compact: bool


def project_party_stats(pokemon) -> PartyStatsPresentation:
    values = []
    for stat in STAT_KEYS:
        current = (getattr(pokemon.current_stats, "max_hp" if stat == "hp" else stat)
                   if pokemon.current_stats is not None else None)
        role = ("up" if stat != "hp" and stat == pokemon.nature_increased_stat else
                "down" if stat != "hp" and stat == pokemon.nature_decreased_stat else "neutral")
        values.append(StatDisplay(stat, str(current) if current is not None else "--",
                                  str(getattr(pokemon, stat + "_iv")) if pokemon.checksum_valid else "--",
                                  role))
    if pokemon.sample_stale and pokemon.battle_stats_stale:
        text = "Showing last valid record; level/HP may be stale"
    elif pokemon.sample_stale:
        text = "Warning: showing last valid RAM sample"
    elif pokemon.battle_stats_stale and pokemon.level is None:
        text = "Warning: invalid level/HP/stats withheld"
    elif pokemon.battle_stats_stale:
        text = "Warning: keeping last sane level/HP/stats"
    else:
        text = "✓ RAM data valid" if pokemon.checksum_valid else "Warning: RAM checksum invalid"
    ram_state = ("warning" if pokemon.sample_stale or pokemon.battle_stats_stale else
                 "valid" if pokemon.checksum_valid else "invalid")
    return PartyStatsPresentation(tuple(values), text, ram_state)


def project_party_metadata(pokemon, *, compact: bool) -> PartyMetadataPresentation:
    ability = pokemon.ability_name or f"Unknown #{pokemon.ability_id}"
    if pokemon.checksum_valid:
        nature = f"{pokemon.nature_name} • {ability}" if compact else f"Nature: {pokemon.nature_name}"
        ability_text = f"Ability: {ability}"
        friendship = pokemon.friendship
    else:
        nature = "Nature: -- • Ability: --" if compact else "Nature: --"
        ability_text = "Ability: --"
        friendship = None
    if friendship is None:
        text = "Friendship: -- / 255"
    elif compact:
        text = f"Friendship {friendship}"
    else:
        text = f"Friendship: {friendship} / 255 • {friendship_label(friendship)}"
    return PartyMetadataPresentation(nature, ability_text, text, friendship, compact)


def project_moves_text(*, checksum_valid: bool, moves: tuple[str, ...]) -> str:
    if not checksum_valid:
        return "Unavailable (checksum invalid)"
    return "\n".join(moves) if moves else "No moves learned"


def apply_party_stats(card: dict, pokemon) -> bool:
    presentation = project_party_stats(pokemon)
    if card.get("stats_presentation") == presentation:
        return False
    for value in presentation.stats:
        value_label = card["stat_values"][value.stat]
        value_label.setText(value.value)
        card["iv_values"][value.stat].setText(value.iv)
        for widget in (card["stat_names"][value.stat], value_label):
            widget.setProperty("natureRole", value.nature_role)
            refresh_style(widget)
    card["checksum"].setText(presentation.checksum_text)
    card["checksum"].setProperty("ramState", presentation.ram_state)
    refresh_style(card["checksum"])
    card["stats_presentation"] = presentation
    return True


def apply_party_metadata(card: dict, pokemon, *, compact: bool) -> bool:
    presentation = project_party_metadata(pokemon, compact=compact)
    if card.get("metadata_presentation") == presentation:
        return False
    card["nature"].setText(presentation.nature_text)
    card["ability"].setText(presentation.ability_text)
    card["ability"].setVisible(not compact)
    if card["friendship_bar_compact"] != compact:
        card["friendship_bar"].setVisible(not compact)
        card["friendship_bar_compact"] = compact
    if card["friendship_value"] != presentation.friendship:
        card["friendship_value"] = presentation.friendship
        if presentation.friendship is not None:
            card["friendship_bar"].setValue(presentation.friendship)
    state = (presentation.friendship, compact)
    if card["friendship_display_state"] != state:
        card["friendship_display_state"] = state
        card["friendship"].setText(presentation.friendship_text)
    card["metadata_presentation"] = presentation
    return True


def refresh_move_displays(
    card: dict, pokemon,
    definition_for_id: Callable[[int], MoveDefinition | None] | None,
) -> None:
    """Cache one move projection per card, with every displayed input in its key.

    Provider catalogs are immutable for a session; replacing the catalog invalidates
    the result. Invalid/missing records always clear the projection. The caller still
    refreshes preferences and live views independently on every update.
    """
    decoded = getattr(pokemon, "decoded", None)
    if pokemon is None or not pokemon.checksum_valid or decoded is None:
        card["move_displays"] = ()
        card.pop("move_projection_state", None)
        return
    move_ids = tuple(getattr(decoded, "move_ids", ()))[:4]
    current_pps = tuple(getattr(decoded, "move_current_pps", ()))
    pp_ups = tuple(getattr(decoded, "move_pp_ups", ()))
    state = (getattr(pokemon, "stable_id", None), move_ids, current_pps, pp_ups, definition_for_id)
    if card.get("move_projection_state") == state:
        return
    lookup = definition_for_id or (lambda _move_id: None)
    card["move_displays"] = tuple(
        resolve_move(PokemonMoveState(move_id, current_pps[index], pp_ups[index]), lookup)
        if index < len(current_pps) and index < len(pp_ups) else None
        for index, move_id in enumerate(move_ids)
    )
    card["move_projection_state"] = state
