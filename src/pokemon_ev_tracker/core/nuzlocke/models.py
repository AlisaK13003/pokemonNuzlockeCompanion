"""Game-independent models and summaries for local Nuzlocke runs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class EncounterStatus(str, Enum):
    NOT_ENCOUNTERED = "NOT_ENCOUNTERED"
    CAUGHT = "CAUGHT"
    FAILED = "FAILED"
    DEAD = "DEAD"
    SKIPPED = "SKIPPED"
    DUPES = "DUPES"


class RunStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WON = "WON"
    WIPED = "WIPED"
    ABANDONED = "ABANDONED"


ENCOUNTER_STATUSES = tuple(status.value for status in EncounterStatus)


@dataclass(frozen=True)
class EncounterLocation:
    location_id: str
    name: str
    order: int
    area_type: str = "—"


@dataclass(frozen=True)
class LevelCap:
    cap_id: str
    name: str
    category: str
    order: int
    level_cap: int
    healing_items: tuple[tuple[str, int], ...] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.level_cap, bool) or not 1 <= self.level_cap <= 100:
            raise ValueError("Level cap must be between 1 and 100.")
        if self.healing_items is not None and any(
            not name.strip() or isinstance(count, bool) or count < 1
            for name, count in self.healing_items
        ):
            raise ValueError("Healing items must have a name and a positive count.")

    @property
    def healing_item_count(self) -> int | None:
        if self.healing_items is None:
            return None
        return sum(count for _name, count in self.healing_items)


@dataclass(frozen=True)
class NuzlockeGameProfile:
    game_id: str
    display_name: str
    locations: tuple[EncounterLocation, ...]
    level_caps: tuple[LevelCap, ...]
    retired_default_locations: tuple[EncounterLocation, ...] = ()
    region_name: str = ""


@dataclass(frozen=True)
class EncounterRecord:
    location_id: str
    location: str
    status: str = EncounterStatus.NOT_ENCOUNTERED.value
    species: str = ""
    nickname: str = ""
    level: int | None = None
    notes: str = ""
    stable_id: str | None = None
    met_location_id: int | None = None
    met_location_name: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ENCOUNTER_STATUSES:
            raise ValueError(f"Unknown encounter status: {self.status}")
        if self.level is not None and (isinstance(self.level, bool) or not 1 <= self.level <= 100):
            raise ValueError("Encounter level must be between 1 and 100, or empty.")


@dataclass(frozen=True)
class DeathRecord:
    death_id: str
    species: str
    nickname: str
    level_at_death: int | None
    location_or_fight: str
    timestamp: str
    notes: str = ""
    encounter_location_id: str | None = None
    stable_id: str | None = None
    detection_source: str = "MANUAL"

    def __post_init__(self) -> None:
        if self.level_at_death is not None and (
            isinstance(self.level_at_death, bool) or not 1 <= self.level_at_death <= 100
        ):
            raise ValueError("Death level must be between 1 and 100, or empty.")


@dataclass(frozen=True)
class PokemonAcquisitionEvent:
    stable_id: str
    species_id: int
    species_name: str
    nickname: str
    level: int | None
    met_level: int | None
    met_location_id: int
    met_location_name: str | None
    egg_location_id: int
    origin_game: int
    is_egg: bool
    detected_at: str
    source: str
    confidence: str
    suggested_location_id: str | None = None
    suggested_location_name: str | None = None
    source_location: str = "PARTY"


@dataclass(frozen=True)
class ShinyEncounter:
    stable_id: str
    species_id: int
    species_name: str
    nickname: str
    level: int | None
    met_level: int | None
    location_id: str | None
    location_name: str
    source_location: str
    detected_at: str
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.stable_id or not 1 <= self.species_id <= 493:
            raise ValueError("A shiny encounter needs a valid identity and species.")
        for level in (self.level, self.met_level):
            if level is not None and (isinstance(level, bool) or not 1 <= level <= 100):
                raise ValueError("Shiny encounter levels must be between 1 and 100.")
        if self.source_location not in {"PARTY", "BOX", "MANUAL"}:
            raise ValueError("Unknown shiny encounter source.")


@dataclass
class NuzlockeRun:
    run_id: str
    name: str
    game: str
    created_at: str
    status: str = RunStatus.ACTIVE.value
    ended_at: str | None = None
    outcome_note: str = ""
    notes: str = ""
    completed: bool = False
    encounters: dict[str, EncounterRecord] = field(default_factory=dict)
    level_cap_overrides: dict[str, int] = field(default_factory=dict)
    completed_cap_ids: list[str] = field(default_factory=list)
    fight_notes: dict[str, str] = field(default_factory=dict)
    deaths: list[DeathRecord] = field(default_factory=list)
    observed_pokemon_ids: list[str] = field(default_factory=list)
    acquisition_events: list[PokemonAcquisitionEvent] = field(default_factory=list)
    resolved_acquisition_ids: list[str] = field(default_factory=list)
    auto_record_unambiguous_encounters: bool = False
    automatically_confirm_deaths: bool = False
    shiny_encounters: list[ShinyEncounter] = field(default_factory=list)
    starter_observation_complete: bool = False

    @property
    def started_at(self) -> str:
        return self.created_at


@dataclass(frozen=True)
class PartyLevel:
    nickname: str
    species: str
    level: int


@dataclass(frozen=True)
class RunSummary:
    caught: int
    failed: int
    deaths: int
    skipped: int
    completed_fights: int
    total_fights: int


def resolved_level_caps(run: NuzlockeRun, profile: NuzlockeGameProfile) -> tuple[LevelCap, ...]:
    overrides = run.level_cap_overrides
    return tuple(
        replace(cap, level_cap=overrides.get(cap.cap_id, cap.level_cap))
        for cap in sorted(profile.level_caps, key=lambda item: item.order)
    )


def next_level_cap(run: NuzlockeRun, caps: tuple[LevelCap, ...]) -> LevelCap | None:
    completed = set(run.completed_cap_ids)
    return next((cap for cap in caps if cap.cap_id not in completed), None)


def summarize_run(run: NuzlockeRun, caps: tuple[LevelCap, ...]) -> RunSummary:
    statuses = [encounter.status for encounter in run.encounters.values()]
    return RunSummary(
        caught=statuses.count(EncounterStatus.CAUGHT.value),
        failed=statuses.count(EncounterStatus.FAILED.value),
        deaths=len(run.deaths),
        skipped=statuses.count(EncounterStatus.SKIPPED.value),
        completed_fights=sum(cap.cap_id in run.completed_cap_ids for cap in caps),
        total_fights=len(caps),
    )


def over_cap_party_members(
    members: tuple[PartyLevel, ...], level_cap: int | None
) -> tuple[PartyLevel, ...]:
    if level_cap is None:
        return ()
    return tuple(member for member in members if member.level > level_cap)
