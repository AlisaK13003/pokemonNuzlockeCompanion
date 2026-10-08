"""Independent C5 orchestration against real domain services and isolated stores."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from pokemon_ev_tracker.core.nuzlocke.acquisition import AcquisitionCandidate
from pokemon_ev_tracker.core.nuzlocke.acquisition_coordinator import (
    AcquisitionCoordinator,
    AcquisitionObservation,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.games.registry import default_game_provider


def candidate(identity, **fields):
    result = AcquisitionCandidate(identity, 396, "Starly", "", 5, 5, 0x10, "Route 201", 0, 12, False)
    return replace(result, **fields)


@pytest.fixture
def workflow(tmp_path):
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("C5", PLATINUM_NUZLOCKE_PROFILE)
    return AcquisitionCoordinator(default_game_provider(), store), store, run


def poll(flow, run, *, party=(), boxed=(), connected=True, valid=True, pc_valid=True,
         session="lua", publish=lambda result: None):
    baseline = flow.prepare_pc(boxed, ready=connected and pc_valid, session_id=session, run=run)
    events = flow.process(AcquisitionObservation(connected, valid, pc_valid, run, party, boxed), publish)
    return baseline, events


def test_starter_then_normal_catch_and_duplicate_snapshot_do_not_save_again(workflow):
    flow, store, run = workflow
    published = []
    starter = candidate("starter", species_id=387, species_name="Turtwig")
    poll(flow, run, party=(starter,), publish=published.append)
    assert published[-1].encounters[0].location == "Starter"
    assert run.starter_observation_complete
    capture = candidate("caught", met_location_id=0x11, met_location_name="Route 202")
    poll(flow, run, party=(starter, capture), publish=published.append)
    assert len(published[-1].encounters) == 1
    assert published[-1].encounters[0].stable_id == "caught"
    assert not run.acquisition_events
    save = Mock(wraps=store.save)
    store.save = save
    poll(flow, run, party=(starter, capture), publish=published.append)
    assert not published[-1].encounters
    assert not save.called


def test_full_party_capture_in_pc_and_party_to_pc_identity_dedup(workflow):
    flow, _, run = workflow
    party = tuple(candidate(f"member-{index}") for index in range(6))
    poll(flow, run, party=party)
    boxed_capture = candidate("boxed", source_location="BOX", box_index=1, slot_index=2)
    _, events = poll(flow, run, party=party, boxed=(boxed_capture,))
    assert [event.stable_id for event in events.boxed] == ["boxed"]
    assert events.boxed[0].stable_id not in run.resolved_acquisition_ids
    moved = replace(party[0], source_location="BOX", box_index=1, slot_index=3)
    _, events = poll(flow, run, party=party[1:], boxed=(boxed_capture, moved))
    assert not events.party and not events.boxed
    assert flow.pc_emitted_count == 1 and flow.pc_addition_count == 2


def test_repeated_identity_across_slots_records_one_pending_event(workflow):
    flow, _, run = workflow
    run.starter_observation_complete = True
    original = candidate("baseline")
    poll(flow, run, party=(original,))
    unknown = candidate("duplicate", met_location_id=0xFFFF, met_location_name=None)
    _, events = poll(flow, run, party=(original, unknown, unknown))
    assert len(events.party) == len(run.acquisition_events) == 1
    assert events.party[0].suggested_location_id is None
    assert not run.resolved_acquisition_ids


def test_shiny_clause_preserves_normal_location_and_is_deduplicated_across_sources(workflow):
    flow, _, run = workflow
    starter = candidate("starter")
    poll(flow, run, party=(starter,))
    original = dict(run.encounters)
    shiny = candidate("shiny", is_shiny=True)
    published = []
    poll(flow, run, party=(starter, shiny), publish=published.append)
    assert len(published[-1].party_shinies) == 1
    assert run.encounters == original and not run.acquisition_events
    moved = replace(shiny, source_location="BOX", box_index=1, slot_index=1)
    _, events = poll(flow, run, party=(starter,), boxed=(moved,), publish=published.append)
    assert not events.boxed and not published[-1].boxed_shinies
    assert len(run.shiny_encounters) == 1


@pytest.mark.parametrize("kind", ["invalid-party", "invalid-pc", "disconnect"])
def test_invalid_observations_do_not_consume_starter_or_emit(workflow, kind):
    flow, _, run = workflow
    unknown = candidate("unknown", met_location_id=0xFFFF)
    baseline, events = poll(
        flow, run, party=() if kind == "invalid-pc" else (unknown,),
        boxed=() if kind == "invalid-party" else (replace(unknown, source_location="BOX"),),
        valid=kind == "invalid-pc",
        pc_valid=kind == "invalid-party", connected=kind != "disconnect",
    )
    assert baseline == (0 if kind == "invalid-party" else None)
    assert not events.party and not events.boxed
    assert not run.starter_observation_complete and not run.observed_pokemon_ids
    assert flow.pc_tracking == (kind == "invalid-party")


def test_pc_disconnect_preserves_baseline_but_explicit_recovery_rebaselines(workflow):
    flow, _, run = workflow
    old = candidate("old", source_location="BOX")
    new = candidate("new", source_location="BOX")
    assert poll(flow, run, boxed=(old,))[0] == 1
    poll(flow, run, boxed=(), connected=False, pc_valid=False)
    assert flow.pc_tracking and flow.pc_baseline_count == 1
    _, events = poll(flow, run, boxed=(old, new))
    assert [event.stable_id for event in events.boxed] == ["new"]
    flow.reset_pc()
    assert not flow.pc_tracking and flow.pc_emitted_count == 0
    baseline, events = poll(flow, run, boxed=(old, new), session="new-lua")
    assert baseline == 2 and not events.boxed


def test_run_switch_baselines_and_captured_run_not_active_store_selection(workflow):
    flow, store, run = workflow
    old = candidate("old", source_location="BOX")
    poll(flow, run, boxed=(old,))
    other = store.create_run("other", PLATINUM_NUZLOCKE_PROFILE)
    new = candidate("new", source_location="BOX")
    _, events = poll(flow, other, boxed=(old, new))
    assert not events.boxed and not other.acquisition_events
    assert flow.pc_baseline_matches("lua", other.run_id)
    third = candidate("third", source_location="BOX")
    _, events = poll(flow, other, boxed=(old, new, third),
                     publish=lambda result: store.switch_run(run.run_id))
    assert store.active_run is run
    assert [event.stable_id for event in events.boxed] == ["third"]
    assert other.acquisition_events and not run.acquisition_events


def test_no_active_run_observes_baselines_without_events(workflow):
    flow, _, _ = workflow
    _, events = poll(flow, None, party=(candidate("one"),))
    assert not events.party and not events.boxed


def test_publication_before_observers_and_failure_skips_observation(workflow):
    flow, _, run = workflow
    first = candidate("first")
    observed = Mock(wraps=flow._party.observe)
    flow._party.observe = observed
    def publish(result):
        assert result.encounters[0].location == "Starter"
        assert not observed.called
        raise OSError("UI handler failed")
    with pytest.raises(OSError, match="UI handler failed"):
        poll(flow, run, party=(first,), publish=publish)
    assert not observed.called
    assert run.starter_observation_complete


def test_failed_starter_save_rolls_back_and_skips_publication(workflow, monkeypatch):
    flow, store, run = workflow
    monkeypatch.setattr(store, "save", Mock(side_effect=OSError("save failed")))
    publish = Mock()
    with pytest.raises(OSError, match="save failed"):
        flow.process(AcquisitionObservation(True, True, False, run, (candidate("one"),), ()), publish)
    assert not publish.called and not run.starter_observation_complete


def test_pc_baseline_failure_leaves_tracking_unarmed(workflow, monkeypatch):
    flow, store, run = workflow
    monkeypatch.setattr(store, "save", Mock(side_effect=OSError("baseline failed")))
    with pytest.raises(OSError, match="baseline failed"):
        flow.prepare_pc((candidate("one", source_location="BOX"),), ready=True, session_id="lua", run=run)
    assert not flow.pc_tracking and flow.pc_baseline_count == 0


def test_occupied_location_stays_review_required(workflow):
    flow, _, run = workflow
    run.starter_observation_complete = True
    original = candidate("original")
    poll(flow, run, party=(original,))
    caught = tuple(row for row in run.encounters.values() if row.status == "CAUGHT")
    replacement = candidate("replacement")
    _, events = poll(flow, run, party=(original, replacement))
    assert len(events.party) == 1
    assert events.party[0].stable_id not in run.resolved_acquisition_ids
    assert tuple(row for row in run.encounters.values() if row.status == "CAUGHT") == caught
