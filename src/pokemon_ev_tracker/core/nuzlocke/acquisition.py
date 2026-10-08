"""Run-scoped acquisition observation for party and PC-storage identities."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from pokemon_ev_tracker.core.nuzlocke.models import (
    EncounterRecord,
    NuzlockeRun,
    PokemonAcquisitionEvent,
    ShinyEncounter,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore


@dataclass(frozen=True)
class AcquisitionCandidate:
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
    source_location: str = "PARTY"
    box_index: int | None = None
    slot_index: int | None = None
    is_shiny: bool = False


Classifier = Callable[[AcquisitionCandidate, NuzlockeRun | None], tuple[str, str, str | None]]
LOGGER = logging.getLogger(__name__)
INVALID_SNAPSHOT_LOG_DELAY_SECONDS = 10.0


def reconcile_initial_starter(candidates, *, connected, valid_snapshot, run, store):
    """Consume the first validated nonempty party once, persisted with the run."""
    if not connected or not valid_snapshot or run is None or run.starter_observation_complete:
        return ()
    party = [c for c in candidates if c.source_location == "PARTY"]
    if not party:
        return ()
    history = (run.observed_pokemon_ids or run.acquisition_events or run.deaths
               or run.shiny_encounters or any(r.status != "NOT_ENCOUNTERED" for r in run.encounters.values()))
    records = ()
    if len(party) == 1 and not history:
        candidate = party[0]
        starter = next((r for r in run.encounters.values() if r.location.casefold() == "starter"), None)
        if (starter is not None and not candidate.is_egg and not candidate.egg_location_id
                and 1 <= candidate.species_id <= 493 and candidate.stable_id):
            records = (replace(starter, status="CAUGHT", species=candidate.species_name,
                               nickname=candidate.nickname, level=candidate.met_level or candidate.level,
                               stable_id=candidate.stable_id, met_location_id=candidate.met_location_id,
                               met_location_name=candidate.met_location_name),)
    return store.reconcile_party_encounters(run.run_id, records, complete_starter_observation=True)


def reconcile_shiny_encounters(candidates, *, connected, valid_snapshot, run, store, classify):
    """Record validated owned shinies, including baseline Pokemon, outside the ledger."""
    if not connected or not valid_snapshot or run is None:
        return ()
    records = []
    existing = {record.stable_id for record in run.shiny_encounters}
    for candidate in candidates:
        if (not candidate.is_shiny or candidate.is_egg or candidate.stable_id in existing
                or not 1 <= candidate.species_id <= 493):
            continue
        _source, _confidence, location_id = classify(candidate, run)
        records.append(ShinyEncounter(
            stable_id=candidate.stable_id, species_id=candidate.species_id,
            species_name=candidate.species_name, nickname=candidate.nickname,
            level=candidate.level, met_level=candidate.met_level,
            location_id=location_id, location_name=candidate.met_location_name or "Unknown location",
            source_location=candidate.source_location,
            detected_at=datetime.now(UTC).isoformat(timespec="seconds"),
        ))
        existing.add(candidate.stable_id)
    return store.record_shiny_encounters(run.run_id, tuple(records))


def reconcile_party_encounters(
    candidates: tuple[AcquisitionCandidate, ...],
    *,
    connected: bool,
    valid_snapshot: bool,
    run: NuzlockeRun | None,
    store: NuzlockeStore,
    classify: Classifier,
) -> tuple[EncounterRecord, ...]:
    """Fill unused ledger rows from validated party met data, including baseline IDs."""
    if not connected or not valid_snapshot or run is None:
        return ()
    by_location: dict[str, dict[str, AcquisitionCandidate]] = {}
    for candidate in candidates:
        if (candidate.source_location != "PARTY" or candidate.is_egg or candidate.is_shiny
                or candidate.egg_location_id or not candidate.met_level
                or not 1 <= candidate.met_level <= 100
                or not 1 <= candidate.species_id <= 493):
            continue
        source, confidence, location_id = classify(candidate, run)
        # Platinum leaves Route 201 medium-confidence until the starter is assigned.
        # A mapped non-starter still carries useful met-location evidence.
        if location_id and (
            (source == "WILD" and confidence == "HIGH")
            or (source == "UNKNOWN" and confidence == "MEDIUM")
        ):
            by_location.setdefault(location_id, {})[candidate.stable_id] = candidate
    records = []
    for location_id, identities in by_location.items():
        base = run.encounters.get(location_id)
        if base is None or len(identities) != 1:
            continue
        candidate = next(iter(identities.values()))
        records.append(replace(
            base, status="CAUGHT", species=candidate.species_name,
            nickname=candidate.nickname, level=candidate.met_level,
            stable_id=candidate.stable_id, met_location_id=candidate.met_location_id,
            met_location_name=candidate.met_location_name,
        ))
    return store.reconcile_party_encounters(run.run_id, tuple(records))


class PartyAcquisitionObserver:
    def __init__(self, *, reset_on_disconnect: bool = True) -> None:
        self._connected = False
        self._reset_on_disconnect = reset_on_disconnect
        self._run_id: str | None = None
        self._baseline_ids: set[str] = set()
        self._empty_party_baseline = False
        self.last_decision: dict[str, object] | None = None
        self._last_invalid_log_at: float | None = None
        self._invalid_snapshot_since: float | None = None
        self._invalid_snapshot_reported = False

    def _note_decision(
        self, candidate: AcquisitionCandidate, status: str, reason: str,
        location: str | None = None,
        *, baseline_member: bool | None = None,
        already_observed: bool | None = None,
        log: bool = True,
    ) -> None:
        self.last_decision = {
            "source": candidate.source_location,
            "stable_id": candidate.stable_id,
            "pokemon": candidate.nickname or candidate.species_name,
            "species": candidate.species_name,
            "box": candidate.box_index,
            "slot": candidate.slot_index,
            "location": location or candidate.met_location_name,
            "status": status,
            "reason": reason,
            "baseline_member": baseline_member,
            "already_observed": already_observed,
            "timestamp": time.monotonic(),
        }
        if not log:
            return
        LOGGER.log(
            logging.DEBUG if reason in {
                "initial baseline for this run", "existing boxed Pokemon baselined",
            } else logging.INFO,
            "Acquisition candidate source=%s id=%s pokemon=%s species=%s location=%s "
            "box=%s slot=%s baseline_member=%s already_observed=%s status=%s reason=%s",
            candidate.source_location, candidate.stable_id,
            candidate.nickname or candidate.species_name, candidate.species_name,
            location or candidate.met_location_name, candidate.box_index,
            candidate.slot_index, baseline_member,
            already_observed, status, reason,
        )

    def observe(
        self,
        candidates: tuple[AcquisitionCandidate, ...],
        *,
        connected: bool,
        valid_snapshot: bool,
        run: NuzlockeRun | None,
        store: NuzlockeStore,
        classify: Classifier,
        classify_first_party: Classifier | None = None,
    ) -> tuple[PokemonAcquisitionEvent, ...]:
        if not connected:
            self._last_invalid_log_at = None
            self._invalid_snapshot_since = None
            self._invalid_snapshot_reported = False
            if self._reset_on_disconnect:
                self._connected = False
                self._baseline_ids.clear()
                self._run_id = run.run_id if run else None
                self._empty_party_baseline = False
            return ()
        if not valid_snapshot:
            now = time.monotonic()
            if self._invalid_snapshot_since is None:
                self._invalid_snapshot_since = now
            if (candidates and now - self._invalid_snapshot_since >= INVALID_SNAPSHOT_LOG_DELAY_SECONDS
                    and (self._last_invalid_log_at is None or now - self._last_invalid_log_at >= 30)):
                LOGGER.warning(
                    "Acquisition monitoring paused: invalid RAM snapshot; "
                    "%d candidate(s) withheld until a validated read arrives.", len(candidates),
                )
                self._last_invalid_log_at = now
                self._invalid_snapshot_reported = True
            for candidate in candidates:
                self._note_decision(candidate, "suppressed", "invalid acquisition snapshot", log=False)
            return ()

        if self._invalid_snapshot_reported:
            LOGGER.info("Acquisition RAM snapshot validated; monitoring resumed.")
            self._invalid_snapshot_reported = False
        self._invalid_snapshot_since = None
        self._last_invalid_log_at = None

        # Shinies have their own clause ledger and never become normal catch suggestions.
        candidates = tuple(candidate for candidate in candidates if not candidate.is_shiny)
        current = {candidate.stable_id for candidate in candidates}
        if not self._connected or (run.run_id if run else None) != self._run_id:
            if run:
                store.mark_pokemon_observed(run.run_id, sorted(current))
            for candidate in candidates:
                self._note_decision(
                    candidate, "suppressed", "initial baseline for this run",
                    baseline_member=True, already_observed=bool(run),
                )
            self._baseline_ids = current
            self._empty_party_baseline = not any(
                candidate.source_location == "PARTY" for candidate in candidates
            )
            self._run_id = run.run_id if run else None
            self._connected = True
            return ()

        newly_seen = current - self._baseline_ids
        newly_seen_party_ids = {
            candidate.stable_id
            for candidate in candidates
            if candidate.source_location == "PARTY" and candidate.stable_id in newly_seen
        }
        first_party_candidate_id = (
            next(
                (
                    candidate.stable_id
                    for candidate in candidates
                    if candidate.source_location == "PARTY"
                    and candidate.stable_id in newly_seen
                ),
                None,
            )
            if self._empty_party_baseline
            else None
        )
        if newly_seen_party_ids:
            self._empty_party_baseline = False
        if run is None:
            for candidate in candidates:
                if candidate.stable_id in newly_seen:
                    self._note_decision(candidate, "suppressed", "no active Nuzlocke run")
            self._baseline_ids = current
            return ()

        events = []
        for candidate in candidates:
            if candidate.stable_id not in newly_seen:
                continue
            already_observed = store.has_observed_pokemon(run.run_id, candidate.stable_id)
            LOGGER.info(
                "Acquisition observer received new source=%s id=%s species=%s nickname=%s "
                "met=%s level=%s baseline_member=False already_observed=%s",
                candidate.source_location, candidate.stable_id, candidate.species_name,
                candidate.nickname, candidate.met_location_name, candidate.met_level,
                already_observed,
            )
            if already_observed:
                self._note_decision(
                    candidate, "suppressed", "already observed or recorded",
                    baseline_member=False, already_observed=True,
                )
                continue
            classifier = (
                classify_first_party
                if candidate.stable_id == first_party_candidate_id and classify_first_party
                else classify
            )
            source, confidence, suggested_location_id = classifier(candidate, run)
            event = PokemonAcquisitionEvent(
                stable_id=candidate.stable_id,
                species_id=candidate.species_id,
                species_name=candidate.species_name,
                nickname=candidate.nickname,
                level=candidate.level,
                met_level=candidate.met_level,
                met_location_id=candidate.met_location_id,
                met_location_name=candidate.met_location_name,
                egg_location_id=candidate.egg_location_id,
                origin_game=candidate.origin_game,
                is_egg=candidate.is_egg,
                detected_at=datetime.now(UTC).isoformat(timespec="seconds"),
                source=source,
                confidence=confidence,
                suggested_location_id=suggested_location_id,
                source_location=candidate.source_location,
                suggested_location_name=(
                    run.encounters[suggested_location_id].location
                    if suggested_location_id in run.encounters
                    else None
                ),
            )
            if store.record_acquisition_event(run.run_id, event):
                events.append(event)
                self._note_decision(
                    candidate, "pending", "acquisition event recorded",
                    event.suggested_location_name,
                    baseline_member=False, already_observed=False,
                )
            else:
                self._note_decision(
                    candidate, "suppressed", "duplicate event rejected by store",
                    baseline_member=False, already_observed=True,
                )
        self._baseline_ids = current
        return tuple(events)

    def baseline_candidates(
        self,
        candidates: tuple[AcquisitionCandidate, ...],
        *,
        run: NuzlockeRun | None,
        store: NuzlockeStore,
    ) -> None:
        """Add a newly enabled source to the current baseline without events."""
        identities = {candidate.stable_id for candidate in candidates}
        if run and identities:
            store.mark_pokemon_observed(run.run_id, sorted(identities))
        for candidate in candidates:
            self._note_decision(
                candidate, "suppressed", "existing boxed Pokemon baselined",
                baseline_member=True, already_observed=bool(run),
            )
        self._baseline_ids.update(identities)


def pending_acquisition_events(run: NuzlockeRun | None) -> tuple[PokemonAcquisitionEvent, ...]:
    if run is None:
        return ()
    resolved = set(run.resolved_acquisition_ids)
    return tuple(event for event in run.acquisition_events if event.stable_id not in resolved)
