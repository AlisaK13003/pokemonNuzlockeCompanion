"""Game-neutral fight timeline and live party readiness projection."""

from __future__ import annotations

from dataclasses import dataclass

from pokemon_ev_tracker.core.nuzlocke.models import NuzlockeGameProfile, NuzlockeRun, PartyLevel


@dataclass(frozen=True)
class PartyFightReadiness:
    name: str
    species: str
    level: int
    over_by: int

    @property
    def status(self) -> str:
        return "OVER_CAP" if self.over_by else "READY"


@dataclass(frozen=True)
class FightDisplayData:
    fight_id: str
    name: str
    category: str
    order: int
    total_fights: int
    default_level_cap: int
    effective_level_cap: int
    healing_items: tuple[tuple[str, int], ...] | None
    completed: bool
    overridden: bool
    notes: str
    status: str
    party: tuple[PartyFightReadiness, ...]

    @property
    def ready_count(self) -> int:
        return sum(member.status == "READY" for member in self.party)

    @property
    def over_count(self) -> int:
        return sum(member.status == "OVER_CAP" for member in self.party)

    @property
    def healing_text(self) -> str:
        if self.healing_items is None:
            return "Unknown"
        return ", ".join(f"{count}× {name}" for name, count in self.healing_items) or "None"


@dataclass(frozen=True)
class FightTimeline:
    fights: tuple[FightDisplayData, ...]
    next_fight: FightDisplayData | None
    completed_count: int

    @property
    def total_fights(self) -> int:
        return len(self.fights)


def build_fight_timeline(
    run: NuzlockeRun | None,
    profile: NuzlockeGameProfile | None,
    party: tuple[PartyLevel, ...] = (),
) -> FightTimeline | None:
    """Recalculate derived fight state from one run, its profile, and current party."""
    if run is None or profile is None or run.game != profile.game_id:
        return None
    caps = sorted(profile.level_caps, key=lambda cap: cap.order)
    completed = set(run.completed_cap_ids)
    next_id = next((cap.cap_id for cap in caps if cap.cap_id not in completed), None)
    fights = tuple(
        FightDisplayData(
            fight_id=cap.cap_id,
            name=cap.name,
            category=cap.category,
            order=index,
            total_fights=len(caps),
            default_level_cap=cap.level_cap,
            effective_level_cap=run.level_cap_overrides.get(cap.cap_id, cap.level_cap),
            healing_items=cap.healing_items,
            completed=cap.cap_id in completed,
            overridden=cap.cap_id in run.level_cap_overrides,
            notes=run.fight_notes.get(cap.cap_id, ""),
            status=("COMPLETED" if cap.cap_id in completed else
                    "UP NEXT" if cap.cap_id == next_id else "FUTURE"),
            party=tuple(
                PartyFightReadiness(
                    name=member.nickname or member.species,
                    species=member.species,
                    level=member.level,
                    over_by=max(0, member.level - run.level_cap_overrides.get(
                        cap.cap_id, cap.level_cap)),
                )
                for member in party
            ) if cap.cap_id == next_id else (),
        )
        for index, cap in enumerate(caps, 1)
    )
    return FightTimeline(
        fights=fights,
        next_fight=next((fight for fight in fights if fight.fight_id == next_id), None),
        completed_count=sum(fight.completed for fight in fights),
    )
