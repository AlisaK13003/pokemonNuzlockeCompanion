"""Immutable observations of existing runtime facts, never a safety controller."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class HealthState(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    STALE = "stale"
    UNSUPPORTED = "unsupported"


class PCPhase(str, Enum):
    UNKNOWN = "unknown"
    CONNECTING = "connecting"
    MONITORING = "monitoring"
    VALIDATING = "validating"
    BASELINE = "baseline"
    DISCOVERING = "discovering"
    PAUSED = "paused"
    REDISCOVERY = "rediscovery"


@dataclass(frozen=True)
class ConnectionHealth:
    state: HealthState
    connected: bool
    age_seconds: float | None
    domain: str | None
    stream_identity: tuple[str | None, ...]


@dataclass(frozen=True)
class PartyReadHealth:
    state: HealthState
    present: bool
    fresh: bool
    raw_valid: bool | None
    display_valid: bool
    members: tuple
    warning: str | None
    error: str | None


@dataclass(frozen=True)
class CommandChannelHealth:
    state: HealthState
    reported_state: str = "UNKNOWN"


@dataclass(frozen=True)
class PCMonitoringHealth:
    state: HealthState = HealthState.UNKNOWN
    phase: PCPhase = PCPhase.UNKNOWN
    layout_resolved: bool | None = None
    monitoring_active: bool | None = None
    boxed_count: int | None = None
    discovery_state: HealthState = HealthState.UNKNOWN
    session_id: str | None = None
    layout_session_id: str | None = None
    recovery_message: str = ""
    last_event: str = ""
    rediscover_enabled: bool = True

    @property
    def recovering(self) -> bool:
        return self.phase in {
            PCPhase.VALIDATING,
            PCPhase.BASELINE,
            PCPhase.DISCOVERING,
            PCPhase.REDISCOVERY,
        }


@dataclass(frozen=True)
class DiagnosticHealthObservation:
    connection: ConnectionHealth
    party: PartyReadHealth
    command: CommandChannelHealth
    pc: PCMonitoringHealth
    game_name: str
    progress_percent: float | None = None
    events: tuple[str, ...] = ()


def finite_number(value) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _payload(wrapper) -> Mapping:
    value = getattr(wrapper, "payload", {})
    return value if isinstance(value, Mapping) else {}


def observe_command_channel(reported_state: object, *, supported: bool = True) -> CommandChannelHealth:
    reported = reported_state if isinstance(reported_state, str) and reported_state else "UNKNOWN"
    token = reported.upper()
    state = (
        HealthState.UNSUPPORTED
        if not supported
        else HealthState.HEALTHY
        if token in {"READY", "ACKNOWLEDGED", "AVAILABLE"}
        else HealthState.STALE
        if token in {"RAM STALE", "ACK STALE"}
        else HealthState.UNHEALTHY
        if token in {"UNAVAILABLE", "START ACK TIMEOUT"}
        else HealthState.UNKNOWN
    )
    return CommandChannelHealth(state, reported)


def observe_diagnostic_health(
    snapshot: object,
    *,
    game_name: str,
    command: CommandChannelHealth,
    pc: PCMonitoringHealth,
    live_party_supported: bool = True,
    now: float,
) -> DiagnosticHealthObservation:
    details = getattr(snapshot, "details", {})
    details = details if isinstance(details, Mapping) else {}
    connection_flag = getattr(snapshot, "connected", None)
    connected = connection_flag is True
    payload = _payload(details.get("party_payload"))
    heartbeat = _payload(details.get("heartbeat"))
    domain = payload.get("active_domain") or heartbeat.get("active_domain")
    domain = domain if isinstance(domain, str) else None
    age = finite_number(details.get("ram_age_seconds"))
    received = finite_number(getattr(details.get("party_payload"), "received_at", None))
    if age is None and received is not None:
        age = max(0.0, now - received)
    connection = ConnectionHealth(
        HealthState.HEALTHY
        if connected
        else HealthState.UNHEALTHY
        if connection_flag is False
        else HealthState.UNKNOWN,
        connected,
        max(0.0, age) if age is not None else None,
        domain,
        tuple(
            payload.get(key) if isinstance(payload.get(key), str) else None
            for key in ("run_id", "core", "domain", "pointer_value")
        ),
    )
    raw = details.get("party_state")
    display = details.get("display_party_state") or raw
    values = getattr(display, "pokemon", ())
    members = tuple(values) if isinstance(values, (tuple, list)) else ()
    fresh = details.get("party_payload_fresh") is True

    def valid(party):
        values = getattr(party, "pokemon", ())
        count_valid = getattr(party, "party_count_valid", None)
        if (
            party is None
            or not isinstance(count_valid, bool)
            or not isinstance(values, (tuple, list))
        ):
            return None
        if count_valid is False or getattr(party, "error", None):
            return False
        flags = tuple(getattr(mon, "checksum_valid", None) for mon in values)
        return all(flags) if all(isinstance(flag, bool) for flag in flags) else None

    raw_valid = valid(raw) if raw is not None else None
    display_valid = valid(display) is True
    warning = getattr(display, "live_read_warning", None)
    error = getattr(display, "error", None)
    warning = warning if isinstance(warning, str) else None
    error = error if isinstance(error, str) else None
    state = (
        HealthState.UNSUPPORTED
        if not live_party_supported
        else HealthState.UNKNOWN
        if raw_valid is None
        else HealthState.STALE
        if not fresh
        else HealthState.HEALTHY
        if raw_valid and not warning
        else HealthState.UNHEALTHY
    )
    party = PartyReadHealth(
        state, display is not None, fresh, raw_valid, display_valid, members, warning, error
    )
    return DiagnosticHealthObservation(connection, party, command, pc, game_name)
