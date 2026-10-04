from __future__ import annotations

from dataclasses import replace

from pokemon_ev_tracker.core.nuzlocke.acquisition import (
    AcquisitionCandidate,
    PartyAcquisitionObserver,
    pending_acquisition_events,
)
from pokemon_ev_tracker.core.nuzlocke.models import PokemonAcquisitionEvent
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.locations import (
    classify_platinum_acquisition,
    platinum_met_location_name,
    platinum_nuzlocke_location_id,
)
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE


def _candidate(
    stable_id: str,
    species_id: int = 396,
    species_name: str = "Starly",
    met_location_id: int = 0x10,
    met_level: int = 3,
    origin_game: int = 12,
    **changes,
) -> AcquisitionCandidate:
    values = {
        "stable_id": stable_id,
        "species_id": species_id,
        "species_name": species_name,
        "nickname": "",
        "level": met_level,
        "met_level": met_level,
        "met_location_id": met_location_id,
        "met_location_name": platinum_met_location_name(met_location_id),
        "egg_location_id": 0,
        "origin_game": origin_game,
        "is_egg": False,
    }
    values.update(changes)
    return AcquisitionCandidate(**values)


def _event(
    candidate: AcquisitionCandidate,
    suggestion: str | None,
    *,
    source: str = "WILD",
    confidence: str = "HIGH",
) -> PokemonAcquisitionEvent:
    return PokemonAcquisitionEvent(
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
        detected_at="2026-09-26T12:00:00+00:00",
        source=source,
        confidence=confidence,
        suggested_location_id=suggestion,
        suggested_location_name=(
            next(
                item.name
                for item in PLATINUM_NUZLOCKE_PROFILE.locations
                if item.location_id == suggestion
            )
            if suggestion
            else None
        ),
    )


def _location_id(name: str) -> str:
    return next(
        location.location_id
        for location in PLATINUM_NUZLOCKE_PROFILE.locations
        if location.name == name
    )


def test_pk4_location_mapping_uses_platinum_names_and_normalized_rows() -> None:
    route_202_id = _location_id("Route 202")
    gate_id = _location_id("Oreburgh Gate")

    assert platinum_met_location_name(0x11) == "Route 202"
    assert platinum_nuzlocke_location_id(0x11, PLATINUM_NUZLOCKE_PROFILE) == route_202_id
    assert platinum_nuzlocke_location_id(0x3B, PLATINUM_NUZLOCKE_PROFILE) == gate_id
    assert platinum_met_location_name(0xFFFF) is None


def test_met_location_names_are_preserved_without_ineligible_default_rows(tmp_path) -> None:
    run = NuzlockeStore(tmp_path / "runs.json").create_run(
        "Platinum", PLATINUM_NUZLOCKE_PROFILE
    )

    assert platinum_met_location_name(0x02) == "Sandgem Town"
    assert platinum_met_location_name(0x06) == "Jubilife City"
    assert platinum_nuzlocke_location_id(0x02, PLATINUM_NUZLOCKE_PROFILE) is None
    assert platinum_nuzlocke_location_id(0x06, PLATINUM_NUZLOCKE_PROFILE) is None
    assert classify_platinum_acquisition(
        _candidate("pid:sandgem", met_location_id=0x02), run
    ) == ("UNKNOWN", "LOW", None)
    assert classify_platinum_acquisition(
        _candidate("pid:jubilife", met_location_id=0x06), run
    ) == ("UNKNOWN", "LOW", None)


def test_water_encounter_cities_have_default_rows(tmp_path) -> None:
    run = NuzlockeStore(tmp_path / "runs.json").create_run(
        "Platinum", PLATINUM_NUZLOCKE_PROFILE
    )
    for met_id, name in (
        (0x01, "Twinleaf Town"),
        (0x05, "Celestic Town"),
        (0x07, "Canalave City"),
        (0x09, "Eterna City"),
        (0x0B, "Pastoria City"),
        (0x0D, "Sunyshore City"),
        (0x0F, "Pokémon League"),
    ):
        assert classify_platinum_acquisition(
            _candidate(f"pid:{met_id}", met_location_id=met_id), run
        ) == ("WILD", "HIGH", _location_id(name))


def test_observer_baselines_then_detects_new_identity_once(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    first = _candidate("pid:one")
    second = _candidate("pid:two", species_id=443, species_name="Gible")

    assert (
        observer.observe(
            (first, second),
            connected=True,
            valid_snapshot=True,
            run=run,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )
    assert store.has_observed_pokemon(run.run_id, first.stable_id)

    assert (
        observer.observe(
            (second, first),
            connected=True,
            valid_snapshot=True,
            run=run,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )
    changed = replace(second, level=9, nickname="wingy")
    assert (
        observer.observe(
            (changed, first),
            connected=True,
            valid_snapshot=True,
            run=run,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )

    new = _candidate(
        "pid:three", species_id=403, species_name="Shinx", met_location_id=0x11, met_level=4
    )
    events = observer.observe(
        (changed, first, new),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    )
    assert len(events) == 1
    assert events[0].stable_id == "pid:three"
    assert events[0].source == "WILD"
    assert events[0].suggested_location_id == _location_id("Route 202")
    assert (
        observer.observe(
            (new, first, changed),
            connected=True,
            valid_snapshot=True,
            run=run,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )

    reloaded = NuzlockeStore(tmp_path / "runs.json").active_run
    assert reloaded is not None
    assert reloaded.observed_pokemon_ids
    assert reloaded.acquisition_events[0] == events[0]
    assert pending_acquisition_events(reloaded) == events
    restarted_observer = PartyAcquisitionObserver()
    assert (
        restarted_observer.observe(
            (changed, first, new),
            connected=True,
            valid_snapshot=True,
            run=reloaded,
            store=NuzlockeStore(tmp_path / "runs.json"),
            classify=classify_platinum_acquisition,
        )
        == ()
    )


def test_startup_reconnect_and_no_run_never_create_acquisition_events(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    observer = PartyAcquisitionObserver()
    existing = _candidate("pid:existing")
    assert (
        observer.observe(
            (existing,),
            connected=True,
            valid_snapshot=True,
            run=None,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )

    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    assert (
        observer.observe(
            (existing,),
            connected=True,
            valid_snapshot=True,
            run=run,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )
    unseen_during_disconnect = _candidate("pid:disconnect")
    assert (
        observer.observe(
            (existing,),
            connected=False,
            valid_snapshot=False,
            run=run,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )
    assert (
        observer.observe(
            (existing, unseen_during_disconnect),
            connected=True,
            valid_snapshot=True,
            run=run,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )
    assert not run.acquisition_events
    assert store.has_observed_pokemon(run.run_id, unseen_during_disconnect.stable_id)


def test_invalid_connected_snapshot_does_not_baseline_away_a_later_valid_party_change(
    tmp_path,
) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    initial = _candidate("pid:initial")
    observer.observe(
        (initial,),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    )
    arrived = _candidate("pid:arrived", species_id=403, species_name="Shinx", met_location_id=0x11)
    assert (
        observer.observe(
            (initial, arrived),
            connected=True,
            valid_snapshot=False,
            run=run,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )
    events = observer.observe(
        (initial, arrived),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    )
    assert len(events) == 1 and events[0].stable_id == arrived.stable_id


def test_acquisition_history_is_per_run_and_baselined_on_run_switch(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    first = store.create_run("First", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    starter = _candidate("pid:starter", species_id=393, species_name="Piplup", met_level=5)
    observer.observe(
        (starter,),
        connected=True,
        valid_snapshot=True,
        run=first,
        store=store,
        classify=classify_platinum_acquisition,
    )
    caught = _candidate(
        "pid:caught", species_id=403, species_name="Shinx", met_location_id=0x11, met_level=4
    )
    observer.observe(
        (starter, caught),
        connected=True,
        valid_snapshot=True,
        run=first,
        store=store,
        classify=classify_platinum_acquisition,
    )

    second = store.create_run("Second", PLATINUM_NUZLOCKE_PROFILE)
    assert (
        observer.observe(
            (starter, caught),
            connected=True,
            valid_snapshot=True,
            run=second,
            store=store,
            classify=classify_platinum_acquisition,
        )
        == ()
    )
    assert first.acquisition_events[0].stable_id == caught.stable_id
    assert second.acquisition_events == []
    assert caught.stable_id in second.observed_pokemon_ids


def test_starter_route_201_does_not_consume_route_and_later_catch_maps_normally(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    starter = _candidate("pid:piplup", species_id=393, species_name="Piplup", met_level=5)
    source, confidence, suggested = classify_platinum_acquisition(starter, run)
    assert (source, confidence, suggested) == ("STARTER", "MEDIUM", "platinum-starter")
    store.record_acquisition_event(
        run.run_id, _event(starter, suggested, source=source, confidence=confidence)
    )
    store.accept_acquisition(run.run_id, starter.stable_id, suggested)

    starter_record = run.encounters["platinum-starter"]
    route_record = run.encounters[_location_id("Route 201")]
    assert (starter_record.status, starter_record.species, starter_record.level) == (
        "CAUGHT",
        "Piplup",
        5,
    )
    assert route_record.status == "NOT_ENCOUNTERED"

    starly = _candidate("pid:starly", species_id=396, species_name="Starly", met_level=3)
    source, confidence, suggested = classify_platinum_acquisition(starly, run)
    assert (source, confidence, suggested) == ("WILD", "HIGH", _location_id("Route 201"))
    store.record_acquisition_event(run.run_id, _event(starly, suggested))
    store.accept_acquisition(run.run_id, starly.stable_id, suggested)
    assert run.encounters[_location_id("Route 201")].species == "Starly"
    assert run.encounters["platinum-starter"].species == "Piplup"


def test_first_party_member_after_empty_baseline_is_suggested_as_starter(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    assert observer.observe(
        (),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    ) == ()

    # Some Platinum RAM records do not carry the level/origin metadata the old
    # starter heuristic expected, even though this is the first party member.
    starter = _candidate(
        "pid:piplup",
        species_id=393,
        species_name="Piplup",
        met_level=4,
        origin_game=0,
    )
    events = observer.observe(
        (starter,),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
        classify_first_party=lambda candidate, active_run: classify_platinum_acquisition(
            candidate, active_run, first_party_member=True
        ),
    )

    assert len(events) == 1
    assert (events[0].source, events[0].suggested_location_id) == (
        "STARTER",
        "platinum-starter",
    )

    later_catch = _candidate("pid:starly", species_id=396, species_name="Starly")
    later_events = observer.observe(
        (starter, later_catch),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
        classify_first_party=lambda candidate, active_run: classify_platinum_acquisition(
            candidate, active_run, first_party_member=True
        ),
    )
    assert len(later_events) == 1
    assert later_events[0].source != "STARTER"


def test_full_party_box_catch_uses_met_location_and_moves_never_duplicate(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    party = tuple(_candidate(f"pid:party-{index}") for index in range(6))
    existing_boxed = _candidate("pid:old-boxed", source_location="BOX")
    observer.observe(
        (*party, existing_boxed),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    )

    caught = _candidate(
        "pid:abra",
        species_id=63,
        species_name="Abra",
        met_location_id=0x12,
        met_level=6,
        source_location="BOX",
    )
    events = observer.observe(
        (*party, existing_boxed, caught),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    )

    assert len(events) == 1
    assert events[0].species_name == "Abra"
    assert events[0].level == 6
    assert events[0].met_location_name == "Route 203"
    assert events[0].suggested_location_id == _location_id("Route 203")
    assert events[0].source_location == "BOX"

    for moved in (
        replace(caught, source_location="PARTY"),
        replace(caught, source_location="BOX"),
    ):
        assert observer.observe(
            (*party, existing_boxed, moved),
            connected=True,
            valid_snapshot=True,
            run=run,
            store=store,
            classify=classify_platinum_acquisition,
        ) == ()

    reloaded_store = NuzlockeStore(tmp_path / "runs.json")
    assert reloaded_store.active_run.acquisition_events[0].source_location == "BOX"
    restarted = PartyAcquisitionObserver()
    assert restarted.observe(
        (*party, replace(caught, source_location="PARTY")),
        connected=True,
        valid_snapshot=True,
        run=reloaded_store.active_run,
        store=reloaded_store,
        classify=classify_platinum_acquisition,
    ) == ()


def test_box_to_party_move_is_suppressed_across_separate_observers(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    party_observer = PartyAcquisitionObserver()
    box_observer = PartyAcquisitionObserver()
    party = tuple(_candidate(f"pid:party-{index}") for index in range(6))
    boxed = _candidate(
        "pid:full-party-catch", species_id=63, species_name="Abra",
        met_location_id=0x12, met_level=6, source_location="BOX",
    )
    party_observer.observe(
        party, connected=True, valid_snapshot=True, run=run, store=store,
        classify=classify_platinum_acquisition,
    )
    box_observer.observe(
        (), connected=True, valid_snapshot=True, run=run, store=store,
        classify=classify_platinum_acquisition,
    )

    box_events = box_observer.observe(
        (boxed,), connected=True, valid_snapshot=True, run=run, store=store,
        classify=classify_platinum_acquisition,
    )
    assert len(box_events) == 1
    assert box_events[0].source_location == "BOX"
    assert box_observer.last_decision["status"] == "pending"
    assert box_observer.last_decision["already_observed"] is False

    party_events = party_observer.observe(
        (*party[:5], replace(boxed, source_location="PARTY")),
        connected=True, valid_snapshot=True, run=run, store=store,
        classify=classify_platinum_acquisition,
    )
    assert party_events == ()
    assert len(run.acquisition_events) == 1
    assert party_observer.last_decision["status"] == "suppressed"
    assert party_observer.last_decision["reason"] == "already observed or recorded"


def test_box_observer_preserves_baseline_across_transient_disconnect(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver(reset_on_disconnect=False)
    existing = _candidate("pid:existing", source_location="BOX")
    caught = _candidate(
        "pid:caught-while-disconnected", species_id=63, species_name="Abra",
        met_location_id=0x12, met_level=6, source_location="BOX",
    )
    observer.observe(
        (existing,), connected=True, valid_snapshot=True, run=run, store=store,
        classify=classify_platinum_acquisition,
    )
    observer.observe(
        (), connected=False, valid_snapshot=False, run=run, store=store,
        classify=classify_platinum_acquisition,
    )

    events = observer.observe(
        (existing, caught), connected=True, valid_snapshot=True, run=run,
        store=store, classify=classify_platinum_acquisition,
    )

    assert len(events) == 1
    assert events[0].stable_id == caught.stable_id
    assert events[0].source_location == "BOX"


def test_party_to_box_move_does_not_create_a_second_acquisition(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    moved = _candidate("pid:moved", 403, "Shinx", 0x11, 4)
    remaining_party = tuple(_candidate(f"pid:party-{index}") for index in range(5))
    observer.observe(
        (*remaining_party, moved),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    )

    assert observer.observe(
        (*remaining_party, replace(moved, source_location="BOX")),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    ) == ()


def test_known_boxed_identity_is_suppressed_after_save_state_rollback(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    party = tuple(_candidate(f"pid:party-{index}") for index in range(6))
    boxed = _candidate("pid:rollback", 63, "Abra", 0x12, 6, source_location="BOX")
    observer.observe(
        party,
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    )
    detected = observer.observe(
        (*party, boxed),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    )
    assert len(detected) == 1

    after_rollback = PartyAcquisitionObserver()
    assert after_rollback.observe(
        (*party, replace(boxed, source_location="PARTY")),
        connected=True,
        valid_snapshot=True,
        run=store.active_run,
        store=store,
        classify=classify_platinum_acquisition,
    ) == ()


def test_newly_enabled_box_source_is_baselined_without_false_encounters(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    party_member = _candidate("pid:party")
    observer.observe(
        (party_member,),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    )
    already_boxed = _candidate("pid:preexisting-box", source_location="BOX")
    observer.baseline_candidates((already_boxed,), run=run, store=store)

    assert observer.observe(
        (party_member, already_boxed),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
    ) == ()
    assert store.has_observed_pokemon(run.run_id, already_boxed.stable_id)


def test_box_candidate_does_not_receive_first_party_override(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    def classify(_candidate, _run):
        return "WILD", "HIGH", "route"

    def first_party(_candidate, _run):
        return "STARTER", "HIGH", "platinum-starter"
    observer.observe(
        (),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify,
    )
    boxed = _candidate("pid:boxed-first", source_location="BOX")
    boxed_event = observer.observe(
        (boxed,),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify,
        classify_first_party=first_party,
    )
    assert boxed_event[0].source == "WILD"

    starter = _candidate("pid:first-party")
    party_event = observer.observe(
        (boxed, starter),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify,
        classify_first_party=first_party,
    )
    assert party_event[0].source == "STARTER"


def test_first_party_starter_assumption_is_disabled_for_populated_baseline(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    observer = PartyAcquisitionObserver()
    existing = _candidate("pid:existing", species_id=393, species_name="Piplup")
    observer.observe(
        (existing,),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
        classify_first_party=lambda candidate, active_run: classify_platinum_acquisition(
            candidate, active_run, first_party_member=True
        ),
    )

    later = _candidate("pid:later", species_id=393, species_name="Piplup")
    events = observer.observe(
        (existing, later),
        connected=True,
        valid_snapshot=True,
        run=run,
        store=store,
        classify=classify_platinum_acquisition,
        classify_first_party=lambda candidate, active_run: classify_platinum_acquisition(
            candidate, active_run, first_party_member=True
        ),
    )

    assert len(events) == 1
    assert events[0].source != "STARTER"
    assert events[0].suggested_location_id == _location_id("Route 201")


def test_gible_route_201_is_not_misclassified_as_starter(tmp_path) -> None:
    gible = _candidate("pid:gible", species_id=443, species_name="Gible", met_level=5)
    run = NuzlockeStore(tmp_path / "runs.json").create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    assert classify_platinum_acquisition(gible, run) == (
        "UNKNOWN",
        "MEDIUM",
        _location_id("Route 201"),
    )


def test_nonstandard_route_201_pokemon_stays_manual_while_starter_is_empty(tmp_path) -> None:
    run = NuzlockeStore(tmp_path / "runs.json").create_run(
        "Randomized Platinum", PLATINUM_NUZLOCKE_PROFILE
    )
    randomized_starter = _candidate(
        "pid:random-starter", species_id=25, species_name="Pikachu", met_level=5
    )
    assert classify_platinum_acquisition(randomized_starter, run) == (
        "UNKNOWN",
        "MEDIUM",
        _location_id("Route 201"),
    )


def test_egg_trade_and_unknown_locations_are_never_auto_wild(tmp_path) -> None:
    run = NuzlockeStore(tmp_path / "runs.json").create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    egg = _candidate("pid:egg", is_egg=True, egg_location_id=5)
    trade = _candidate("pid:trade", origin_game=11)
    unknown = _candidate("pid:unknown", met_location_id=0xFFFF)
    event_gift = _candidate("pid:event", met_location_id=0x3F)

    assert classify_platinum_acquisition(egg, run)[0] == "EGG"
    assert classify_platinum_acquisition(trade, run)[0] == "TRADE"
    assert classify_platinum_acquisition(unknown, run) == ("UNKNOWN", "LOW", None)
    assert classify_platinum_acquisition(event_gift, run) == (
        "STATIC",
        "MEDIUM",
        None,
    )
    for met_id in (0x33, 0x3C, 0x4F, 0x55, 0x56, 0x57, 0x58, 0x59, 0x75):
        assert classify_platinum_acquisition(
            _candidate(f"pid:static-{met_id}", met_location_id=met_id), run
        ) == ("STATIC", "MEDIUM", None)
    for met_id in (0x08, 0x0A, 0x5E):
        assert classify_platinum_acquisition(
            _candidate(f"pid:gift-{met_id}", met_location_id=met_id), run
        ) == ("GIFT", "MEDIUM", None)


def test_occupied_encounter_is_not_overwritten_and_extra_is_separate(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    route_id = _location_id("Route 202")
    existing = run.encounters[route_id]
    from pokemon_ev_tracker.core.nuzlocke.models import EncounterRecord

    store.update_encounter(
        run.run_id,
        EncounterRecord(route_id, existing.location, "CAUGHT", "Bidoof", "", 3),
    )
    candidate = _candidate("pid:shinx", 403, "Shinx", 0x11, 4)
    store.record_acquisition_event(run.run_id, _event(candidate, route_id))

    try:
        store.accept_acquisition(run.run_id, candidate.stable_id, route_id)
    except ValueError as error:
        assert "already has an encounter" in str(error)
    else:
        raise AssertionError("Occupied route should not accept an encounter")
    assert run.encounters[route_id].species == "Bidoof"

    extra = store.accept_acquisition(run.run_id, candidate.stable_id, route_id, mode="extra")
    assert extra.location.endswith("(extra)")
    assert extra.species == "Shinx"
    assert candidate.stable_id in run.resolved_acquisition_ids


def test_clear_or_ignore_suggestion_is_persisted_without_changing_encounters(tmp_path) -> None:
    path = tmp_path / "runs.json"
    store = NuzlockeStore(path)
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    candidate = _candidate("pid:ignored", 403, "Shinx", 0x11, 4)
    store.record_acquisition_event(run.run_id, _event(candidate, _location_id("Route 202")))
    store.resolve_acquisition(run.run_id, candidate.stable_id)

    loaded = NuzlockeStore(path).active_run
    assert loaded is not None
    assert pending_acquisition_events(loaded) == ()
    assert loaded.encounters[_location_id("Route 202")].status == "NOT_ENCOUNTERED"


def test_replace_is_explicit_and_persists_auto_setting(tmp_path) -> None:
    path = tmp_path / "runs.json"
    store = NuzlockeStore(path)
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    route_id = _location_id("Route 202")
    from pokemon_ev_tracker.core.nuzlocke.models import EncounterRecord

    store.update_encounter(
        run.run_id,
        EncounterRecord(route_id, "Route 202", "CAUGHT", "Bidoof", "", 3),
    )
    candidate = _candidate("pid:replace", 403, "Shinx", 0x11, 4)
    store.record_acquisition_event(run.run_id, _event(candidate, route_id))
    store.set_auto_record_unambiguous(run.run_id, True)
    replaced = store.accept_acquisition(run.run_id, candidate.stable_id, route_id, mode="replace")

    loaded = NuzlockeStore(path).active_run
    assert replaced.species == "Shinx"
    assert loaded is not None and loaded.auto_record_unambiguous_encounters
    assert loaded.encounters[route_id].species == "Shinx"
