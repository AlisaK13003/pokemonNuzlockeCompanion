"""Capabilities and the application-facing boundary for a game integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pokemon_ev_tracker.games.base import GameProfile


@dataclass(frozen=True)
class GameCapabilities:
    live_party: bool = False
    live_opponents: bool = False
    ev_yields: bool = False
    friendship: bool = False
    friendship_walk: bool = False
    pc_storage: bool = False
    nuzlocke: bool = False
    death_detection: bool = False
    wipe_detection: bool = False
    move_metadata: bool = False
    player_position: bool = False

    def supports(self, feature: str) -> bool:
        return bool(getattr(self, feature, False))


@dataclass(frozen=True)
class GameTheme:
    accent_primary: str = "#78dccb"
    accent_secondary: str = "#9bbeff"
    motif_id: str = "default"


class GameProvider(Protocol):
    game_id: str
    display_name: str
    generation: str
    platform: str
    emulator: str
    capabilities: GameCapabilities
    theme: GameTheme
    profile: GameProfile
    party_decoder: object | None
    battle_reader: object | None
    move_catalog: object | None
    friendship_rules: object | None
    nuzlocke_profile: object | None
    pc_storage: object | None

    def make_data_source(self): ...

    def species_catalog(self): ...

    def species_names(self) -> dict[int, str]: ...

    def get_training_ev_yield(self, species_id: int): ...

    def format_ev_yield(self, species_id: int) -> str: ...

    def format_ev_yield_summary(self, species_id: int) -> str: ...

    def item_hint(self, item_id: int): ...

    def nickname_is_default(self, nickname: str, species: str) -> bool: ...

    def classify_acquisition(self, candidate, run, **kwargs): ...


@dataclass(frozen=True)
class GameSession:
    """Small runtime context shared by workspaces; no second state cache."""

    provider: GameProvider
    data_source: object


class UnsupportedGameProvider:
    def __init__(self, game_id: str) -> None:
        self.game_id = game_id
        self.display_name = "Unsupported game"
        self.generation = "Unknown"
        self.platform = "Unknown"
        self.emulator = "Unknown"
        self.capabilities = GameCapabilities()
        self.theme = GameTheme()
        self.profile = GameProfile(
            game_id, self.display_name, (), (), None, lambda *_args, **_kwargs: None)
        self.nuzlocke_profile = None
        self.pc_storage = None
        self.party_decoder = None
        self.battle_reader = None
        self.move_catalog = None
        self.friendship_rules = None

    def make_data_source(self):
        from pokemon_ev_tracker.data_sources.base import DataSourceSnapshot

        class UnsupportedDataSource:
            backend_name = "Unsupported game"

            def start(self):
                pass

            def stop(self):
                pass

            def snapshot(self):
                return DataSourceSnapshot(self.backend_name, False, {})

        return UnsupportedDataSource()

    def species_catalog(self):
        return ()

    def species_names(self):
        return {}

    def nickname_is_default(self, nickname: str, species: str) -> bool:
        return nickname.casefold() == species.casefold()

    def get_training_ev_yield(self, _species_id: int):
        return None

    def format_ev_yield(self, _species_id: int) -> str:
        return "EV yield unavailable"

    format_ev_yield_summary = format_ev_yield

    def item_hint(self, _item_id: int):
        return None

    def classify_acquisition(self, _candidate, _run, **_kwargs):
        return "UNKNOWN", "LOW", None

    def party_acquisition_candidate(self, _pokemon):
        return None

    def boxed_acquisition_candidate(self, _pokemon):
        return None

    def decode_player_position(self, _payload):
        return None
