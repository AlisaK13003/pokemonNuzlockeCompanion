"""Decode Generation IV party blocks."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from pokemon_ev_tracker.games.platinum.abilities import get_platinum_ability
from pokemon_ev_tracker.games.platinum.items import get_gen4_item_name
from pokemon_ev_tracker.games.platinum.locations import platinum_met_location_name
from pokemon_ev_tracker.games.platinum.moves import get_platinum_move_name
from pokemon_ev_tracker.games.platinum.species import Species, load_gen4_species
from pokemon_ev_tracker.pokemon.gen4.structure import (
    PARTY_POKEMON_SIZE,
    CurrentStats,
    DecodedPokemon,
    decode_party_pokemon,
)


@dataclass(frozen=True)
class PartyPokemon:
    slot: int
    species_id: int
    species: str
    nickname: str
    held_item_id: int
    held_item_name: str | None
    moves: tuple[str, ...]
    friendship: int
    level: int | None
    current_hp: int | None
    max_hp: int | None
    evs: Mapping[str, int]
    ev_total: int
    met_location_id: int
    met_location_name: str | None
    egg_location_id: int
    origin_game: int
    met_level: int | None
    met_date: tuple[int, int, int] | None
    is_egg: bool
    stable_id: str
    checksum_valid: bool
    decoded: DecodedPokemon
    nature_id: int
    nature_name: str
    nature_increased_stat: str | None
    nature_decreased_stat: str | None
    ability_slot: int | None
    ability_id: int
    ability_name: str | None
    hp_iv: int
    attack_iv: int
    defense_iv: int
    special_attack_iv: int
    special_defense_iv: int
    speed_iv: int
    current_stats: CurrentStats | None
    sample_stale: bool = False
    battle_stats_stale: bool = False

    @property
    def is_shiny(self) -> bool:
        return self.decoded.is_shiny


@dataclass(frozen=True)
class PartyState:
    party_count: int | None
    party_count_valid: bool
    pokemon: tuple[PartyPokemon, ...]
    error: str | None = None
    candidate_count: int = 0
    live_read_warning: str | None = None


def decode_party(
    raw_party: bytes,
    base_address: int | None = None,
    species_catalog: tuple[Species, ...] | None = None,
) -> PartyState:
    if len(raw_party) < 4:
        return PartyState(None, False, (), "Party payload is shorter than the count field.")

    party_count = int.from_bytes(raw_party[:4], "little")
    if party_count > 6:
        return PartyState(
            party_count,
            False,
            (),
            f"Party count {party_count} is outside the valid range 0-6.",
        )

    needed = 4 + (6 * PARTY_POKEMON_SIZE)
    if len(raw_party) < needed:
        return PartyState(
            party_count,
            True,
            (),
            f"Party payload is {len(raw_party)} bytes; expected at least {needed}.",
        )

    species_names = _species_names(species_catalog)
    decoded_slots = []
    for slot_index in range(party_count):
        start = 4 + (slot_index * PARTY_POKEMON_SIZE)
        address = base_address + start if base_address is not None else None
        decoded = decode_party_pokemon(raw_party[start : start + PARTY_POKEMON_SIZE], address,
                                       allow_decrypted_ram=True)
        if decoded.species_id == 0:
            continue
        species = species_names.get(decoded.species_id, f"Unknown #{decoded.species_id}")
        evs = {
            "hp": decoded.evs.hp,
            "attack": decoded.evs.attack,
            "defense": decoded.evs.defense,
            "special_attack": decoded.evs.special_attack,
            "special_defense": decoded.evs.special_defense,
            "speed": decoded.evs.speed,
        }
        ability = get_platinum_ability(decoded.species_id, decoded.ability_id)
        decoded_slots.append(
            PartyPokemon(
                slot=slot_index + 1,
                species_id=decoded.species_id,
                species=species,
                nickname=decoded.nickname or species,
                held_item_id=decoded.held_item_id,
                held_item_name=get_gen4_item_name(decoded.held_item_id),
                moves=tuple(
                    name
                    for move_id in decoded.move_ids
                    if (name := get_platinum_move_name(move_id)) is not None
                ),
                friendship=decoded.friendship,
                level=decoded.level,
                current_hp=decoded.current_hp,
                max_hp=decoded.max_hp,
                evs=evs,
                ev_total=decoded.evs.total,
                met_location_id=decoded.met_location_id,
                met_location_name=platinum_met_location_name(decoded.met_location_id),
                egg_location_id=decoded.egg_location_id,
                origin_game=decoded.origin_game,
                met_level=decoded.met_level,
                met_date=decoded.met_date,
                is_egg=decoded.is_egg,
                stable_id=decoded.stable_id,
                checksum_valid=decoded.diagnostics.checksum_valid,
                decoded=decoded,
                nature_id=decoded.nature_id,
                nature_name=decoded.nature_name,
                nature_increased_stat=decoded.nature_increased_stat,
                nature_decreased_stat=decoded.nature_decreased_stat,
                ability_slot=ability.slot,
                ability_id=ability.id,
                ability_name=ability.name,
                hp_iv=decoded.ivs.hp,
                attack_iv=decoded.ivs.attack,
                defense_iv=decoded.ivs.defense,
                special_attack_iv=decoded.ivs.special_attack,
                special_defense_iv=decoded.ivs.special_defense,
                speed_iv=decoded.ivs.speed,
                current_stats=(
                    decoded.current_stats
                    if decoded.diagnostics.checksum_valid
                    and decoded.diagnostics.battle_stats_valid
                    else None
                ),
            )
        )

    return PartyState(party_count, True, tuple(decoded_slots))


def valid_checksum_count(party: PartyState) -> int:
    return sum(1 for pokemon in party.pokemon if pokemon.checksum_valid)


def nickname_is_default(nickname: str | None, species: str) -> bool:
    if not nickname:
        return True
    normalized_nickname = unicodedata.normalize("NFKC", nickname).strip().casefold()
    normalized_species = unicodedata.normalize("NFKC", species).strip().casefold()
    return normalized_nickname == normalized_species


def _species_names(species_catalog: tuple[Species, ...] | None) -> dict[int, str]:
    catalog = species_catalog or load_gen4_species()
    return {species.national_dex_number: species.name for species in catalog}
