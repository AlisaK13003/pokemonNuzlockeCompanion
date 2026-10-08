from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from pokemon_ev_tracker.data_sources.base import DataSourceSnapshot
from pokemon_ev_tracker.ui.diagnostics_view import DiagnosticsView


@pytest.fixture
def diagnostics():
    app = QApplication.instance() or QApplication([])
    advanced = QWidget()
    QVBoxLayout(advanced).addWidget(QLabel("Existing runtime tooling"))
    calls = []
    view = DiagnosticsView(advanced, lambda: calls.append("rediscover"))
    yield app, view, advanced, calls
    view.close()


def _snapshot(*, connected=False, fresh=False, members=(), age=None):
    return DataSourceSnapshot(
        "BizHawk RAM",
        connected,
        {
            "party_payload_fresh": fresh,
            "ram_age_seconds": age,
            "party_state": SimpleNamespace(
                party_count_valid=True, error=None, pokemon=members, live_read_warning=None,
            ) if members or fresh else None,
            "heartbeat": SimpleNamespace(payload={"active_domain": "Main RAM"}) if connected else None,
        },
    )


def test_disconnected_diagnostics_has_no_sample_data_or_ready_command_channel(diagnostics):
    _app, view, _advanced, calls = diagnostics
    view.refresh(_snapshot(), {})

    assert view.fields["connection"].value.text() == "Disconnected"
    assert view.fields["command"].value.text() == "Unverified"
    assert view.fields["game"].value.text() == "Unavailable"
    assert view.fields["boxed"].value.text() == "—"
    assert all(row[0].isHidden() for row in view.party_rows)
    assert view.event_list.item(0).text() == "No runtime events received yet."
    assert not view.rediscover_button.isEnabled()
    assert calls == []


def test_heartbeat_does_not_imply_fresh_party_or_command_health(diagnostics):
    _app, view, _advanced, _calls = diagnostics
    mon = SimpleNamespace(
        species_id=183, species="Marill", nickname="Marill", stable_id="pid:1234:ot:0011:0022",
        checksum_valid=True, current_hp=19, max_hp=31,
    )
    view.refresh(_snapshot(connected=True, fresh=False, members=(mon,), age=8.4))

    assert view.fields["connection"].value.text() == "Connected"
    assert view.fields["party"].value.text() == "Stale"
    assert view.global_status.text() == "ATTENTION"
    assert view.party_state_label.text() == "LAST SNAPSHOT"
    assert view.fields["command"].value.text() == "Unverified"
    assert view.last_update.text().endswith("8.4s ago")
    assert view.party_rows[0][2].toolTip() == mon.stable_id
    assert view.party_rows[0][3].text() == "HP 19 / 31"

    view.refresh(_snapshot(connected=True, fresh=True, members=(mon,), age=0.2))

    assert view.fields["party"].value.text() == "Valid"
    assert view.global_status.text() == "LIVE RAM"
    assert view.fields["command"].value.text() == "Unverified"
    assert view.party_state_label.text() == "LIVE"


def test_pc_recovery_uses_existing_messages_and_preserves_advanced_widget(diagnostics):
    _app, view, advanced, calls = diagnostics
    presentation = {
        "game_name": "Pokémon Platinum",
        "command_state": "ACKNOWLEDGED",
        "pc_status": "Status: Discovering PC layout",
        "pc_layout": "Box layout: Unresolved",
        "boxed_count": "Boxed Pokemon: 7",
        "acquisition_status": "Monitoring new catches: No",
        "pc_event": "Last PC event: Session changed; PC layout discovery requested.",
        "recovery_message": "Box 1 Slot 1 may be empty.",
        "rediscover_enabled": False,
        "discovery_progress": 43,
    }
    view.refresh(_snapshot(connected=True, fresh=True), presentation)
    view.refresh(_snapshot(connected=True, fresh=True), presentation)

    assert view.fields["pc"].value.text() == "Discovering PC layout"
    assert view.fields["layout"].value.text() == "Unresolved"
    assert view.fields["boxed"].value.text() == "7"
    assert view.fields["acquisition"].value.text() == "No"
    assert view.global_status.text() == "RECOVERING"
    assert view.event_list.count() == 2
    assert view.progress.value() == 43
    assert not view.rediscover_button.isEnabled()
    assert view.advanced_widget is advanced
    assert view.advanced_scroll_area.widget() is advanced

    view.advanced_button.click()
    assert view.stack.currentWidget() is view.advanced_scroll_area
    view.section_buttons["status"].click()
    assert view.stack.currentWidget() is view.status_scroll_area
    presentation["rediscover_enabled"] = True
    view.refresh(_snapshot(connected=True, fresh=True), presentation)
    view.rediscover_button.click()
    assert calls == ["rediscover"]


def test_healthy_hero_requires_verified_commands_and_current_pc_monitoring(diagnostics):
    _app, view, _advanced, _calls = diagnostics
    snapshot = _snapshot(connected=True, fresh=True)
    presentation = {"command_state": "ACKNOWLEDGED", "pc_status": "Status: Monitoring",
                    "pc_layout": "Box layout: Resolved"}
    view.refresh(snapshot, presentation)
    assert view.hero_title.text() == "Connected & healthy"
    for key in ("party", "pc", "layout", "command"):
        assert view.fields[key].value.property("statusRole") == "success"
    for key, value in (("command_state", "UNKNOWN"), ("pc_status", "Status: Monitoring paused"),
                       ("pc_layout", "Box layout: Unresolved")):
        view.refresh(snapshot, {**presentation, key: value})
        assert view.hero_title.text() != "Connected & healthy"
    view.refresh(_snapshot(connected=True, fresh=False), presentation)
    assert view.hero_title.text() != "Connected & healthy"


@pytest.mark.parametrize("width, columns", [(1200, 4), (620, 2), (400, 1)])
def test_status_reflows_without_horizontal_overflow(diagnostics, width, columns):
    app, view, _advanced, _calls = diagnostics
    view.resize(width, 480)
    view.show()
    app.processEvents()

    assert view._columns == columns
    viewport_width = view.status_scroll_area.viewport().width()
    assert view.status_page.width() <= viewport_width
    for field in view.fields.values():
        assert field.geometry().right() < view.status_page.width()
    assert view.status_scroll_area.verticalScrollBar().maximum() > 0
