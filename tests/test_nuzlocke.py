from __future__ import annotations

from pokemon_ev_tracker.core.nuzlocke.models import (
    DeathRecord,
    EncounterRecord,
    EncounterStatus,
    PartyLevel,
    next_level_cap,
    over_cap_party_members,
    resolved_level_caps,
    summarize_run,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.nuzlocke import (
    PLATINUM_NUZLOCKE_PROFILE,
    PLATINUM_RETIRED_DEFAULT_LOCATIONS,
)


def test_platinum_honey_tree_locations_have_unique_encounter_rows(tmp_path) -> None:
    honey_tree_locations = {
        *(f"Route {number}" for number in (
            205, 206, 207, 208, 209, 210, 211, 212, 213, 214, 215, 218, 221, 222,
        )),
        "Eterna Forest",
        "Floaroma Meadow",
        "Fuego Ironworks",
        "Valley Windworks",
    }
    locations = PLATINUM_NUZLOCKE_PROFILE.locations
    assert all(sum(location.name == name for location in locations) == 1
               for name in honey_tree_locations)

    run = NuzlockeStore(tmp_path / "runs.json").create_run(
        "Platinum", PLATINUM_NUZLOCKE_PROFILE
    )
    assert honey_tree_locations <= {
        encounter.location for encounter in run.encounters.values()
    }


def test_run_create_switch_delete_and_runs_keep_independent_state(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "nuzlocke_runs.json")
    first = store.create_run("Platinum Randomizer #1", PLATINUM_NUZLOCKE_PROFILE)
    second = store.create_run("Platinum Randomizer #2", PLATINUM_NUZLOCKE_PROFILE)

    route = PLATINUM_NUZLOCKE_PROFILE.locations[1]
    store.update_encounter(
        first.run_id,
        EncounterRecord(route.location_id, route.name, "CAUGHT", "Starly", "birby", 3),
    )
    store.switch_run(first.run_id)

    assert store.active_run is first
    assert store.active_run.encounters[route.location_id].species == "Starly"
    assert store.get_run(second.run_id).encounters[route.location_id].species == ""
    assert store.delete_run(second.run_id)
    assert len(store.runs) == 1


def test_encounter_status_death_count_and_persistence(tmp_path) -> None:
    path = tmp_path / "nuzlocke_runs.json"
    store = NuzlockeStore(path)
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    location = PLATINUM_NUZLOCKE_PROFILE.locations[1]
    record = EncounterRecord(
        location.location_id, location.name, EncounterStatus.DEAD.value, "Shinx", "sparky", 4
    )
    store.update_encounter(run.run_id, record)
    store.update_run_details(run.run_id, "Randomized seed notes", True)

    summary = summarize_run(run, resolved_level_caps(run, PLATINUM_NUZLOCKE_PROFILE))
    assert summary.deaths == 1
    assert run.deaths[0].nickname == "sparky"

    reloaded = NuzlockeStore(path)
    loaded = reloaded.get_run(run.run_id)
    assert loaded is not None
    assert reloaded.active_run is None
    assert loaded.status == "WON"
    assert loaded.encounters[location.location_id] == record
    assert loaded.notes == "Randomized seed notes"
    assert loaded.completed
    assert len(loaded.deaths) == 1


def test_manual_death_entry_is_counted_and_removable(tmp_path) -> None:
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    death = DeathRecord("death-1", "Bidoof", "", 6, "Route 202", "2026-09-26T12:00:00Z")
    store.add_death(run.run_id, death)

    assert summarize_run(run, ()).deaths == 1
    assert store.remove_death(run.run_id, death.death_id)
    assert summarize_run(run, ()).deaths == 0


def test_level_caps_override_persist_and_advance(tmp_path) -> None:
    path = tmp_path / "runs.json"
    store = NuzlockeStore(path)
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    caps = resolved_level_caps(run, PLATINUM_NUZLOCKE_PROFILE)
    assert next_level_cap(run, caps) == caps[0]

    store.set_level_cap_override(run.run_id, caps[0].cap_id, 16)
    store.set_cap_completed(run.run_id, caps[0].cap_id, True)
    store.switch_run(run.run_id)
    loaded = NuzlockeStore(path).active_run
    assert loaded is not None
    loaded_caps = resolved_level_caps(loaded, PLATINUM_NUZLOCKE_PROFILE)
    assert loaded_caps[0].level_cap == 16
    assert next_level_cap(loaded, loaded_caps) == loaded_caps[1]
    assert summarize_run(loaded, loaded_caps).completed_fights == 1


def test_over_cap_detection_is_read_only() -> None:
    members = (
        PartyLevel("sparky", "Shinx", 15),
        PartyLevel("", "Starly", 14),
        PartyLevel("", "Bidoof", 13),
    )

    assert over_cap_party_members(members, 14) == (members[0],)
    assert over_cap_party_members(members, None) == ()
    assert tuple(member.level for member in members) == (15, 14, 13)


def test_platinum_profile_has_ordered_locations_and_caps() -> None:
    profile = PLATINUM_NUZLOCKE_PROFILE
    location_names = {item.name for item in profile.locations}

    assert profile.game_id == "pokemon-platinum"
    assert len(profile.locations) == 67
    assert [item.order for item in profile.locations] == list(range(len(profile.locations)))
    assert profile.locations[0].name == "Starter"
    assert profile.locations[0].location_id == "platinum-starter"
    assert profile.locations[1].name == "Twinleaf Town"
    assert {"Sandgem Town", "Jubilife City", "Oreburgh City"}.isdisjoint(location_names)
    assert {
        "Route 201", "Route 202", "Oreburgh Gate", "Oreburgh Mine", "Hearthome City",
        "Twinleaf Town", "Eterna City", "Canalave City", "Pastoria City",
        "Celestic Town", "Sunyshore City", "Pokémon League",
    } <= location_names
    assert next(item for item in profile.locations if item.name == "Hearthome City").location_id == "platinum-025"
    assert len(PLATINUM_RETIRED_DEFAULT_LOCATIONS) == 16
    assert len(profile.level_caps) >= 15
    assert [item.order for item in profile.level_caps] == list(range(len(profile.level_caps)))
    rival_caps = {
        item.name: item.level_cap
        for item in profile.level_caps
        if item.category == "Rival"
    }
    assert rival_caps == {
        "Barry - Route 201": 5,
        "Barry - Route 203": 9,
        "Barry - Route 209": 27,
        "Barry - Pastoria City": 36,
        "Barry - Canalave City": 38,
        "Barry - Pokémon League": 51,
    }
    assert "Barry - Victory Road" not in {item.name for item in profile.level_caps}
    assert profile.level_caps[-1].name == "Cynthia"


def test_platinum_level_caps_include_trainer_healing_item_limits() -> None:
    caps = {cap.name: cap for cap in PLATINUM_NUZLOCKE_PROFILE.level_caps}

    assert all(cap.healing_item_count is not None for cap in caps.values())
    assert caps["Barry - Route 203"].healing_items == ()
    assert caps["Roark"].healing_items == (("Potion", 2),)
    assert caps["Gardenia"].healing_items == (("Super Potion", 2),)
    assert caps["Fantina"].healing_items == (("Super Potion", 2),)
    assert caps["Maylene"].healing_item_count == 0
    assert caps["Crasher Wake"].healing_items == (("Hyper Potion", 2),)
    assert caps["Cyrus - Veilstone HQ"].healing_items == (
        ("Hyper Potion", 1),
        ("Full Restore", 1),
    )
    assert caps["Byron"].healing_items == (("Hyper Potion", 1), ("Full Restore", 1))
    assert caps["Candice"].healing_items == (("Hyper Potion", 1), ("Full Restore", 1))
    assert caps["Volkner"].healing_items == (("Hyper Potion", 1), ("Full Restore", 1))
    assert caps["Barry - Pokémon League"].healing_item_count == 0
    assert caps["Cynthia"].healing_items == (("Full Restore", 4),)
    assert caps["Barry - Route 201"].healing_item_count == 0


def test_platinum_level_caps_use_platinum_boss_ace_levels() -> None:
    caps = {cap.name: cap.level_cap for cap in PLATINUM_NUZLOCKE_PROFILE.level_caps}

    assert caps["Mars - Valley Windworks"] == 17
    assert caps["Jupiter - Eterna Building"] == 23
    assert caps["Crasher Wake"] == 37
    assert caps["Cyrus - Veilstone HQ"] == 46
    assert caps["Mars & Jupiter - Spear Pillar"] == 46
    assert caps["Cyrus - Distortion World"] == 48
    assert caps["Candice"] == 44


def test_location_cleanup_removes_only_pristine_retired_defaults(tmp_path) -> None:
    path = tmp_path / "runs.json"
    store = NuzlockeStore(path)
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    retired = {item.name: item for item in PLATINUM_RETIRED_DEFAULT_LOCATIONS}

    run.encounters[retired["Jubilife City"].location_id] = EncounterRecord(
        retired["Jubilife City"].location_id, "Jubilife City"
    )
    run.encounters[retired["Sandgem Town"].location_id] = EncounterRecord(
        retired["Sandgem Town"].location_id,
        "Sandgem Town",
        "CAUGHT",
        "Bidoof",
        "beaver",
        4,
    )
    run.encounters[retired["Oreburgh City"].location_id] = EncounterRecord(
        retired["Oreburgh City"].location_id,
        "Oreburgh City",
        notes="Old run note",
    )
    store.save()

    reloaded = NuzlockeStore(path)
    migrated = reloaded.active_run
    assert migrated is not None
    reloaded.ensure_locations(
        migrated.run_id,
        PLATINUM_NUZLOCKE_PROFILE.locations,
        obsolete_locations=PLATINUM_RETIRED_DEFAULT_LOCATIONS,
    )

    assert retired["Jubilife City"].location_id not in migrated.encounters
    assert migrated.encounters[retired["Sandgem Town"].location_id].species == "Bidoof"
    assert migrated.encounters[retired["Oreburgh City"].location_id].notes == "Old run note"
