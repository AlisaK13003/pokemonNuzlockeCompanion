from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.core.nuzlocke.death_detection import DeathCandidate
from pokemon_ev_tracker.core.nuzlocke.models import (
    EncounterRecord,
    EncounterStatus,
    PartyLevel,
    PokemonAcquisitionEvent,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.ui.nuzlocke_view import NuzlockeView


@pytest.fixture
def nuzlocke_view(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = NuzlockeStore(tmp_path / "runs.json")
    view = NuzlockeView(store, (PLATINUM_NUZLOCKE_PROFILE,))
    yield app, view, store
    view.close()


def test_view_shows_platinum_encounters_and_next_cap(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum Randomizer", PLATINUM_NUZLOCKE_PROFILE)
    view._refresh_all()

    assert view.encounters_table.rowCount() == len(PLATINUM_NUZLOCKE_PROFILE.locations)
    assert view.encounters_table.item(0, 0).text() == "Starter"
    assert view.encounters_table.item(0, 1).text() == ""
    assert view.encounters_table.cellWidget(0, 1).currentText() == "Not encountered"
    assert view.next_cap_label.text() == "Barry - Route 201  ·  Lv. 5"
    assert view.healing_item_hint_label.text() == "Opponent healing items: none"

    view._complete_next_cap()
    assert run.completed_cap_ids == [PLATINUM_NUZLOCKE_PROFILE.level_caps[0].cap_id]
    assert view.next_cap_label.text() == "Barry - Route 203  ·  Lv. 9"
    assert view.healing_item_hint_label.text() == "Opponent healing items: none"
    assert "1 / 24" in view.cap_progress_label.text()


def test_encounter_status_filter_and_death_count_render(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    location = PLATINUM_NUZLOCKE_PROFILE.locations[1]
    view._set_encounter_status(location.location_id, EncounterStatus.DEAD.value)

    assert len(run.deaths) == 1
    assert "Deaths 1" in view.summary_label.text()
    view.encounter_filter.setCurrentIndex(
        view.encounter_filter.findData(EncounterStatus.DEAD.value)
    )
    assert view.encounters_table.rowCount() == 1
    assert view.encounters_table.item(0, 0).text() == location.name


def test_party_levels_only_drive_read_only_over_cap_warning(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    members = (PartyLevel("sparky", "Shinx", 15), PartyLevel("", "Starly", 14))
    view.set_party_levels(members)

    assert (
        "Over current cap (5): sparky (Shinx) Lv. 15, Starly Lv. 14"
        == view.party_warning_label.text()
    )
    assert view.party_levels == members


def test_level_cap_override_changes_warning_threshold(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    store.set_level_cap_override(run.run_id, PLATINUM_NUZLOCKE_PROFILE.level_caps[0].cap_id, 16)
    view.set_party_levels((PartyLevel("", "Shinx", 15),))

    assert view.next_cap_label.text() == "Barry - Route 201  ·  Lv. 16"
    assert view.party_warning_label.text() == "No party members are above the current cap."


def _acquisition_event(stable_id="pid:shinx", *, source="WILD", suggested="Route 202"):
    location = next(item for item in PLATINUM_NUZLOCKE_PROFILE.locations if item.name == suggested)
    return PokemonAcquisitionEvent(
        stable_id=stable_id,
        species_id=403,
        species_name="Shinx",
        nickname="sparky",
        level=6,
        met_level=4,
        met_location_id=0x11,
        met_location_name="Route 202",
        egg_location_id=0,
        origin_game=12,
        is_egg=False,
        detected_at="2026-09-26T12:00:00+00:00",
        source=source,
        confidence="HIGH" if source == "WILD" else "MEDIUM",
        suggested_location_id=location.location_id,
        suggested_location_name=location.name,
    )


def test_new_acquisition_can_be_added_to_suggested_route(
    nuzlocke_view,
) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    assert not run.auto_record_unambiguous_encounters
    store.record_acquisition_event(run.run_id, _acquisition_event())
    view._refresh_all()

    assert view.acquisition_group.title() == "New Pokémon"
    assert "Last acquisition candidate" in view.acquisition_status_label.text()
    assert view.acquisition_selector.currentData() == "pid:shinx"
    assert view.acquisition_location_selector.currentText() == "Route 202"
    assert view.accept_acquisition_button.isEnabled()

    view.accept_acquisition_button.click()
    route = next(item for item in run.encounters.values() if item.location == "Route 202")
    assert (route.status, route.species, route.nickname, route.level) == (
        "CAUGHT",
        "Shinx",
        "sparky",
        4,
    )
    assert run.resolved_acquisition_ids == ["pid:shinx"]
    assert "status: accepted" in view.acquisition_status_label.text()


def test_occupied_suggestion_requires_replace_extra_or_ignore(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    route_id = next(
        item.location_id for item in PLATINUM_NUZLOCKE_PROFILE.locations if item.name == "Route 202"
    )
    store.update_encounter(
        run.run_id,
        EncounterRecord(route_id, "Route 202", "CAUGHT", "Bidoof", "", 3),
    )
    store.record_acquisition_event(run.run_id, _acquisition_event("pid:dupe"))
    view._refresh_all()

    assert not view.accept_acquisition_button.isEnabled()
    assert view.replace_acquisition_button.isEnabled()
    assert view.extra_acquisition_button.isEnabled()
    assert "Encounter already recorded for Route 202" in view.acquisition_detail_label.text()

    view.ignore_acquisition_button.click()
    assert run.resolved_acquisition_ids == ["pid:dupe"]
    assert run.encounters[route_id].species == "Bidoof"
    assert "status: ignored" in view.acquisition_status_label.text()


def test_box_candidate_at_oreburgh_mine_uses_duplicate_location_actions(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    location = next(
        item for item in PLATINUM_NUZLOCKE_PROFILE.locations
        if item.name == "Oreburgh Mine"
    )
    store.update_encounter(
        run.run_id,
        EncounterRecord(location.location_id, location.name, "CAUGHT", "Geodude", "", 5),
    )
    event = replace(
        _acquisition_event("pid:boxed-oreburgh", suggested="Oreburgh Mine"),
        source_location="BOX",
        met_location_id=0x2E,
        met_location_name="Oreburgh Mine",
    )
    store.record_acquisition_event(run.run_id, event)

    view._refresh_all()

    assert view.acquisition_selector.currentData() == event.stable_id
    assert "Stored in PC" in view.acquisition_detail_label.text()
    assert view.acquisition_location_selector.currentText() == "Oreburgh Mine"
    assert not view.accept_acquisition_button.isEnabled()
    assert view.replace_acquisition_button.isEnabled()
    assert view.extra_acquisition_button.isEnabled()
    assert view.ignore_acquisition_button.isEnabled()


def test_suggestion_location_can_be_manually_overridden(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    store.record_acquisition_event(run.run_id, _acquisition_event("pid:override"))
    view._refresh_all()
    route_203_id = next(
        item.location_id for item in PLATINUM_NUZLOCKE_PROFILE.locations if item.name == "Route 203"
    )
    view.acquisition_location_selector.setCurrentIndex(
        view.acquisition_location_selector.findData(route_203_id)
    )
    assert view.accept_acquisition_button.isEnabled()
    view.accept_acquisition_button.click()

    assert run.encounters[route_203_id].species == "Shinx"
    assert (
        run.encounters[
            next(
                item.location_id
                for item in PLATINUM_NUZLOCKE_PROFILE.locations
                if item.name == "Route 202"
            )
        ].species
        == ""
    )


def test_auto_record_is_opt_in_and_only_records_unambiguous_wild_row(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    store.record_acquisition_event(run.run_id, _acquisition_event("pid:auto"))
    view._refresh_all()
    assert (
        run.encounters[
            next(
                item.location_id
                for item in PLATINUM_NUZLOCKE_PROFILE.locations
                if item.name == "Route 202"
            )
        ].species
        == ""
    )

    view.auto_record_acquisitions.setChecked(True)
    route = run.encounters[
        next(
            item.location_id
            for item in PLATINUM_NUZLOCKE_PROFILE.locations
            if item.name == "Route 202"
        )
    ]
    assert route.species == "Shinx"
    assert run.auto_record_unambiguous_encounters


def test_starter_suggestion_is_never_auto_recorded(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    event = _acquisition_event("pid:starter", source="STARTER", suggested="Starter")
    store.record_acquisition_event(run.run_id, event)
    store.set_auto_record_unambiguous(run.run_id, True)
    view._refresh_all()

    assert run.encounters["platinum-starter"].species == ""
    assert view.acquisition_selector.currentData() == "pid:starter"


def test_ambiguous_route_201_suggestion_is_not_auto_recorded(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Randomized Platinum", PLATINUM_NUZLOCKE_PROFILE)
    route_201 = next(
        item for item in PLATINUM_NUZLOCKE_PROFILE.locations if item.name == "Route 201"
    )
    event = replace(
        _acquisition_event("pid:random-starter", suggested="Route 201"),
        met_location_id=0x10,
        met_location_name="Route 201",
        source="UNKNOWN",
        confidence="MEDIUM",
    )
    store.record_acquisition_event(run.run_id, event)
    store.set_auto_record_unambiguous(run.run_id, True)
    view._refresh_all()

    assert run.encounters[route_201.location_id].species == ""
    assert run.encounters["platinum-starter"].species == ""
    assert view.acquisition_selector.currentData() == "pid:random-starter"


def _recorded_party_member(run, stable_id="pid:gastly"):
    location = next(item for item in run.encounters.values() if item.location == "Old Chateau")
    record = replace(
        location,
        status="CAUGHT",
        species="Gastly",
        nickname="ghosty",
        level=5,
        stable_id=stable_id,
        met_location_id=0x46,
        met_location_name="Old Chateau",
    )
    return record


def _faint_candidate(stable_id="pid:gastly", species="Gastly", nickname="ghosty"):
    return DeathCandidate(
        stable_id=stable_id,
        species=species,
        nickname=nickname,
        level=14,
        met_location_id=0x46,
        met_location_name="Old Chateau",
        met_level=5,
        detected_at="2026-09-27T12:00:00+00:00",
    )


def test_faint_candidate_requires_confirmation_and_preserves_encounter(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    encounter = _recorded_party_member(run)
    store.update_encounter(run.run_id, encounter)
    view._refresh_all()

    assert not view.auto_confirm_deaths.isChecked()
    view.receive_ram_death_candidates(run.run_id, (_faint_candidate(),))
    assert not view.ram_death_group.isHidden()
    assert "Encounter: Old Chateau" in view.ram_death_detail_label.text()
    assert run.encounters[encounter.location_id].status == "CAUGHT"

    view.confirm_ram_death_button.click()
    updated = run.encounters[encounter.location_id]
    assert (updated.status, updated.species, updated.nickname, updated.level) == (
        "DEAD", "Gastly", "ghosty", 5
    )
    assert run.deaths[0].level_at_death == 14
    assert run.deaths[0].location_or_fight == "Unknown"
    assert run.deaths[0].detection_source == "RAM_AUTO"
    assert view.deaths_table.item(0, 3).text() == "Old Chateau"
    assert view.deaths_table.item(0, 4).text() == "Unknown"
    assert view.deaths_table.item(0, 6).text() == "RAM_AUTO"


def test_ignoring_faint_candidate_does_not_change_run(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    encounter = _recorded_party_member(run)
    store.update_encounter(run.run_id, encounter)
    view.receive_ram_death_candidates(run.run_id, (_faint_candidate(),))

    view.ignore_ram_death_button.click()

    assert run.encounters[encounter.location_id].status == "CAUGHT"
    assert not run.deaths
    assert view.ram_death_group.isHidden()


def test_automatic_death_confirmation_logs_notice_and_deduplicates(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    encounter = _recorded_party_member(run)
    store.update_encounter(run.run_id, encounter)
    view.auto_confirm_deaths.setChecked(True)

    view.receive_ram_death_candidates(run.run_id, (_faint_candidate(),))
    view.receive_ram_death_candidates(run.run_id, (_faint_candidate(),))

    assert run.automatically_confirm_deaths
    assert run.encounters[encounter.location_id].status == "DEAD"
    assert len(run.deaths) == 1
    assert "RAM faint recorded" in view.ram_death_detail_label.text()


def test_manual_dead_to_caught_clears_pending_ram_prompt(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    encounter = _recorded_party_member(run)
    store.update_encounter(run.run_id, encounter)
    view.receive_ram_death_candidates(run.run_id, (_faint_candidate(),))

    view._set_encounter_status(encounter.location_id, "DEAD")
    view._set_encounter_status(encounter.location_id, "CAUGHT")

    assert run.encounters[encounter.location_id].status == "CAUGHT"
    assert not run.deaths
    assert not view._ram_death_queue


def test_unlinked_faint_can_be_logged_without_consuming_encounter(nuzlocke_view) -> None:
    _app, view, store = nuzlocke_view
    run = store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    view.receive_ram_death_candidates(
        run.run_id,
        (_faint_candidate("pid:unlinked", "Pikachu", "sparky"),),
    )

    assert "not linked" in view.ram_death_detail_label.text()
    view.ram_death_location_selector.setCurrentIndex(0)
    view.confirm_ram_death_button.click()

    assert len(run.deaths) == 1
    assert run.deaths[0].encounter_location_id is None
    assert all(not encounter.species for encounter in run.encounters.values())
