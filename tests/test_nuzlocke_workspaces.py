from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QBoxLayout, QScrollArea

from pokemon_ev_tracker.core.nuzlocke.models import PartyLevel, PokemonAcquisitionEvent
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.ui.compact_nuzlocke import CompactNuzlockeView
from pokemon_ev_tracker.ui.nuzlocke_panels import RunColumns
from pokemon_ev_tracker.ui.nuzlocke_view import NuzlockeView
from pokemon_ev_tracker.ui.theme import apply_theme


@pytest.fixture
def workspace(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = NuzlockeStore(tmp_path / "runs.json")
    view = NuzlockeView(store, (PLATINUM_NUZLOCKE_PROFILE,))
    apply_theme(view)
    yield app, store, view
    view.close()


def test_run_routes_and_empty_library_are_available(workspace):
    _app, _store, view = workspace
    assert [view.sections.tabText(index) for index in range(view.sections.count())] == [
        "Dashboard", "Encounters", "Deaths", "Fights", "Run Library", "Manage locations",
    ]
    assert not view.empty_panel.isHidden()
    assert view.sections.isHidden()
    view.show_section("Run Library")
    assert view.empty_panel.isHidden()
    assert not view.sections.isHidden()
    assert view.create_button.isEnabled()


def test_dashboard_preview_is_independent_of_ledger_filter(workspace):
    _app, store, view = workspace
    run = store.create_run("Local run", PLATINUM_NUZLOCKE_PROFILE)
    first = next(iter(run.encounters.values()))
    store.update_encounter(run.run_id, replace(first, species="Piplup", status="CAUGHT", level=5))
    view._refresh_all()
    view.encounter_filter.setCurrentIndex(view.encounter_filter.findData("DEAD"))
    assert view.encounters_table.rowCount() == 0
    assert view.encounter_preview.rowCount() == 1
    assert view.run_metrics["caught"].text() == "1"
    view._open_preview_encounter(view.encounter_preview.model().index(0, 0))
    assert view.sections.tabText(view.sections.currentIndex()) == "Encounters"
    assert view._selected_location_id() == first.location_id


def test_compact_view_tracks_shared_caps_pending_records_and_pc_status(workspace):
    _app, store, view = workspace
    compact = CompactNuzlockeView(view)
    run = store.create_run("Real saved run", PLATINUM_NUZLOCKE_PROFILE)
    cap = PLATINUM_NUZLOCKE_PROFILE.level_caps[0]
    store.set_level_cap_override(run.run_id, cap.cap_id, 16)
    store.record_acquisition_event(run.run_id, PokemonAcquisitionEvent(
        stable_id="pid:test", species_id=403, species_name="Shinx", nickname="Sparky",
        level=7, met_level=4, met_location_id=17, met_location_name="Route 202",
        egg_location_id=0, origin_game=12, is_egg=False,
        detected_at="2026-09-26T12:00:00+00:00", source="WILD", confidence="HIGH",
        source_location="BOX",
    ))
    view._refresh_all()
    view.set_party_levels((PartyLevel("Sparky", "Shinx", 17),))
    view.set_pc_monitor_status("PC monitor: paused · baseline unavailable")
    assert compact.run_name_label.text() == "Real saved run"
    assert compact.cap_label.text() == "16"
    assert "Sparky Lv. 17" in compact.party_warning_label.text()
    assert compact.pending_label.text() == "1 pending"
    assert "BOX" in compact.latest_encounter_label.text()
    assert compact.pc_status_label.text() == view.pc_monitor_label.text()
    routes = []
    compact.review_requested.connect(routes.append)
    compact.review_button.click()
    assert routes == ["Encounters"]
    # Repainting and compact navigation never resolve or mutate run records.
    compact.refresh()
    assert run.resolved_acquisition_ids == []
    assert all(not record.species for record in run.encounters.values())
    store.delete_run(run.run_id)
    view._refresh_all()
    assert compact.run_name_label.text() == "No active Nuzlocke run"
    assert compact.cap_label.text() == "—"
    assert compact.pending_label.text() == ""
    compact.close()


def test_run_library_preserves_historical_notes_and_outcome(workspace, monkeypatch):
    _app, store, view = workspace
    first = store.create_run("First", PLATINUM_NUZLOCKE_PROFILE)
    store.update_run_details(first.run_id, notes="First notes", completed=True)
    store.create_run("Second", PLATINUM_NUZLOCKE_PROFILE)
    view._refresh_all()
    row = next(row for row in range(view.run_library_table.rowCount())
               if view.run_library_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == first.run_id)
    from pokemon_ev_tracker.ui.nuzlocke_dialogs import RunHistoryDialog

    shown = []
    monkeypatch.setattr(RunHistoryDialog, "exec", lambda dialog: shown.append(dialog))
    view._activate_library_run(view.run_library_table.model().index(row, 0))
    assert store.active_run_id != first.run_id
    assert first.status == "WON"
    assert shown[0].windowTitle() == "Run History: First"


def test_narrow_dashboard_reflows_and_ledger_scrolls_locally(workspace):
    app, store, view = workspace
    store.create_run("Narrow", PLATINUM_NUZLOCKE_PROFILE)
    view._refresh_all()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(view)
    scroll.resize(500, 480)
    scroll.show()
    app.processEvents()
    columns = next(item for item in view.findChildren(RunColumns) if item.isVisible())
    assert columns.columns.direction() == QBoxLayout.Direction.TopToBottom
    view.show_section("Encounters")
    app.processEvents()
    assert view.encounters_table.horizontalScrollBar().maximum() > 0
    assert view.width() <= scroll.viewport().width()
    scroll.takeWidget()
    scroll.close()


def test_compact_minimum_window_uses_internal_scroll_without_clipping(workspace):
    app, store, view = workspace
    store.create_run("Small window", PLATINUM_NUZLOCKE_PROFILE)
    view._refresh_all()
    compact = CompactNuzlockeView(view)
    compact.resize(320, 300)
    compact.show()
    app.processEvents()
    assert compact.size().width() == 320
    assert compact.size().height() == 300
    assert compact.scroll_area.widget().width() <= compact.scroll_area.viewport().width()
    assert compact.scroll_area.verticalScrollBar().maximum() > 0
    compact.close()
