"""Legacy presentation-mapping adapter. Production passes typed health directly.

Old callers can still preview status strings. These claimed display facts never
feed MainWindow, command guards, acquisition observers or recovery decisions.
"""

from collections.abc import Mapping
from dataclasses import replace

from pokemon_ev_tracker.core.diagnostic_health import (
    HealthState,
    PCMonitoringHealth,
    PCPhase,
    finite_number,
    observe_command_channel,
    observe_diagnostic_health,
)
from pokemon_ev_tracker.ui.diagnostic_health_presentation import PC_LABELS


def _without_prefix(value, prefix, fallback="Unavailable"):
    text = str(value).strip() if value is not None else ""
    if text.lower().startswith(prefix.lower()):
        text = text[len(prefix) :].strip()
    return text or fallback


def legacy_health_observation(snapshot, presentation, *, now):
    presentation = presentation if isinstance(presentation, Mapping) else {}
    status = _without_prefix(presentation.get("pc_status"), "Status:")
    layout = _without_prefix(presentation.get("pc_layout"), "Box layout:")
    boxed = _without_prefix(presentation.get("boxed_count"), "Boxed Pokemon:", "—")
    acquisition = _without_prefix(presentation.get("acquisition_status"), "Monitoring new catches:")
    phase = next(
        (phase for phase, text in PC_LABELS.items() if text.casefold() == status.casefold()),
        PCPhase.UNKNOWN,
    )
    if phase is PCPhase.UNKNOWN and any(
        word in status.lower()
        for word in ("discovering", "validating", "baseline", "rediscovery", "recover")
    ):
        phase = PCPhase.DISCOVERING
    pc = PCMonitoringHealth(
        state=HealthState.HEALTHY if status.casefold() == "monitoring" else HealthState.UNKNOWN,
        phase=phase,
        layout_resolved=layout.casefold() == "resolved",
        recovery_message=str(presentation.get("recovery_message") or ""),
        last_event=_without_prefix(presentation.get("pc_event"), "Last PC event:", ""),
        rediscover_enabled=bool(
            presentation.get("rediscover_enabled", getattr(snapshot, "connected", False))
        ),
    )
    command = observe_command_channel(str(presentation.get("command_state") or "UNKNOWN"))
    if command.state is HealthState.UNKNOWN and any(
        word in command.reported_state.upper() for word in ("STALE", "TIMEOUT", "UNAVAILABLE")
    ):
        command = replace(command, state=HealthState.UNHEALTHY)
    health = observe_diagnostic_health(
        snapshot,
        game_name=str(presentation.get("game_name") or "Unavailable"),
        pc=pc,
        command=command,
        now=now,
    )
    events = presentation.get("events") or ()
    events = (
        (events,)
        if isinstance(events, str)
        else events
        if isinstance(events, (tuple, list))
        else ()
    )
    health = replace(
        health,
        progress_percent=finite_number(presentation.get("discovery_progress")),
        events=tuple(str(event) for event in events),
    )
    return health, {"pc": status, "layout": layout, "boxed": boxed, "acquisition": acquisition}
