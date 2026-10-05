"""Small game-profile contract shared by emulator data sources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pokemon_ev_tracker.core.friendship_training import FriendshipWalkRules
from pokemon_ev_tracker.core.moves import MoveDefinition


class PartyDecoder(Protocol):
    def __call__(self, raw_party: bytes, base_address: int | None = None, **kwargs): ...


@dataclass(frozen=True)
class GameProfile:
    game_id: str
    display_name: str
    supported_emulators: tuple[str, ...]
    supported_cores: tuple[str, ...]
    memory_profile: object
    party_decoder: Callable
    move_definition: Callable[[int], MoveDefinition | None] | None = None
    friendship_walk_rules: FriendshipWalkRules | None = None
