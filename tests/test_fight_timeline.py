"""Run-specific fight presentation and live readiness."""

from pokemon_ev_tracker.core.nuzlocke.fights import build_fight_timeline
from pokemon_ev_tracker.core.nuzlocke.models import PartyLevel
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE as PROFILE


def test_timeline_resolution_progress_healing_and_all_complete(tmp_path):
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("One", PROFILE)
    caps = PROFILE.level_caps
    timeline = build_fight_timeline(run, PROFILE)
    assert timeline.next_fight.fight_id == caps[0].cap_id
    assert (timeline.next_fight.order, timeline.total_fights) == (1, 24)
    assert timeline.next_fight.healing_text == "None"
    for cap in caps[:4]:
        store.set_cap_completed(run.run_id, cap.cap_id, True)
    timeline = build_fight_timeline(run, PROFILE)
    assert timeline.next_fight.name == "Gardenia"
    assert timeline.next_fight.order == 5
    assert timeline.completed_count == 4
    assert timeline.next_fight.healing_text == "2× Super Potion"
    assert [fight.status for fight in timeline.fights[:6]] == [
        "COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED", "UP NEXT", "FUTURE",
    ]
    for cap in caps[4:]:
        store.set_cap_completed(run.run_id, cap.cap_id, True)
    timeline = build_fight_timeline(run, PROFILE)
    assert timeline.next_fight is None
    assert timeline.completed_count == timeline.total_fights == 24
    assert build_fight_timeline(None, PROFILE) is None


def test_run_isolation_override_notes_and_live_party(tmp_path):
    path = tmp_path / "runs.json"
    store = NuzlockeStore(path)
    first = store.create_run("First", PROFILE)
    second = store.create_run("Second", PROFILE)
    cap_id = PROFILE.level_caps[0].cap_id
    store.set_level_cap_override(first.run_id, cap_id, 10)
    store.set_fight_notes(first.run_id, cap_id, "Bring four Pokémon\nNo setup")
    store.set_cap_completed(second.run_id, cap_id, True)
    party = (PartyLevel("Below", "Shinx", 9), PartyLevel("At", "Starly", 10),
             PartyLevel("Above", "Piplup", 11))
    first_fight = build_fight_timeline(first, PROFILE, party).next_fight
    assert first_fight.effective_level_cap == 10
    assert first_fight.default_level_cap == 5
    assert first_fight.overridden
    assert first_fight.notes == "Bring four Pokémon\nNo setup"
    assert (first_fight.ready_count, first_fight.over_count) == (2, 1)
    assert [member.status for member in first_fight.party] == ["READY", "READY", "OVER_CAP"]
    assert [member.over_by for member in first_fight.party] == [0, 0, 1]
    assert build_fight_timeline(second, PROFILE).next_fight.order == 2
    reloaded = NuzlockeStore(path)
    restored = reloaded.get_run(first.run_id)
    assert restored.fight_notes[cap_id] == first_fight.notes
    assert cap_id not in reloaded.get_run(second.run_id).fight_notes
    assert build_fight_timeline(restored, PROFILE, tuple(reversed(party))).next_fight.party[0].name == "Above"
    reloaded.set_level_cap_override(first.run_id, cap_id, None)
    reset = build_fight_timeline(restored, PROFILE, party).next_fight
    assert reset.effective_level_cap == reset.default_level_cap == 5
    assert reset.over_count == 3
    assert not reset.overridden
