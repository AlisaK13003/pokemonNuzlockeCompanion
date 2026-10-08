"""Pure formatting of already-observed RAM coordinates and battle diagnostics.

No validation, provider lookup, live state, widgets or recovery commands live here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RamBattleDiagnosticObservation:
    payload: Mapping
    position: object | None
    walk_moves: int
    battlers: tuple
    active_enemies: tuple
    enemy_yields: tuple[str, ...]


def _format_optional_hex(value: int | None) -> str:
    return f"0x{value:08X}" if value is not None else "--"


def format_ram_battle_diagnostics(observation: RamBattleDiagnosticObservation) -> tuple[str, ...]:
    payload, position, walk_moves = observation.payload, observation.position, observation.walk_moves
    battle_battlers, active_enemies = observation.battlers, observation.active_enemies
    lines = []
    lines.extend(
        (
            f"Frame: {payload.get('frame', '--')}",
            f"Domain: {payload.get('domain', '--')}",
            f"Player X: {position.x if position else '--'}",
            f"Player Y: {position.y if position else '--'}",
            f"Delta X: {position.delta_x if position and position.delta_x is not None else '--'}",
            f"Delta Y: {position.delta_y if position and position.delta_y is not None else '--'}",
            f"Coordinate offsets: {payload.get('player_x_offset', '--')} / {payload.get('player_y_offset', '--')} (signed 16-bit LE; validated)",
            (
                f"Friendship Walk: {payload.get('friendship_walk_status', 'Idle')}; "
                f"successful moves this session: {walk_moves}"
            ),
            f"Pointer address: {payload.get('pointer_address', '--')}",
            f"Pointer offset: {payload.get('pointer_offset', '--')}",
            f"Pointer value: {payload.get('pointer_value', '--')}",
            f"Party relative offset: {payload.get('party_relative_offset', '--')}",
            f"Party address: {payload.get('party_address', '--')}",
            f"Party offset: {payload.get('party_offset', '--')}",
            f"Party records address: {payload.get('party_records_address', '--')}",
            f"Party records offset: {payload.get('party_records_offset', '--')}",
            "Offset candidates:",
            *(f"  {candidate}" for candidate in payload.get("party_offset_candidates", [])),
            "",
        )
    )
    lines.append("Battle Battlers (candidate offsets/fields; validate in gameplay):")
    lines.append(f"Base pointer: {payload.get('pointer_value', '--')}")
    for battler in battle_battlers:
        species_id = battler.species_id if battler.species_id is not None else "--"
        level = battler.level if battler.level is not None else "--"
        hp = (
            f"{battler.current_hp} / {battler.max_hp or '?'}"
            if battler.current_hp is not None
            else "-- / ?"
        )
        stats = ", ".join(
            f"{name}={value if value is not None else '--'}"
            for name, value in battler.stats.items()
        )
        lines.extend(
            (
                f"Battler {battler.battler_index} - {battler.role}",
                f"Relative offset: 0x{battler.relative_offset:05X}",
                f"Record address: {_format_optional_hex(battler.address)}",
                f"Species ID: {species_id}; species: {battler.species}",
                f"Level: {level}; current HP (diagnostic): {hp}",
                f"Max HP candidate at +0x4E (diagnostic): {battler.max_hp}",
                f"HP region raw (+0x48..+0x53): {battler.hp_region_raw_hex or '--'}",
                f"Candidate stats: {stats}",
                f"Candidate PID: {_format_optional_hex(battler.pid)}",
                (
                    f"Candidate ability ID: {battler.ability_id if battler.ability_id is not None else '--'}; "
                    f"candidate held item ID: {battler.held_item_id if battler.held_item_id is not None else '--'}"
                ),
                f"Candidate nickname bytes: {battler.nickname_raw_hex or '--'}",
                f"Validation state: {battler.validation_state.value}"
                + (f" ({battler.validation_reason})" if battler.validation_reason else ""),
                f"Raw record: {battler.raw_hex or '--'}",
                "",
            )
        )
    lines.append("Currently Battling:")
    if active_enemies:
        for enemy_index, battler in enumerate(active_enemies):
            lines.append(
                f"Enemy {1 if battler.battler_index == 1 else 2}: "
                f"{battler.species}, Lv. {battler.level}, HP "
                f"{battler.current_hp} / {battler.max_hp or '?'}; "
                f"Base EV Yield: {observation.enemy_yields[enemy_index]}"
            )
    else:
        lines.append("No validated active enemy battlers.")
    lines.append("")
    return tuple(lines)
