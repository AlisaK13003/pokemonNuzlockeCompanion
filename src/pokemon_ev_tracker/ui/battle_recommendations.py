"""One bounded, shared projection of game-neutral battle EV recommendations.

Enemy validation and provider lookup happen at the caller on every update. This
module retains no battlers, clocks, widgets, targets or selected-slot state.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pokemon_ev_tracker.core.ev_training import (
    EV_STAT_KEYS,
    BattleEVRecommendation,
    EVTrainingPreference,
    EVYield,
    recommend_battle_evs,
)


@dataclass(frozen=True)
class TrainingMember:
    slot: int
    identity: str
    preference: EVTrainingPreference | None


@dataclass(frozen=True)
class BattleRecommendationPresentation:
    battle_state: str
    recommendations: Mapping[int, BattleEVRecommendation]
    combined_yield: tuple[tuple[str, int], ...]


def project_battle_recommendations(
    members: tuple[TrainingMember, ...], yields: tuple[EVYield | None, ...], *, available: bool,
) -> BattleRecommendationPresentation:
    state = (
        "unavailable" if not available or any(value is None for value in yields) else
        "active" if any(any(value.as_mapping().values()) for value in yields) else "idle"
    )
    results = {member.slot: recommend_battle_evs(member.preference, yields)
               for member in members} if state == "active" else {}
    combined = tuple(
        (stat, sum(getattr(value, stat) for value in yields)) for stat in EV_STAT_KEYS
    ) if available and yields and all(value is not None for value in yields) else ()
    return BattleRecommendationPresentation(state, MappingProxyType(results), combined)


class BattleRecommendationProjector:
    """Retain only the latest equivalent input/result pair for a UI owner."""
    def __init__(self) -> None:
        self._provider = None
        self._key = None
        self._result: BattleRecommendationPresentation | None = None

    def project(
        self, members: tuple[TrainingMember, ...], yields: tuple[EVYield | None, ...],
        *, available: bool, provider: object,
    ) -> BattleRecommendationPresentation:
        key = (members, yields, available)
        if self._provider is not provider or key != self._key:
            self._result = project_battle_recommendations(members, yields, available=available)
            self._provider, self._key = provider, key
        assert self._result is not None
        return self._result
