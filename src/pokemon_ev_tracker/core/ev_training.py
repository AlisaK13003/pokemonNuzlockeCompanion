"""Game-neutral EV training preferences and combined battle recommendations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

EV_STAT_KEYS = (
    "hp",
    "attack",
    "defense",
    "special_attack",
    "special_defense",
    "speed",
)


@dataclass(frozen=True)
class EVTrainingPreference:
    """Stats the user allows gaining, independent of current EV amounts."""

    allowed_stats: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if isinstance(self.allowed_stats, (str, bytes, Mapping)):
            raise ValueError(  # noqa: TRY004 - model validation consistently reports ValueError.
                "Allowed stats must be a collection of canonical stat keys."
            )
        try:
            stats = frozenset(self.allowed_stats)
        except TypeError as error:
            raise ValueError("Allowed stats must be a collection of canonical stat keys.") from error
        if any(not isinstance(stat, str) or stat not in EV_STAT_KEYS for stat in stats):
            raise ValueError("Allowed stats contain an unknown stat key.")
        object.__setattr__(self, "allowed_stats", stats)


@dataclass(frozen=True)
class EVYield:
    """Base EV reward supplied by a game integration, never read from RAM here."""

    hp: int = 0
    attack: int = 0
    defense: int = 0
    special_attack: int = 0
    special_defense: int = 0
    speed: int = 0

    def __post_init__(self) -> None:
        for stat, value in self.as_mapping().items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{stat} yield must be a nonnegative integer.")

    def as_mapping(self) -> dict[str, int]:
        return {stat: getattr(self, stat) for stat in EV_STAT_KEYS}


class RecommendationStatus(str, Enum):
    GOOD = "good"
    AVOID = "avoid"
    MIXED = "mixed"
    NO_FOCUS = "no-focus"


@dataclass(frozen=True)
class BattleEVRecommendation:
    status: RecommendationStatus
    allowed_yields: Mapping[str, int]
    unwanted_yields: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", RecommendationStatus(self.status))
        for name in ("allowed_yields", "unwanted_yields"):
            values = dict(getattr(self, name))
            if any(stat not in EV_STAT_KEYS or isinstance(value, bool)
                   or not isinstance(value, int) or value <= 0
                   for stat, value in values.items()):
                raise ValueError("Recommendation reasons must contain positive canonical EV yields.")
            object.__setattr__(self, name, MappingProxyType(values))


def recommend_battle_evs(
    preference: EVTrainingPreference | None,
    opponent_yields: Iterable[EVYield],
) -> BattleEVRecommendation:
    """Classify every positive stat in the sum of all active opponent yields.

    No numeric target, current EV total, species, or Qt state affects this
    calculation. The UI presents no opponents/all-zero rewards as idle; a
    nonempty preference vacuously allows an empty combined reward here.
    """
    if preference is not None and not isinstance(preference, EVTrainingPreference):
        raise TypeError("Preference must be an EVTrainingPreference instance or None.")
    combined = dict.fromkeys(EV_STAT_KEYS, 0)
    for reward in opponent_yields:
        if not isinstance(reward, EVYield):
            raise TypeError("Opponent yields must be EVYield instances.")
        for stat, value in reward.as_mapping().items():
            combined[stat] += value
    if preference is None or not preference.allowed_stats:
        return BattleEVRecommendation(RecommendationStatus.NO_FOCUS, {}, {})
    allowed = {stat: value for stat, value in combined.items()
               if value > 0 and stat in preference.allowed_stats}
    unwanted = {stat: value for stat, value in combined.items()
                if value > 0 and stat not in preference.allowed_stats}
    status = (
        RecommendationStatus.MIXED if allowed and unwanted
        else RecommendationStatus.AVOID if unwanted
        else RecommendationStatus.GOOD
    )
    return BattleEVRecommendation(status, allowed, unwanted)
