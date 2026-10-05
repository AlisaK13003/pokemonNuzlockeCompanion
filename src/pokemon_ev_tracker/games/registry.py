"""Resolve installed game providers without importing games from UI modules."""

from __future__ import annotations

from pokemon_ev_tracker.games.provider import GameProvider, UnsupportedGameProvider

DEFAULT_GAME_ID = "pokemon-platinum"


def get_game_provider(game_id: str | None) -> GameProvider:
    if game_id == DEFAULT_GAME_ID:
        from pokemon_ev_tracker.games.platinum.provider import PokemonPlatinumProvider

        return PokemonPlatinumProvider()
    return UnsupportedGameProvider(game_id or "unknown")


def default_game_provider() -> GameProvider:
    return get_game_provider(DEFAULT_GAME_ID)
