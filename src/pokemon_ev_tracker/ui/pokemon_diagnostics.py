"""Pure text for existing decoded party and boxed Pokemon diagnostic models.

Acquisition decisions, file/resource observations and address calculation belong
at the caller. No clocks, widgets, filesystem probes or RAM decoding live here.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pokemon_ev_tracker.games.platinum.decoder import PartyPokemon
from pokemon_ev_tracker.games.platinum.pc_storage import BoxedPokemon
from pokemon_ev_tracker.pokemon.gen4.structure import (
    HELD_ITEM_BOX_DATA_OFFSET,
    HELD_ITEM_RECORD_OFFSET,
    IVS_BOX_DATA_OFFSET,
    IVS_RECORD_OFFSET,
)


@dataclass(frozen=True)
class PartyPokemonDiagnosticObservation:
    acquisition_source: str
    acquisition_confidence: str
    suggested_id: str | None
    suggested_name: str | None
    already_observed: bool
    item_slug: str | None
    item_path: Path | str | None
    item_exists: bool


def _format_optional_hex(value: int | None) -> str:
    return f"0x{value:08X}" if value is not None else "--"


def format_party_pokemon_diagnostics(
    pokemon: PartyPokemon,
    observation: PartyPokemonDiagnosticObservation,
) -> Iterator[str]:
    diag = pokemon.decoded.diagnostics
    nickname = pokemon.decoded.nickname_diagnostics
    acquisition = pokemon.decoded.acquisition_metadata_diagnostics
    source, confidence = observation.acquisition_source, observation.acquisition_confidence
    suggested_id, suggested_name = observation.suggested_id, observation.suggested_name
    already_observed = observation.already_observed
    terminator = nickname.terminator_unit_index
    terminator_label = (
        f"unit {terminator} (field byte +0x{terminator * 2:02X})"
        if terminator is not None
        else "not present; decoded to field end"
    )
    yield from (
        f"Slot {pokemon.slot} - {pokemon.species}",
        f"Address: {_format_optional_hex(diag.address)}",
        f"PID: 0x{diag.pid:08X}",
        f"Stable Pokémon ID: {pokemon.stable_id}",
        (
            f"Checksum: {'VALID' if pokemon.checksum_valid else 'INVALID'} "
            f"(stored 0x{diag.checksum:04X}, calculated 0x{diag.calculated_checksum:04X})"
        ),
        f"Permutation: {diag.block_order} (index {diag.shuffle_index})",
        f"Species ID: {pokemon.species_id}",
        f"Nature ID: {pokemon.nature_id}",
        f"Nature: {pokemon.nature_name}",
        f"Friendship: {pokemon.friendship}",
        f"Nature increased stat: {pokemon.nature_increased_stat or '--'}",
        f"Nature decreased stat: {pokemon.nature_decreased_stat or '--'}",
        f"Ability slot: {pokemon.ability_slot or '--'}",
        f"Ability ID: {pokemon.ability_id}",
        f"Ability name: {pokemon.ability_name or '--'}",
        "IVs:",
        f"  HP: {pokemon.hp_iv}",
        f"  Attack: {pokemon.attack_iv}",
        f"  Defense: {pokemon.defense_iv}",
        f"  Sp. Atk: {pokemon.special_attack_iv}",
        f"  Sp. Def: {pokemon.special_defense_iv}",
        f"  Speed: {pokemon.speed_iv}",
        (
            "IV packed word: "
            f"0x{pokemon.decoded.packed_ivs:08X} "
            f"(record +0x{IVS_RECORD_OFFSET:02X}, "
            f"decrypted box data +0x{IVS_BOX_DATA_OFFSET:02X})"
        ),
        (
            "IV packed flags: "
            f"egg={str(pokemon.decoded.ivs.is_egg).lower()}, "
            f"has_nickname={str(pokemon.decoded.ivs.has_nickname).lower()}"
        ),
        f"Held item ID: {pokemon.held_item_id}",
        f"Held item name: {pokemon.held_item_name or 'None'}",
        f"Held item sprite slug: {observation.item_slug or '--'}",
        f"Held item sprite path: {observation.item_path or '--'}",
        f"Held item sprite exists: {str(observation.item_exists).lower()}",
        f"Held item decrypted box-data offset: 0x{HELD_ITEM_BOX_DATA_OFFSET:02X}",
        f"Held item party-record layout offset: 0x{HELD_ITEM_RECORD_OFFSET:02X}",
        f"Nickname absolute address: {_format_optional_hex(nickname.absolute_address)}",
        f"Nickname record-relative offset: 0x{nickname.record_relative_offset:02X}",
        f"Nickname decrypted box-data offset: 0x{nickname.box_data_relative_offset:02X}",
        f"Nickname raw bytes: {nickname.raw_bytes_hex}",
        f"Nickname code units: {[f'0x{unit:04X}' for unit in nickname.code_units]}",
        f"Nickname terminator: {terminator_label}",
        f"Nickname decoded string: {nickname.decoded_string!r}",
        f"Nickname final: {pokemon.nickname!r}",
        (f"Met location: ID {pokemon.met_location_id} ({pokemon.met_location_name or 'Unknown'})"),
        (
            f"Met location extended offsets: record +0x{acquisition.met_location_record_offset:02X}, "
            f"decrypted box +0x{acquisition.met_location_box_data_offset:02X}, "
            f"absolute {_format_optional_hex(diag.address + acquisition.met_location_record_offset if diag.address is not None else None)}; "
            f"decrypted field bytes {pokemon.met_location_id.to_bytes(2, 'little').hex(' ').upper()}"
        ),
        (
            f"Met location DP-style ID: {pokemon.decoded.met_location_dp_id} "
            f"(record +0x{acquisition.met_location_dp_record_offset:02X}, "
            f"decrypted box +0x{acquisition.met_location_dp_box_data_offset:02X})"
        ),
        (
            f"Met level: {pokemon.met_level if pokemon.met_level is not None else '--'} "
            f"(record +0x{acquisition.met_level_record_offset:02X}, "
            f"decrypted box +0x{acquisition.met_level_box_data_offset:02X}, "
            f"raw 0x{pokemon.decoded.met_level or 0:02X})"
        ),
        (
            f"Egg location ID: {pokemon.egg_location_id} "
            f"(record +0x{acquisition.egg_location_record_offset:02X}, "
            f"decrypted box +0x{acquisition.egg_location_box_data_offset:02X}, "
            f"raw {pokemon.egg_location_id.to_bytes(2, 'little').hex(' ').upper()})"
        ),
        (
            f"Origin game ID: {pokemon.origin_game} "
            f"(record +0x{acquisition.origin_game_record_offset:02X}, "
            f"decrypted box +0x{acquisition.origin_game_box_data_offset:02X}, "
            f"raw 0x{pokemon.origin_game:02X})"
        ),
        (
            f"Met date (YY/MM/DD): {pokemon.met_date or '--'} "
            f"(record +0x{acquisition.met_date_record_offset:02X}, "
            f"decrypted box +0x{acquisition.met_date_box_data_offset:02X})"
        ),
        f"Is egg: {str(pokemon.is_egg).lower()}",
        f"Acquisition classification: {source} ({confidence.lower()} confidence)",
        f"Already observed in active run: {str(already_observed).lower()}",
        (
            f"Suggested Nuzlocke location: {suggested_name or '--'}"
            + (f" ({suggested_id})" if suggested_id else "")
        ),
        f"Level: {pokemon.level if pokemon.level is not None else '--'}",
        (
            f"Current HP: {pokemon.current_hp if pokemon.current_hp is not None else '--'} / "
            f"{pokemon.max_hp if pokemon.max_hp is not None else '--'}"
        ),
        "Current stats from party tail:",
        f"  Max HP: {pokemon.decoded.current_stats.max_hp}",
        f"  Attack: {pokemon.decoded.current_stats.attack}",
        f"  Defense: {pokemon.decoded.current_stats.defense}",
        f"  Sp. Atk: {pokemon.decoded.current_stats.special_attack}",
        f"  Sp. Def: {pokemon.decoded.current_stats.special_defense}",
        f"  Speed: {pokemon.decoded.current_stats.speed}",
        f"Battle stats raw (0x88-0x9B): {diag.battle_stats_raw_hex}",
        f"Battle stats decrypted (0x88-0x9B): {diag.battle_stats_decrypted_hex}",
        f"Battle stats valid: {str(diag.battle_stats_valid).lower()}",
        f"Battle stats validation: {diag.battle_stats_error or 'OK'}",
    )
    if pokemon.checksum_valid:
        yield from (
            *(f"{stat}: {value}" for stat, value in pokemon.evs.items()),
            f"Total EVs: {pokemon.ev_total}",
            "",
        )
    else:
        yield from ("EVs withheld because checksum is invalid", "")


def format_boxed_change_diagnostics(mon: BoxedPokemon) -> tuple[str, ...]:
    decoded = mon.decoded
    location = mon.met_location_name or f"Unknown #{decoded.met_location_id}"
    return (
        f"NEW: Box {mon.box_index} Slot {mon.slot_index}",
        f"Species: {mon.species_name}; nickname: {decoded.nickname or mon.species_name}",
        (f"PID: 0x{decoded.pid:08X}; checksum: {'VALID' if decoded.checksum_valid else 'INVALID'}"),
        (
            f"Met location: {location} (ID {decoded.met_location_id}); "
            f"met level: {decoded.met_level if decoded.met_level is not None else '--'}"
        ),
    )


def format_boxed_record_diagnostics(
    mon: BoxedPokemon, record_address: int | None
) -> tuple[str, ...]:
    decoded = mon.decoded
    location = mon.met_location_name or f"Unknown #{decoded.met_location_id}"
    return (
        f"Box {mon.box_index} Slot {mon.slot_index}",
        f"  Address: {_format_optional_hex(record_address)}",
        f"  Species: {mon.species_name} (#{decoded.species_id})",
        f"  Nickname: {decoded.nickname or mon.species_name}",
        f"  PID: 0x{decoded.pid:08X}",
        (
            f"  Checksum: {'VALID' if decoded.checksum_valid else 'INVALID'} "
            f"(stored 0x{decoded.checksum:04X}, calculated 0x{decoded.calculated_checksum:04X})"
        ),
        f"  Met location: {location} (ID {decoded.met_location_id})",
        f"  Met level: {decoded.met_level if decoded.met_level is not None else '--'}",
        f"  Raw 136-byte record: {getattr(mon, 'raw_hex', '') or '--'}",
    )
