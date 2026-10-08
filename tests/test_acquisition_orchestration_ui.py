"""Characterize full-poll ordering and source/run boundaries before C5 extraction."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from test_pc_storage_acquisition_ui import _box_mon, _snapshot, _SnapshotSource
from test_training_integration import make_window as window_fixture

from pokemon_ev_tracker.core.nuzlocke import acquisition_coordinator as orchestration
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE

acquisition_window = window_fixture


def test_full_poll_reconciliation_publication_precedes_observation(acquisition_window, monkeypatch):
    window, _, _ = acquisition_window(source=_SnapshotSource(_snapshot()))
    window.nuzlocke_view.store.create_run("order", PLATINUM_NUZLOCKE_PROFILE)
    order = []
    for name in ("reconcile_initial_starter", "reconcile_shiny_encounters", "reconcile_party_encounters"):
        original = getattr(orchestration, name)
        monkeypatch.setattr(orchestration, name, Mock(side_effect=lambda *a, fn=original, key=name, **kw:
                                                  (order.append(key), fn(*a, **kw))[1]))
    for key, observer in (("party", window.acquisitions._party), ("box", window.acquisitions._pc)):
        original = observer.observe
        monkeypatch.setattr(observer, "observe", Mock(side_effect=lambda *a, fn=original, key=key, **kw:
                                                      (order.append(key), fn(*a, **kw))[1]))
    window._refresh_ram_backend_debug()
    assert order == ["reconcile_initial_starter", "reconcile_shiny_encounters",
                     "reconcile_shiny_encounters", "reconcile_party_encounters", "party", "box"]


@pytest.mark.parametrize("boundary", ["duplicate", "disconnect", "stale", "run-switch"])
def test_pc_baseline_recovery_and_run_isolation(acquisition_window, boundary):
    window, source, _ = acquisition_window(source=_SnapshotSource(_snapshot()))
    store = window.nuzlocke_view.store
    run = store.create_run("baseline", PLATINUM_NUZLOCKE_PROFILE)
    existing = _box_mon(pid=1)
    capture = _box_mon(pid=2, nickname="new")
    source.current_snapshot = _snapshot((existing,))
    window._refresh_ram_backend_debug()
    assert not run.acquisition_events
    if boundary == "run-switch":
        run = store.create_run("other", PLATINUM_NUZLOCKE_PROFILE)
        source.current_snapshot = _snapshot((existing, capture))
    else:
        if boundary == "disconnect":
            source.current_snapshot = _snapshot((existing,))
            source.current_snapshot = replace(source.current_snapshot, connected=False)
            window._refresh_ram_backend_debug()
        if boundary == "stale":
            source.current_snapshot = _snapshot((existing, capture))
            source.current_snapshot.details["pc_storage_payload_fresh"] = False
            window._refresh_ram_backend_debug()
            assert not run.acquisition_events
        source.current_snapshot = _snapshot((existing, capture))
    window._refresh_ram_backend_debug()
    window._refresh_ram_backend_debug()
    assert [event.stable_id for event in run.acquisition_events] == (
        [] if boundary == "run-switch" else [capture.stable_id])


def test_old_run_notification_cannot_confirm_into_new_run(acquisition_window):
    from pokemon_ev_tracker.ui.notification_events import _acquisition_action

    window, source, _ = acquisition_window(source=_SnapshotSource(_snapshot()))
    store = window.nuzlocke_view.store
    old = store.create_run("old", PLATINUM_NUZLOCKE_PROFILE)
    window._refresh_ram_backend_debug()
    source.current_snapshot = _snapshot((_box_mon(pid=9),))
    window._refresh_ram_backend_debug()
    event = old.acquisition_events[0]
    new = store.create_run("new", PLATINUM_NUZLOCKE_PROFILE)
    assert not _acquisition_action(window, old.run_id, event, "replace")
    assert not old.resolved_acquisition_ids and not new.acquisition_events
    assert all(row.status == "NOT_ENCOUNTERED" for row in new.encounters.values())
