"""Conservative, game-neutral full-party faint transition observer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pokemon_ev_tracker.core.nuzlocke.death_detection import PartyHpSample


@dataclass(frozen=True)
class WipeMember:
    stable_id: str
    species: str
    nickname: str
    level: int | None
    current_hp: int
    met_location_id: int
    met_location_name: str | None
    met_level: int | None


@dataclass(frozen=True)
class PartyWipeCandidate:
    members: tuple[WipeMember, ...]
    detected_at: str
    run_id: str


class PartyWipeObserver:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._previous: dict[str, PartyHpSample] | None = None
        self._run_id: str | None = None
        self._stream_identity = None
        self._last_frame: int | None = None
        self._armed = False

    def observe(
        self,
        members: tuple[PartyHpSample, ...],
        *,
        connected: bool,
        valid_snapshot: bool,
        run_id: str | None,
        stream_identity=None,
        frame: int | None = None,
    ) -> PartyWipeCandidate | None:
        if not connected or not valid_snapshot or not run_id or not members:
            self.reset()
            return None
        current = {member.stable_id: member for member in members if member.stable_id}
        if (len(current) != len(members) or any(
            type(member.current_hp) is not int or member.current_hp < 0
            for member in members
        )):
            self.reset()
            return None
        incompatible = (
            run_id != self._run_id
            or stream_identity != self._stream_identity
            or (frame is not None and self._last_frame is not None and frame < self._last_frame)
            or (self._previous is not None and current.keys() != self._previous.keys())
        )
        if incompatible:
            self._previous = None
            self._armed = False
        previous = self._previous
        self._previous = current
        self._run_id = run_id
        self._stream_identity = stream_identity
        self._last_frame = frame
        conscious = any(member.current_hp > 0 for member in current.values())
        if conscious:
            self._armed = True
            return None
        if previous is None or not self._armed:
            return None
        self._armed = False
        return PartyWipeCandidate(
            members=tuple(WipeMember(
                stable_id=member.stable_id,
                species=member.species,
                nickname=member.nickname,
                level=member.level,
                current_hp=member.current_hp,
                met_location_id=member.met_location_id,
                met_location_name=member.met_location_name,
                met_level=member.met_level,
            ) for member in members),
            detected_at=datetime.now(UTC).isoformat(timespec="seconds"),
            run_id=run_id,
        )
