"""Game-neutral goal, session telemetry, and approximate walking ETA."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import ceil, isfinite


@dataclass(frozen=True)
class FriendshipWalkRules:
    movement_interval_steps: int
    friendship_gain_probability: float
    friendship_gain_amount: int
    max_friendship: int = 255
    evolution_goal: int = 220
    default_goal: int = 160

    def __post_init__(self) -> None:
        if (self.movement_interval_steps <= 0 or
                not 0 < self.friendship_gain_probability <= 1 or
                self.friendship_gain_amount <= 0 or self.max_friendship <= 0
                or not 0 <= self.default_goal <= self.max_friendship):
            raise ValueError("Invalid walking friendship rules")


@dataclass(frozen=True)
class FriendshipGoal:
    target: int

    def __post_init__(self) -> None:
        if type(self.target) is not int or not 0 <= self.target <= 255:
            raise ValueError("Friendship goal must be from 0 to 255")


class ETAStatus(str, Enum):
    READY = "ready"
    CALIBRATING = "calibrating"
    AVAILABLE = "available"
    PAUSED = "paused"
    REACHED = "reached"


@dataclass(frozen=True)
class FriendshipETA:
    status: ETAStatus
    estimated_seconds: int | None = None
    estimated_steps: int | None = None
    confidence: str = "low"


def estimate_friendship_eta(
    current_friendship: int,
    target_friendship: int,
    rules: FriendshipWalkRules,
    movement_rate: float | None,
    *,
    active_seconds: float = 0,
    movement_units: int = 0,
    observed_gain: int = 0,
    paused: bool = False,
) -> FriendshipETA:
    """Use expected Gen-specific gain, then lightly blend sustained live gain.

    Movement units are coordinate changes, an approximate step proxy. An
    observed gain is never assumed to have been caused solely by walking.
    """
    remaining = max(0, min(target_friendship, rules.max_friendship) - current_friendship)
    if remaining == 0:
        return FriendshipETA(ETAStatus.REACHED, 0, 0, "high")
    expected_steps = ceil(
        remaining * rules.movement_interval_steps
        / (rules.friendship_gain_probability * rules.friendship_gain_amount)
    )
    if paused:
        return FriendshipETA(ETAStatus.PAUSED, estimated_steps=expected_steps)
    if (movement_rate is None or not isfinite(movement_rate) or movement_rate <= 0
            or active_seconds < 5 or movement_units < 4):
        return FriendshipETA(ETAStatus.CALIBRATING, estimated_steps=expected_steps)
    theoretical_gain_per_unit = (
        rules.friendship_gain_probability * rules.friendship_gain_amount
        / rules.movement_interval_steps
    )
    gain_per_unit = theoretical_gain_per_unit
    confidence = "low"
    if movement_units >= 256 and observed_gain >= 2:
        observed = observed_gain / movement_units
        # Limit non-walking gains and stochastic bursts from swinging the ETA.
        observed = min(theoretical_gain_per_unit * 2,
                       max(theoretical_gain_per_unit * 0.5, observed))
        gain_per_unit = theoretical_gain_per_unit * 0.75 + observed * 0.25
        confidence = "medium"
    units = ceil(remaining / gain_per_unit)
    return FriendshipETA(
        ETAStatus.AVAILABLE,
        estimated_seconds=max(0, ceil(units / movement_rate)),
        estimated_steps=units,
        confidence=confidence,
    )


@dataclass
class FriendshipWalkSessionStats:
    started_at: float
    starting_friendship: int
    current_friendship: int
    active_seconds: float = 0.0
    paused_seconds: float = 0.0
    movement_units: int = 0
    reversals: int = 0
    _last_at: float = field(init=False, repr=False)
    _last_active: bool = field(default=False, repr=False)
    _last_movement_count: int = field(default=0, repr=False)
    _last_reversal_frame: int | None = field(default=None, repr=False)
    _smoothed_rate: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._last_at = self.started_at

    @property
    def friendship_gained(self) -> int:
        return max(0, self.current_friendship - self.starting_friendship)

    @property
    def movement_rate(self) -> float | None:
        return self._smoothed_rate

    def retarget(self, friendship: int) -> None:
        self.starting_friendship = friendship
        self.current_friendship = friendship

    def observe(
        self, now: float, *, active: bool, friendship: int | None = None,
        controller_moves: int | None = None, reversal_frame: int | None = None,
    ) -> None:
        elapsed = max(0.0, now - self._last_at)
        if self._last_active:
            self.active_seconds += elapsed
        else:
            self.paused_seconds += elapsed
        self._last_at = max(now, self._last_at)
        if friendship is not None:
            self.current_friendship = friendship
        if controller_moves is not None:
            delta = max(0, controller_moves - self._last_movement_count)
            self._last_movement_count = controller_moves
            if active and delta:
                self.movement_units += delta
            if self._last_active and active and elapsed > 0:
                instantaneous = delta / elapsed
                weight = min(0.2, elapsed / 5)
                self._smoothed_rate = (
                    instantaneous if self._smoothed_rate is None else
                    self._smoothed_rate * (1 - weight) + instantaneous * weight
                )
        if reversal_frame is not None and reversal_frame != self._last_reversal_frame:
            self.reversals += 1
            self._last_reversal_frame = reversal_frame
        self._last_active = active

    def eta(self, goal: FriendshipGoal, rules: FriendshipWalkRules, *, paused: bool = False):
        return estimate_friendship_eta(
            self.current_friendship, goal.target, rules, self.movement_rate,
            active_seconds=self.active_seconds, movement_units=self.movement_units,
            observed_gain=self.friendship_gained, paused=paused,
        )
