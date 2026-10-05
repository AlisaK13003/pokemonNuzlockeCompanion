"""Platinum integration composed from the existing proven game modules."""

from __future__ import annotations

from dataclasses import dataclass

from pokemon_ev_tracker.core.nuzlocke.acquisition import AcquisitionCandidate
from pokemon_ev_tracker.data_sources.bizhawk import BizHawkRamDataSource
from pokemon_ev_tracker.games.platinum.battle import decode_battle_battlers
from pokemon_ev_tracker.games.platinum.coordinate_discovery import (
    MAX_SCAN_BYTES,
    CoordinateDiscovery,
    find_coordinate_candidates,
)
from pokemon_ev_tracker.games.platinum.decoder import nickname_is_default
from pokemon_ev_tracker.games.platinum.ev_yields import (
    format_ev_yield,
    format_ev_yield_summary,
    get_training_ev_yield,
)
from pokemon_ev_tracker.games.platinum.items import get_gen4_item_hint
from pokemon_ev_tracker.games.platinum.locations import (
    classify_platinum_acquisition,
    platinum_nuzlocke_location_id,
)
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.games.platinum.pc_storage import (
    BOX_POKEMON_SIZE,
    PC_BOX_COUNT,
    PC_BOX_RECORDS_SIZE,
    PC_BOX_SLOTS,
    SAVE_DATA_POINTER_ADDRESS,
)
from pokemon_ev_tracker.games.platinum.player_position import decode_player_position
from pokemon_ev_tracker.games.platinum.profile import PLATINUM_PROFILE
from pokemon_ev_tracker.games.platinum.species import load_gen4_species
from pokemon_ev_tracker.games.provider import GameCapabilities, GameTheme
from pokemon_ev_tracker.pokemon.gen4.moves import get_gen4_move_definition
from pokemon_ev_tracker.pokemon.gen4.structure import decode_box_pokemon


@dataclass(frozen=True)
class PlatinumPCStorage:
    box_pokemon_size: int = BOX_POKEMON_SIZE
    box_count: int = PC_BOX_COUNT
    slots_per_box: int = PC_BOX_SLOTS
    records_size: int = PC_BOX_RECORDS_SIZE
    save_data_pointer_address: int = SAVE_DATA_POINTER_ADDRESS
    decode_box_pokemon = staticmethod(decode_box_pokemon)


class PokemonPlatinumProvider:
    game_id = PLATINUM_PROFILE.game_id
    display_name = PLATINUM_PROFILE.display_name
    generation = "Generation IV"
    platform = "Nintendo DS"
    emulator = "BizHawk / EmuHawk"
    capabilities = GameCapabilities(
        live_party=True, live_opponents=True, ev_yields=True, friendship=True,
        friendship_walk=True, pc_storage=True, nuzlocke=True,
        death_detection=True, wipe_detection=True, move_metadata=True,
        player_position=True)
    theme = GameTheme("#78dccb", "#9bbeff", "platinum")
    profile = PLATINUM_PROFILE
    party_decoder = PLATINUM_PROFILE.party_decoder
    battle_reader = staticmethod(decode_battle_battlers)
    move_catalog = staticmethod(get_gen4_move_definition)
    friendship_rules = PLATINUM_PROFILE.friendship_walk_rules
    nuzlocke_profile = PLATINUM_NUZLOCKE_PROFILE
    pc_storage = PlatinumPCStorage()
    coordinate_discovery_type = CoordinateDiscovery
    coordinate_scan_max_bytes = MAX_SCAN_BYTES
    find_coordinate_candidates = staticmethod(find_coordinate_candidates)
    decode_player_position = staticmethod(decode_player_position)
    classify_acquisition = staticmethod(classify_platinum_acquisition)
    nuzlocke_location_id = staticmethod(platinum_nuzlocke_location_id)
    nickname_is_default = staticmethod(nickname_is_default)
    get_training_ev_yield = staticmethod(get_training_ev_yield)
    format_ev_yield = staticmethod(format_ev_yield)
    format_ev_yield_summary = staticmethod(format_ev_yield_summary)
    item_hint = staticmethod(get_gen4_item_hint)

    def __init__(self) -> None:
        self._species_names: dict[int, str] | None = None

    def make_data_source(self):
        return BizHawkRamDataSource(profile=self.profile)

    def species_catalog(self):
        return load_gen4_species()

    def species_names(self) -> dict[int, str]:
        if self._species_names is None:
            self._species_names = {
                species.national_dex_number: species.name
                for species in self.species_catalog()
            }
        return self._species_names

    def party_acquisition_candidate(self, pokemon) -> AcquisitionCandidate:
        nickname = pokemon.decoded.nickname or ""
        if self.nickname_is_default(nickname, pokemon.species):
            nickname = ""
        return AcquisitionCandidate(
            pokemon.stable_id, pokemon.species_id, pokemon.species, nickname,
            pokemon.level, pokemon.met_level, pokemon.met_location_id,
            pokemon.met_location_name, pokemon.egg_location_id, pokemon.origin_game,
            pokemon.is_egg, source_location="PARTY")

    def boxed_acquisition_candidate(self, pokemon) -> AcquisitionCandidate:
        decoded = pokemon.decoded
        nickname = decoded.nickname or ""
        if self.nickname_is_default(nickname, pokemon.species_name):
            nickname = ""
        return AcquisitionCandidate(
            decoded.stable_id, decoded.species_id, pokemon.species_name, nickname,
            decoded.met_level, decoded.met_level, decoded.met_location_id,
            pokemon.met_location_name, decoded.egg_location_id, decoded.origin_game,
            decoded.is_egg, source_location="BOX", box_index=pokemon.box_index,
            slot_index=pokemon.slot_index)
