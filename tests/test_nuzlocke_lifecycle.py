"""Lifecycle migration, run isolation, and conservative wipe detection."""

import json

import pytest

from pokemon_ev_tracker.core.nuzlocke.death_detection import PartyHpSample
from pokemon_ev_tracker.core.nuzlocke.models import RunStatus
from pokemon_ev_tracker.core.nuzlocke.run_prompt import NoRunPromptObserver
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.core.nuzlocke.wipe_detection import PartyWipeObserver
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE as PROFILE


def sample(identity: str, hp: int) -> PartyHpSample:
    return PartyHpSample(identity, "Shinx", identity, 12, hp, 1, "Route 202")


def observe(observer, members, *, run="run", stream="stream", frame=1,
            connected=True, valid=True):
    return observer.observe(tuple(members), connected=connected, valid_snapshot=valid,
                            run_id=run, stream_identity=stream, frame=frame)


def test_wipe_only_after_same_party_conscious_to_zero_and_rearms():
    observer = PartyWipeObserver()
    assert observe(observer, [sample("a", 0)], frame=1) is None
    assert observe(observer, [sample("a", 0)], frame=2) is None
    assert observe(observer, [sample("a", 5)], frame=3) is None
    wipe = observe(observer, [sample("a", 0)], frame=4)
    assert wipe.run_id == "run"
    assert [member.stable_id for member in wipe.members] == ["a"]
    assert observe(observer, [sample("a", 0)], frame=5) is None
    assert observe(observer, [sample("a", 3)], frame=6) is None
    assert observe(observer, [sample("a", 0)], frame=7) is not None


@pytest.mark.parametrize("change", [
    {"members": []},
    {"members": [sample("a", 0), sample("c", 0)]},
    {"members": [sample("a", 0), sample("b", 0)], "stream": "new"},
    {"members": [sample("a", 0), sample("b", 0)], "run": "other"},
    {"members": [sample("a", 0), sample("b", 0)], "frame": 0},
    {"members": [sample("a", 0), sample("b", 0)], "valid": False},
    {"members": [sample("a", 0), sample("b", 0)], "connected": False},
])
def test_wipe_rebaselines_incompatible_snapshots(change):
    observer = PartyWipeObserver()
    observe(observer, [sample("a", 4), sample("b", 3)], frame=1)
    assert observe(observer, **change) is None


def test_wipe_reorder_partial_faint_and_party_removal():
    observer = PartyWipeObserver()
    observe(observer, [sample("a", 4), sample("b", 3)], frame=1)
    assert observe(observer, [sample("b", 3), sample("a", 0)], frame=2) is None
    assert observe(observer, [sample("b", 0)], frame=3) is None
    assert observe(observer, [sample("b", 0)], frame=4) is None
    observe(observer, [sample("b", 2)], frame=5)
    assert observe(observer, [sample("b", 0)], frame=6) is not None


def test_six_member_wipe_and_invalid_hp():
    observer = PartyWipeObserver()
    observe(observer, [sample(str(i), 10) for i in range(6)], frame=1)
    assert observe(observer, [sample(str(i), 0) for i in range(5)]
                   + [sample("5", 1)], frame=2) is None
    assert len(observe(observer, [sample(str(i), 0) for i in range(6)], frame=3).members) == 6
    assert observe(observer, [sample("0", -1)], frame=4) is None


def test_run_lifecycle_persists_and_legacy_completed_migrates(tmp_path):
    path = tmp_path / "runs.json"
    store = NuzlockeStore(path)
    first = store.create_run("One", PROFILE)
    store.set_fight_notes(first.run_id, PROFILE.level_caps[0].cap_id, "Keep history")
    second = store.create_run("Two", PROFILE)
    store.set_run_status(first.run_id, RunStatus.WON, "Champion defeated")
    assert first.ended_at and first.completed
    assert store.active_run is second
    store.set_run_status(second.run_id, RunStatus.WIPED)
    assert store.active_run is None
    assert second.ended_at and not second.completed
    restored = NuzlockeStore(path)
    assert restored.get_run(first.run_id).status == "WON"
    assert restored.get_run(first.run_id).fight_notes[PROFILE.level_caps[0].cap_id] == "Keep history"
    assert restored.get_run(second.run_id).status == "WIPED"
    with pytest.raises(ValueError):
        restored.switch_run(second.run_id)
    restored.set_run_status(second.run_id, RunStatus.ABANDONED)
    assert restored.get_run(second.run_id).status == "ABANDONED"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["runs"][0].pop("status", None)
    payload["runs"][0]["completed"] = True
    payload["runs"][1].pop("status", None)
    payload["runs"][1]["completed"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    legacy = NuzlockeStore(path)
    assert legacy.get_run(first.run_id).status == "WON"
    assert legacy.get_run(second.run_id).status == "ACTIVE"


def test_no_run_prompt_requires_live_progress_and_suppresses_session():
    observer = NoRunPromptObserver()
    kwargs = {"connected": True, "valid_snapshot": True, "party_size": 1,
              "active_run": False, "stream_identity": "session", "enabled": True}
    assert not observer.observe(**kwargs, frame=1)
    assert not observer.observe(**kwargs, frame=2)
    assert not observer.observe(**kwargs, frame=3)
    assert observer.observe(**kwargs, frame=4)
    assert not observer.observe(**kwargs, frame=5)
    other = NoRunPromptObserver()
    assert not other.observe(**{**kwargs, "connected": False}, frame=1)
    assert not other.observe(**{**kwargs, "party_size": 0}, frame=2)
    assert not other.observe(**{**kwargs, "active_run": True}, frame=3)
    assert not other.observe(**kwargs, frame=4)
    other.suppress_for_session()
    assert not other.observe(**kwargs, frame=100)


def test_migration_preserves_unknown_saved_fields(tmp_path):
    path = tmp_path / "runs.json"
    store = NuzlockeStore(path)
    run = store.create_run("Legacy", PROFILE)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["future_root_field"] = {"keep": True}
    payload["runs"][0]["future_run_field"] = ["keep", 42]
    payload["runs"][0].pop("status")
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = NuzlockeStore(path)
    loaded.rename_run(run.run_id, "Renamed")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["future_root_field"] == {"keep": True}
    assert saved["runs"][0]["future_run_field"] == ["keep", 42]
    assert saved["runs"][0]["status"] == "ACTIVE"
