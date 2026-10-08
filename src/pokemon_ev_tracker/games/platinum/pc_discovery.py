"""Python-side analysis for chunked BizHawk PC storage diagnostics."""

from __future__ import annotations

import struct
import time
from array import array
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from pokemon_ev_tracker.pokemon.gen4.structure import decode_box_pokemon

MAIN_RAM_BASE = 0x02000000
MAIN_RAM_SIZE = 0x400000
BOX_COUNT = 18
SLOTS_PER_BOX = 30
RECORD_SIZE = 136
RECORDS_SIZE = BOX_COUNT * SLOTS_PER_BOX * RECORD_SIZE
ANCHOR_CONTEXT_BYTES = 0x400
INSPECTION_BEFORE_BYTES = 0x4000
INSPECTION_AFTER_BYTES = 0x20000
MAX_CHUNK_BYTES = 0x1000
MAX_BROAD_HITS = 256
MAX_PC_REGION_INVALID_RECORDS = 0
MIN_SESSION_LAYOUT_OCCUPIED_RECORDS = 1
PC_DISCOVERY_STALL_SECONDS = 2.0
PLATINUM_PARTY_POINTER_ADDRESS = 0x02101D2C
# Historical failed hypothesis retained for old diagnostic payload decoding only.
PLATINUM_RUNTIME_PC_HEADER_OFFSET = 0x19EB4
PLATINUM_RUNTIME_PC_RECORD_OFFSET = 0x19EDC


def _empty_box_record_kind(record: bytes) -> str | None:
    if len(record) != RECORD_SIZE or record[:8] != bytes(8):
        return None
    return "zeroed" if record.count(0) == RECORD_SIZE else "zero-header"


def _decode_occupied(record: bytes, address: int) -> dict[str, Any] | None:
    if len(record) != RECORD_SIZE or _empty_box_record_kind(record) is not None:
        return None
    if int.from_bytes(record[4:6], "little") != 0:
        return None
    decoded = decode_box_pokemon(record, address)
    if not decoded.checksum_valid or not 1 <= decoded.species_id <= 493:
        return None
    return {
        "address": f"0x{address:08X}",
        "species_id": decoded.species_id,
        "nickname": decoded.nickname,
        "stable_id": decoded.stable_id,
        "raw_hex": record.hex().upper(),
        "pid": f"0x{decoded.pid:08X}",
        "checksum": f"0x{int.from_bytes(record[6:8], 'little'):04X}",
    }


@dataclass
class PCStorageDiscoveryAccumulator:
    scan_id: str
    mode: str
    total_bytes: int
    start_address: int
    anchor_address: int | None = None
    anchor_data_offset: int = 0
    expected_species_id: int | None = None
    expected_nickname: str | None = None
    lua_run_id: str | None = None
    runtime_base_address: int | None = None
    runtime_pointer_slot: int | None = None
    runtime_pointer_semantics: str | None = None
    runtime_header_offset: int | None = None
    runtime_record_offset: int | None = None
    predicted_pc_header_address: int | None = None
    predicted_first_box_record_address: int | None = None
    party_records_address: int | None = None
    party_count: int = 0
    party_record_size: int = 236
    party_range_valid: bool = False
    source: str = "tcp"
    started_at: float = field(default_factory=time.monotonic)
    data: bytearray = field(default_factory=bytearray)
    valid_hits: list[dict[str, Any]] = field(default_factory=list)
    valid_offsets: set[int] = field(default_factory=set)
    expected_hits: list[dict[str, Any]] = field(default_factory=list)
    broad_valid_record_count: int = 0
    inspection_hits: list[dict[str, Any]] = field(default_factory=list)
    inspection_record_count: int = 0
    received_chunk_count: int = 0
    received_chunk_indexes: set[int] = field(default_factory=set)
    highest_chunk_index_seen: int = -1
    missing_chunk_indexes: set[int] = field(default_factory=set)
    expected_chunk_count: int | None = None
    estimated_chunk_count: int | None = None
    expected_byte_count: int | None = None
    end_received: bool = False
    last_chunk_received_at: float = field(default_factory=time.monotonic)
    last_transport_log_at: float = field(default_factory=time.monotonic)
    pending_chunks: dict[int, tuple[int, bytes, int]] = field(default_factory=dict)
    retry_attempts: dict[int, int] = field(default_factory=dict)
    retry_in_flight: set[int] = field(default_factory=set)
    retry_requested_at: dict[int, float] = field(default_factory=dict)
    completion_event: dict[str, Any] | None = None
    completion_source: str | None = None
    _record_scan_offset: int = 0
    _anchor_next_slot: int = 0
    _anchor_records: list[dict[str, Any]] = field(default_factory=list)
    _active_party_ranges: tuple[tuple[int, int, int], ...] = field(
        default=(), init=False, repr=False
    )

    def __post_init__(self) -> None:
        if not self._party_range_available():
            return
        self._active_party_ranges = tuple(
            (
                self.party_records_address + index * self.party_record_size,
                self.party_records_address + (index + 1) * self.party_record_size,
                index + 1,
            )
            for index in range(self.party_count)
        )

    def _party_range_available(self) -> bool:
        return (
            self.party_range_valid
            and self.party_records_address is not None
            and self.party_record_size == 236
            and 0 <= self.party_count <= 6
            and MAIN_RAM_BASE <= self.party_records_address
            and self.party_records_address + self.party_count * self.party_record_size
            <= MAIN_RAM_BASE + MAIN_RAM_SIZE
        )

    def _party_ranges(self) -> tuple[tuple[int, int, int], ...]:
        return self._active_party_ranges

    def _party_slot_overlapping(self, address: int, size: int) -> int | None:
        end = address + size
        for party_start, party_end, slot in self._party_ranges():
            if address < party_end and end > party_start:
                return slot
        return None

    def _region_overlaps_party(self, address: int, size: int) -> bool:
        end = address + size
        return any(
            address < party_end and end > party_start
            for party_start, party_end, _slot in self._party_ranges()
        )

    def _excluded_party_records(self) -> list[dict[str, Any]]:
        records = []
        for party_start, _party_end, party_slot in self._party_ranges():
            offset = party_start - self.start_address
            if offset < 0 or offset + RECORD_SIZE > len(self.data):
                continue
            decoded = _decode_occupied(
                bytes(self.data[offset : offset + RECORD_SIZE]), party_start
            )
            if decoded is None:
                continue
            records.append(
                {
                    "address": decoded["address"],
                    "party_slot": party_slot,
                    "species_id": decoded["species_id"],
                    "nickname": decoded["nickname"],
                    "checksum_valid": True,
                }
            )
        return records

    def append_chunk(
        self,
        offset: int,
        chunk: bytes,
        *,
        chunk_index: int | None = None,
        byte_count: int | None = None,
    ) -> None:
        if not chunk or len(chunk) > MAX_CHUNK_BYTES:
            raise ValueError("PC discovery chunk size is outside the allowed range.")
        expected_offset = len(self.data)
        expected_address = self.start_address + expected_offset
        if chunk_index is not None and chunk_index != self.received_chunk_count:
            if chunk_index > self.received_chunk_count:
                raise ValueError(
                    "Discovery transport error: missing chunk "
                    f"{self.received_chunk_count} at 0x{expected_address:08X}"
                )
            raise ValueError(
                f"Discovery transport error: duplicate chunk {chunk_index} "
                f"(next expected is {self.received_chunk_count})."
            )
        if byte_count is not None and byte_count != len(chunk):
            raise ValueError(
                f"Discovery transport error: chunk {self.received_chunk_count} "
                f"at 0x{expected_address:08X} declared {byte_count} bytes "
                f"but carried {len(chunk)}."
            )
        if offset != len(self.data):
            if offset > expected_offset:
                raise ValueError(
                    "Discovery transport error: missing chunk "
                    f"{self.received_chunk_count} at 0x{expected_address:08X}"
                )
            raise ValueError(
                f"Out-of-order PC discovery chunk: expected {len(self.data)}, got {offset}."
            )
        if len(self.data) + len(chunk) > self.total_bytes:
            raise ValueError("PC discovery chunk exceeds the announced scan size.")
        self.data.extend(chunk)
        if self.mode in {"anchor", "runtime_relative"}:
            self._process_anchor_records()
        elif self.mode == "inspect":
            self._process_inspection_records()
        else:
            self._process_broad_records()
        appended_index = (
            chunk_index if chunk_index is not None else self.received_chunk_count
        )
        self.received_chunk_count += 1
        self._note_chunk_received(appended_index)

    def queue_chunk(
        self, offset: int, chunk: bytes, chunk_index: int, byte_count: int
    ) -> str:
        if not chunk or len(chunk) > MAX_CHUNK_BYTES:
            raise ValueError("Discovery transport error: chunk size is outside the allowed range.")
        if chunk_index < 0:
            raise ValueError("Discovery transport error: negative chunk index.")
        if byte_count != len(chunk):
            raise ValueError(
                f"Discovery transport error: chunk {chunk_index} declared "
                f"{byte_count} bytes but carried {len(chunk)}."
            )
        if offset < 0 or offset + len(chunk) > self.total_bytes:
            raise ValueError(
                f"Discovery transport error: chunk {chunk_index} has an invalid offset."
            )
        if (
            self.expected_chunk_count is not None
            and chunk_index >= self.expected_chunk_count
        ):
            raise ValueError(
                f"Discovery transport error: chunk {chunk_index} is beyond the "
                f"announced count {self.expected_chunk_count}."
            )
        if chunk_index < self.received_chunk_count:
            end = offset + len(chunk)
            if (
                offset >= 0
                and end <= len(self.data)
                and self.data[offset:end] == chunk
            ):
                self.retry_in_flight.discard(chunk_index)
                self.retry_requested_at.pop(chunk_index, None)
                return "duplicate"
            raise ValueError(
                f"Discovery transport error: duplicate chunk {chunk_index} differs "
                "from the previously received bytes."
            )
        if chunk_index > self.received_chunk_count:
            previous = self.pending_chunks.get(chunk_index)
            current = (offset, chunk, byte_count)
            if previous is not None and previous != current:
                raise ValueError(
                    f"Discovery transport error: conflicting copies of chunk {chunk_index}."
                )
            if previous is not None:
                return "duplicate"
            self.pending_chunks[chunk_index] = current
            self._note_chunk_received(chunk_index)
            self._refresh_missing_chunk_indexes()
            return "gap"

        self.append_chunk(
            offset,
            chunk,
            chunk_index=chunk_index,
            byte_count=byte_count,
        )
        while self.received_chunk_count in self.pending_chunks:
            next_index = self.received_chunk_count
            pending_offset, pending_data, pending_count = self.pending_chunks.pop(next_index)
            self.retry_in_flight.discard(next_index)
            self.retry_requested_at.pop(next_index, None)
            self.append_chunk(
                pending_offset,
                pending_data,
                chunk_index=next_index,
                byte_count=pending_count,
            )
        self._refresh_missing_chunk_indexes()
        return "accepted"

    def _note_chunk_received(self, chunk_index: int) -> None:
        self.received_chunk_indexes.add(chunk_index)
        self.highest_chunk_index_seen = max(self.highest_chunk_index_seen, chunk_index)
        self.last_chunk_received_at = time.monotonic()
        self.retry_in_flight.discard(chunk_index)
        self.retry_requested_at.pop(chunk_index, None)

    def _refresh_missing_chunk_indexes(self) -> None:
        last_expected = (
            self.expected_chunk_count
            if self.expected_chunk_count is not None
            else self.highest_chunk_index_seen + 1
        )
        self.missing_chunk_indexes = {
            index
            for index in range(self.received_chunk_count, last_expected)
            if index not in self.received_chunk_indexes
        }

    def first_missing_chunk_index(self) -> int | None:
        self._refresh_missing_chunk_indexes()
        return min(self.missing_chunk_indexes) if self.missing_chunk_indexes else None

    def _process_broad_records(self) -> None:
        while self._record_scan_offset + RECORD_SIZE <= len(self.data):
            offset = self._record_scan_offset
            self._record_scan_offset += 4
            address = self.start_address + offset
            if self._party_slot_overlapping(address, RECORD_SIZE) is not None:
                continue
            record = bytes(self.data[offset : offset + RECORD_SIZE])
            if _empty_box_record_kind(record) is not None:
                continue
            if int.from_bytes(record[4:6], "little") != 0:
                continue
            decoded = _decode_occupied(record, address)
            if decoded is not None:
                hit = {"offset": offset, "decoded": decoded}
                self.valid_offsets.add(offset)
                self.broad_valid_record_count += 1
                if (
                    self.expected_species_id == decoded["species_id"]
                    and (
                        self.expected_nickname is None
                        or (decoded["nickname"] or "").casefold()
                        == self.expected_nickname.casefold()
                    )
                ):
                    self.expected_hits.append(hit)
                if len(self.valid_hits) < MAX_BROAD_HITS:
                    self.valid_hits.append(hit)

    def _process_anchor_records(self) -> None:
        if self.anchor_address is None:
            return
        while self._anchor_next_slot < BOX_COUNT * SLOTS_PER_BOX:
            relative = self.anchor_data_offset + self._anchor_next_slot * RECORD_SIZE
            if relative + RECORD_SIZE > len(self.data):
                break
            record = bytes(self.data[relative : relative + RECORD_SIZE])
            address = self.anchor_address + self._anchor_next_slot * RECORD_SIZE
            occupied = _decode_occupied(record, address)
            if _empty_box_record_kind(record) is not None:
                status = "empty"
            elif occupied is not None:
                status = "occupied"
            else:
                status = "invalid"
            self._anchor_records.append(
                {"status": status, "decoded": occupied, "raw": record, "address": address}
            )
            self._anchor_next_slot += 1

    def _process_inspection_records(self) -> None:
        while self._record_scan_offset + RECORD_SIZE <= len(self.data):
            offset = self._record_scan_offset
            self._record_scan_offset += 4
            record = bytes(self.data[offset : offset + RECORD_SIZE])
            decoded = _decode_occupied(record, self.start_address + offset)
            if decoded is None:
                continue
            party_slot = self._party_slot_overlapping(
                self.start_address + offset, RECORD_SIZE
            )
            self.inspection_hits.append(
                {
                    "offset": offset,
                    "decoded": decoded,
                    "party_slot": party_slot,
                }
            )
            self.inspection_record_count += 1

    @property
    def candidates_found(self) -> int:
        if self.mode == "broad":
            return self.broad_valid_record_count
        if self.mode == "inspect":
            return self.inspection_record_count
        return sum(item["status"] == "occupied" for item in self._anchor_records)

    def progress(self) -> dict[str, Any]:
        scanned = len(self.data)
        self._refresh_missing_chunk_indexes()
        seconds_since_last_chunk = max(
            0.0, time.monotonic() - self.last_chunk_received_at
        )
        if self.end_received:
            producer_status = "finished"
        elif (
            not self.missing_chunk_indexes
            and seconds_since_last_chunk >= PC_DISCOVERY_STALL_SECONDS
        ):
            producer_status = "stalled"
        else:
            producer_status = "active"
        return {
            "scan_id": self.scan_id,
            "mode": self.mode,
            "status": "scanning",
            "progress_percent": min(100, int(scanned * 100 / max(1, self.total_bytes))),
            "current_address": f"0x{self.start_address + scanned:08X}",
            "bytes_scanned": scanned,
            "total_bytes": self.total_bytes,
            "chunks_received": len(self.received_chunk_indexes),
            "received_chunk_count": len(self.received_chunk_indexes),
            "expected_next_chunk_index": self.received_chunk_count,
            "expected_chunk_count": self.expected_chunk_count,
            "estimated_chunk_count": self.estimated_chunk_count,
            "expected_byte_count": self.expected_byte_count,
            "highest_chunk_index_seen": self.highest_chunk_index_seen,
            "highest_chunk_seen": self.highest_chunk_index_seen,
            "next_unproduced_chunk": self.highest_chunk_index_seen + 1,
            "missing_chunk_indexes": sorted(self.missing_chunk_indexes),
            "proven_missing_chunks": sorted(self.missing_chunk_indexes),
            "missing_chunk_count": len(self.missing_chunk_indexes),
            "oldest_missing_chunk_index": min(self.missing_chunk_indexes)
            if self.missing_chunk_indexes
            else None,
            "last_chunk_received_at": self.last_chunk_received_at,
            "seconds_since_last_chunk": seconds_since_last_chunk,
            "end_received": self.end_received,
            "producer_status": producer_status,
            "retry_attempts": dict(self.retry_attempts),
            "candidates_found": self.candidates_found,
            "elapsed_seconds": max(0.0, time.monotonic() - self.started_at),
            "source": self.source,
        }

    def finalize(self) -> dict[str, Any]:
        if len(self.data) != self.total_bytes:
            raise ValueError("PC discovery completed before all announced bytes arrived.")
        if self.mode in {"anchor", "runtime_relative"}:
            return self._finalize_anchor()
        if self.mode == "inspect":
            return self._finalize_inspection()
        if self.mode == "session_layout":
            return self._finalize_session_layout()
        return self._finalize_broad()

    def _record_sample(
        self, hit: dict[str, Any], slot: int, *, include_raw: bool = True
    ) -> dict[str, Any]:
        decoded = hit.get("decoded", hit)
        sample = {
            "box": slot // SLOTS_PER_BOX + 1,
            "slot": slot % SLOTS_PER_BOX + 1,
            "address": decoded["address"],
            "species_id": decoded["species_id"],
            "nickname": decoded["nickname"],
            "checksum_valid": True,
        }
        if include_raw:
            sample["raw_hex"] = decoded["raw_hex"]
        return sample

    def _finalize_anchor(self) -> dict[str, Any]:
        anchor = self.anchor_address
        if anchor is None or len(self._anchor_records) != BOX_COUNT * SLOTS_PER_BOX:
            raise ValueError("Anchor discovery did not receive the complete 540-record region.")
        counts = {
            name: sum(record["status"] == name for record in self._anchor_records)
            for name in ("occupied", "empty", "invalid")
        }
        occupied_slots = [
            self._record_sample(record["decoded"], slot, include_raw=False)
            for slot, record in enumerate(self._anchor_records)
            if record["status"] == "occupied"
        ]
        key_position_checks = []
        header_size = 0x28
        base = anchor - header_size
        party_range_available = self._party_range_available()
        region_party_overlap = party_range_available and self._region_overlaps_party(
            base, header_size + RECORDS_SIZE
        )
        party_slot = self._party_slot_overlapping(anchor, RECORD_SIZE)
        overlapping_party_records = sum(
            base < party_end and base + header_size + RECORDS_SIZE > party_start
            for party_start, party_end, _slot in self._party_ranges()
        )
        runtime_relative_strongly_validated = False
        candidate = {
            "rank": 1,
            "candidate_pc_boxes_base": f"0x{base:08X}",
            "candidate_pc_boxes_header_size": header_size,
            "first_box_pokemon_address": f"0x{anchor:08X}",
            "record_size": RECORD_SIZE,
            "valid_occupied_records": counts["occupied"],
            "empty_records": counts["empty"],
            "invalid_records": counts["invalid"],
            "party_record_overlaps": overlapping_party_records,
            "party_region_overlap": region_party_overlap,
            "aligned_valid_record_hits": counts["occupied"],
            "fits_main_ram": (
                MAIN_RAM_BASE <= base
                and anchor + RECORDS_SIZE <= MAIN_RAM_BASE + MAIN_RAM_SIZE
            ),
            "spacing_consistency": "136-byte contiguous records",
            "confidence_score": (
                counts["occupied"] * 1000
                + counts["empty"]
                - counts["invalid"] * 10000
            ),
            "reasons": [
                "540 records evaluated at the supplied Box 1 Slot 1 anchor",
                f"{counts['occupied']} checksum-valid occupied records",
                f"{counts['empty']} all-zero empty records",
                f"{counts['invalid']} malformed non-empty records",
                "candidate header begins 0x28 bytes before the first record; bytes are diagnostic only",
            ],
            "runtime_relative_resolver_validated": runtime_relative_strongly_validated,
            "sample_records": occupied_slots[:12],
        }
        anchor_record = self._anchor_records[0]
        record_data = bytes(self.data)
        header_offset = self.anchor_data_offset - header_size
        header_bytes = record_data[header_offset : self.anchor_data_offset]
        header_u32_values = [
            {
                "relative_offset": f"0x{offset:02X}",
                "address": f"0x{base + offset:08X}",
                "u32_le": f"0x{int.from_bytes(header_bytes[offset:offset + 4], 'little'):08X}",
            }
            for offset in range(0, header_size, 4)
        ]
        nearby_start = max(0, self.anchor_data_offset - 0x20)
        nearby_hex = record_data[nearby_start : nearby_start + 0x40].hex().upper()
        relative_values = []
        for relative in (-0x10, -0x0C, -0x08, -0x04, 0, 4, 8, 0x0C):
            offset = self.anchor_data_offset + relative
            value = int.from_bytes(record_data[offset : offset + 4], "little")
            relative_values.append(
                {
                    "relative_offset": f"{'-' if relative < 0 else ''}0x{abs(relative):X}",
                    "address": f"0x{anchor + relative:08X}",
                    "u32_le": f"0x{value:08X}",
                }
            )
        target_addresses = {base: "candidate 0x28-byte header", anchor: "Box 1 Slot 1 record"}
        pointer_references = []
        for offset in range(0, len(record_data) - 3, 4):
            value = int.from_bytes(record_data[offset : offset + 4], "little")
            target = target_addresses.get(value)
            if target is None:
                continue
            reference_address = self.start_address + offset
            pointer_references.append(
                {
                    "reference_address": f"0x{reference_address:08X}",
                    "target_address": f"0x{value:08X}",
                    "target": target,
                    "runtime_base_relative_offset": (
                        f"{reference_address - self.runtime_base_address:+d}"
                        if self.runtime_base_address is not None
                        else None
                    ),
                }
            )
            if len(pointer_references) >= 64:
                break
        runtime_delta = (
            f"{anchor - self.runtime_base_address:+d}"
            if self.runtime_base_address is not None
            else None
        )
        summary = {
            "mode": self.mode,
            "bytes_scanned": len(self.data),
            "box_count": BOX_COUNT,
            "slots_per_box": SLOTS_PER_BOX,
            "total_slots": BOX_COUNT * SLOTS_PER_BOX,
            "record_size": RECORD_SIZE,
            "valid_occupied_records": counts["occupied"],
            "empty_records": counts["empty"],
            "invalid_records": counts["invalid"],
            "candidate_records_found": counts["occupied"],
            "party_range_available": party_range_available,
            "party_range": (
                {
                    "start_address": f"0x{self.party_records_address:08X}",
                    "end_address_exclusive": f"0x{self.party_records_address + self.party_count * self.party_record_size:08X}",
                    "record_size": self.party_record_size,
                    "party_count": self.party_count,
                }
                if party_range_available
                else None
            ),
            "party_overlapping_region_rejected": region_party_overlap,
            "duration_ms": round((time.monotonic() - self.started_at) * 1000),
        }
        if self.mode == "runtime_relative":
            summary.update(
                {
                    "runtime_pointer_slot": f"0x{(self.runtime_pointer_slot or 0):08X}",
                    "runtime_pointer_semantics": self.runtime_pointer_semantics,
                    "runtime_base_address": (
                        f"0x{self.runtime_base_address:08X}"
                        if self.runtime_base_address is not None
                        else None
                    ),
                    "runtime_header_offset": self.runtime_header_offset,
                    "runtime_record_offset": self.runtime_record_offset,
                    "predicted_pc_header_address": (
                        f"0x{self.predicted_pc_header_address:08X}"
                        if self.predicted_pc_header_address is not None
                        else None
                    ),
                    "predicted_first_box_record_address": (
                        f"0x{self.predicted_first_box_record_address:08X}"
                        if self.predicted_first_box_record_address is not None
                        else None
                    ),
                    "runtime_relative_resolver_validated": runtime_relative_strongly_validated,
                    "key_validation_targets_found": sum(
                        item["matches_expected"] for item in key_position_checks
                    ),
                }
            )
        checksum_valid = anchor_record["status"] == "occupied"
        candidate_rejected_reason = None
        if not party_range_available:
            candidate_rejected_reason = "Active party RAM range is unavailable."
        elif region_party_overlap:
            candidate_rejected_reason = "Anchored 540-slot region overlaps active party RAM."
        if candidate_rejected_reason:
            summary["error"] = candidate_rejected_reason
        diagnostic = {
            "anchor_address": f"0x{anchor:08X}",
            "anchor_source": "operator-supplied Box 1 Slot 1 record address",
            "record_address_ram_valid": (
                MAIN_RAM_BASE <= anchor
                and anchor + RECORD_SIZE <= MAIN_RAM_BASE + MAIN_RAM_SIZE
            ),
            "record_checksum_valid": checksum_valid,
            "species_id": anchor_record["decoded"]["species_id"] if checksum_valid else None,
            "candidate_pc_boxes_base": f"0x{base:08X}",
            "candidate_pc_boxes_header_size": header_size,
            "candidate_pc_boxes_base_ram_valid": (
                MAIN_RAM_BASE <= base
                and base + header_size + RECORDS_SIZE <= MAIN_RAM_BASE + MAIN_RAM_SIZE
            ),
            "full_540_slot_region_fits_ram": anchor + RECORDS_SIZE <= MAIN_RAM_BASE + MAIN_RAM_SIZE,
            "record_count": BOX_COUNT * SLOTS_PER_BOX,
            "box_count": BOX_COUNT,
            "slots_per_box": SLOTS_PER_BOX,
            "record_size": RECORD_SIZE,
            "occupied_slots": occupied_slots,
            "invalid_slots": [
                {
                    "box": slot // SLOTS_PER_BOX + 1,
                    "slot": slot % SLOTS_PER_BOX + 1,
                    "address": f"0x{record['address']:08X}",
                }
                for slot, record in enumerate(self._anchor_records)
                if record["status"] == "invalid"
            ],
            "key_position_checks": key_position_checks,
            "runtime_relative_resolver_validated": runtime_relative_strongly_validated,
            "header_address": f"0x{base:08X}",
            "header_byte_count": len(header_bytes),
            "header_bytes_hex": header_bytes.hex().upper(),
            "header_u32_le_values": header_u32_values,
            "header_interpretation": "raw bytes only; runtime fields are not identified",
            "bytes_before_and_at_record_address": nearby_hex,
            "nearby_dump_start": f"0x{anchor - 0x20:08X}",
            "relative_u32_values": relative_values,
            "pointer_references": pointer_references,
            "runtime_base_to_anchor_delta": runtime_delta,
            "party_records_address": (
                f"0x{self.party_records_address:08X}"
                if self.party_records_address is not None
                else None
            ),
            "party_count": self.party_count,
            "matches_party_record": party_slot is not None,
            "party_record_slot": party_slot,
            "candidate_rejected_party_overlap": region_party_overlap,
            "candidate_rejected_reason": candidate_rejected_reason,
            "pcboxes_candidate": candidate,
        }
        return self._result_payload(
            summary,
            [] if candidate_rejected_reason else [candidate],
            diagnostic,
            (
                runtime_relative_strongly_validated
                if self.mode == "runtime_relative"
                else checksum_valid and candidate_rejected_reason is None
            ),
            self._excluded_party_records(),
            [],
        )

    def _finalize_inspection(self) -> dict[str, Any]:
        anchor = self.anchor_address
        ram_end = MAIN_RAM_BASE + MAIN_RAM_SIZE
        expected_start = max(MAIN_RAM_BASE, anchor - INSPECTION_BEFORE_BYTES) if anchor else 0
        expected_end = min(ram_end, anchor + INSPECTION_AFTER_BYTES) if anchor else 0
        if (
            anchor is None
            or self.start_address != expected_start
            or len(self.data) != expected_end - expected_start
            or self.anchor_data_offset != anchor - expected_start
        ):
            raise ValueError("Pokemon anchor inspection requires its complete bounded neighborhood.")

        anchor_offset = anchor - self.start_address
        anchor_record = next(
            (hit for hit in self.inspection_hits if hit["offset"] == anchor_offset),
            None,
        )
        primary_hit = anchor_record
        if primary_hit is None:
            nearby_non_party_hits = [
                hit for hit in self.inspection_hits if hit["party_slot"] is None
            ]
            if nearby_non_party_hits:
                primary_hit = min(
                    nearby_non_party_hits,
                    key=lambda hit: abs(
                        int(hit["decoded"]["address"], 16) - anchor
                    ),
                )
        primary_identity = (
            primary_hit["decoded"]["stable_id"] if primary_hit is not None else None
        )
        identity_hits = [
            hit
            for hit in self.inspection_hits
            if primary_identity is not None
            and hit["decoded"]["stable_id"] == primary_identity
        ]
        region_center = anchor
        region_start = self.start_address
        region_end = self.start_address + len(self.data)
        region_data = bytes(self.data)

        def format_record(hit: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
            decoded = hit["decoded"]
            address = int(decoded["address"], 16)
            delta = address - anchor
            item = {
                "address": decoded["address"],
                "delta_from_anchor": delta,
                "delta_hex": f"{delta:+#x}",
                "species_id": decoded["species_id"],
                "nickname": decoded["nickname"],
                "checksum_valid": True,
                "party_overlap": (
                    hit["party_slot"] is not None
                    if self._party_range_available()
                    else None
                ),
                "party_slot": hit["party_slot"],
                "stable_id": decoded["stable_id"],
                "pid": decoded["pid"],
                "checksum": decoded["checksum"],
                "delta_multiple_of_136": delta % RECORD_SIZE == 0,
                "delta_multiple_of_236": delta % 236 == 0,
                "delta_aligned_4": delta % 4 == 0,
            }
            if include_raw:
                item["raw_hex"] = decoded["raw_hex"]
            return item

        region_hits = [
            hit
            for hit in self.inspection_hits
            if region_start <= int(hit["decoded"]["address"], 16) < region_end
        ]
        region_hits.sort(key=lambda hit: int(hit["decoded"]["address"], 16))
        nearby_records = [format_record(hit) for hit in region_hits]
        spacing_counts: dict[int, int] = {}
        for previous, current in pairwise(region_hits):
            delta = int(current["decoded"]["address"], 16) - int(
                previous["decoded"]["address"], 16
            )
            spacing_counts[delta] = spacing_counts.get(delta, 0) + 1
        repeated_spacing = [
            {"delta_bytes": delta, "occurrences": count}
            for delta, count in sorted(
                spacing_counts.items(), key=lambda item: (-item[1], item[0])
            )[:24]
        ]
        spacing_summary = {
            "adjacent_record_pairs": max(0, len(region_hits) - 1),
            "pairs_multiple_of_136": sum(
                1 for delta in spacing_counts for _ in range(spacing_counts[delta])
                if delta % RECORD_SIZE == 0
            ),
            "pairs_multiple_of_236": sum(
                1 for delta in spacing_counts for _ in range(spacing_counts[delta])
                if delta % 236 == 0
            ),
            "repeated_neighbor_spacings": repeated_spacing,
        }

        adjacent_start = max(self.start_address, region_center - 0x40)
        adjacent_end = min(region_end, region_center + 0x100)
        adjacent_bytes = bytes(
            self.data[
                adjacent_start - self.start_address : adjacent_end - self.start_address
            ]
        )
        adjacent_u32 = []
        for relative in range(-0x20, 0x44, 4):
            address = region_center + relative
            if address < MAIN_RAM_BASE or address + 4 > ram_end:
                continue
            offset = address - self.start_address
            value = int.from_bytes(self.data[offset : offset + 4], "little")
            adjacent_u32.append(
                {
                    "relative_offset": f"{relative:+#x}",
                    "address": f"0x{address:08X}",
                    "u32_le": f"0x{value:08X}",
                }
            )

        valid_record_index = [format_record(hit) for hit in self.inspection_hits]
        primary_decoded = primary_hit["decoded"] if primary_hit is not None else None
        search_identity = (
            {
                "pid": primary_decoded["pid"],
                "checksum": primary_decoded["checksum"],
                "species_id": primary_decoded["species_id"],
                "nickname": primary_decoded["nickname"],
                "stable_id": primary_decoded["stable_id"],
            }
            if primary_decoded is not None
            else None
        )

        pointer_targets: dict[int, set[str]] = {
            anchor: {"supplied Pokemon anchor"},
            anchor - 4: {"four bytes before supplied anchor"},
        }
        for hit in identity_hits:
            address = int(hit["decoded"]["address"], 16)
            pointer_targets.setdefault(address, set()).add("matching Pokemon identity record")
            pointer_targets.setdefault(address - 4, set()).add("four bytes before matching record")
        references_by_target: dict[int, list[dict[str, Any]]] = {
            target: [] for target in pointer_targets
        }
        for offset in range(0, len(self.data) - 3, 4):
            value = int.from_bytes(self.data[offset : offset + 4], "little")
            if value not in references_by_target:
                continue
            references = references_by_target[value]
            if len(references) < 64:
                references.append(
                    {
                        "reference_address": f"0x{self.start_address + offset:08X}",
                        "target_address": f"0x{value:08X}",
                        "target": sorted(pointer_targets[value]),
                    }
                )

        def nearby_for_copy(copy_hit: dict[str, Any]) -> list[dict[str, Any]]:
            address = int(copy_hit["decoded"]["address"], 16)
            low = max(self.start_address, address - INSPECTION_BEFORE_BYTES)
            high = min(ram_end, address + INSPECTION_AFTER_BYTES)
            hits = [
                hit
                for hit in self.inspection_hits
                if low <= int(hit["decoded"]["address"], 16) < high
            ]
            hits.sort(
                key=lambda hit: abs(int(hit["decoded"]["address"], 16) - address)
            )
            return [format_record(hit) for hit in hits[:64]]

        identity_copies = []
        for hit in identity_hits:
            address = int(hit["decoded"]["address"], 16)
            item = format_record(hit)
            surrounding = nearby_for_copy(hit)
            item["surrounding_valid_pokemon_count"] = len(surrounding)
            item["surrounding_valid_pokemon"] = surrounding
            item["same_as_anchor_identity"] = (
                hit["decoded"]["stable_id"] == primary_identity
                if primary_identity is not None
                else None
            )
            item["pointer_references"] = (
                references_by_target.get(address, [])
                + references_by_target.get(address - 4, [])
            )
            identity_copies.append(item)

        summary = {
            "mode": "inspect",
            "bytes_scanned": len(self.data),
            "record_alignment_scan": "4-byte starts across bounded anchor neighborhood",
            "valid_records_found": self.inspection_record_count,
            "nearby_valid_records_found": len(nearby_records),
            "matching_identity_copies_found": len(identity_copies),
            "party_range_available": self._party_range_available(),
            "party_range": (
                {
                    "start_address": f"0x{self.party_records_address:08X}",
                    "end_address_exclusive": f"0x{self.party_records_address + self.party_count * self.party_record_size:08X}",
                    "record_size": self.party_record_size,
                    "party_count": self.party_count,
                }
                if self._party_range_available()
                else None
            ),
            "duration_ms": round((time.monotonic() - self.started_at) * 1000),
        }
        anchor_record_result = (
            format_record(anchor_record, include_raw=True)
            if anchor_record is not None
            else None
        )
        result = self._result_payload(
            summary,
            [],
            None,
            True,
            self._excluded_party_records(),
            identity_copies,
        )
        result.update(
            {
                "pc_storage_pokemon_inspection_result": True,
                "pc_storage_inspection_anchor_address": f"0x{anchor:08X}",
                "pc_storage_inspection_anchor_record": anchor_record_result,
                "pc_storage_inspection_primary_stable_id": primary_identity,
                "pc_storage_inspection_identity": search_identity,
                "pc_storage_inspection_valid_record_index": valid_record_index,
                "pc_storage_inspection_nearby_records": nearby_records,
                "pc_storage_inspection_spacing_summary": spacing_summary,
                "pc_storage_inspection_region_start": f"0x{region_start:08X}",
                "pc_storage_inspection_region_end_exclusive": f"0x{region_end:08X}",
                "pc_storage_inspection_region_center": f"0x{region_center:08X}",
                "pc_storage_inspection_region_hex": region_data.hex().upper(),
                "pc_storage_inspection_adjacent_start": f"0x{adjacent_start:08X}",
                "pc_storage_inspection_adjacent_hex": adjacent_bytes.hex().upper(),
                "pc_storage_inspection_adjacent_u32": adjacent_u32,
                "pc_storage_inspection_pointer_references": (
                    references_by_target.get(anchor, [])
                    + references_by_target.get(anchor - 4, [])
                ),
            }
        )
        return result

    def _finalize_session_layout(self) -> dict[str, Any]:
        excluded_party_records = self._excluded_party_records()
        if not self._party_range_available():
            result = self._result_payload(
                {
                    "mode": "session_layout",
                    "bytes_scanned": len(self.data),
                    "error": "Active party RAM range is unavailable; refusing to resolve PC storage.",
                    "matching_records_found": len(self.expected_hits),
                },
                [],
                None,
                False,
                excluded_party_records,
                [],
            )
            result.update(
                {
                    "pc_storage_session_layout_result": True,
                    "pc_storage_resolver_status": "unresolved",
                    "session_pc_first_record_address": None,
                    "session_lua_run_id": self.lua_run_id,
                    "expected_box1_slot1": {
                        "nickname": self.expected_nickname,
                        "species_id": self.expected_species_id,
                    },
                    "pc_storage_decoded_occupied": [],
                }
            )
            return result

        structural = self._finalize_save_table_layout(excluded_party_records)
        if structural is not None:
            return structural

        candidates = []
        for hit in self.expected_hits:
            first_offset = hit["offset"]
            first_address = self.start_address + first_offset
            if first_offset + RECORDS_SIZE > len(self.data):
                continue
            if self._region_overlaps_party(first_address, RECORDS_SIZE):
                continue

            occupied = []
            empty_count = 0
            zero_header_empty_count = 0
            empty_patterns: Counter[bytes] = Counter()
            invalid_count = 0
            invalid_samples = []
            for index in range(BOX_COUNT * SLOTS_PER_BOX):
                offset = first_offset + index * RECORD_SIZE
                record = bytes(self.data[offset : offset + RECORD_SIZE])
                empty_kind = _empty_box_record_kind(record)
                if empty_kind is not None:
                    empty_count += 1
                    zero_header_empty_count += empty_kind == "zero-header"
                    empty_patterns[record] += 1
                    continue
                decoded = _decode_occupied(record, self.start_address + offset)
                if decoded is None:
                    invalid_count += 1
                    if len(invalid_samples) < 4:
                        invalid_samples.append({
                            "box": index // SLOTS_PER_BOX + 1,
                            "slot": index % SLOTS_PER_BOX + 1,
                            "address": f"0x{self.start_address + offset:08X}",
                            "first_32_bytes_hex": record[:32].hex().upper(),
                        })
                    continue
                occupied.append(self._record_sample(decoded, index, include_raw=False))

            dominant_empty_count = max(empty_patterns.values(), default=0)
            empty_pattern_summary = [
                {"count": count, "first_32_bytes_hex": pattern[:32].hex().upper()}
                for pattern, count in empty_patterns.most_common(3)
            ]
            candidates.append(
                {
                    "first_box_pokemon_address": f"0x{first_address:08X}",
                    "candidate_pc_boxes_base": f"0x{first_address:08X}",
                    "box_count": BOX_COUNT,
                    "slots_per_box": SLOTS_PER_BOX,
                    "record_size": RECORD_SIZE,
                    "valid_occupied_records": len(occupied),
                    "empty_records": empty_count,
                    "zero_header_nonzero_payload_empty_records": zero_header_empty_count,
                    "distinct_empty_patterns": len(empty_patterns),
                    "dominant_empty_pattern_count": dominant_empty_count,
                    "empty_pattern_samples": empty_pattern_summary,
                    "invalid_records": invalid_count,
                    "invalid_record_samples": invalid_samples,
                    "party_region_overlap": False,
                    "fits_main_ram": True,
                    "spacing_consistency": "136-byte contiguous records",
                    "_occupied_records": occupied,
                    "plausible": (
                        invalid_count <= MAX_PC_REGION_INVALID_RECORDS
                        and len(occupied) >= MIN_SESSION_LAYOUT_OCCUPIED_RECORDS
                        and (len(occupied) >= 2 or dominant_empty_count >= empty_count * 0.9)
                    ),
                    "confidence_score": (
                        len(occupied) * 1000
                        + empty_count
                        - invalid_count * 10000
                    ),
                }
            )

        candidates.sort(
            key=lambda candidate: (
                not candidate["plausible"],
                candidate["invalid_records"],
                -candidate["valid_occupied_records"],
                candidate["first_box_pokemon_address"],
            )
        )
        top_candidate = candidates[0] if candidates else None
        plausible = [candidate for candidate in candidates if candidate["plausible"]]
        resolved = False
        if plausible:
            best = plausible[0]
            tied = len(plausible) > 1 and (
                best["invalid_records"], best["valid_occupied_records"]
            ) == (
                plausible[1]["invalid_records"],
                plausible[1]["valid_occupied_records"],
            )
            resolved = not tied
        else:
            best = None

        if best is None:
            error = (
                f"No non-party {self.expected_nickname or 'species'} Box 1 Slot 1 candidate "
                "produced a plausible 540-slot layout with independent occupied-record evidence."
            )
        elif not resolved:
            error = "Multiple candidate arrays have equally strong validation; address remains unresolved."
        else:
            error = None

        winner = best if resolved else None
        if winner is not None:
            winner["rank"] = 1
        decoded_occupied = (
            winner.pop("_occupied_records", []) if winner is not None else []
        )
        for rank, candidate in enumerate(candidates, 1):
            candidate["rank"] = rank
            candidate.pop("_occupied_records", None)

        winner_offset = (
            int(winner["first_box_pokemon_address"], 16) - self.start_address
            if winner is not None else None
        )

        summary = {
            "mode": "session_layout",
            "bytes_scanned": len(self.data),
            "candidate_records_found": self.broad_valid_record_count,
            "matching_box1_slot1_records_found": len(self.expected_hits),
            "candidate_layouts_evaluated": len(candidates),
            "valid_occupied_records": (
                top_candidate["valid_occupied_records"]
                if top_candidate is not None
                else 0
            ),
            "empty_records": (
                top_candidate["empty_records"] if top_candidate is not None else 0
            ),
            "invalid_records": (
                top_candidate["invalid_records"] if top_candidate is not None else 0
            ),
            "box_count": BOX_COUNT,
            "slots_per_box": SLOTS_PER_BOX,
            "total_slots": BOX_COUNT * SLOTS_PER_BOX,
            "record_size": RECORD_SIZE,
            "maximum_malformed_records_allowed": MAX_PC_REGION_INVALID_RECORDS,
            "minimum_occupied_records_for_resolution": MIN_SESSION_LAYOUT_OCCUPIED_RECORDS,
            "party_range_available": True,
            "party_range": {
                "start_address": f"0x{self.party_records_address:08X}",
                "end_address_exclusive": f"0x{self.party_records_address + self.party_count * self.party_record_size:08X}",
                "record_size": self.party_record_size,
                "party_count": self.party_count,
            },
            "excluded_party_record_count": len(excluded_party_records),
            "resolved": resolved,
            "error": error,
            "duration_ms": round((time.monotonic() - self.started_at) * 1000),
        }
        result = self._result_payload(
            summary,
            candidates[:8],
            None,
            resolved,
            excluded_party_records,
            [],
        )
        result.update(
            {
                "pc_storage_session_layout_result": True,
                "pc_storage_resolver_status": "cache-pending" if resolved else "unresolved",
                "session_pc_first_record_address": (
                    winner["first_box_pokemon_address"] if winner else None
                ),
                "session_lua_run_id": self.lua_run_id,
                "expected_box1_slot1": {
                    "nickname": self.expected_nickname,
                    "species_id": self.expected_species_id,
                },
                "pc_storage_decoded_occupied": decoded_occupied,
                "pc_storage_anchor_raw_hex": (
                    self.data[winner_offset:winner_offset + RECORD_SIZE].hex().upper()
                    if winner_offset is not None else None
                ),
            }
        )
        if error:
            result["pc_storage_anchor_error"] = error
        return result

    def _finalize_save_table_layout(self, excluded_party_records):
        """Resolve the live SaveData via its page table and active Party pointer.

        SaveDataBody is 0x20000 bytes; pageInfo follows its counters at +0x20010.
        Page 2 is Party, page 37 is PCBoxes. See pret/pokeplatinum savedata.h.
        Zero-filled RAM alone is never evidence of an empty PC.
        """
        signature = (37).to_bytes(4, "little") + (0x121D0).to_bytes(4, "little")
        matches = []
        offset = self.data.find(signature)
        while offset >= 0:
            table = offset - 37 * 16
            body = table - 0x20010
            if body >= 0 and body % 4 == 0 and table + 38 * 16 <= len(self.data):
                pages = [struct.unpack_from("<IIIHH", self.data, table + index * 16)
                         for index in range(38)]
                valid = all(
                    page_id == index and size > 0 and size % 4 == 0
                    and location % 4 == 0 and location + size <= 0x20000
                    and block == (1 if index == 37 else 0)
                    for index, (page_id, size, location, _checksum, block) in enumerate(pages)
                )
                valid = valid and all(
                    pages[index][2] + pages[index][1] <= pages[index + 1][2]
                    for index in range(37)
                )
                party = body + pages[2][2]
                first = body + pages[37][2] + 4
                tail = first + RECORDS_SIZE
                valid = (valid and pages[1][1] >= 24 and pages[2][1] >= 8 + 6 * 236
                         and self.start_address + party + 8 == self.party_records_address
                         and party + 8 <= len(self.data)
                         and struct.unpack_from("<II", self.data, party) == (6, self.party_count)
                         and tail + 18 * 40 + 19 <= len(self.data)
                         and not self._region_overlaps_party(self.start_address + first, RECORDS_SIZE))
                if valid:
                    current_box = int.from_bytes(self.data[first - 4:first], "little")
                    names = [struct.unpack_from("<20H", self.data, tail + index * 40)
                             for index in range(18)]
                    wallpapers = self.data[tail + 720:tail + 738]
                    valid = (current_box < 18 and all(0xFFFF in name and name[0] != 0xFFFF
                                                      for name in names)
                             and all(value < 24 for value in wallpapers))
                if valid:
                    occupied = []
                    for index in range(540):
                        record_offset = first + index * RECORD_SIZE
                        raw = bytes(self.data[record_offset:record_offset + RECORD_SIZE])
                        if _empty_box_record_kind(raw) is not None:
                            continue
                        decoded = _decode_occupied(raw, self.start_address + record_offset)
                        if decoded is None:
                            valid = False
                            break
                        occupied.append(self._record_sample(decoded, index, include_raw=False))
                    if valid:
                        matches.append((body, first, occupied, pages))
            offset = self.data.find(signature, offset + 4)
        if len(matches) != 1:
            return None
        body, first, occupied, pages = matches[0]
        first_decoded = _decode_occupied(bytes(self.data[first:first + RECORD_SIZE]), self.start_address + first)
        player = body + pages[1][2]
        trainer_id = int.from_bytes(self.data[player + 20:player + 24], "little")
        summary = {
            "mode": "session_layout", "method": "save_table", "resolved": True,
            "bytes_scanned": len(self.data), "error": None,
            "valid_occupied_records": len(occupied), "empty_records": 540 - len(occupied),
            "invalid_records": 0, "candidate_records_found": self.broad_valid_record_count,
            "matching_box1_slot1_records_found": len(self.expected_hits),
            "pc_empty": not occupied, "box1_slot1_empty": first_decoded is None,
            "save_trainer_id": f"0x{trainer_id:08X}",
        }
        result = self._result_payload(summary, [], None, True, excluded_party_records, [])
        result.update({
            "pc_storage_session_layout_result": True, "pc_storage_resolver_status": "cache-pending",
            "pc_storage_resolver_method": "save_table",
            "session_pc_first_record_address": f"0x{self.start_address + first:08X}",
            "session_save_data_body_address": f"0x{self.start_address + body:08X}",
            "session_lua_run_id": self.lua_run_id,
            "expected_box1_slot1": {
                "species_id": first_decoded["species_id"] if first_decoded else None,
                "nickname": first_decoded["nickname"] if first_decoded else None,
            },
            "pc_storage_decoded_occupied": occupied,
            "pc_storage_anchor_raw_hex": self.data[first:first + RECORD_SIZE].hex().upper(),
        })
        return result

    def _finalize_broad(self) -> dict[str, Any]:
        excluded_party_records = self._excluded_party_records()
        party_range_available = self._party_range_available()
        party_range = None
        if party_range_available:
            party_range = {
                "start_address": f"0x{self.party_records_address:08X}",
                "end_address_exclusive": f"0x{self.party_records_address + self.party_count * self.party_record_size:08X}",
                "record_size": self.party_record_size,
                "party_count": self.party_count,
            }

        if not party_range_available:
            summary = {
                "mode": "broad",
                "bytes_scanned": len(self.data),
                "candidate_records_found": self.broad_valid_record_count,
                "candidate_bases_considered": 0,
                "candidate_bases_evaluated": 0,
                "party_range_available": False,
                "excluded_party_record_count": len(excluded_party_records),
                "error": "Active party RAM range is unavailable; refusing to rank PC candidates.",
                "duration_ms": round((time.monotonic() - self.started_at) * 1000),
            }
            return self._result_payload(
                summary, [], None, False, excluded_party_records, []
            )

        valid_by_offset = self.valid_offsets
        prefix_by_residue: dict[int, tuple[array, array, array]] = {}
        for residue in range(0, RECORD_SIZE, 4):
            valid_prefix = array("I", [0])
            empty_prefix = array("I", [0])
            invalid_prefix = array("I", [0])
            offset = residue
            while offset + RECORD_SIZE <= MAIN_RAM_SIZE:
                occupied = offset in valid_by_offset
                empty = (
                    not occupied
                    and self.data[offset : offset + RECORD_SIZE].count(0) == RECORD_SIZE
                )
                valid_prefix.append(valid_prefix[-1] + int(occupied))
                empty_prefix.append(empty_prefix[-1] + int(empty))
                invalid_prefix.append(invalid_prefix[-1] + int(not occupied and not empty))
                offset += RECORD_SIZE
            prefix_by_residue[residue] = (valid_prefix, empty_prefix, invalid_prefix)

        candidate_starts: set[int] = set()
        for hit in self.valid_hits:
            for slot in range(BOX_COUNT * SLOTS_PER_BOX):
                start = hit["offset"] - slot * RECORD_SIZE
                if start >= 4 and start + RECORDS_SIZE <= MAIN_RAM_SIZE:
                    candidate_starts.add(start)

        evaluated = []
        party_overlapping_bases_rejected = 0
        malformed_bases_rejected = 0
        expected_anchor_offsets = {hit["offset"] for hit in self.expected_hits}
        for start in candidate_starts:
            base_address = self.start_address + start - 4
            if self._region_overlaps_party(base_address, 4 + RECORDS_SIZE):
                party_overlapping_bases_rejected += 1
                continue
            residue = start % RECORD_SIZE
            index = (start - residue) // RECORD_SIZE
            valid_prefix, empty_prefix, invalid_prefix = prefix_by_residue[residue]
            end = index + BOX_COUNT * SLOTS_PER_BOX
            valid_count = valid_prefix[end] - valid_prefix[index]
            empty_count = empty_prefix[end] - empty_prefix[index]
            invalid_count = invalid_prefix[end] - invalid_prefix[index]
            if invalid_count > MAX_PC_REGION_INVALID_RECORDS or valid_count < 1:
                malformed_bases_rejected += 1
                continue
            current_box = int.from_bytes(self.data[start - 4 : start], "little")
            evaluated.append(
                {
                    "start": start,
                    "candidate_pc_boxes_base": f"0x{base_address:08X}",
                    "first_box_pokemon_address": f"0x{self.start_address + start:08X}",
                    "known_expected_identity_anchor": start in expected_anchor_offsets,
                    "current_box": current_box,
                    "current_box_valid": 0 <= current_box < BOX_COUNT,
                    "valid_occupied_records": valid_count,
                    "empty_records": empty_count,
                    "invalid_records": invalid_count,
                    "party_record_overlaps": 0,
                    "aligned_valid_record_hits": valid_count,
                    "fits_main_ram": True,
                    "spacing_consistency": "136-byte contiguous records",
                    "confidence_score": valid_count * 1000 + empty_count - invalid_count * 10000,
                    "reasons": [
                        f"{valid_count} checksum-valid occupied records",
                        f"{empty_count} all-zero empty records",
                        f"{invalid_count} malformed non-empty records",
                        "540 records fit inside Nintendo DS Main RAM",
                    ],
                    "sample_records": [],
                }
            )
        evaluated.sort(
            key=lambda item: (
                item["invalid_records"],
                not item["known_expected_identity_anchor"],
                -item["valid_occupied_records"],
                -item["empty_records"],
                not item["current_box_valid"],
                int(item["candidate_pc_boxes_base"], 16),
            )
        )
        hits_by_offset = {hit["offset"]: hit for hit in self.valid_hits}
        candidates = []
        for rank, candidate in enumerate(evaluated[:8], 1):
            start = candidate["start"]
            for slot in range(BOX_COUNT * SLOTS_PER_BOX):
                hit = hits_by_offset.get(start + slot * RECORD_SIZE)
                if hit is not None:
                    candidate["sample_records"].append(self._record_sample(hit, slot))
                    if len(candidate["sample_records"]) >= 12:
                        break
            candidate["rank"] = rank
            del candidate["start"]
            candidates.append(candidate)
        summary = {
            "mode": "broad",
            "bytes_scanned": len(self.data),
            "scan_stride": 4,
            "candidate_records_found": self.broad_valid_record_count,
            "candidate_bases_considered": len(candidate_starts),
            "candidate_bases_evaluated": len(evaluated),
            "party_range_available": True,
            "party_range": party_range,
            "excluded_party_record_count": len(excluded_party_records),
            "party_overlapping_candidate_bases_rejected": party_overlapping_bases_rejected,
            "malformed_candidate_bases_rejected": malformed_bases_rejected,
            "maximum_malformed_records_allowed": MAX_PC_REGION_INVALID_RECORDS,
            "duration_ms": round((time.monotonic() - self.started_at) * 1000),
            "ranking_note": (
                "Party-overlapping records and regions are excluded. Regions with more "
                f"than {MAX_PC_REGION_INVALID_RECORDS} malformed slots are rejected. "
                "An expected identity match at the supplied Box 1 Slot 1 address "
                "prioritizes that anchored layout when one is configured."
            ),
        }
        if not candidates:
            summary["no_plausible_candidates"] = True
        identity_matches = [
            {
                "address": hit["decoded"]["address"],
                "species_id": hit["decoded"]["species_id"],
                "nickname": hit["decoded"]["nickname"],
                "checksum_valid": True,
                "party_overlap": False,
            }
            for hit in self.expected_hits
        ]
        return self._result_payload(
            summary, candidates, None, True, excluded_party_records, identity_matches
        )

    def _result_payload(
        self,
        summary: dict[str, Any],
        candidates: list[dict[str, Any]],
        anchor_diagnostic: dict[str, Any] | None,
        ok: bool,
        excluded_party_records: list[dict[str, Any]] | None = None,
        identity_matches: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "pc_storage_discovery_result",
            "run_id": self.scan_id,
            "frame": None,
            "pc_storage_discovery_requested": True,
            "pc_storage_discovery_result": True,
            "pc_storage_anchor_result": self.mode == "anchor",
            "pc_storage_runtime_relative_result": self.mode == "runtime_relative",
            "pc_storage_runtime_relative_result_ok": (
                ok if self.mode == "runtime_relative" else False
            ),
            "pc_storage_pokemon_inspection_result": self.mode == "inspect",
            "pc_storage_anchor_result_ok": ok if self.mode == "anchor" else False,
            "pc_storage_anchor_address": (
                f"0x{self.anchor_address:08X}"
                if self.anchor_address is not None
                else None
            ),
            "pc_storage_anchor_error": (
                None
                if ok or (self.mode == "runtime_relative" and not summary.get("error"))
                else summary.get("error", "Anchor record did not validate.")
            ),
            "pc_storage_discovery_summary": summary,
            "pc_storage_discovery_candidates": candidates,
            "pc_storage_excluded_party_records": excluded_party_records or [],
            "pc_storage_discovery_identity_matches": identity_matches or [],
            "pc_storage_anchor_diagnostic": anchor_diagnostic,
            "runtime_base_probe_rows": [],
            "lua_build_id": "python-pc-discovery-analysis",
            "runtime_pointer_slot": (
                f"0x{self.runtime_pointer_slot:08X}"
                if self.runtime_pointer_slot is not None
                else None
            ),
            "runtime_pointer_semantics": self.runtime_pointer_semantics,
            "runtime_base_address": (
                f"0x{self.runtime_base_address:08X}"
                if self.runtime_base_address is not None
                else None
            ),
            "runtime_header_offset": self.runtime_header_offset,
            "runtime_record_offset": self.runtime_record_offset,
            "predicted_pc_header_address": (
                f"0x{self.predicted_pc_header_address:08X}"
                if self.predicted_pc_header_address is not None
                else None
            ),
            "predicted_first_box_record_address": (
                f"0x{self.predicted_first_box_record_address:08X}"
                if self.predicted_first_box_record_address is not None
                else None
            ),
        }
