"""Characterization of health wording before the typed observation migration."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_diagnostics_view import _snapshot, diagnostics
from test_training_integration import make_window

from pokemon_ev_tracker.core.diagnostic_health import (
    HealthState,
    PCMonitoringHealth,
    PCPhase,
    observe_command_channel,
    observe_diagnostic_health,
)
from pokemon_ev_tracker.ui.diagnostic_health_presentation import (
    format_diagnostic_health,
    format_pc_health,
)

health_view = diagnostics
health_window = make_window


def scenarios():
    member = SimpleNamespace(
        species_id=183,
        species="Marill",
        nickname="Blue",
        stable_id="pid:1",
        checksum_valid=True,
        current_hp=20,
        max_hp=31,
    )
    good = _snapshot(connected=True, fresh=True, members=(member,), age=0.2)
    invalid = _snapshot(
        connected=True,
        fresh=True,
        members=(SimpleNamespace(**{**vars(member), "checksum_valid": False}),),
        age=0.2,
    )
    ready = {
        "game_name": "Pokémon Platinum",
        "command_state": "READY",
        "pc_status": "Status: Monitoring",
        "pc_layout": "Box layout: Resolved",
        "boxed_count": "Boxed Pokemon: 2",
        "acquisition_status": "Monitoring new catches: Yes",
    }
    return {
        "connected-valid": (good, ready),
        "connected-invalid": (invalid, ready),
        "connected-stale": (
            _snapshot(connected=True, fresh=False, members=(member,), age=7),
            ready,
        ),
        "disconnected": (_snapshot(), {}),
        "unknown-command": (good, {**ready, "command_state": "UNKNOWN"}),
        "pending-command": (good, {**ready, "command_state": "WAITING FOR ACK"}),
        "stale-command": (good, {**ready, "command_state": "ACK STALE"}),
        "pc-unavailable": (
            good,
            {**ready, "pc_status": "Status: Connecting", "pc_layout": "Box layout: Unresolved"},
        ),
        "pc-discovering": (
            good,
            {**ready, "pc_status": "Status: Discovering PC layout", "rediscover_enabled": False},
        ),
        "pc-baseline": (good, {**ready, "pc_status": "Status: Establishing baseline"}),
        "pc-failed": (
            good,
            {
                **ready,
                "pc_status": "Status: Monitoring paused",
                "recovery_message": "Retry current layout.",
            },
        ),
    }


def capture(view):
    return {
        "hero": view.hero_title.text(),
        "description": view.hero_description.text(),
        "global": view.global_status.text(),
        "update": view.last_update.text(),
        "domain": view.domain.text(),
        "fields": {
            key: (field.value.text(), field.value.property("statusRole"))
            for key, field in view.fields.items()
        },
        "recovery": view.recovery_message.text(),
        "rediscover": view.rediscover_button.isEnabled(),
        "party-state": view.party_state_label.text(),
    }


@pytest.mark.parametrize("name", scenarios())
def test_existing_health_wording(health_view, name):
    _app, view, _, _ = health_view
    snapshot, presentation = scenarios()[name]
    view.refresh(snapshot, presentation)
    expected = json.loads(
        (Path(__file__).parent / "fixtures/diagnostic-health.json").read_text(encoding="utf-8")
    )
    # JSON represents tuples as lists.
    assert json.loads(json.dumps(capture(view))) == expected[name]


def observe(snapshot, **kwargs):
    return observe_diagnostic_health(
        snapshot,
        game_name="Pokémon Platinum",
        now=100,
        command=kwargs.pop("command", observe_command_channel("UNKNOWN")),
        pc=kwargs.pop("pc", PCMonitoringHealth()),
        **kwargs,
    )


@pytest.mark.parametrize(
    "case,state",
    [
        ("connected-valid", HealthState.HEALTHY),
        ("connected-invalid", HealthState.UNHEALTHY),
        ("connected-stale", HealthState.STALE),
        ("disconnected", HealthState.UNKNOWN),
    ],
)
def test_party_health_observed_independently_of_heartbeat(case, state):
    health = observe(scenarios()[case][0])
    assert health.party.state is state
    assert health.command.state is HealthState.UNKNOWN
    assert health.pc.state is HealthState.UNKNOWN
    assert format_diagnostic_health(health).title != "Connected & healthy"
    with pytest.raises(FrozenInstanceError):
        health.party.fresh = True


@pytest.mark.parametrize(
    "token,state",
    [
        ("READY", HealthState.HEALTHY),
        ("WAITING FOR ACK", HealthState.UNKNOWN),
        ("ACK STALE", HealthState.STALE),
        ("RAM STALE", HealthState.STALE),
        ("START ACK TIMEOUT", HealthState.UNHEALTHY),
        ("UNAVAILABLE", HealthState.UNHEALTHY),
        ("Ready-looking message", HealthState.UNKNOWN),
        ({"ready": True}, HealthState.UNKNOWN),
    ],
)
def test_exact_runtime_command_tokens(token, state):
    assert observe_command_channel(token).state is state
    assert observe_command_channel(token, supported=False).state is HealthState.UNSUPPORTED


def test_raw_invalid_display_fallback_cannot_claim_healthy():
    snapshot = scenarios()["connected-invalid"][0]
    display = scenarios()["connected-valid"][0].details["party_state"]
    snapshot.details["display_party_state"] = display
    health = observe(snapshot)
    assert health.party.raw_valid is False and health.party.display_valid
    assert health.party.state is HealthState.UNHEALTHY
    assert format_diagnostic_health(health).global_status == "ATTENTION"
    snapshot.details["display_party_state"] = replace_display(display)
    assert dict(format_diagnostic_health(observe(snapshot)).fields)["party"].value == "Read warning"


def replace_display(display):
    return SimpleNamespace(**{**vars(display), "live_read_warning": "last good sample"})


@pytest.mark.parametrize(
    "details",
    [
        None,
        "bad",
        {},
        {"ram_age_seconds": float("nan")},
        {"ram_age_seconds": True},
        {"ram_age_seconds": 10**1000},
        {
            "party_payload": SimpleNamespace(payload={"active_domain": []}, received_at="bad"),
            "party_payload_fresh": "true",
            "party_state": SimpleNamespace(pokemon="bad", party_count_valid="true"),
        },
    ],
    ids=["none", "string", "empty", "nan", "boolean", "overflow", "malformed"],
)
def test_missing_malformed_observations_do_not_invent_readiness(details):
    health = observe(SimpleNamespace(connected="true", details=details))
    assert health.connection.state is HealthState.UNKNOWN
    assert health.party.state is HealthState.UNKNOWN
    assert health.connection.age_seconds is None
    assert format_diagnostic_health(health).global_status == "DISCONNECTED"


def test_session_restart_and_unsupported_health_are_explicit():
    snapshot = scenarios()["connected-valid"][0]
    snapshot.details["party_payload"] = SimpleNamespace(payload={"run_id": "one", "core": "NDS"})
    first = observe(snapshot)
    snapshot.details["party_payload"].payload["run_id"] = "two"
    second = observe(
        snapshot,
        live_party_supported=False,
        command=observe_command_channel("READY", supported=False),
        pc=PCMonitoringHealth(state=HealthState.UNSUPPORTED),
    )
    assert first.connection.stream_identity[0] == "one"
    assert second.connection.stream_identity[0] == "two"
    assert second.party.state is second.command.state is second.pc.state is HealthState.UNSUPPORTED
    assert format_diagnostic_health(second).title != "Connected & healthy"


@pytest.mark.parametrize(
    "lifecycle,progress,ready,phase,state",
    [
        ("discovering", {"status": "scanning"}, False, PCPhase.DISCOVERING, HealthState.UNKNOWN),
        (
            "cache-pending",
            {"status": "cache-pending"},
            False,
            PCPhase.VALIDATING,
            HealthState.UNKNOWN,
        ),
        (
            "resolved-awaiting-baseline",
            {"status": "completed"},
            False,
            PCPhase.BASELINE,
            HealthState.UNKNOWN,
        ),
        (
            "discovery-failed",
            {"status": "failed", "error": "missing anchor"},
            False,
            PCPhase.PAUSED,
            HealthState.UNHEALTHY,
        ),
        ("stale", {}, False, PCPhase.REDISCOVERY, HealthState.STALE),
        ("baseline-ready", {"status": "completed"}, True, PCPhase.MONITORING, HealthState.HEALTHY),
    ],
    ids=["pending", "validation", "baseline", "failed", "stale", "ready"],
)
def test_pc_projection_matches_widgets_and_controls(
    health_window, lifecycle, progress, ready, phase, state
):
    window, _, _ = health_window()
    window._pc_lua_run_id = "lua"
    window._pc_resolver_lifecycle = lifecycle
    window._pc_monitoring_ready = ready
    payload = {
        "pc_storage_resolver_status": "resolved for this session",
        "session_pc_run_id": "lua",
    }
    storage = SimpleNamespace(available=True, valid_pokemon=())
    health = window._refresh_pc_storage_compact_status(
        True, storage, payload, None, progress, {}, {}
    )
    assert health.phase is phase and health.state is state
    text = format_pc_health(health)
    assert window.pc_storage_status_label.text() == f"Status: {text.status}"
    assert window.pc_discover_current_layout_button.isEnabled() == health.rediscover_enabled
    assert health.rediscover_enabled == (phase not in {PCPhase.DISCOVERING, PCPhase.VALIDATING})
    snapshot = scenarios()["connected-valid"][0]
    window.diagnostics_view.refresh_health(
        observe(snapshot, pc=health, command=observe_command_channel("READY"))
    )
    assert window.diagnostics_view.fields["pc"].value.text() == text.status
    assert window.diagnostics_view.recovery_message.text() == health.recovery_message
    if phase is PCPhase.MONITORING:
        assert window.diagnostics_view.hero_title.text() == "Connected & healthy"


def test_ui_label_text_and_enabled_state_are_not_health_authority(health_window, monkeypatch):
    window, source, _ = health_window()
    source.opponents(66)
    for label in (
        window.pc_storage_status_label,
        window.pc_storage_layout_label,
        window.pc_storage_occupied_label,
        window.pc_storage_catch_status_label,
        window.pc_storage_last_event_label,
        window.pc_storage_recovery_label,
    ):
        monkeypatch.setattr(label, "text", Mock(side_effect=AssertionError("widget readback")))
    monkeypatch.setattr(
        window.pc_discover_current_layout_button,
        "isEnabled",
        Mock(side_effect=AssertionError("widget state readback")),
    )
    acquire = Mock(wraps=window.acquisitions._party.observe)
    window.acquisitions._party.observe = acquire
    window._refresh_ram_backend_debug()
    assert acquire.call_count == 1
    assert window.diagnostics_view.fields["pc"].value.text() == "Connecting"
    assert "Status: Connecting" in window.shell.system_label.text()


def test_health_readiness_ignores_recovery_message_wording():
    pc = PCMonitoringHealth(
        state=HealthState.UNHEALTHY,
        phase=PCPhase.PAUSED,
        recovery_message="Monitoring Resolved Ready",
        layout_resolved=False,
    )
    health = observe(
        scenarios()["connected-valid"][0], pc=pc, command=observe_command_channel("UNKNOWN")
    )
    assert format_diagnostic_health(health).title != "Connected & healthy"
    assert not pc.recovering


def test_recovery_transition_and_session_change_do_not_retain_health(health_window):
    window, _, _ = health_window()
    window._pc_lua_run_id = "old"
    storage = SimpleNamespace(available=True, valid_pokemon=())
    payload = {
        "pc_storage_resolver_status": "resolved for this session",
        "session_pc_run_id": "old",
    }
    snapshot = scenarios()["connected-valid"][0]
    for lifecycle, progress, ready in (
        ("discovering", {"status": "scanning"}, False),
        ("discovery-failed", {"status": "failed", "error": "anchor missing"}, False),
        ("baseline-ready", {"status": "completed"}, True),
    ):
        window._pc_resolver_lifecycle, window._pc_monitoring_ready = lifecycle, ready
        pc = window._refresh_pc_storage_compact_status(
            True, storage, payload, None, progress, {}, {}
        )
        window.diagnostics_view.refresh_health(
            observe(snapshot, pc=pc, command=observe_command_channel("READY"))
        )
        assert window.diagnostics_view.recovery_message.text() == pc.recovery_message
    assert window.diagnostics_view.hero_title.text() == "Connected & healthy"
    assert window.diagnostics_view.recovery_message.isHidden()
    window._reset_pc_baseline()
    window._pc_lua_run_id = "new"
    window._pc_resolver_lifecycle = "resolved-awaiting-baseline"
    changed = window._refresh_pc_storage_compact_status(True, storage, payload, None, {}, {}, {})
    assert changed.session_id == "new" and changed.layout_session_id == "old"
    assert not changed.layout_resolved and not changed.monitoring_active
    window.diagnostics_view.refresh_health(
        observe(snapshot, pc=changed, command=observe_command_channel("READY"))
    )
    assert window.diagnostics_view.hero_title.text() != "Connected & healthy"


def test_missing_layout_session_tag_is_not_resolved(health_window):
    window, _, _ = health_window()
    window._pc_lua_run_id = None
    window._pc_resolver_lifecycle = "resolved-awaiting-baseline"
    health = window._refresh_pc_storage_compact_status(
        True, None, {"pc_storage_resolver_status": "resolved for this session"},
        None, {}, {}, {})
    assert health.layout_resolved is False
    assert window.pc_storage_layout_label.text() == "Box layout: Unresolved"
