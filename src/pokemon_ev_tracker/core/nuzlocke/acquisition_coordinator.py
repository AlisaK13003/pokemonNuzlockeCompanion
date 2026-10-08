"""Run-scoped acquisition sequencing and PC baseline ownership, independent of Qt."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pokemon_ev_tracker.core.nuzlocke.acquisition import (
    AcquisitionCandidate,
    PartyAcquisitionObserver,
    reconcile_initial_starter,
    reconcile_party_encounters,
    reconcile_shiny_encounters,
)
from pokemon_ev_tracker.core.nuzlocke.models import (
    EncounterRecord,
    NuzlockeRun,
    PokemonAcquisitionEvent,
    ShinyEncounter,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.provider import GameProvider

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcquisitionObservation:
    connected: bool
    party_valid: bool
    pc_valid: bool
    run: NuzlockeRun | None
    party: tuple[AcquisitionCandidate, ...]
    boxed: tuple[AcquisitionCandidate, ...]


@dataclass(frozen=True)
class ReconciledEncounters:
    run: NuzlockeRun | None
    party: tuple[AcquisitionCandidate, ...]
    encounters: tuple[EncounterRecord, ...]
    party_shinies: tuple[ShinyEncounter, ...]
    boxed_shinies: tuple[ShinyEncounter, ...]


@dataclass(frozen=True)
class ObservedAcquisitions:
    party: tuple[PokemonAcquisitionEvent, ...]
    boxed: tuple[PokemonAcquisitionEvent, ...]


class AcquisitionCoordinator:
    """Own observer baselines; existing domain services own persistence policy.

    prepare_pc preserves its original error boundary outside process. The
    synchronous reconciliation handler publishes UI notices before observation;
    failure propagates, preserving partial progress and store rollback behavior.
    No snapshot is retained and no user confirmation is performed here.
    """

    def __init__(self, provider: GameProvider, store: NuzlockeStore) -> None:
        self._provider = provider
        self._store = store
        self._party = PartyAcquisitionObserver()
        self.reset_pc()

    def reset_pc(self) -> None:
        self._pc = PartyAcquisitionObserver(reset_on_disconnect=False)
        self._tracking = False
        self._baseline_key = None
        self._baseline_ids: set[str] = set()
        self._post_baseline_ids: set[str] = set()
        self._emitted_ids: set[str] = set()

    @property
    def pc_tracking(self) -> bool:
        return self._tracking

    @property
    def pc_baseline_count(self) -> int:
        return len(self._baseline_ids)

    @property
    def pc_addition_count(self) -> int:
        return len(self._post_baseline_ids)

    @property
    def pc_emitted_count(self) -> int:
        return len(self._emitted_ids)

    @property
    def party_decision(self) -> dict | None:
        return self._party.last_decision

    @property
    def pc_decision(self) -> dict | None:
        return self._pc.last_decision

    def pc_baseline_matches(self, session_id: str | None, run_id: str) -> bool:
        return self._tracking and self._baseline_key == (session_id, run_id)

    def prepare_pc(
        self, candidates: tuple[AcquisitionCandidate, ...], *, ready: bool,
        session_id: str | None, run: NuzlockeRun | None,
    ) -> int | None:
        if not ready:
            return None
        key = (session_id, run.run_id if run else None)
        established = None
        if not self._tracking or key != self._baseline_key:
            self._pc.baseline_candidates(candidates, run=run, store=self._store)
            self._baseline_ids = {candidate.stable_id for candidate in candidates}
            self._post_baseline_ids.clear()
            self._emitted_ids.clear()
            self._tracking = True
            self._baseline_key = key
            established = len(self._baseline_ids)
            LOGGER.debug(
                "PC baseline established: Lua run=%s Nuzlocke run=%s occupied=%s ids=%s",
                session_id, key[1], established, sorted(self._baseline_ids),
            )
        self._post_baseline_ids.update(
            candidate.stable_id for candidate in candidates
            if candidate.stable_id not in self._baseline_ids
        )
        return established

    def process(
        self, observation: AcquisitionObservation,
        on_reconciled: Callable[[ReconciledEncounters], None],
    ) -> ObservedAcquisitions:
        connected, run = observation.connected, observation.run
        party, boxed = observation.party, observation.boxed
        starter = reconcile_initial_starter(
            party, connected=connected, valid_snapshot=observation.party_valid,
            run=run, store=self._store,
        )
        shinies = reconcile_shiny_encounters(
            party if observation.party_valid else (), connected=connected,
            valid_snapshot=observation.party_valid, run=run, store=self._store,
            classify=self._provider.classify_acquisition,
        )
        boxed_shinies = reconcile_shiny_encounters(
            boxed, connected=connected, valid_snapshot=observation.pc_valid,
            run=run, store=self._store, classify=self._provider.classify_acquisition,
        )
        reconciled = starter + reconcile_party_encounters(
            party, connected=connected, valid_snapshot=observation.party_valid,
            run=run, store=self._store, classify=self._provider.classify_acquisition,
        )
        on_reconciled(ReconciledEncounters(run, party, reconciled, shinies, boxed_shinies))
        party_events = self._party.observe(
            party, connected=connected, valid_snapshot=observation.party_valid,
            run=run, store=self._store, classify=self._provider.classify_acquisition,
            classify_first_party=self._classify_first_party,
        )
        boxed_events = self._pc.observe(
            boxed, connected=connected, valid_snapshot=observation.pc_valid,
            run=run, store=self._store, classify=self._provider.classify_acquisition,
        )
        self._emitted_ids.update(event.stable_id for event in boxed_events)
        return ObservedAcquisitions(party_events, boxed_events)

    def _classify_first_party(self, candidate, run):
        return self._provider.classify_acquisition(candidate, run, first_party_member=True)
