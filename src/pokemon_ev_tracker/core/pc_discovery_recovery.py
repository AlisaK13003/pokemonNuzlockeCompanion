"""Per-scan chunk recovery and completeness gating, independent of IO and RAM decoding.

The caller serializes access using its existing discovery lock. Transitions are
consumed synchronously: failure stops processing before any later retry is sent.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Protocol

PC_DISCOVERY_RETRY_TIMEOUT_SECONDS = 2.0
PC_DISCOVERY_RETRY_LIMIT = 3
PC_DISCOVERY_STALL_SECONDS = 2.0
LOGGER = logging.getLogger(__name__)


class ChunkScan(Protocol):
    """Only completeness facts are needed; memory interpretation stays with the game."""

    scan_id: str
    source: str
    last_chunk_received_at: float
    end_received: bool
    expected_chunk_count: int | None
    expected_byte_count: int | None
    total_bytes: int
    data: bytearray
    received_chunk_indexes: set[int]
    missing_chunk_indexes: set[int]

    def first_missing_chunk_index(self) -> int | None: ...


class RecoveryKind(Enum):
    RETRYING = auto()
    MISSING_FAILED = auto()
    STALLED = auto()
    COUNTS_FAILED = auto()
    READY = auto()


@dataclass(frozen=True, slots=True)
class RecoveryTransition:
    kind: RecoveryKind
    chunk_index: int | None = None
    reason: str | None = None


# The delivery port returns the accepted route, or None. It alone performs IO.
RetrySender = Callable[[str, int, str], str | None]


@dataclass
class PCDiscoveryRecovery:
    """One owner for attempts, in-flight retries and deadlines for one scan.

    The existing accumulator exposes these collections for compatibility, without
    maintaining second copies. A new accumulator creates a fresh recovery owner.
    """

    attempts: dict[int, int] = field(default_factory=dict)
    in_flight: set[int] = field(default_factory=set)
    requested_at: dict[int, float] = field(default_factory=dict)
    clock: Callable[[], float] = field(default=lambda: time.monotonic(), repr=False)

    def received(self, chunk_index: int) -> None:
        self.in_flight.discard(chunk_index)
        self.requested_at.pop(chunk_index, None)

    def request_retry(
        self, scan_id: str, chunk_index: int, source: str, send: RetrySender,
    ) -> bool:
        if chunk_index in self.in_flight:
            return True
        attempts = self.attempts.get(chunk_index, 0)
        if attempts >= PC_DISCOVERY_RETRY_LIMIT:
            return False
        route = send(scan_id, chunk_index, source)
        if route is None:
            return False
        self.attempts[chunk_index] = attempts + 1
        self.in_flight.add(chunk_index)
        self.requested_at[chunk_index] = self.clock()
        LOGGER.warning(
            "Requesting retransmission of PC discovery chunk: scan=%s chunk=%d attempt=%d via=%s",
            scan_id, chunk_index, attempts + 1, route,
        )
        return True

    def expired_retries(
        self, scan: ChunkScan, now: float, send: RetrySender,
    ) -> Iterator[RecoveryTransition]:
        expired = [
            index for index in self.in_flight
            if now - self.requested_at.get(index, now) >= PC_DISCOVERY_RETRY_TIMEOUT_SECONDS
        ]
        for chunk_index in sorted(expired):
            self.received(chunk_index)
            if self.attempts.get(chunk_index, 0) >= PC_DISCOVERY_RETRY_LIMIT:
                yield RecoveryTransition(
                    RecoveryKind.MISSING_FAILED, chunk_index, "retransmission timed out",
                )
                return
            if self.request_retry(scan.scan_id, chunk_index, scan.source, send):
                yield RecoveryTransition(RecoveryKind.RETRYING, chunk_index)
            else:
                yield RecoveryTransition(
                    RecoveryKind.MISSING_FAILED, chunk_index, "retry request could not be sent",
                )
                return

    def stalled(
        self, scan: ChunkScan, now: float, send: RetrySender,
    ) -> RecoveryTransition | None:
        if self.in_flight or now - scan.last_chunk_received_at < PC_DISCOVERY_STALL_SECONDS:
            return None
        missing_index = scan.first_missing_chunk_index()
        if missing_index is not None:
            return self._recover_missing(scan, missing_index, scan.source, send)
        if not scan.end_received:
            return RecoveryTransition(RecoveryKind.STALLED)
        return None

    def completion(
        self, scan: ChunkScan, source: str, send: RetrySender,
    ) -> RecoveryTransition | None:
        expected_count = scan.expected_chunk_count
        expected_bytes = scan.expected_byte_count
        if not scan.end_received or expected_count is None:
            return None
        missing = sorted(set(range(expected_count)) - scan.received_chunk_indexes)
        scan.missing_chunk_indexes = set(missing)
        if missing:
            return self._recover_missing(scan, missing[0], source, send)
        if (
            scan.received_chunk_indexes != set(range(expected_count))
            or expected_bytes != scan.total_bytes
            or len(scan.data) != expected_bytes
        ):
            return RecoveryTransition(RecoveryKind.COUNTS_FAILED, reason=(
                "Discovery transport error: end packet counts do not match the "
                "received chunk indexes and bytes."
            ))
        return RecoveryTransition(RecoveryKind.READY)

    def _recover_missing(
        self, scan: ChunkScan, chunk_index: int, source: str, send: RetrySender,
    ) -> RecoveryTransition:
        if self.request_retry(scan.scan_id, chunk_index, source, send):
            return RecoveryTransition(RecoveryKind.RETRYING, chunk_index)
        return RecoveryTransition(
            RecoveryKind.MISSING_FAILED, chunk_index, "retry limit reached or request failed",
        )
