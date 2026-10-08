"""Adapt application events to notification cards without changing detector rules."""

from pokemon_ev_tracker.core.nuzlocke.acquisition import pending_acquisition_events
from pokemon_ev_tracker.ui.notifications import Notice, NoticeAction


def connection_notice(window, connected):
    window.notifications.show_notice(Notice(
        f"connection:{window._last_connection}:{connected}:{window._connection_notice_until}",
        "connection", "CONNECTION RESTORED" if connected else "CONNECTION LOST",
        "BizHawk is live again" if connected else "Waiting for BizHawk",
        f"{window.provider.display_name} · Party data is updating." if connected
        else "The emulator connection was lost. The companion will reconnect automatically.",
        timeout_ms=8000,
    ))


def friendship_notice(window):
    panel = window.friendship_walk_group
    pokemon = panel.selected_pokemon
    name = (pokemon.nickname or pokemon.species) if pokemon is not None else "Pokémon"
    window.notifications.show_notice(Notice(
        f"friendship:{panel.selected_identity}:{panel.goal}:{window._walk_session_identity}",
        "friendship", "FRIENDSHIP GOAL", f"{name} reached {panel.goal}",
        "The selected friendship target has been reached.",
        (NoticeAction("View Friendship Walk", lambda: window.set_route("training"), True),),
        timeout_ms=12000,
    ))


def shiny_notice(window, run, record):
    window.notifications.show_notice(Notice(
        f"shiny:{run.run_id}:{record.stable_id}", "shiny", "SHINY DETECTED",
        f"{record.nickname or record.species_name} · {record.location_name}",
        "Shiny Clause recorded. The original encounter is preserved.",
        (NoticeAction("Review Shiny", lambda: window._expand_nuzlocke("Dashboard"), True),
         NoticeAction("Dismiss", lambda: None)), record.species_id, True,
    ))


def recorded_encounter_notice(window, run, record, species_id):
    window.notifications.show_notice(Notice(
        f"encounter:{run.run_id}:{record.stable_id}", "encounter", "ENCOUNTER RECORDED · PARTY",
        f"{record.nickname or record.species} · Lv. {record.level or '—'}",
        f"Recorded at {record.location} from the Pokémon's met data.",
        (NoticeAction("Review", lambda: window._expand_nuzlocke("Encounters"), True),),
        species_id, timeout_ms=8000,
    ))


def _acquisition_action(window, run_id, event, mode):
    view = window.nuzlocke_view
    run = view.store.active_run
    if run is None or run.run_id != run_id:
        return False
    if mode == "review" or (mode != "ignore" and not event.suggested_location_id):
        window._expand_nuzlocke("Encounters")
        index = view.acquisition_selector.findData(event.stable_id)
        if index >= 0:
            view.acquisition_selector.setCurrentIndex(index)
        return True
    try:
        if mode == "ignore":
            view.store.resolve_acquisition(run_id, event.stable_id)
        else:
            view.store.accept_acquisition(run_id, event.stable_id, event.suggested_location_id, mode=mode)
    except (OSError, ValueError, KeyError) as error:
        view._show_error("Could not update encounter", error)
        return False
    view._refresh_all()
    return True


def _death_action(window, entry, mode):
    view = window.nuzlocke_view
    run_id, candidate, location_id, ambiguous = entry
    run = view.store.active_run
    if run is None or run.run_id != run_id or entry not in view._ram_death_queue:
        return False
    if mode == "review":
        window._expand_nuzlocke("Deaths")
        return True
    if mode == "mark":
        if not location_id or ambiguous:
            window._expand_nuzlocke("Deaths")
            return True
        try:
            recorded = view.store.record_ram_death(run_id, candidate, location_id)
        except (OSError, ValueError, KeyError) as error:
            view._show_error("Could not record faint", error)
            return False
        if recorded:
            view._set_ram_death_notice(run_id, candidate, "recorded")
    view._ram_death_queue.remove(entry)
    view._refresh_all()
    return True


def _review_wipe(window, wipe):
    run = window.nuzlocke_view.store.active_run
    if run is None or run.run_id != wipe.run_id or window._pending_wipe is not wipe:
        return False
    window._review_pending_wipe()
    return True


def refresh_event_notices(window):
    view = window.nuzlocke_view
    run = view.store.active_run
    enabled = {"connection": window.settings.notify_connection,
               "encounter": window.settings.notify_encounters, "shiny": window.settings.notify_encounters,
               "friendship": window.settings.notify_friendship, "faint": window.settings.notify_faints,
               "wipe": window.settings.notify_faints}
    pending_keys = {f"encounter:{run.run_id}:{e.stable_id}" for e in pending_acquisition_events(run)} if run else set()
    pending_keys.update(f"faint:{run_id}:{c.stable_id}:{c.detected_at}"
                        for run_id, c, _, _ in view._ram_death_queue
                        if run is not None and run_id == run.run_id)
    wipe = window._pending_wipe
    if run is not None and wipe is not None and wipe.run_id == run.run_id:
        pending_keys.add(f"wipe:{run.run_id}:{wipe}")
    window.notifications.discard(lambda notice: not notice.key.startswith("preview:") and (
        not enabled[notice.kind] or
        (notice.kind in {"encounter", "faint", "wipe"} and not notice.timeout_ms and notice.key not in pending_keys) or
        (notice.kind == "shiny" and (run is None or not notice.key.startswith(f"shiny:{run.run_id}:")))))
    if run is None:
        return
    if window.settings.notify_faints:
        for entry in tuple(view._ram_death_queue):
            run_id, candidate, location_id, ambiguous = entry
            if run_id != run.run_id:
                continue
            encounter = run.encounters.get(location_id)
            species_id = next((p.species_id for card in window.tracker_party_cards.values()
                               if (p := card.get("pokemon")) is not None
                               and p.stable_id == candidate.stable_id), None)
            action = "Review" if ambiguous or not encounter else "Mark Dead"
            window.notifications.show_notice(Notice(
                f"faint:{run_id}:{candidate.stable_id}:{candidate.detected_at}",
                "faint", "POTENTIAL DEATH", f"{candidate.nickname or candidate.species} reached 0 HP",
                f"Matched encounter: {encounter.location}." if encounter else "Choose the matching encounter before recording a death.",
                (NoticeAction(action, lambda entry=entry, action=action: _death_action(window, entry, "mark" if action == "Mark Dead" else "review"), True),
                 NoticeAction("Ignore", lambda entry=entry: _death_action(window, entry, "ignore"))),
                species_id,
            ))
        if window._pending_wipe is not None and window._pending_wipe.run_id == run.run_id:
            wipe = window._pending_wipe
            window.notifications.show_notice(Notice(
                f"wipe:{run.run_id}:{wipe}", "wipe", "PARTY WIPE", "Your party has fainted",
                "Review the run outcome before finishing this run.",
                (NoticeAction("Review Run", lambda wipe=wipe: _review_wipe(window, wipe), True),),
            ))
    if window.settings.notify_encounters:
        for event in pending_acquisition_events(run):
            original = run.encounters.get(event.suggested_location_id)
            occupied = original is not None and original.status != "NOT_ENCOUNTERED"
            action = lambda mode, event=event, run_id=run.run_id: _acquisition_action(window, run_id, event, mode)
            actions = (NoticeAction("Add as Extra", lambda action=action: action("extra"), True),
                       NoticeAction("Replace Existing", lambda action=action: action("replace"))) if occupied else (
                       NoticeAction("Record Encounter" if original else "Review", lambda action=action, original=original: action("add" if original else "review"), True),)
            window.notifications.show_notice(Notice(
                f"encounter:{run.run_id}:{event.stable_id}", "encounter",
                f"NEW POKÉMON · {event.source_location}",
                f"{event.nickname or event.species_name} · Lv. {event.met_level or event.level or '—'}",
                f"{original.location} already has an encounter." if occupied else
                f"Met at {event.met_location_name or 'an unknown location'}.",
                (*actions, NoticeAction("Ignore", lambda action=action: action("ignore"))), event.species_id,
            ))
