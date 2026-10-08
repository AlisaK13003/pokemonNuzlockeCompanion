from dataclasses import asdict, replace

import pytest
from test_companion_redesign import window as _window_fixture
from test_nuzlocke_acquisition import _candidate, _event, _location_id

from pokemon_ev_tracker.core.nuzlocke.death_detection import DeathCandidate
from pokemon_ev_tracker.core.nuzlocke.models import EncounterRecord
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.ui.notification_events import refresh_event_notices
from pokemon_ev_tracker.ui.notifications import Notice, NoticeAction

window = _window_fixture


@pytest.mark.parametrize("kind", ("connection", "encounter", "shiny", "friendship", "faint"))
def test_settings_notification_previews_do_not_change_run_or_preferences(window, kind):
    widget, _, _, store, app = window
    # Settle the unrelated debounced geometry save before comparing preferences.
    widget._geometry_save_timer.stop()
    widget._save_window_state()
    run = store.create_run("Preview", PLATINUM_NUZLOCKE_PROFILE)
    before = asdict(run)
    preferences = asdict(widget.settings)
    widget.preferences_view.preview_buttons[kind].click()
    app.processEvents()
    card = widget.notifications.card
    assert card.notice.key == f"preview:{kind}"
    assert card.isVisible()
    assert card.geometry().right() < widget.shell.width()
    if card.buttons:
        next(iter(card.buttons.values())).click()
    else:
        card.close_button.click()
    assert widget.notifications.card is None
    assert asdict(run) == before and asdict(widget.settings) == preferences


def test_notification_dismissal_is_stable_across_repeated_refreshes(window):
    widget, _, _, store, _ = window
    run = store.create_run("Pending", PLATINUM_NUZLOCKE_PROFILE)
    event = _event(_candidate("toast-mon", species_id=77, species_name="Ponyta"), _location_id("Route 201"))
    store.record_acquisition_event(run.run_id, event)
    refresh_event_notices(widget)
    card = widget.notifications.card
    for _ in range(10):
        refresh_event_notices(widget)
    assert widget.notifications.card is card and not widget.notifications.pending
    card.close_button.click()
    refresh_event_notices(widget)
    assert widget.notifications.card is None
    assert event.stable_id not in run.resolved_acquisition_ids


def test_encounter_extra_action_preserves_original_route(window):
    widget, _, _, store, _ = window
    run = store.create_run("Extra", PLATINUM_NUZLOCKE_PROFILE)
    route = _location_id("Route 201")
    original = replace(run.encounters[route], status="CAUGHT", species="Starly", nickname="Original")
    run.encounters[route] = original
    event = _event(_candidate("bonus-pony", species_id=77, species_name="Ponyta"), route)
    store.record_acquisition_event(run.run_id, event)
    refresh_event_notices(widget)
    widget.notifications.card.buttons["Add as Extra"].click()
    assert run.encounters[route] == original
    assert any(r.stable_id == event.stable_id and r.location_id.startswith("extra-")
               for r in run.encounters.values())
    assert event.stable_id in run.resolved_acquisition_ids


def test_notification_action_never_writes_to_a_different_active_run(window):
    widget, _, _, store, _ = window
    run = store.create_run("First", PLATINUM_NUZLOCKE_PROFILE)
    event = _event(_candidate("first-mon"), _location_id("Route 201"))
    store.record_acquisition_event(run.run_id, event)
    refresh_event_notices(widget)
    card = widget.notifications.card
    other = store.create_run("Second", PLATINUM_NUZLOCKE_PROFILE)
    before = asdict(other)
    card.buttons["Ignore"].click()
    assert widget.notifications.card is card
    assert asdict(other) == before
    assert not run.resolved_acquisition_ids
    refresh_event_notices(widget)
    assert widget.notifications.card is None


def test_faint_action_records_only_the_notified_candidate(window):
    widget, source, _, store, _ = window
    run = store.create_run("Faint", PLATINUM_NUZLOCKE_PROFILE)
    pokemon = source.party.pokemon[0]
    route = _location_id("Route 201")
    run.encounters[route] = EncounterRecord(route, "Route 201", "CAUGHT", pokemon.species,
                                           pokemon.nickname, pokemon.level, stable_id=pokemon.stable_id)
    candidate = DeathCandidate(pokemon.stable_id, pokemon.species, pokemon.nickname,
                               pokemon.level, pokemon.met_location_id, pokemon.met_location_name,
                               pokemon.met_level, "2026-10-07T22:00:00+00:00")
    widget.nuzlocke_view.receive_ram_death_candidates(run.run_id, (candidate,))
    refresh_event_notices(widget)
    widget.notifications.card.buttons["Mark Dead"].click()
    assert run.encounters[route].status == "DEAD"
    assert len(run.deaths) == 1 and run.deaths[0].stable_id == pokemon.stable_id
    assert not widget.nuzlocke_view._ram_death_queue


def test_disabled_notifications_do_not_show_pending_event_and_preview_still_works(window):
    widget, _, _, store, _ = window
    run = store.create_run("Muted", PLATINUM_NUZLOCKE_PROFILE)
    event = _event(_candidate("muted-mon"), _location_id("Route 201"))
    store.record_acquisition_event(run.run_id, event)
    widget.settings.notify_encounters = False
    refresh_event_notices(widget)
    assert widget.notifications.card is None
    widget.preferences_view.preview_buttons["encounter"].click()
    assert widget.notifications.card.notice.key == "preview:encounter"


def test_live_notification_preempts_preview_and_actions_keep_card_on_failure(window):
    widget, _, _, _, _ = window
    center = widget.notifications
    center.preview("shiny")
    notice = Notice("real-test", "connection", "CONNECTION", "Real event", "Details",
                    (NoticeAction("Retry", lambda: False),))
    center.show_notice(notice)
    assert center.card.notice is notice
    center.card.buttons["Retry"].click()
    assert center.card.notice is notice
    center.card.close_button.click()
    assert center.card is None


def test_live_connection_and_friendship_events_use_new_cards(window):
    widget, source, _, _, _ = window
    source.connected = False
    widget._refresh_ram_backend_debug()
    assert widget.notifications.card.notice.kicker == "CONNECTION LOST"
    widget.notifications.dismiss()
    source.connected = True
    widget._refresh_ram_backend_debug()
    assert widget.notifications.card.title.text() == "BizHawk is live again"
    widget.notifications.dismiss()
    widget._complete_friendship_goal()
    assert widget.notifications.card.notice.kind == "friendship"
    assert str(widget.friendship_walk_group.goal) in widget.notifications.card.title.text()
    widget.notifications.card.buttons["View Friendship Walk"].click()
    assert widget.notifications.card is None
    widget.settings.notify_connection = False
    source.connected = False
    widget._refresh_ram_backend_debug()
    assert widget.notifications.card is None


def test_live_shiny_is_already_recorded_and_notification_review_does_not_duplicate(window):
    from test_shiny_clause import shiny_record

    from pokemon_ev_tracker.pokemon.gen4.structure import decode_party_pokemon

    widget, source, _, store, _ = window
    run = store.create_run("Live shiny", PLATINUM_NUZLOCKE_PROFILE)
    decoded = decode_party_pokemon(shiny_record())
    pokemon = replace(source.party.pokemon[0], species_id=77, species="Ponyta", nickname="Ember",
                      stable_id=decoded.stable_id, decoded=decoded)
    source.party = replace(source.party, pokemon=(pokemon,), party_count=1)
    before = run.encounters.copy()
    widget._refresh_ram_backend_debug()
    assert widget.notifications.card.notice.kind == "shiny"
    assert widget.notifications.card.notice.shiny
    assert len(run.shiny_encounters) == 1
    starter = next(row for row in run.encounters.values() if row.location == "Starter")
    assert starter.status == "CAUGHT" and starter.stable_id == decoded.stable_id
    assert all(row == before[key] for key, row in run.encounters.items() if row.location != "Starter")
    widget.notifications.card.buttons["Review Shiny"].click()
    widget._refresh_ram_backend_debug()
    assert widget.notifications.card is None
    assert len(run.shiny_encounters) == 1


def test_queued_wipe_review_cannot_target_a_different_run(window):
    from pokemon_ev_tracker.core.nuzlocke.wipe_detection import PartyWipeCandidate

    widget, _, _, store, _ = window
    first = store.create_run("Wiped run", PLATINUM_NUZLOCKE_PROFILE)
    widget._pending_wipe = PartyWipeCandidate((), "2026-10-07T22:00:00+00:00", first.run_id)
    refresh_event_notices(widget)
    card = widget.notifications.card
    assert card.notice.kind == "wipe"
    other = store.create_run("New run", PLATINUM_NUZLOCKE_PROFILE)
    before = asdict(other)
    card.buttons["Review Run"].click()
    assert asdict(other) == before
    refresh_event_notices(widget)
    assert widget.notifications.card is None
