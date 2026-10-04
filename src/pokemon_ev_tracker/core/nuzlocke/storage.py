"""Atomic JSON persistence for local Nuzlocke run profiles."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pokemon_ev_tracker.config.paths import app_data_directory
from pokemon_ev_tracker.core.nuzlocke.models import (
    ENCOUNTER_STATUSES,
    DeathRecord,
    EncounterLocation,
    EncounterRecord,
    EncounterStatus,
    NuzlockeGameProfile,
    NuzlockeRun,
    PokemonAcquisitionEvent,
)

DEFAULT_NUZLOCKE_PATH = app_data_directory() / "nuzlocke_runs.json"


class NuzlockeStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_NUZLOCKE_PATH
        self.runs: list[NuzlockeRun] = []
        self.active_run_id: str | None = None
        self._load()

    @property
    def active_run(self) -> NuzlockeRun | None:
        return self.get_run(self.active_run_id) if self.active_run_id else None

    def get_run(self, run_id: str | None) -> NuzlockeRun | None:
        if run_id is None:
            return None
        return next((run for run in self.runs if run.run_id == run_id), None)

    def create_run(self, name: str, profile: NuzlockeGameProfile) -> NuzlockeRun:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Run name cannot be empty.")
        run = NuzlockeRun(
            run_id=uuid4().hex,
            name=cleaned_name,
            game=profile.game_id,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            encounters={
                location.location_id: EncounterRecord(
                    location_id=location.location_id,
                    location=location.name,
                )
                for location in sorted(profile.locations, key=lambda item: item.order)
            },
        )
        self.runs.append(run)
        previous_active_id = self.active_run_id
        self.active_run_id = run.run_id
        try:
            self.save()
        except OSError:
            self.runs.remove(run)
            self.active_run_id = previous_active_id
            raise
        return run

    def switch_run(self, run_id: str) -> None:
        if self.get_run(run_id) is None:
            raise KeyError(f"Unknown Nuzlocke run: {run_id}")
        previous = self.active_run_id
        self.active_run_id = run_id
        try:
            self.save()
        except OSError:
            self.active_run_id = previous
            raise

    def rename_run(self, run_id: str, name: str) -> None:
        run = self._require_run(run_id)
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Run name cannot be empty.")
        previous = run.name
        run.name = cleaned_name
        try:
            self.save()
        except OSError:
            run.name = previous
            raise

    def update_run_details(self, run_id: str, notes: str, completed: bool) -> None:
        run = self._require_run(run_id)
        previous_notes, previous_completed = run.notes, run.completed
        run.notes = notes
        run.completed = bool(completed)
        try:
            self.save()
        except OSError:
            run.notes, run.completed = previous_notes, previous_completed
            raise

    def delete_run(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        if run is None:
            return False
        index = self.runs.index(run)
        previous_active = self.active_run_id
        self.runs.remove(run)
        if self.active_run_id == run_id:
            self.active_run_id = (
                self.runs[min(index, len(self.runs) - 1)].run_id if self.runs else None
            )
        try:
            self.save()
        except OSError:
            self.runs.insert(index, run)
            self.active_run_id = previous_active
            raise
        return True

    def ensure_locations(
        self,
        run_id: str,
        locations: tuple[EncounterLocation, ...],
        *,
        obsolete_locations: tuple[EncounterLocation, ...] = (),
    ) -> bool:
        run = self._require_run(run_id)
        previous = run.encounters.copy()
        changed = False
        for location in locations:
            if location.location_id not in run.encounters:
                run.encounters[location.location_id] = EncounterRecord(
                    location_id=location.location_id,
                    location=location.name,
                )
                changed = True
        death_location_ids = {death.encounter_location_id for death in run.deaths}
        for location in obsolete_locations:
            encounter = run.encounters.get(location.location_id)
            if (
                encounter is not None
                and encounter.location == location.name
                and encounter.location_id not in death_location_ids
                and encounter.status == EncounterStatus.NOT_ENCOUNTERED.value
                and not encounter.species
                and not encounter.nickname
                and encounter.level is None
                and not encounter.notes.strip()
            ):
                del run.encounters[location.location_id]
                changed = True
        if changed:
            try:
                self.save()
            except OSError:
                run.encounters = previous
                raise
        return changed

    def update_encounter(self, run_id: str, encounter: EncounterRecord) -> None:
        run = self._require_run(run_id)
        if encounter.location_id not in run.encounters:
            raise KeyError(f"Unknown encounter location: {encounter.location_id}")
        previous = run.encounters[encounter.location_id]
        previous_deaths = run.deaths.copy()
        run.encounters[encounter.location_id] = encounter
        linked_death = next(
            (death for death in run.deaths if death.encounter_location_id == encounter.location_id),
            None,
        )
        if encounter.status == EncounterStatus.DEAD.value:
            death = DeathRecord(
                death_id=linked_death.death_id if linked_death else uuid4().hex,
                species=encounter.species,
                nickname=encounter.nickname,
                level_at_death=encounter.level,
                location_or_fight=encounter.location,
                timestamp=(
                    linked_death.timestamp
                    if linked_death
                    else datetime.now(UTC).isoformat(timespec="seconds")
                ),
                notes=encounter.notes,
                encounter_location_id=encounter.location_id,
                stable_id=encounter.stable_id,
                detection_source=(linked_death.detection_source if linked_death else "MANUAL"),
            )
            if linked_death:
                run.deaths[run.deaths.index(linked_death)] = death
            else:
                run.deaths.append(death)
        elif linked_death:
            run.deaths.remove(linked_death)
        try:
            self.save()
        except OSError:
            run.encounters[encounter.location_id] = previous
            run.deaths = previous_deaths
            raise

    def has_observed_pokemon(self, run_id: str, stable_id: str) -> bool:
        run = self._require_run(run_id)
        return stable_id in run.observed_pokemon_ids

    def mark_pokemon_observed(self, run_id: str, stable_ids) -> None:
        run = self._require_run(run_id)
        previous = run.observed_pokemon_ids.copy()
        seen = set(previous)
        for stable_id in stable_ids:
            if stable_id not in seen:
                seen.add(stable_id)
                run.observed_pokemon_ids.append(stable_id)
        if run.observed_pokemon_ids == previous:
            return
        try:
            self.save()
        except OSError:
            run.observed_pokemon_ids = previous
            raise

    def record_acquisition_event(self, run_id: str, event: PokemonAcquisitionEvent) -> bool:
        run = self._require_run(run_id)
        if event.stable_id in run.observed_pokemon_ids:
            return False
        previous_ids = run.observed_pokemon_ids.copy()
        previous_events = run.acquisition_events.copy()
        run.observed_pokemon_ids.append(event.stable_id)
        run.acquisition_events.append(event)
        try:
            self.save()
        except OSError:
            run.observed_pokemon_ids = previous_ids
            run.acquisition_events = previous_events
            raise
        return True

    def resolve_acquisition(self, run_id: str, stable_id: str) -> None:
        run = self._require_run(run_id)
        if stable_id in run.resolved_acquisition_ids:
            return
        previous = run.resolved_acquisition_ids.copy()
        run.resolved_acquisition_ids.append(stable_id)
        try:
            self.save()
        except OSError:
            run.resolved_acquisition_ids = previous
            raise

    def set_auto_record_unambiguous(self, run_id: str, enabled: bool) -> None:
        run = self._require_run(run_id)
        previous = run.auto_record_unambiguous_encounters
        run.auto_record_unambiguous_encounters = bool(enabled)
        try:
            self.save()
        except OSError:
            run.auto_record_unambiguous_encounters = previous
            raise

    def set_automatically_confirm_deaths(self, run_id: str, enabled: bool) -> None:
        run = self._require_run(run_id)
        previous = run.automatically_confirm_deaths
        run.automatically_confirm_deaths = bool(enabled)
        try:
            self.save()
        except OSError:
            run.automatically_confirm_deaths = previous
            raise

    def accept_acquisition(
        self,
        run_id: str,
        stable_id: str,
        location_id: str,
        *,
        mode: str = "add",
    ) -> EncounterRecord:
        run = self._require_run(run_id)
        event = next((item for item in run.acquisition_events if item.stable_id == stable_id), None)
        if event is None:
            raise KeyError(f"Unknown acquisition event: {stable_id}")
        if stable_id in run.resolved_acquisition_ids:
            raise ValueError("This acquisition suggestion has already been resolved.")
        if mode not in {"add", "replace", "extra"}:
            raise ValueError("Acquisition mode must be add, replace, or extra.")
        base = run.encounters.get(location_id)
        if base is None:
            raise KeyError(f"Unknown encounter location: {location_id}")
        target_id = location_id
        target_name = base.location
        if mode == "extra":
            target_id = "extra-" + stable_id.replace(":", "-")
            target_name = f"{base.location} (extra)"
            if target_id in run.encounters:
                raise ValueError("An extra encounter is already recorded for this Pokémon.")
        elif mode == "add" and not _encounter_is_unused(base):
            raise ValueError(f"{base.location} already has an encounter.")

        record = EncounterRecord(
            location_id=target_id,
            location=target_name,
            status=EncounterStatus.CAUGHT.value,
            species=event.species_name,
            nickname=event.nickname,
            level=event.met_level or event.level,
            stable_id=event.stable_id,
            met_location_id=event.met_location_id,
            met_location_name=event.met_location_name,
        )
        previous_encounters = run.encounters.copy()
        previous_deaths = run.deaths.copy()
        previous_resolved = run.resolved_acquisition_ids.copy()
        run.encounters[target_id] = record
        if mode == "replace":
            run.deaths = [death for death in run.deaths if death.encounter_location_id != target_id]
        run.resolved_acquisition_ids.append(stable_id)
        try:
            self.save()
        except OSError:
            run.encounters = previous_encounters
            run.deaths = previous_deaths
            run.resolved_acquisition_ids = previous_resolved
            raise
        return record

    def record_ram_death(
        self,
        run_id: str,
        candidate,
        encounter_location_id: str | None,
    ) -> bool:
        """Persist one confirmed HP transition, optionally linking its encounter."""
        run = self._require_run(run_id)
        if any(death.stable_id == candidate.stable_id for death in run.deaths):
            return False

        previous_deaths = run.deaths.copy()
        encounter = run.encounters.get(encounter_location_id) if encounter_location_id else None
        if encounter_location_id and encounter is None:
            raise KeyError(f"Unknown encounter location: {encounter_location_id}")
        previous_encounter = encounter
        if encounter is not None:
            if encounter.status == EncounterStatus.DEAD.value:
                return False
            unused = (
                encounter.status == EncounterStatus.NOT_ENCOUNTERED.value
                and not encounter.species
            )
            if encounter.stable_id and encounter.stable_id != candidate.stable_id:
                raise ValueError("This encounter is linked to a different Pokémon.")
            same_identity = encounter.stable_id == candidate.stable_id
            if not unused and not same_identity and not _same_candidate(encounter, candidate):
                raise ValueError("This encounter contains a different Pokémon.")
            if unused:
                encounter = replace(
                    encounter,
                    status=EncounterStatus.DEAD.value,
                    species=candidate.species,
                    nickname=candidate.nickname,
                    level=candidate.met_level or candidate.level,
                    stable_id=candidate.stable_id,
                    met_location_id=candidate.met_location_id,
                    met_location_name=candidate.met_location_name,
                )
            else:
                encounter = replace(
                    encounter,
                    status=EncounterStatus.DEAD.value,
                    stable_id=candidate.stable_id,
                    met_location_id=(
                        encounter.met_location_id
                        if encounter.met_location_id is not None
                        else candidate.met_location_id
                    ),
                    met_location_name=encounter.met_location_name or candidate.met_location_name,
                )
            run.encounters[encounter.location_id] = encounter

        run.deaths.append(
            DeathRecord(
                death_id=uuid4().hex,
                species=candidate.species,
                nickname=candidate.nickname,
                level_at_death=candidate.level,
                location_or_fight="Unknown",
                timestamp=candidate.detected_at,
                notes="Detected automatically from RAM.",
                encounter_location_id=encounter.location_id if encounter else None,
                stable_id=candidate.stable_id,
                detection_source="RAM_AUTO",
            )
        )
        try:
            self.save()
        except OSError:
            run.deaths = previous_deaths
            if previous_encounter is not None:
                run.encounters[previous_encounter.location_id] = previous_encounter
            raise
        return True

    def add_death(self, run_id: str, death: DeathRecord) -> None:
        run = self._require_run(run_id)
        previous_deaths = run.deaths.copy()
        location_id = death.encounter_location_id
        previous_encounter = run.encounters.get(location_id) if location_id else None
        if location_id and location_id in run.encounters:
            encounter = run.encounters[location_id]
            death = replace(death, stable_id=death.stable_id or encounter.stable_id)
            run.encounters[location_id] = replace(
                encounter,
                status=EncounterStatus.DEAD.value,
                species=death.species,
                nickname=death.nickname,
                level=death.level_at_death,
            )
            existing = next(
                (item for item in run.deaths if item.encounter_location_id == location_id),
                None,
            )
            if existing:
                run.deaths[run.deaths.index(existing)] = death
            else:
                run.deaths.append(death)
        else:
            run.deaths.append(replace(death, encounter_location_id=None))
        try:
            self.save()
        except OSError:
            run.deaths = previous_deaths
            if location_id and previous_encounter:
                run.encounters[location_id] = previous_encounter
            raise

    def remove_death(self, run_id: str, death_id: str) -> bool:
        run = self._require_run(run_id)
        death = next((item for item in run.deaths if item.death_id == death_id), None)
        if death is None:
            return False
        if death.encounter_location_id:
            raise ValueError("Change the linked encounter status to remove this death.")
        previous_deaths = run.deaths.copy()
        run.deaths.remove(death)
        try:
            self.save()
        except OSError:
            run.deaths = previous_deaths
            raise
        return True

    def set_level_cap_override(self, run_id: str, cap_id: str, level: int | None) -> None:
        run = self._require_run(run_id)
        if level is not None and (isinstance(level, bool) or not 1 <= level <= 100):
            raise ValueError("Level cap must be between 1 and 100.")
        previous = run.level_cap_overrides.copy()
        if level is None:
            run.level_cap_overrides.pop(cap_id, None)
        else:
            run.level_cap_overrides[cap_id] = level
        try:
            self.save()
        except OSError:
            run.level_cap_overrides = previous
            raise

    def set_cap_completed(self, run_id: str, cap_id: str, completed: bool) -> None:
        run = self._require_run(run_id)
        previous = run.completed_cap_ids.copy()
        if completed and cap_id not in run.completed_cap_ids:
            run.completed_cap_ids.append(cap_id)
        elif not completed and cap_id in run.completed_cap_ids:
            run.completed_cap_ids.remove(cap_id)
        try:
            self.save()
        except OSError:
            run.completed_cap_ids = previous
            raise

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "active_run_id": self.active_run_id,
            "runs": [asdict(run) for run in self.runs],
        }
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary_path.replace(self.path)

    def _require_run(self, run_id: str) -> NuzlockeRun:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown Nuzlocke run: {run_id}")
        return run

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        raw_runs = payload.get("runs", [])
        if not isinstance(raw_runs, list):
            return
        for raw_run in raw_runs:
            if not isinstance(raw_run, dict):
                continue
            try:
                run = _deserialize_run(raw_run)
            except (KeyError, TypeError, ValueError):
                continue
            self.runs.append(run)
        requested_active_id = payload.get("active_run_id")
        if self.get_run(requested_active_id):
            self.active_run_id = requested_active_id
        elif self.runs:
            self.active_run_id = self.runs[0].run_id


def _deserialize_run(raw: dict) -> NuzlockeRun:
    encounters = {}
    for location_id, raw_encounter in raw.get("encounters", {}).items():
        if not isinstance(raw_encounter, dict):
            continue
        status = raw_encounter.get("status", EncounterStatus.NOT_ENCOUNTERED.value)
        if status not in ENCOUNTER_STATUSES:
            status = EncounterStatus.NOT_ENCOUNTERED.value
        level = _optional_level(raw_encounter.get("level"))
        encounter = EncounterRecord(
            location_id=str(location_id),
            location=str(raw_encounter.get("location", location_id)),
            status=status,
            species=str(raw_encounter.get("species", "")),
            nickname=str(raw_encounter.get("nickname", "")),
            level=level,
            notes=str(raw_encounter.get("notes", "")),
            stable_id=(str(raw_encounter["stable_id"]) if raw_encounter.get("stable_id") else None),
            met_location_id=_optional_int(raw_encounter.get("met_location_id")),
            met_location_name=(
                str(raw_encounter["met_location_name"])
                if raw_encounter.get("met_location_name") is not None
                else None
            ),
        )
        encounters[encounter.location_id] = encounter
    deaths = []
    for raw_death in raw.get("deaths", []):
        if not isinstance(raw_death, dict):
            continue
        deaths.append(
            DeathRecord(
                death_id=str(raw_death.get("death_id") or uuid4().hex),
                species=str(raw_death.get("species", "")),
                nickname=str(raw_death.get("nickname", "")),
                level_at_death=_optional_level(raw_death.get("level_at_death")),
                location_or_fight=str(raw_death.get("location_or_fight", "")),
                timestamp=str(raw_death.get("timestamp", "")),
                notes=str(raw_death.get("notes", "")),
                encounter_location_id=(
                    str(raw_death["encounter_location_id"])
                    if raw_death.get("encounter_location_id")
                    else None
                ),
                stable_id=(str(raw_death["stable_id"]) if raw_death.get("stable_id") else None),
                detection_source=str(raw_death.get("detection_source", "MANUAL")),
            )
        )
    overrides = {}
    for cap_id, value in raw.get("level_cap_overrides", {}).items():
        try:
            level = int(value)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, bool) and 1 <= level <= 100:
            overrides[str(cap_id)] = level
    completed_ids = raw.get("completed_cap_ids", [])
    if not isinstance(completed_ids, list):
        completed_ids = []
    observed_ids = raw.get("observed_pokemon_ids", [])
    if not isinstance(observed_ids, list):
        observed_ids = []
    resolved_ids = raw.get("resolved_acquisition_ids", [])
    if not isinstance(resolved_ids, list):
        resolved_ids = []
    raw_events = raw.get("acquisition_events", [])
    events = []
    if isinstance(raw_events, list):
        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                continue
            events.append(
                PokemonAcquisitionEvent(
                    stable_id=str(raw_event["stable_id"]),
                    species_id=int(raw_event["species_id"]),
                    species_name=str(raw_event["species_name"]),
                    nickname=str(raw_event.get("nickname", "")),
                    level=_optional_level(raw_event.get("level")),
                    met_level=_optional_level(raw_event.get("met_level")),
                    met_location_id=int(raw_event.get("met_location_id", 0)),
                    met_location_name=(
                        str(raw_event["met_location_name"])
                        if raw_event.get("met_location_name") is not None
                        else None
                    ),
                    egg_location_id=int(raw_event.get("egg_location_id", 0)),
                    origin_game=int(raw_event.get("origin_game", 0)),
                    is_egg=bool(raw_event.get("is_egg", False)),
                    detected_at=str(raw_event.get("detected_at", "")),
                    source=str(raw_event.get("source", "UNKNOWN")),
                    confidence=str(raw_event.get("confidence", "LOW")),
                    suggested_location_id=(
                        str(raw_event["suggested_location_id"])
                        if raw_event.get("suggested_location_id")
                        else None
                    ),
                    suggested_location_name=(
                        str(raw_event["suggested_location_name"])
                        if raw_event.get("suggested_location_name")
                        else None
                    ),
                    source_location=str(raw_event.get("source_location", "PARTY")),
                )
            )
    return NuzlockeRun(
        run_id=str(raw["run_id"]),
        name=str(raw["name"]),
        game=str(raw["game"]),
        created_at=str(raw.get("created_at") or datetime.now(UTC).isoformat(timespec="seconds")),
        notes=str(raw.get("notes", "")),
        completed=bool(raw.get("completed", False)),
        encounters=encounters,
        level_cap_overrides=overrides,
        completed_cap_ids=[str(cap_id) for cap_id in completed_ids],
        deaths=deaths,
        observed_pokemon_ids=[str(item) for item in observed_ids],
        acquisition_events=events,
        resolved_acquisition_ids=[str(item) for item in resolved_ids],
        auto_record_unambiguous_encounters=bool(
            raw.get("auto_record_unambiguous_encounters", False)
        ),
        automatically_confirm_deaths=bool(raw.get("automatically_confirm_deaths", False)),
    )


def _optional_level(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    return level if 1 <= level <= 100 else None


def _optional_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _same_candidate(encounter: EncounterRecord, candidate) -> bool:
    if not encounter.species or encounter.species.casefold() != candidate.species.casefold():
        return False
    return (encounter.nickname or encounter.species).strip().casefold() == (
        candidate.nickname or candidate.species
    ).strip().casefold()


def _encounter_is_unused(encounter: EncounterRecord) -> bool:
    return encounter.status == EncounterStatus.NOT_ENCOUNTERED.value and not encounter.species
