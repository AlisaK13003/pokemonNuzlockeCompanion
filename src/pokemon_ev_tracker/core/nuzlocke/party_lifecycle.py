"""Own raw-party lifecycle observation; UI handlers own dialogs and persistence."""

from collections.abc import Callable

from pokemon_ev_tracker.core.nuzlocke.death_detection import (
    DeathCandidate,
    PartyHpObserver,
    PartyHpSample,
)
from pokemon_ev_tracker.core.nuzlocke.party_snapshot import frame_number, valid_death_snapshot
from pokemon_ev_tracker.core.nuzlocke.run_prompt import NoRunPromptObserver
from pokemon_ev_tracker.core.nuzlocke.wipe_detection import PartyWipeCandidate, PartyWipeObserver
from pokemon_ev_tracker.games.provider import GameProvider


class PartyLifecycleCoordinator:
    """One owner for HP baselines, wipe arming and no-run prompt suppression.

    Handlers execute synchronously in the existing order. In particular, an
    accepted wipe calls run_ended before no-run prompting is evaluated. The
    coordinator neither retains snapshots nor owns the store or Qt scheduling.
    """

    def __init__(self, provider: GameProvider, *, clock: Callable[[], float]) -> None:
        self._provider = provider
        self._clock = clock
        self._hp = PartyHpObserver()
        self._wipe = PartyWipeObserver()
        self._prompt = NoRunPromptObserver()

    def run_ended(self) -> None:
        """Reset transition baselines and preserve session prompt suppression."""
        self._hp.reset()
        self._wipe.reset()
        self._prompt.suppress_for_session()

    def process(
        self,
        party_state,
        party_payload,
        *,
        connected: bool,
        run_id: str | None,
        stale_after: float,
        prompt_enabled: bool,
        on_deaths: Callable[[str | None, tuple[DeathCandidate, ...]], None],
        on_wipe: Callable[[PartyWipeCandidate], None],
        on_no_run: Callable[[], None],
    ) -> None:
        valid = valid_death_snapshot(
            party_state, party_payload, stale_after, clock=self._clock)
        members = tuple(
            PartyHpSample(
                stable_id=pokemon.stable_id,
                species=pokemon.species,
                nickname=(self._provider.party_acquisition_candidate(pokemon).nickname
                          if self._provider.capabilities.nuzlocke else pokemon.nickname),
                level=pokemon.level,
                current_hp=pokemon.current_hp,
                met_location_id=pokemon.met_location_id,
                met_location_name=pokemon.met_location_name,
                met_level=pokemon.met_level,
                origin_game=pokemon.origin_game,
            )
            for pokemon in getattr(party_state, "pokemon", ())
        )
        payload = party_payload.payload if party_payload is not None else {}
        identity = tuple(payload.get(key) for key in ("run_id", "core", "domain", "pointer_value"))
        frame = frame_number(payload)
        deaths = self._hp.observe(
            members, connected=connected, valid_snapshot=valid, run_id=run_id,
            stream_identity=identity, frame=frame,
        )
        if deaths:
            on_deaths(run_id, deaths)
        wipe = self._wipe.observe(
            members, connected=connected, valid_snapshot=valid, run_id=run_id,
            stream_identity=identity, frame=frame,
        )
        if wipe:
            on_wipe(wipe)
        if self._prompt.observe(
            connected=connected, valid_snapshot=valid, party_size=len(members),
            active_run=run_id is not None, stream_identity=identity, frame=frame,
            enabled=prompt_enabled,
        ):
            on_no_run()
