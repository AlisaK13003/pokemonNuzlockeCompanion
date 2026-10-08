from dataclasses import replace

import pytest
from test_nuzlocke_acquisition import _candidate

from pokemon_ev_tracker.core.nuzlocke.acquisition import (
    reconcile_initial_starter,
    reconcile_party_encounters,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.locations import classify_platinum_acquisition
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE


def test_first_party_member_records_starter_once_across_restart_and_reset(tmp_path):
    path = tmp_path / 'runs.json'
    store = NuzlockeStore(path)
    run = store.create_run('Fresh save', PLATINUM_NUZLOCKE_PROFILE)
    first = _candidate('starter', species_id=41, species_name='Zubat')
    result = reconcile_initial_starter((first,), connected=True, valid_snapshot=True, run=run, store=store)
    assert result[0].location == 'Starter'
    assert result[0].stable_id == first.stable_id
    assert reconcile_party_encounters((first,), connected=True, valid_snapshot=True,
                                     run=run, store=store, classify=classify_platinum_acquisition) == ()
    assert all(r.status == 'NOT_ENCOUNTERED' for r in run.encounters.values() if r.location == 'Route 201')
    store.update_encounter(run.run_id, replace(result[0], status='NOT_ENCOUNTERED', stable_id=None))
    restarted = NuzlockeStore(path)
    run = restarted.active_run
    second = _candidate('later')
    assert run.starter_observation_complete
    assert reconcile_initial_starter((second,), connected=True, valid_snapshot=True, run=run, store=restarted) == ()
    assert reconcile_party_encounters((second,), connected=True, valid_snapshot=True,
                                     run=run, store=restarted, classify=classify_platinum_acquisition)[0].location == 'Route 201'


def test_empty_invalid_and_disconnected_do_not_consume_starter_rule(tmp_path):
    store = NuzlockeStore(tmp_path / 'runs.json')
    run = store.create_run('Fresh', PLATINUM_NUZLOCKE_PROFILE)
    for candidates, connected, valid in [((), True, True), ((_candidate('one'),), False, True), ((_candidate('one'),), True, False)]:
        assert reconcile_initial_starter(candidates, connected=connected, valid_snapshot=valid, run=run, store=store) == ()
        assert not run.starter_observation_complete


def test_existing_party_consumes_rule_without_guessing_starter(tmp_path):
    store = NuzlockeStore(tmp_path / 'runs.json')
    run = store.create_run('Existing', PLATINUM_NUZLOCKE_PROFILE)
    assert reconcile_initial_starter((_candidate('one'), _candidate('two')), connected=True,
                                     valid_snapshot=True, run=run, store=store) == ()
    assert run.starter_observation_complete
    assert all(row.status == 'NOT_ENCOUNTERED' for row in run.encounters.values())


def test_failed_starter_save_rolls_back_one_time_flag(tmp_path, monkeypatch):
    store = NuzlockeStore(tmp_path / 'runs.json')
    run = store.create_run('Fresh', PLATINUM_NUZLOCKE_PROFILE)
    monkeypatch.setattr(store, 'save', lambda: (_ for _ in ()).throw(OSError('Disk full')))
    with pytest.raises(OSError):
        reconcile_initial_starter((_candidate('one'),), connected=True, valid_snapshot=True, run=run, store=store)
    assert not run.starter_observation_complete
    assert not run.observed_pokemon_ids
