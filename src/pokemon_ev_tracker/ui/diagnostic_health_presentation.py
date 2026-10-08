"""Pure wording for typed runtime health; messages never determine readiness."""

from dataclasses import dataclass

from pokemon_ev_tracker.core.diagnostic_health import (
    DiagnosticHealthObservation,
    HealthState,
    PCMonitoringHealth,
    PCPhase,
)

PC_LABELS = {
    PCPhase.UNKNOWN: "Unavailable",
    PCPhase.CONNECTING: "Connecting",
    PCPhase.MONITORING: "Monitoring",
    PCPhase.VALIDATING: "Validating PC layout",
    PCPhase.BASELINE: "Establishing baseline",
    PCPhase.DISCOVERING: "Discovering PC layout",
    PCPhase.PAUSED: "Monitoring paused",
    PCPhase.REDISCOVERY: "Rediscovery required",
}


@dataclass(frozen=True)
class PCHealthPresentation:
    status: str
    layout: str
    boxed: str
    acquisition: str
    event: str

    @property
    def summary(self) -> str:
        return f"Status: {self.status}\nBox layout: {self.layout}\nBoxed Pokemon: {self.boxed}"


def format_pc_health(pc: PCMonitoringHealth) -> PCHealthPresentation:
    return PCHealthPresentation(
        PC_LABELS[pc.phase],
        "Resolved"
        if pc.layout_resolved
        else "Unresolved"
        if pc.layout_resolved is False
        else "Unavailable",
        str(pc.boxed_count) if pc.boxed_count is not None else "--",
        "Yes" if pc.monitoring_active else "No" if pc.monitoring_active is False else "Unavailable",
        pc.last_event,
    )


@dataclass(frozen=True)
class HealthField:
    value: str
    detail: str
    role: str = "info"


@dataclass(frozen=True)
class DiagnosticHealthPresentation:
    fields: tuple[tuple[str, HealthField], ...]
    last_update: str
    domain: str
    title: str
    description: str
    global_status: str
    role: str


def format_diagnostic_health(health: DiagnosticHealthObservation) -> DiagnosticHealthPresentation:
    connection, party, command, pc = health.connection, health.party, health.command, health.pc
    connected, fresh, warning = connection.connected, party.fresh, party.warning
    valid = (
        party.display_valid
        and party.raw_valid is True
        and party.state is not HealthState.UNSUPPORTED
    )
    pc_text = format_pc_health(pc)
    ready = command.state is HealthState.HEALTHY
    problem = command.state in {HealthState.UNHEALTHY, HealthState.STALE}
    pc_ready = pc.state is HealthState.HEALTHY and pc.phase is PCPhase.MONITORING
    layout_ready = pc.layout_resolved is True
    party_value = (
        "Unavailable"
        if party.state is HealthState.UNSUPPORTED
        else "Valid"
        if valid and fresh and not warning
        else (
            "Read warning"
            if fresh and warning
            else "Invalid"
            if fresh and not valid
            else "Stale"
            if party.present
            else "Waiting"
        )
    )
    fields = (
        (
            "connection",
            HealthField(
                "Connected" if connected else "Disconnected",
                "Emulator heartbeat received."
                if connected
                else "Waiting for the BizHawk Lua connection.",
                "success" if connected else "warning",
            ),
        ),
        (
            "game",
            HealthField(health.game_name, f"RAM domain · {connection.domain or 'Unavailable'}"),
        ),
        (
            "party",
            HealthField(
                party_value,
                str(
                    warning
                    or party.error
                    or (
                        f"{len(party.members)} party members decoded."
                        if valid
                        else "No valid party snapshot received."
                    )
                ),
                "success" if valid and fresh and not warning else "warning",
            ),
        ),
        (
            "command",
            HealthField(
                "Unverified"
                if command.reported_state == "UNKNOWN"
                else command.reported_state.replace("_", " ").title(),
                "Status from movement command acknowledgements.",
                "success" if ready else "warning" if problem else "info",
            ),
        ),
        (
            "pc",
            HealthField(
                pc_text.status,
                "Current PC resolver and monitor state.",
                "success" if pc_ready else "warning",
            ),
        ),
        (
            "layout",
            HealthField(
                pc_text.layout,
                "Validated layout for the current emulator session."
                if layout_ready
                else "Awaiting validation in this session.",
                "success" if layout_ready else "warning",
            ),
        ),
        (
            "acquisition",
            HealthField(pc_text.acquisition, "PC acquisition monitoring reported by the runtime."),
        ),
        (
            "boxed",
            HealthField(pc_text.boxed, pc.last_event or "Waiting for a validated PC snapshot."),
        ),
    )
    if not connected:
        title, description, state, role = (
            "Waiting for BizHawk",
            "Connect the emulator to inspect live system health.",
            "DISCONNECTED",
            "warning",
        )
    elif not fresh or not valid or warning:
        title, description, state, role = (
            "Connection needs attention",
            "The emulator heartbeat is available; party data is stale or has a read warning.",
            "ATTENTION",
            "warning",
        )
    elif pc.recovering:
        title, description, state, role = (
            "PC resolver is recovering",
            "Party data is current. PC monitoring is waiting for layout validation and its baseline.",
            "RECOVERING",
            "warning",
        )
    else:
        title, description, state, role = (
            "Connected · party current",
            f"BizHawk · {health.game_name}. Review command and PC health below.",
            "LIVE RAM",
            "success",
        )
        if ready and pc_ready and layout_ready:
            title, description = (
                "Connected & healthy",
                f"BizHawk · {health.game_name} · Party and PC monitoring current.",
            )
    return DiagnosticHealthPresentation(
        fields,
        f"LAST RAM UPDATE  ·  {connection.age_seconds:.1f}s ago"
        if connection.age_seconds is not None
        else "LAST RAM UPDATE  ·  Never",
        f"RAM DOMAIN  ·  {connection.domain or 'Unavailable'}",
        title,
        description,
        state,
        role,
    )
