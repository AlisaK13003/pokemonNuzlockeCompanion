from __future__ import annotations

import json
import os
import time
from pathlib import Path

from pokemon_ev_tracker.games.platinum.pc_discovery import (
    ANCHOR_CONTEXT_BYTES,
    INSPECTION_AFTER_BYTES,
    INSPECTION_BEFORE_BYTES,
    MAIN_RAM_BASE,
    RECORD_SIZE,
    RECORDS_SIZE,
    PCStorageDiscoveryAccumulator,
    _decode_occupied,
)
from pokemon_ev_tracker.games.platinum.pc_sram_search import analyze_sram_snapshot
from pokemon_ev_tracker.pokemon.gen4.crypto import calculate_checksum, encrypt_box_data
from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer

INSPECTION_TEST_ANCHOR = MAIN_RAM_BASE + 0x28000


class _CommandSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


def _boxed_record(
    species_id: int = 298, pid: int = 0x12345678, nickname: str | None = None
) -> bytes:
    plain = bytearray(128)
    plain[0:2] = species_id.to_bytes(2, "little")
    if nickname:
        codes = []
        for character in nickname:
            if "0" <= character <= "9":
                codes.append(0x121 + ord(character) - ord("0"))
            elif "A" <= character <= "Z":
                codes.append(0x12B + ord(character) - ord("A"))
            elif "a" <= character <= "z":
                codes.append(0x145 + ord(character) - ord("a"))
            else:
                raise ValueError(f"Test helper does not encode {character!r}.")
        encoded = b"".join(code.to_bytes(2, "little") for code in codes)
        plain[0x40 : 0x40 + len(encoded)] = encoded
        plain[0x40 + len(encoded) : 0x42 + len(encoded)] = b"\xff\xff"
    checksum = calculate_checksum(bytes(plain))
    return (
        pid.to_bytes(4, "little")
        + bytes(2)
        + checksum.to_bytes(2, "little")
        + encrypt_box_data(bytes(plain), pid, checksum)
    )


def _anchor_memory() -> bytes:
    memory = bytearray(ANCHOR_CONTEXT_BYTES + RECORDS_SIZE + ANCHOR_CONTEXT_BYTES)
    memory[ANCHOR_CONTEXT_BYTES - 4 : ANCHOR_CONTEXT_BYTES] = (0).to_bytes(4, "little")
    memory[ANCHOR_CONTEXT_BYTES : ANCHOR_CONTEXT_BYTES + RECORD_SIZE] = _boxed_record()
    return bytes(memory)


def _inspection_start_event(
    scan_id: str, anchor: int = INSPECTION_TEST_ANCHOR
) -> dict:
    start = max(MAIN_RAM_BASE, anchor - INSPECTION_BEFORE_BYTES)
    end = min(MAIN_RAM_BASE + 0x400000, anchor + INSPECTION_AFTER_BYTES)
    return {
        "type": "pc_discovery_started",
        "scan_id": scan_id,
        "mode": "inspect",
        "start_address": f"0x{start:08X}",
        "total_bytes": end - start,
        "anchor_address": f"0x{anchor:08X}",
        "anchor_data_offset": anchor - start,
        "estimated_chunk_count": (end - start + 0xFFF) // 0x1000,
    }


def _send_live_sram_capture(
    server: BizHawkDebugServer,
    image: bytes,
    *,
    scan_id: str,
    run_id: str = "live-sram-test-run",
) -> None:
    chunk_bytes = 0x1000
    chunk_count = (len(image) + chunk_bytes - 1) // chunk_bytes
    server._record_line(
        json.dumps(
            {
                "type": "pc_sram_search_started",
                "scan_id": scan_id,
                "domain": "SRAM",
                "domain_size": len(image),
                "chunk_bytes": chunk_bytes,
                "chunk_count": chunk_count,
                "lua_run_id": run_id,
                "lua_build_id": "pc-storage-v20-live-sram-search",
                "domains": [
                    {
                        "name": "SRAM",
                        "size": len(image),
                        "readable": True,
                        "save_like": True,
                        "eligible_save_copy_count": 2,
                    }
                ],
                "eligible_save_copy_count": 2,
            }
        ),
        "tcp",
    )
    for chunk_index, offset in enumerate(range(0, len(image), chunk_bytes)):
        chunk = image[offset : offset + chunk_bytes]
        server._record_line(
            json.dumps(
                {
                    "type": "pc_sram_search_chunk",
                    "scan_id": scan_id,
                    "domain": "SRAM",
                    "chunk_index": chunk_index,
                    "offset": offset,
                    "byte_count": len(chunk),
                    "data_encoding": "hex",
                    "data": chunk.hex().upper(),
                }
            ),
            "tcp",
        )
    server._record_line(
        json.dumps(
            {
                "type": "pc_sram_search_complete",
                "scan_id": scan_id,
                "domain": "SRAM",
                "chunk_count": chunk_count,
                "total_bytes": len(image),
                "bytes_scanned": len(image),
            }
        ),
        "tcp",
    )


def test_lua_discovery_is_chunked_and_has_no_full_ram_read_path() -> None:
    lua_source = (Path(__file__).parents[1] / "bizhawk" / "ev_tracker.lua").read_text(
        encoding="utf-8"
    )

    assert sum(line.startswith("local ") for line in lua_source.splitlines()) < 150
    assert "local PCState = {" in lua_source
    assert "local TransportState = {" in lua_source
    assert "function SRAMDiag.advance_pc_sram_search(frame)" in lua_source
    assert "or not boxed_record_checksum_valid(raw_records, record_start)" in lua_source
    assert '"pc_storage_acquisition_enabled", raw_records ~= nil and address_valid' in lua_source
    assert '"pc_storage_rediscovery_required", rediscovery_required' in lua_source
    assert "species == PCState.session_pc_expected_species_id" in lua_source
    assert "local runtime_base, party_address, party_count, party_valid = read_active_party_range(domain)" in lua_source
    assert 'local PC_DISCOVERY_CHUNK_BYTES = 0x1000' in lua_source
    assert 'local PLATINUM_PARTY_COUNT_OFFSET = 0xD090' in lua_source
    assert 'local PLATINUM_PARTY_RECORDS_OFFSET = 0xD094' in lua_source
    assert '{"party_record_size", PARTY_POKEMON_SIZE}' in lua_source
    assert '{"party_range_valid", party_range_valid}' in lua_source
    assert 'local LUA_BUILD_ID = "pc-storage-v24-cache-validation"' in lua_source
    assert "(math.floor(pid / 0x2000) % 32) % 24" in lua_source
    assert '"cache_validation", JSON.json_raw(validation_diagnostic)' in lua_source
    assert 'local PC_SAVE_COPY_RECORD_OFFSETS = {0x0C104, 0x4C104}' in lua_source
    assert 'local PC_SAVE_TEST_CHUNK_BYTES = 0x1000' in lua_source
    assert 'if line == "PC_TEST_SAVE_PC_OFFSETS" then' in lua_source
    assert lua_source.index("local bizhawk_client_api = _G and _G.client or nil") < lua_source.index("local client = nil")
    assert 'bizhawk_client_api.saveram end, nil)' in lua_source
    assert "pcall(saveram_function)" in lua_source
    assert '"type", "pc_save_offset_test_file_capture"' in lua_source
    assert '"lua_system_id", try_call(function() return emu.getsystemid() end, nil)' in lua_source
    assert '"lua_rom_path_api", rom_path_api or "not exposed"' in lua_source
    assert '"save_ram_flush_semantics"' in lua_source
    assert '{"data_encoding", "hex"}' in lua_source
    assert "SaveRAMDiag.advance_pc_save_offset_test(frame)" in lua_source
    assert 'local PC_SRAM_SEARCH_CHUNK_BYTES = 0x1000' in lua_source
    assert 'if line == "PC_SEARCH_SRAM_POKEMON" then' in lua_source
    assert '"type", "pc_sram_search_started"' in lua_source
    assert '"type", "pc_sram_search_chunk"' in lua_source
    assert '"type", "pc_sram_search_complete"' in lua_source
    assert "SRAMDiag.advance_pc_sram_search(frame)" in lua_source
    assert "local discovery_active = PCState.pc_sram_search ~= nil" in lua_source
    assert '"eligible_save_copy_count", selected_eligible_copies' in lua_source
    assert "math.floor(os.clock() * 1000)" in lua_source
    assert 'clear_session_pc_resolver("ROM/session identity changed")' in lua_source
    assert 'print("EV Tracker transport reconnected; preserving PC cache for the current Lua run")' in lua_source
    assert 'clear_session_pc_resolver("PC layout rediscovery requested")' in lua_source
    assert lua_source.count("SessionState.run_id = new_lua_run_id()") == 1
    assert "cache_run_id == SessionState.run_id" in lua_source
    assert 'pcall(debug.getinfo, 2, "S")' in lua_source
    assert 'line:match("^PC_INSPECT_POKEMON|(%x+)$")' in lua_source
    assert "PC_DISCOVER_CURRENT_LAYOUT|(%d+)|([%x]*)" in lua_source
    assert "PC_CACHE_SESSION_PC|([%w%-]+)|(%x+)" in lua_source
    assert 'PCState.session_pc_first_record_address = cache_address' in lua_source
    assert 'pc_storage_message(frame, domain)' in lua_source
    assert '"pc_storage_cache_ready" or "pc_storage_cache_rejected"' in lua_source
    assert '"occupied_records", occupied' in lua_source
    assert '"empty_records", empty' in lua_source
    assert '"malformed_records", malformed' in lua_source
    assert "PLATINUM_RUNTIME_PC_RECORD_OFFSET" not in lua_source
    assert "SavePageInfo index" not in lua_source
    assert '"pc_storage_resolver_kind", "session_pc_cache"' in lua_source
    assert 'line:match("^PC_SEARCH_POKEMON|(%x+)|(%x+)|(%x+)|(%x+)$")' in lua_source
    assert '"^PC_SEARCH_PC_POINTERS|(%x+)|(%x+)$"' in lua_source
    assert 'local PC_INSPECTION_BEFORE_BYTES = 0x4000' in lua_source
    assert 'local PC_INSPECTION_AFTER_BYTES = 0x20000' in lua_source
    assert '"type", "pc_pokemon_search_match"' in lua_source
    assert '"type", "pc_pokemon_search_progress"' in lua_source
    assert '"type", "pc_structure_pointer_search_end"' in lua_source
    assert "local PC_POINTER_SEARCH_CHUNK_BYTES = 0x1000" in lua_source
    assert "local function advance_pc_structure_pointer_search(frame, domain)" in lua_source
    search_start = lua_source.index("local function start_pc_pokemon_search(")
    search_end = lua_source.index("local function advance_pc_pokemon_search(")
    search_source = lua_source[search_start:search_end]
    assert '"type", "pc_pokemon_search_started"' in search_source
    search_step_end = lua_source.index("local function advance_pc_discovery(")
    search_step = lua_source[search_end:search_step_end]
    assert "candidate_span = math.min(0x1000" in search_step
    assert '{"record_hex", record_hex}' in search_step
    assert '{"data", encoded}' not in search_step
    assert "PCState.pc_pokemon_search_waiting_ack ~= nil" in lua_source
    assert 'PC_ACK_POKEMON_SEARCH|([%w%-]+)$' in lua_source
    assert 'local mode = expected_identity and "session_layout"' in lua_source
    assert 'or (inspection_address ~= nil and "inspect"' in lua_source
    assert "PC_TEST_RUNTIME_PC_ADDRESS" not in lua_source
    assert "local function advance_pc_discovery(frame, domain)" in lua_source
    assert (
        "address_to_domain_offset(scan.start_address) + scan.cursor + processed"
        in lua_source
    )
    assert 'PC_RETRY_DISCOVERY_CHUNK|([%w%-]+)|(%d+)$' in lua_source
    assert (
        "PC_DISCOVERY_RETRY_CACHE_CHUNKS = MAIN_RAM_SIZE / PC_DISCOVERY_MIN_CHUNK_BYTES"
        in lua_source
    )
    assert '{"chunk_index", chunk_index}' in lua_source
    assert '{"data_encoding", "hex"}' in lua_source
    assert '{"data", encoded}' in lua_source
    assert '{"type", "pc_discovery_end"}' in lua_source
    assert '{"chunk_count", scan.next_chunk_index}' in lua_source
    assert '{"total_bytes", scan.cursor}' in lua_source
    assert "EV Tracker PC discovery resend received" in lua_source
    assert "EV Tracker PC discovery resend queued" in lua_source
    assert "EV Tracker PC discovery resend emitted" in lua_source
    assert "local function service_pc_discovery_resend(frame)" in lua_source
    assert "local PC_DISCOVERY_RESEND_QUEUE_MAX = 64" in lua_source
    assert 'if chunk_index % 50 == 0 then' in lua_source
    assert "current_address=0x%08X bytes_scanned=%d scan_active=%s" in lua_source
    command_handler_start = lua_source.index("local function process_command(")
    resend_service_start = lua_source.index("local function service_pc_discovery_resend(")
    command_handler = lua_source[command_handler_start:resend_service_start]
    assert "table.insert(PCState.pc_discovery_resend_queue" in command_handler
    assert "send_line(cached_line" not in command_handler
    assert "PCState.pc_discovery_scan = nil" not in command_handler
    resend_service = lua_source[
        resend_service_start : lua_source.index("local function poll_command_file(")
    ]
    assert "if PCState.pc_discovery_scan ~= nil or #PCState.pc_discovery_resend_queue == 0 then" in resend_service
    assert (
        "advance_pc_discovery(frame, active_domain)\n"
        "    advance_pc_pokemon_search(frame, active_domain)\n"
        "    advance_pc_structure_pointer_search(frame, active_domain)\n"
        "    service_pc_discovery_resend(frame)"
    ) in lua_source
    assert (
        "if COMMAND_FILE ~= nil then\n    os.remove(COMMAND_FILE)\nend\n\nwhile true do"
        not in lua_source
    )
    assert 'if line == "PC_CANCEL_DISCOVERY" then' in lua_source
    assert "read_bytes_bulk(domain, 0, MAIN_RAM_SIZE)" not in lua_source
    assert "for offset = 0, MAIN_RAM_SIZE" not in lua_source


def test_incremental_broad_accumulator_counts_checksum_valid_records() -> None:
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="broad-test",
        mode="broad",
        total_bytes=0x400000,
        start_address=MAIN_RAM_BASE,
    )
    chunk = bytearray(0x1000)
    chunk[0x100 : 0x100 + RECORD_SIZE] = _boxed_record()

    accumulator.append_chunk(0, bytes(chunk))

    progress = accumulator.progress()
    assert progress["status"] == "scanning"
    assert progress["bytes_scanned"] == 0x1000
    assert progress["candidates_found"] == 1


def test_broad_ranking_penalizes_malformed_records() -> None:
    base_offset = 0x100000
    memory = bytearray(0x400000)
    memory[base_offset : base_offset + RECORD_SIZE] = _boxed_record()
    malformed = bytes([0xA5]) + bytes(RECORD_SIZE - 1)
    for index in range(1, 110):
        start = base_offset - index * RECORD_SIZE
        memory[start : start + RECORD_SIZE] = malformed
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="broad-ranking",
        mode="broad",
        total_bytes=len(memory),
        start_address=MAIN_RAM_BASE,
        party_records_address=MAIN_RAM_BASE + 0x300000,
        party_count=0,
        party_record_size=236,
        party_range_valid=True,
    )
    for offset in range(0, len(memory), 0x1000):
        accumulator.append_chunk(offset, memory[offset : offset + 0x1000])

    result = accumulator.finalize()
    strongest = result["pc_storage_discovery_candidates"][0]
    assert strongest["first_box_pokemon_address"] == f"0x{MAIN_RAM_BASE + base_offset:08X}"
    assert strongest["valid_occupied_records"] == 1
    assert strongest["empty_records"] == 539
    assert strongest["invalid_records"] == 0
    assert result["pc_storage_discovery_summary"][
        "malformed_candidate_bases_rejected"
    ] > 0
    assert all(
        candidate["invalid_records"] <= 2
        for candidate in result["pc_storage_discovery_candidates"]
    )


def test_broad_scan_excludes_party_and_tracks_configured_identity() -> None:
    memory = bytearray(0x400000)
    party_offset = 0x8000
    target_offset = 0x200000
    memory[party_offset : party_offset + RECORD_SIZE] = _boxed_record(
        species_id=16, pid=0xAABBCCDD, nickname="PartyMon"
    )
    memory[target_offset : target_offset + RECORD_SIZE] = _boxed_record(
        species_id=415, pid=0x11223344, nickname="Mitsu"
    )
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="party-exclusion-identity",
        mode="broad",
        total_bytes=len(memory),
        start_address=MAIN_RAM_BASE,
        party_records_address=MAIN_RAM_BASE + party_offset,
        party_count=1,
        party_record_size=236,
        party_range_valid=True,
        expected_species_id=415,
        expected_nickname="mitsu",
    )
    for offset in range(0, len(memory), 0x1000):
        accumulator.append_chunk(offset, memory[offset : offset + 0x1000])

    result = accumulator.finalize()
    summary = result["pc_storage_discovery_summary"]
    excluded = result["pc_storage_excluded_party_records"]
    identity_matches = result["pc_storage_discovery_identity_matches"]
    candidates = result["pc_storage_discovery_candidates"]

    assert summary["party_range_available"] is True
    assert summary["candidate_records_found"] == 1
    assert summary["party_range"] == {
        "start_address": f"0x{MAIN_RAM_BASE + party_offset:08X}",
        "end_address_exclusive": f"0x{MAIN_RAM_BASE + party_offset + 236:08X}",
        "record_size": 236,
        "party_count": 1,
    }
    assert excluded == [
        {
            "address": f"0x{MAIN_RAM_BASE + party_offset:08X}",
            "party_slot": 1,
            "species_id": 16,
            "nickname": "PartyMon",
            "checksum_valid": True,
        }
    ]
    assert len(identity_matches) == 1
    assert identity_matches[0]["address"] == f"0x{MAIN_RAM_BASE + target_offset:08X}"
    assert identity_matches[0]["species_id"] == 415
    assert identity_matches[0]["nickname"] == "Mitsu"
    assert identity_matches[0]["checksum_valid"] is True
    assert identity_matches[0]["party_overlap"] is False
    assert candidates
    assert candidates[0]["first_box_pokemon_address"] == identity_matches[0]["address"]
    assert candidates[0]["known_expected_identity_anchor"] is True
    assert candidates[0]["valid_occupied_records"] == 1
    assert candidates[0]["empty_records"] == 539
    assert candidates[0]["invalid_records"] == 0
    assert all(candidate["party_record_overlaps"] == 0 for candidate in candidates)


def test_broad_scan_rejects_party_overlapping_candidate_region() -> None:
    memory = bytearray(0x400000)
    target_offset = 0x200000
    memory[target_offset : target_offset + RECORD_SIZE] = _boxed_record(
        species_id=415, pid=0x11223344, nickname="Mitsu"
    )
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="region-overlap",
        mode="broad",
        total_bytes=len(memory),
        start_address=MAIN_RAM_BASE,
        party_records_address=MAIN_RAM_BASE + target_offset - 300,
        party_count=1,
        party_record_size=236,
        party_range_valid=True,
        expected_species_id=415,
        expected_nickname="mitsu",
    )
    for offset in range(0, len(memory), 0x1000):
        accumulator.append_chunk(offset, memory[offset : offset + 0x1000])

    result = accumulator.finalize()
    assert result["pc_storage_discovery_identity_matches"][0]["party_overlap"] is False
    assert result["pc_storage_discovery_candidates"]
    assert all(
        candidate["party_record_overlaps"] == 0
        for candidate in result["pc_storage_discovery_candidates"]
    )
    assert result["pc_storage_discovery_summary"][
        "party_overlapping_candidate_bases_rejected"
    ] > 0
    assert result["pc_storage_discovery_summary"][
        "party_overlapping_candidate_bases_rejected"
    ] > 0


def test_broad_scan_refuses_to_rank_without_party_range() -> None:
    memory = bytearray(0x400000)
    memory[0x100000 : 0x100000 + RECORD_SIZE] = _boxed_record()
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="missing-party-range",
        mode="broad",
        total_bytes=len(memory),
        start_address=MAIN_RAM_BASE,
    )
    for offset in range(0, len(memory), 0x1000):
        accumulator.append_chunk(offset, memory[offset : offset + 0x1000])

    result = accumulator.finalize()
    assert result["pc_storage_discovery_candidates"] == []
    assert result["pc_storage_discovery_summary"]["party_range_available"] is False
    assert "refusing to rank" in result["pc_storage_discovery_summary"]["error"]


def test_party_record_exclusion_uses_byte_span_overlap() -> None:
    party_address = MAIN_RAM_BASE + 0x1000
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="party-span",
        mode="broad",
        total_bytes=0x400000,
        start_address=MAIN_RAM_BASE,
        party_records_address=party_address,
        party_count=1,
        party_record_size=236,
        party_range_valid=True,
    )

    assert accumulator._party_slot_overlapping(party_address + 232, RECORD_SIZE) == 1
    assert accumulator._party_slot_overlapping(party_address - RECORD_SIZE, RECORD_SIZE) is None


def test_inspection_scans_local_records_pointers_and_identity_copies() -> None:
    anchor = MAIN_RAM_BASE + 0x80000
    start_address = anchor - INSPECTION_BEFORE_BYTES
    memory = bytearray(INSPECTION_BEFORE_BYTES + INSPECTION_AFTER_BYTES)
    party_address = anchor + 0x1000
    anchor_record = _boxed_record(415, 0x11223344, "Mitsu")
    anchor_offset = anchor - start_address
    memory[anchor_offset : anchor_offset + RECORD_SIZE] = anchor_record
    memory[anchor_offset + 136 : anchor_offset + 272] = anchor_record
    memory[
        party_address - start_address : party_address - start_address + RECORD_SIZE
    ] = _boxed_record(16, 0xAABBCCDD, "PartyMon")
    memory[0x100:0x104] = anchor.to_bytes(4, "little")
    memory[0x104:0x108] = (anchor - 4).to_bytes(4, "little")
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="inspect-anchor",
        mode="inspect",
        total_bytes=len(memory),
        start_address=start_address,
        anchor_address=anchor,
        anchor_data_offset=anchor_offset,
        party_records_address=party_address,
        party_count=1,
        party_record_size=236,
        party_range_valid=True,
    )
    for offset in range(0, len(memory), 0x1000):
        accumulator.append_chunk(offset, memory[offset : offset + 0x1000])

    result = accumulator.finalize()
    records = result["pc_storage_inspection_nearby_records"]
    copies = result["pc_storage_discovery_identity_matches"]
    pointers = result["pc_storage_inspection_pointer_references"]

    assert result["pc_storage_pokemon_inspection_result"] is True
    assert result["pc_storage_inspection_anchor_record"]["checksum_valid"] is True
    assert result["pc_storage_inspection_anchor_record"]["species_id"] == 415
    assert result["pc_storage_inspection_region_start"] == f"0x{anchor - INSPECTION_BEFORE_BYTES:08X}"
    assert result["pc_storage_inspection_region_end_exclusive"] == f"0x{anchor + INSPECTION_AFTER_BYTES:08X}"
    assert len(result["pc_storage_inspection_region_hex"]) == (
        INSPECTION_BEFORE_BYTES + INSPECTION_AFTER_BYTES
    ) * 2
    assert [item["address"] for item in records] == [
        f"0x{anchor:08X}",
        f"0x{anchor + 136:08X}",
        f"0x{party_address:08X}",
    ]
    assert records[1]["delta_from_anchor"] == 136
    assert records[1]["delta_multiple_of_136"] is True
    assert records[1]["delta_multiple_of_236"] is False
    assert len(copies) == 2
    assert all(item["checksum_valid"] and not item["party_overlap"] for item in copies)
    assert all(item["same_as_anchor_identity"] is True for item in copies)
    assert any(
        item["reference_address"] == f"0x{start_address + 0x100:08X}"
        for item in pointers
    )
    assert any(
        item["reference_address"] == f"0x{start_address + 0x104:08X}"
        for item in pointers
    )
    assert result["pc_storage_excluded_party_records"][0]["nickname"] == "PartyMon"


def test_inspection_follows_moved_identity_and_reports_236_and_four_byte_offsets() -> None:
    old_anchor = MAIN_RAM_BASE + 0x80000
    start_address = old_anchor - INSPECTION_BEFORE_BYTES
    memory = bytearray(INSPECTION_BEFORE_BYTES + INSPECTION_AFTER_BYTES)
    party_address = old_anchor + 0x1000
    moved_record_address = old_anchor + 236
    aligned_record_address = old_anchor + 0x2004
    memory[
        party_address - start_address : party_address - start_address + RECORD_SIZE
    ] = _boxed_record(415, 0x11223344, "Mitsu")
    memory[
        moved_record_address - start_address : moved_record_address - start_address + RECORD_SIZE
    ] = _boxed_record(415, 0x11223344, "Mitsu")
    memory[
        aligned_record_address - start_address : aligned_record_address - start_address + RECORD_SIZE
    ] = _boxed_record(298, 0x55667788, "Shizuki")
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="inspect-moved-identity",
        mode="inspect",
        total_bytes=len(memory),
        start_address=start_address,
        anchor_address=old_anchor,
        anchor_data_offset=INSPECTION_BEFORE_BYTES,
        party_records_address=party_address,
        party_count=1,
        party_record_size=236,
        party_range_valid=True,
    )
    for offset in range(0, len(memory), 0x1000):
        accumulator.append_chunk(offset, memory[offset : offset + 0x1000])

    result = accumulator.finalize()
    identity_copies = result["pc_storage_discovery_identity_matches"]
    nearby = result["pc_storage_inspection_nearby_records"]
    moved_record = next(item for item in identity_copies if item["party_overlap"] is False)
    aligned_record = next(
        item for item in nearby if item["address"] == f"0x{aligned_record_address:08X}"
    )

    assert result["pc_storage_inspection_anchor_record"] is None
    assert result["pc_storage_inspection_region_center"] == f"0x{old_anchor:08X}"
    assert moved_record["delta_from_anchor"] == 236
    assert moved_record["delta_multiple_of_236"] is True
    assert aligned_record["delta_aligned_4"] is True
    assert aligned_record["delta_multiple_of_136"] is False
    assert aligned_record["delta_multiple_of_236"] is False
    assert any(item["party_overlap"] is True for item in identity_copies)


def test_inspection_movement_comparison_detects_136_byte_move() -> None:
    server = BizHawkDebugServer()
    old_offset = 0x80000
    new_offset = old_offset + 136
    stable_id = "pid:11223344:ot:1234:5678"
    old_record = {
        "stable_id": stable_id,
        "address": f"0x{MAIN_RAM_BASE + old_offset:08X}",
        "party_overlap": False,
    }
    new_record = {
        "stable_id": stable_id,
        "address": f"0x{MAIN_RAM_BASE + new_offset:08X}",
        "party_overlap": False,
    }
    server._pc_inspection_baseline = {
        "scan_id": "move-before",
        "stable_id": stable_id,
        "identity_addresses": [old_record],
        "anchor_address": MAIN_RAM_BASE + old_offset,
    }
    accumulator = type(
        "InspectionSnapshot",
        (),
        {
            "scan_id": "move-after",
            "anchor_address": MAIN_RAM_BASE + old_offset,
        },
    )()
    result = {
        "pc_storage_inspection_primary_stable_id": stable_id,
        "pc_storage_inspection_valid_record_index": [new_record],
    }

    comparison = server._compare_and_advance_inspection_baseline(accumulator, result)

    assert comparison["movement_detected"] is True
    assert comparison["movement_candidates"][0]["delta_bytes"] == 136
    assert comparison["movement_candidates"][0]["delta_multiple_of_136"] is True
    assert "data" not in server._pc_inspection_baseline
    assert comparison["baseline_advanced"] is True


def test_hex_chunk_json_round_trips_arbitrary_ram_bytes() -> None:
    raw = bytes([0x00, 0x22, 0x5C, 0x0D, 0x0A, *range(0x80, 0x100)])
    payload = {
        "type": "pc_discovery_chunk",
        "scan_id": "binary-safe",
        "chunk_index": 0,
        "offset": 0,
        "byte_count": len(raw),
        "data_encoding": "hex",
        "data": raw.hex().upper(),
    }
    line = json.dumps(payload, separators=(",", ":"))

    decoded = json.loads(line)

    assert bytes.fromhex(decoded["data"]) == raw
    assert decoded["data"] == raw.hex().upper()
    assert all(character in "0123456789ABCDEF" for character in decoded["data"])


def test_fallback_reader_waits_for_a_complete_ndjson_line(tmp_path: Path) -> None:
    fallback = tmp_path / "bizhawk.jsonl"
    fallback.write_bytes(b"")
    server = BizHawkDebugServer(fallback_file=fallback)
    server._poll_log_file(fallback)
    event = _inspection_start_event("partial-line")
    line = json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"
    split = len(line) // 2
    with fallback.open("ab") as handle:
        handle.write(line[:split])

    server._poll_log_file(fallback)

    assert server._pc_discovery_scan is None
    assert server._file_position == 0
    with fallback.open("ab") as handle:
        handle.write(line[split:])

    server._poll_log_file(fallback)

    assert server._pc_discovery_scan is not None
    assert server._pc_discovery_scan.scan_id == "partial-line"
    assert server._file_position == len(line)
    complete_scan = server._pc_discovery_scan
    server._poll_log_file(fallback)
    assert server._pc_discovery_scan is complete_scan
    assert server._file_position == len(line)


def test_fallback_file_truncation_resets_cursor_without_skipping_new_lines(
    tmp_path: Path, caplog
) -> None:
    fallback = tmp_path / "bizhawk.jsonl"
    fallback.write_bytes(b"")
    server = BizHawkDebugServer(fallback_file=fallback)
    server._poll_log_file(fallback)

    first = json.dumps({"type": "heartbeat", "frame": 1}).encode() + b"\n"
    fallback.write_bytes(first)
    server._poll_log_file(fallback)
    assert server.latest_heartbeat().payload["frame"] == 1
    first_cursor = server._file_position

    with caplog.at_level("WARNING"):
        fallback.write_bytes(b"")
        server._poll_log_file(fallback)
    assert "truncated or rotated" in caplog.text
    assert server._file_position == 0

    second = json.dumps({"type": "heartbeat", "frame": 2}).encode() + b"\n"
    fallback.write_bytes(second)
    server._poll_log_file(fallback)
    assert server.latest_heartbeat().payload["frame"] == 2
    assert server._file_position == len(second)
    assert first_cursor == len(first)


def test_fallback_file_reset_during_active_discovery_fails_scan_visibly(
    tmp_path: Path, caplog
) -> None:
    fallback = tmp_path / "bizhawk.jsonl"
    fallback.write_bytes(b"")
    server = BizHawkDebugServer(fallback_file=fallback)
    server._poll_log_file(fallback)
    start_line = json.dumps(_inspection_start_event("reset-active")).encode() + b"\n"
    fallback.write_bytes(start_line)
    server._poll_log_file(fallback)
    assert server._pc_discovery_scan is not None

    with caplog.at_level("ERROR"):
        fallback.write_bytes(b"{}")
        server._poll_log_file(fallback)

    assert server.pc_storage_discovery_progress()["status"] == "failed"
    result = server.latest_pc_storage_discovery_payload().payload
    assert "Discovery transport reset during active scan" in result["pc_storage_anchor_error"]
    assert "old_size=" in caplog.text
    assert "new_size=2" in caplog.text
    assert "active_scan_id=reset-active" in caplog.text


def test_fallback_reader_startup_keeps_trailing_partial_line(tmp_path: Path) -> None:
    fallback = tmp_path / "bizhawk.jsonl"
    previous = json.dumps({"type": "heartbeat", "frame": 1}).encode() + b"\n"
    started = json.dumps(
        _inspection_start_event("startup-partial"),
        separators=(",", ":"),
    ).encode() + b"\n"
    split = len(started) // 2
    fallback.write_bytes(previous + started[:split])
    server = BizHawkDebugServer(fallback_file=fallback)

    server._poll_log_file(fallback)
    assert server._file_position == len(previous)
    assert server._pc_discovery_scan is None

    with fallback.open("ab") as handle:
        handle.write(started[split:])
    server._poll_log_file(fallback)
    accumulator = server._pc_discovery_scan
    assert accumulator is not None
    assert accumulator.scan_id == "startup-partial"
    assert server._file_position == len(previous) + len(started)

    server._poll_log_file(fallback)
    assert server._pc_discovery_scan is accumulator


def test_out_of_order_middle_chunk_is_retried_and_duplicates_are_idempotent() -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket
    scan_id = "middle-chunk"
    server._record_line(
        json.dumps(_inspection_start_event(scan_id)),
        "tcp",
    )

    def send_chunk(index: int, offset: int, value: bytes) -> None:
        server._record_line(
            json.dumps(
                {
                    "type": "pc_discovery_chunk",
                    "scan_id": scan_id,
                    "chunk_index": index,
                    "offset": offset,
                    "byte_count": len(value),
                    "data_encoding": "hex",
                    "data": value.hex(),
                }
            ),
            "tcp",
        )

    send_chunk(0, 0, b"A")
    send_chunk(2, 2, b"C")
    progress = server.pc_storage_discovery_progress()
    assert progress["expected_next_chunk_index"] == 1
    assert progress["highest_chunk_index_seen"] == 2
    assert progress["received_chunk_count"] == 2
    assert progress["missing_chunk_indexes"] == [1]
    assert command_socket.sent == [b"PC_RETRY_DISCOVERY_CHUNK|middle-chunk|1\n"]

    send_chunk(1, 1, b"B")
    accumulator = server._pc_discovery_scan
    assert bytes(accumulator.data) == b"ABC"
    assert accumulator.received_chunk_count == 3
    assert server.pc_storage_discovery_progress()["missing_chunk_indexes"] == []

    send_chunk(2, 2, b"C")
    assert bytes(accumulator.data) == b"ABC"
    assert server.pc_storage_discovery_progress()["received_chunk_count"] == 3


def test_end_packet_arriving_before_chunks_requests_the_first_missing_chunk() -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket
    scan_id = "early-end"
    server._record_line(
        json.dumps(_inspection_start_event(scan_id)),
        "tcp",
    )
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_end",
                "scan_id": scan_id,
                "chunk_count": 36,
                "total_bytes": INSPECTION_BEFORE_BYTES + INSPECTION_AFTER_BYTES,
            }
        ),
        "tcp",
    )

    progress = server.pc_storage_discovery_progress()
    assert progress["end_received"] is True
    assert progress["expected_chunk_count"] == 36
    assert progress["missing_chunk_count"] == 36
    assert progress["status"] == "retrying"
    assert command_socket.sent == [b"PC_RETRY_DISCOVERY_CHUNK|early-end|0\n"]


def test_missing_chunk_retry_and_json_diagnostics(caplog) -> None:
    server = BizHawkDebugServer()
    socket = _CommandSocket()
    server._active_client = socket
    scan_id = "chunk-gap"
    server._record_line(
        json.dumps(_inspection_start_event(scan_id)),
        "tcp",
    )
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_chunk",
                "scan_id": scan_id,
                "chunk_index": 1,
                "offset": 1,
                "byte_count": 1,
                "data_encoding": "hex",
                "data": "00",
            }
        ),
        "tcp",
    )

    assert server.pc_storage_discovery_progress()["status"] == "retrying"
    assert socket.sent == [b"PC_RETRY_DISCOVERY_CHUNK|chunk-gap|0\n"]
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_chunk",
                "scan_id": scan_id,
                "chunk_index": 0,
                "offset": 0,
                "byte_count": 1,
                "data_encoding": "hex",
                "data": "FF",
            }
        ),
        "tcp",
    )
    assert server.pc_storage_discovery_progress()["status"] == "scanning"
    assert server.pc_storage_discovery_progress()["bytes_scanned"] == 2
    assert server.pc_storage_discovery_progress()["chunks_received"] == 2
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_failed",
                "scan_id": scan_id,
                "mode": "inspect",
                "error": "Requested discovery chunk is no longer in the retransmit cache.",
            }
        ),
        "tcp",
    )
    assert server.pc_storage_discovery_progress()["status"] == "failed"
    result = server.latest_pc_storage_discovery_payload().payload
    assert "retransmit cache" in result["pc_storage_anchor_error"]

    malformed = (
        '{"type":"pc_discovery_chunk","scan_id":"diagnostic",'
        '"chunk_index":0,"offset":0,"byte_count":2,"data_encoding":"hex","data":"'
        + ("A" * 500)
    )
    other_server = BizHawkDebugServer()
    diagnostic_socket = _CommandSocket()
    other_server._active_client = diagnostic_socket
    other_server._record_line(
        json.dumps(_inspection_start_event("diagnostic")),
        "tcp",
    )
    with caplog.at_level("WARNING"):
        other_server._record_line(malformed, "tcp")
    assert f"raw_line_length={len(malformed)}" in caplog.text
    assert "char_offset=" in caplog.text
    assert "prefix=" in caplog.text
    assert "suffix=" in caplog.text
    assert other_server.pc_storage_discovery_progress()["status"] == "retrying"
    assert diagnostic_socket.sent == [b"PC_RETRY_DISCOVERY_CHUNK|diagnostic|0\n"]


def test_retry_timeout_fails_instead_of_leaving_discovery_stuck() -> None:
    server = BizHawkDebugServer()
    socket = _CommandSocket()
    server._active_client = socket
    scan_id = "retry-timeout"
    server._record_line(
        json.dumps(_inspection_start_event(scan_id)),
        "tcp",
    )
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_chunk",
                "scan_id": scan_id,
                "chunk_index": 1,
                "offset": 1,
                "byte_count": 1,
                "data_encoding": "hex",
                "data": "00",
            }
        ),
        "tcp",
    )

    accumulator = server._pc_discovery_scan
    accumulator.retry_requested_at[0] = time.monotonic() - 4
    server._check_pc_discovery_retry_timeout()
    assert len(socket.sent) == 2
    accumulator.retry_requested_at[0] = time.monotonic() - 4
    server._check_pc_discovery_retry_timeout()
    assert len(socket.sent) == 3
    accumulator.retry_requested_at[0] = time.monotonic() - 4
    server._check_pc_discovery_retry_timeout()

    progress = server.pc_storage_discovery_progress()
    assert progress["status"] == "failed"
    assert progress["error"].startswith(
        f"Discovery failed: missing chunk 0 at "
        f"0x{INSPECTION_TEST_ANCHOR - INSPECTION_BEFORE_BYTES:08X}"
    )
    assert "retransmission timed out" in progress["error"]


def test_idle_transport_reports_stalled_without_retrying_future_chunk(caplog) -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket
    server._record_line(
        json.dumps(_inspection_start_event("idle-transport")),
        "tcp",
    )
    accumulator = server._pc_discovery_scan
    accumulator.last_chunk_received_at = time.monotonic() - 3
    accumulator.last_transport_log_at = time.monotonic() - 2

    with caplog.at_level("INFO"):
        server._check_pc_discovery_retry_timeout()

    assert command_socket.sent == []
    assert "received=0/36" in caplog.text
    assert "highest_chunk=-1" in caplog.text
    assert "next_unproduced=0" in caplog.text
    assert "missing_count=0" in caplog.text
    assert "end_received=False" in caplog.text
    assert "producer_status=stalled" in caplog.text
    progress = server.pc_storage_discovery_progress()
    assert progress["status"] == "stalled"
    assert progress["producer_status"] == "stalled"
    assert progress["missing_chunk_indexes"] == []
    assert progress["retry_attempts"] == {}


def test_contiguous_chunks_do_not_mark_the_next_unproduced_index_missing() -> None:
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="future-not-missing",
        mode="inspect",
        total_bytes=2048,
        start_address=MAIN_RAM_BASE,
    )
    for index in range(897):
        accumulator.queue_chunk(index, b"A", index, 1)

    progress = accumulator.progress()
    assert progress["received_chunk_count"] == 897
    assert progress["highest_chunk_seen"] == 896
    assert progress["next_unproduced_chunk"] == 897
    assert progress["proven_missing_chunks"] == []
    assert accumulator.first_missing_chunk_index() is None


def test_later_chunk_proves_the_intervening_index_is_missing() -> None:
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="proven-gap",
        mode="inspect",
        total_bytes=2048,
        start_address=MAIN_RAM_BASE,
    )
    for index in range(897):
        accumulator.queue_chunk(index, b"A", index, 1)
    accumulator.queue_chunk(898, b"C", 898, 1)

    progress = accumulator.progress()
    assert progress["highest_chunk_seen"] == 898
    assert progress["proven_missing_chunks"] == [897]
    assert accumulator.first_missing_chunk_index() == 897


def test_end_packet_proves_trailing_chunk_is_missing() -> None:
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="end-gap",
        mode="inspect",
        total_bytes=2048,
        start_address=MAIN_RAM_BASE,
    )
    for index in range(1023):
        accumulator.queue_chunk(index, b"A", index, 1)
    accumulator.end_received = True
    accumulator.expected_chunk_count = 1024
    accumulator.expected_byte_count = 2048

    progress = accumulator.progress()
    assert progress["producer_status"] == "finished"
    assert progress["highest_chunk_seen"] == 1022
    assert progress["proven_missing_chunks"] == [1023]
    assert accumulator.first_missing_chunk_index() == 1023


def test_producer_resumes_after_stall_without_any_retransmission() -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket
    scan_id = "resume-after-stall"
    server._record_line(
        json.dumps(_inspection_start_event(scan_id)),
        "tcp",
    )
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_chunk",
                "scan_id": scan_id,
                "chunk_index": 0,
                "offset": 0,
                "byte_count": 1,
                "data_encoding": "hex",
                "data": "41",
            }
        ),
        "tcp",
    )
    accumulator = server._pc_discovery_scan
    accumulator.last_chunk_received_at = time.monotonic() - 3
    server._check_pc_discovery_retry_timeout()
    assert server.pc_storage_discovery_progress()["producer_status"] == "stalled"
    assert command_socket.sent == []

    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_chunk",
                "scan_id": scan_id,
                "chunk_index": 1,
                "offset": 1,
                "byte_count": 1,
                "data_encoding": "hex",
                "data": "42",
            }
        ),
        "tcp",
    )
    progress = server.pc_storage_discovery_progress()
    assert progress["status"] == "scanning"
    assert progress["producer_status"] == "active"
    assert progress["missing_chunk_indexes"] == []
    assert command_socket.sent == []


def test_stalled_discovery_can_still_be_cancelled() -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket
    server._record_line(
        json.dumps(_inspection_start_event("cancel-stalled")),
        "tcp",
    )
    server._pc_discovery_scan.last_chunk_received_at = time.monotonic() - 3
    server._check_pc_discovery_retry_timeout()

    assert server.pc_storage_discovery_progress()["status"] == "stalled"
    assert server.request_pc_storage_discovery_cancel()
    assert command_socket.sent == [b"PC_CANCEL_DISCOVERY\n"]


def test_anchor_layout_counts_empty_and_occupied_slots_at_exact_anchor() -> None:
    anchor = MAIN_RAM_BASE + 0x28000
    data = _anchor_memory()
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="anchor-test",
        mode="anchor",
        total_bytes=len(data),
        start_address=anchor - ANCHOR_CONTEXT_BYTES,
        anchor_address=anchor,
        anchor_data_offset=ANCHOR_CONTEXT_BYTES,
        party_records_address=MAIN_RAM_BASE + 0x390000,
        party_count=0,
        party_record_size=236,
        party_range_valid=True,
    )
    for offset in range(0, len(data), 0x1000):
        accumulator.append_chunk(offset, data[offset : offset + 0x1000])

    result = accumulator.finalize()
    summary = result["pc_storage_discovery_summary"]
    candidate = result["pc_storage_discovery_candidates"][0]
    diagnostic = result["pc_storage_anchor_diagnostic"]

    assert summary["valid_occupied_records"] == 1
    assert summary["empty_records"] == 539
    assert summary["invalid_records"] == 0
    assert candidate["first_box_pokemon_address"] == f"0x{anchor:08X}"
    assert candidate["sample_records"][0]["box"] == 1
    assert candidate["sample_records"][0]["slot"] == 1
    assert candidate["sample_records"][0]["species_id"] == 298
    assert diagnostic["candidate_pc_boxes_base"] == f"0x{anchor - 0x28:08X}"
    assert diagnostic["header_byte_count"] == 0x28
    assert diagnostic["header_bytes_hex"] == "00" * 0x28
    assert diagnostic["key_position_checks"] == []
    assert diagnostic["party_record_slot"] is None
    assert result["pc_storage_discovery_summary"]["party_range_available"] is True


def test_anchor_layout_decodes_generic_box1_and_box18_positions() -> None:
    anchor = MAIN_RAM_BASE + 0x28000
    data = bytearray(_anchor_memory())
    records_offset = ANCHOR_CONTEXT_BYTES
    data[records_offset : records_offset + RECORD_SIZE] = _boxed_record(
        species_id=415, nickname="mitsu"
    )
    data[records_offset + RECORD_SIZE : records_offset + 2 * RECORD_SIZE] = _boxed_record(
        species_id=187, nickname="petal"
    )
    box18_slot1_offset = records_offset + 510 * RECORD_SIZE
    box18_slot2_offset = records_offset + 511 * RECORD_SIZE
    data[box18_slot1_offset : box18_slot1_offset + RECORD_SIZE] = _boxed_record(
        species_id=258, nickname="sprout"
    )
    data[box18_slot2_offset : box18_slot2_offset + RECORD_SIZE] = _boxed_record(
        species_id=220, nickname="frost"
    )
    header = bytes(range(0x28))
    header_offset = ANCHOR_CONTEXT_BYTES - len(header)
    data[header_offset:ANCHOR_CONTEXT_BYTES] = header

    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="platinum-box-layout",
        mode="anchor",
        total_bytes=len(data),
        start_address=anchor - ANCHOR_CONTEXT_BYTES,
        anchor_address=anchor,
        anchor_data_offset=ANCHOR_CONTEXT_BYTES,
        party_records_address=MAIN_RAM_BASE + 0x390000,
        party_count=0,
        party_record_size=236,
        party_range_valid=True,
    )
    for offset in range(0, len(data), 0x1000):
        accumulator.append_chunk(offset, data[offset : offset + 0x1000])
    result = accumulator.finalize()
    summary = result["pc_storage_discovery_summary"]
    diagnostic = result["pc_storage_anchor_diagnostic"]

    assert anchor + 510 * RECORD_SIZE == anchor + 510 * 136
    assert anchor + 511 * RECORD_SIZE == anchor + 511 * 136
    assert summary["valid_occupied_records"] == 4
    assert summary["empty_records"] == 536
    assert summary["invalid_records"] == 0
    assert [
        (item["box"], item["slot"], item["nickname"], item["species_id"])
        for item in diagnostic["occupied_slots"]
    ] == [
        (1, 1, "mitsu", 415),
        (1, 2, "petal", 187),
        (18, 1, "sprout", 258),
        (18, 2, "frost", 220),
    ]
    assert diagnostic["key_position_checks"] == []
    assert diagnostic["header_address"] == f"0x{anchor - 0x28:08X}"
    assert diagnostic["header_bytes_hex"] == header.hex().upper()
    assert diagnostic["header_interpretation"].endswith("not identified")


def test_transport_reports_chunk_progress_and_cancel_result() -> None:
    server = BizHawkDebugServer()
    socket = _CommandSocket()
    server._active_client = socket
    anchor = MAIN_RAM_BASE + 0x28000
    data = _anchor_memory()
    scan_id = "transport-anchor"
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_started",
                "scan_id": scan_id,
                "mode": "anchor",
                "start_address": f"0x{anchor - ANCHOR_CONTEXT_BYTES:08X}",
                "total_bytes": len(data),
                "anchor_address": f"0x{anchor:08X}",
                "anchor_data_offset": ANCHOR_CONTEXT_BYTES,
                "party_records_address": f"0x{MAIN_RAM_BASE + 0x390000:08X}",
                "party_count": 0,
                "party_record_size": 236,
                "party_range_valid": True,
            }
        ),
        "tcp",
    )
    first_chunk = data[:0x1000]
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_chunk",
                "scan_id": scan_id,
                "offset": 0,
                "data_hex": first_chunk.hex(),
            }
        ),
        "tcp",
    )

    progress = server.pc_storage_discovery_progress()
    assert progress["status"] == "scanning"
    assert progress["current_address"] == f"0x{anchor - ANCHOR_CONTEXT_BYTES + 0x1000:08X}"
    assert progress["candidates_found"] == 1
    assert server.request_pc_storage_discovery_cancel()
    assert socket.sent == [b"PC_CANCEL_DISCOVERY\n"]

    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_cancelled",
                "scan_id": scan_id,
                "mode": "anchor",
                "bytes_scanned": 0x1000,
            }
        ),
        "tcp",
    )
    assert server.pc_storage_discovery_progress()["status"] == "cancelled"
    assert server.latest_pc_storage_discovery_payload().payload[
        "pc_storage_discovery_summary"
    ]["cancelled"] is True


def test_pokemon_inspection_command_dispatches_and_sets_progress_mode() -> None:
    server = BizHawkDebugServer()
    socket = _CommandSocket()
    server._active_client = socket
    anchor = INSPECTION_TEST_ANCHOR

    assert server.request_pc_pokemon_inspection(anchor)
    assert socket.sent == [f"PC_INSPECT_POKEMON|{anchor:08X}\n".encode()]
    assert server.pc_storage_discovery_progress()["mode"] == "inspect"
    assert not server.request_pc_pokemon_inspection(0x01000000)


def test_identity_search_dispatches_matches_and_compares_box_move() -> None:
    server = BizHawkDebugServer()
    socket = _CommandSocket()
    server._active_client = socket
    anchor = INSPECTION_TEST_ANCHOR
    old_address = anchor
    new_address = anchor + 136
    record = _boxed_record(415, 0x11223344, "Mitsu")
    checksum = int.from_bytes(record[6:8], "little")
    stable_id = "pid:11223344:ot:0000:0000"
    server._pc_inspection_baseline = {
        "scan_id": "baseline",
        "anchor_address": anchor,
        "identity": {
            "pid": "0x11223344",
            "checksum": f"0x{checksum:04X}",
            "species_id": 415,
            "nickname": "Mitsu",
            "stable_id": stable_id,
        },
        "identity_addresses": [
            {
                "address": f"0x{old_address:08X}",
                "stable_id": stable_id,
                "party_overlap": False,
            }
        ],
    }

    assert server.request_pc_pokemon_search()
    assert socket.sent == [
        f"PC_SEARCH_POKEMON|11223344|{checksum:04X}|19F|{anchor:08X}\n".encode()
    ]
    server._record_line(
        json.dumps(
            {
                "type": "pc_pokemon_search_started",
                "scan_id": "move-search",
                "anchor_address": f"0x{anchor:08X}",
                "pid": "0x11223344",
                "checksum": f"0x{checksum:04X}",
                "species_id": 415,
                "total_bytes": 0x400000,
                "party_records_address": f"0x{MAIN_RAM_BASE:08X}",
                "party_count": 0,
                "party_record_size": 236,
                "party_range_valid": True,
            }
        ),
        "tcp",
    )
    server._record_line(
        json.dumps(
            {
                "type": "pc_pokemon_search_match",
                "scan_id": "move-search",
                "address": f"0x{new_address:08X}",
                "record_hex": record.hex().upper(),
            }
        ),
        "tcp",
    )
    server._record_line(
        json.dumps(
            {
                "type": "pc_pokemon_search_end",
                "scan_id": "move-search",
                "anchor_address": f"0x{anchor:08X}",
                "bytes_scanned": 0x400000,
                "total_bytes": 0x400000,
                "match_count": 1,
            }
        ),
        "tcp",
    )

    result = server.latest_pc_storage_discovery_payload().payload
    movement = result["pc_storage_movement_comparison"]
    assert result["pc_storage_pokemon_search_result"] is True
    assert result["pc_storage_search_matches"][0]["checksum_valid"] is True
    assert result["pc_storage_search_matches"][0]["party_overlap"] is False
    assert movement["movement_detected"] is True
    assert movement["movement_candidates"][0]["delta_bytes"] == 136
    assert movement["movement_candidates"][0]["old_checksum_valid"] is True
    assert server._pc_pokemon_search is None
    assert server.pc_storage_discovery_progress()["status"] == "completed"


def test_transport_accepts_incremental_inspection_scan_announcement() -> None:
    server = BizHawkDebugServer()
    scan_id = "inspection-announcement"
    anchor = INSPECTION_TEST_ANCHOR
    server._record_line(
        json.dumps(
            {
                **_inspection_start_event(scan_id, anchor),
                "party_records_address": f"0x{MAIN_RAM_BASE + 0x300000:08X}",
                "party_count": 1,
                "party_record_size": 236,
                "party_range_valid": True,
            }
        ),
        "tcp",
    )

    assert server._pc_discovery_scan is not None
    assert server._pc_discovery_scan.mode == "inspect"
    assert server._pc_discovery_scan.anchor_address == anchor
    assert server.pc_storage_discovery_progress()["status"] == "scanning"


def test_inspection_transport_rejects_full_main_ram_snapshot() -> None:
    server = BizHawkDebugServer()
    old_full_ram_announcement = {
        "type": "pc_discovery_started",
        "scan_id": "inspect-full-ram-rejected",
        "mode": "inspect",
        "start_address": f"0x{MAIN_RAM_BASE:08X}",
        "total_bytes": 0x400000,
        "anchor_address": f"0x{INSPECTION_TEST_ANCHOR:08X}",
        "anchor_data_offset": 0,
    }

    server._record_line(json.dumps(old_full_ram_announcement), "tcp")

    assert server._pc_discovery_scan is None
    assert server.pc_storage_discovery_progress()["status"] == "failed"
    assert "invalid PC discovery scan range" in server.pc_storage_discovery_progress()["error"]


def test_identity_search_requires_a_captured_inspection_baseline() -> None:
    server = BizHawkDebugServer()
    assert server.request_pc_pokemon_search() is False


def test_failed_lua_identity_search_is_reported_and_acknowledged() -> None:
    server = BizHawkDebugServer()
    socket = _CommandSocket()
    server._active_client = socket
    anchor = INSPECTION_TEST_ANCHOR
    server._pc_inspection_baseline = {
        "scan_id": "baseline",
        "anchor_address": anchor,
        "identity": {
            "pid": "0x11223344",
            "checksum": "0x1234",
            "species_id": 220,
            "stable_id": "pid:11223344:ot:0000:0000",
        },
        "identity_addresses": [],
    }
    assert server.request_pc_pokemon_search()

    server._record_line(
        json.dumps(
            {
                "type": "pc_pokemon_search_failed",
                "scan_id": "failed-search",
                "anchor_address": f"0x{anchor:08X}",
                "error": "Another PC RAM diagnostic is already active.",
            }
        ),
        "tcp",
    )

    assert socket.sent[-1] == b"PC_ACK_POKEMON_SEARCH|failed-search\n"
    assert server.pc_storage_discovery_progress()["status"] == "failed"
    result = server.latest_pc_storage_discovery_payload().payload
    assert "already active" in result["pc_storage_anchor_error"]


def test_pending_identity_search_can_be_cancelled_before_lua_assigns_scan_id() -> None:
    server = BizHawkDebugServer()
    server._active_client = _CommandSocket()
    anchor = INSPECTION_TEST_ANCHOR
    server._pc_inspection_baseline = {
        "scan_id": "baseline",
        "anchor_address": anchor,
        "identity": {
            "pid": "0x11223344",
            "checksum": "0x1234",
            "species_id": 415,
            "stable_id": "pid:11223344:ot:0000:0000",
        },
        "identity_addresses": [],
    }
    assert server.request_pc_pokemon_search()

    server._record_line(
        json.dumps(
            {
                "type": "pc_pokemon_search_cancelled",
                "scan_id": None,
                "anchor_address": f"0x{anchor:08X}",
                "bytes_scanned": 0,
            }
        ),
        "file",
    )

    assert server._pc_pokemon_search is None
    assert server.pc_storage_discovery_progress()["status"] == "cancelled"
    assert server.latest_pc_storage_discovery_payload().payload[
        "pc_storage_discovery_summary"
    ]["cancelled"] is True


def test_transport_completes_anchor_stream_and_retains_decoded_result() -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket
    anchor = MAIN_RAM_BASE + 0x28000
    data = _anchor_memory()
    scan_id = "completed-anchor"
    start_address = anchor - ANCHOR_CONTEXT_BYTES
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_started",
                "scan_id": scan_id,
                "mode": "anchor",
                "start_address": f"0x{start_address:08X}",
                "total_bytes": len(data),
                "anchor_address": f"0x{anchor:08X}",
                "anchor_data_offset": ANCHOR_CONTEXT_BYTES,
                "party_records_address": f"0x{MAIN_RAM_BASE + 0x390000:08X}",
                "party_count": 0,
                "party_record_size": 236,
                "party_range_valid": True,
            }
        ),
        "file",
    )
    for offset in range(0x1000, len(data), 0x1000):
        chunk = data[offset : offset + 0x1000]
        chunk_index = offset // 0x1000
        server._record_line(
            json.dumps(
                {
                    "type": "pc_discovery_chunk",
                    "scan_id": scan_id,
                    "chunk_index": chunk_index,
                    "offset": offset,
                    "byte_count": len(chunk),
                    "data_encoding": "hex",
                    "data": chunk.hex().upper(),
                }
            ),
            "tcp",
        )
    expected_chunk_count = (len(data) + 0xFFF) // 0x1000
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_end",
                "scan_id": scan_id,
                "chunk_count": expected_chunk_count,
                "total_bytes": len(data),
                "frame": 900,
            }
        ),
        "tcp",
    )
    assert server.pc_storage_discovery_progress()["status"] == "retrying"
    assert command_socket.sent == [b"PC_RETRY_DISCOVERY_CHUNK|completed-anchor|0\n"]
    first_chunk = data[:0x1000]
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_chunk",
                "scan_id": scan_id,
                "chunk_index": 0,
                "offset": 0,
                "byte_count": len(first_chunk),
                "data_encoding": "hex",
                "data": first_chunk.hex().upper(),
            }
        ),
        "tcp",
    )

    deadline = time.monotonic() + 5
    result = None
    while time.monotonic() < deadline:
        result = server.latest_pc_storage_discovery_payload()
        if result is not None:
            break
        time.sleep(0.01)

    assert result is not None
    assert result.payload["pc_storage_discovery_summary"]["valid_occupied_records"] == 1
    assert result.payload["pc_storage_discovery_candidates"][0][
        "first_box_pokemon_address"
    ] == f"0x{anchor:08X}"
    assert server.pc_storage_discovery_progress()["status"] == "completed"


def test_structure_pointer_search_dispatches_and_stores_incremental_result() -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket
    header = MAIN_RAM_BASE + 0x28000
    first_record = header + 0x28

    assert server.request_pc_structure_pointer_search(header, first_record)
    assert command_socket.sent == [
        f"PC_SEARCH_PC_POINTERS|{header:08X}|{first_record:08X}\n".encode()
    ]
    assert not server.request_pc_structure_pointer_search(header, first_record + 4)
    assert not server.request_pc_structure_pointer_search(0, 0x28)

    scan_id = "pc-pointer-search-test"
    server._record_line(
        json.dumps(
            {
                "type": "pc_structure_pointer_search_started",
                "scan_id": scan_id,
                "header_address": f"0x{header:08X}",
                "first_record_address": f"0x{first_record:08X}",
                "runtime_pointer_slot": "0x02101D2C",
                "total_bytes": 0x400000,
            }
        ),
        "tcp",
    )
    server._record_line(
        json.dumps(
            {
                "type": "pc_structure_pointer_search_progress",
                "scan_id": scan_id,
                "bytes_scanned": 0x200000,
                "total_bytes": 0x400000,
                "current_address": "0x02200000",
                "reference_count": 1,
            }
        ),
        "tcp",
    )
    assert server.pc_storage_discovery_progress()["progress_percent"] == 50
    server._record_line(
        json.dumps(
            {
                "type": "pc_structure_pointer_search_end",
                "scan_id": scan_id,
                "header_address": f"0x{header:08X}",
                "first_record_address": f"0x{first_record:08X}",
                "runtime_pointer_slot": "0x02101D2C",
                "bytes_scanned": 0x400000,
                "total_bytes": 0x400000,
                "reference_count": 2,
                "references": [
                    {
                        "reference_address": f"0x{MAIN_RAM_BASE + 0x1000:08X}",
                        "target_address": f"0x{header:08X}",
                    },
                    {
                        "reference_address": f"0x{MAIN_RAM_BASE + 0x2000:08X}",
                        "target_address": f"0x{first_record:08X}",
                    },
                ],
            }
        ),
        "tcp",
    )

    result = server.latest_pc_storage_discovery_payload()
    assert result is not None
    assert result.payload["pc_storage_structure_pointer_search_result"] is True
    assert result.payload["pc_storage_discovery_summary"]["reference_count"] == 2
    assert result.payload["pc_storage_pointer_references"] == [
        {
            "reference_address": f"0x{MAIN_RAM_BASE + 0x1000:08X}",
            "target_address": f"0x{header:08X}",
            "target": "header",
        },
        {
            "reference_address": f"0x{MAIN_RAM_BASE + 0x2000:08X}",
            "target_address": f"0x{first_record:08X}",
            "target": "first_record",
        },
    ]
    assert server.pc_storage_discovery_progress()["status"] == "completed"


def _session_layout_accumulator(
    *, party_address: int = MAIN_RAM_BASE + 0x3F0000, party_count: int = 0
) -> tuple[PCStorageDiscoveryAccumulator, int, bytearray]:
    data = bytearray(0x400000)
    address = MAIN_RAM_BASE + 0x80000
    offset = address - MAIN_RAM_BASE
    first_record = _boxed_record(415, 0x11223344, "Mitsu")
    data[offset : offset + RECORD_SIZE] = first_record
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="session-layout-test",
        mode="session_layout",
        total_bytes=len(data),
        start_address=MAIN_RAM_BASE,
        expected_species_id=415,
        expected_nickname="mitsu",
        lua_run_id="lua-run-current",
        party_records_address=party_address,
        party_count=party_count,
        party_record_size=236,
        party_range_valid=True,
    )
    accumulator.data = data
    accumulator.expected_hits = [
        {
            "offset": offset,
            "decoded": _decode_occupied(first_record, address),
        }
    ]
    accumulator.broad_valid_record_count = 1
    return accumulator, address, data


def test_session_local_layout_resolves_one_expected_box1_anchor() -> None:
    accumulator, address, data = _session_layout_accumulator()

    result = accumulator.finalize()

    summary = result["pc_storage_discovery_summary"]
    assert summary["resolved"] is True
    assert summary["valid_occupied_records"] == 1
    assert summary["empty_records"] == 539
    assert summary["invalid_records"] == 0
    assert result["pc_storage_resolver_status"] == "cache-pending"
    assert result["session_pc_first_record_address"] == f"0x{address:08X}"
    assert result["session_lua_run_id"] == "lua-run-current"
    assert result["expected_box1_slot1"] == {"nickname": "mitsu", "species_id": 415}
    assert result["pc_storage_anchor_raw_hex"] == data[
        address - MAIN_RAM_BASE : address - MAIN_RAM_BASE + RECORD_SIZE
    ].hex().upper()
    assert summary["party_range"] == {
        "start_address": f"0x{MAIN_RAM_BASE + 0x3F0000:08X}",
        "end_address_exclusive": f"0x{MAIN_RAM_BASE + 0x3F0000:08X}",
        "record_size": 236,
        "party_count": 0,
    }
    assert result["pc_storage_decoded_occupied"] == [
        {
            "box": 1,
            "slot": 1,
            "address": f"0x{address:08X}",
            "species_id": 415,
            "nickname": "Mitsu",
            "checksum_valid": True,
        }
    ]


def test_session_layout_recognizes_zero_header_with_nonzero_empty_payload() -> None:
    accumulator, _address, data = _session_layout_accumulator()
    empty_slot = 0x80000 + RECORD_SIZE
    data[empty_slot + 8 : empty_slot + RECORD_SIZE] = bytes([0xA5]) * (RECORD_SIZE - 8)

    result = accumulator.finalize()

    assert result["pc_storage_resolver_status"] == "cache-pending"
    candidate = result["pc_storage_discovery_candidates"][0]
    assert candidate["empty_records"] == 539
    assert candidate["zero_header_nonzero_payload_empty_records"] == 1
    assert candidate["dominant_empty_pattern_count"] == 538
    assert candidate["invalid_records"] == 0


def test_session_layout_rejects_inconsistent_empty_payload_patterns() -> None:
    accumulator, _address, data = _session_layout_accumulator()
    for slot in range(1, 101):
        data[0x80000 + slot * RECORD_SIZE + 8] = slot

    result = accumulator.finalize()

    assert result["pc_storage_resolver_status"] == "unresolved"
    candidate = result["pc_storage_discovery_candidates"][0]
    assert candidate["invalid_records"] == 0
    assert candidate["distinct_empty_patterns"] == 101
    assert candidate["plausible"] is False


def test_session_layout_refuses_party_overlap_and_malformed_regions() -> None:
    overlapping, _address, _data = _session_layout_accumulator(
        party_address=MAIN_RAM_BASE + 0x80000, party_count=1
    )
    overlap_result = overlapping.finalize()
    assert overlap_result["pc_storage_resolver_status"] == "unresolved"
    assert overlap_result["session_pc_first_record_address"] is None

    malformed, _address, data = _session_layout_accumulator()
    for slot in (10, 20, 30):
        offset = 0x80000 + slot * RECORD_SIZE
        data[offset : offset + RECORD_SIZE] = bytes([0xA5]) + bytes(RECORD_SIZE - 1)
    malformed.expected_hits[0]["decoded"] = _decode_occupied(
        bytes(data[0x80000 : 0x80000 + RECORD_SIZE]), MAIN_RAM_BASE + 0x80000
    )
    malformed_result = malformed.finalize()
    assert malformed_result["pc_storage_resolver_status"] == "unresolved"
    assert malformed_result["pc_storage_discovery_summary"]["invalid_records"] == 3

    one_malformed, _address, data = _session_layout_accumulator()
    data[0x80000 + RECORD_SIZE : 0x80000 + 2 * RECORD_SIZE] = (
        bytes([0xA5]) + bytes(RECORD_SIZE - 1)
    )
    assert one_malformed.finalize()["pc_storage_resolver_status"] == "unresolved"


def test_session_layout_leaves_duplicate_equal_candidates_unresolved() -> None:
    accumulator, _address, data = _session_layout_accumulator()
    second_offset = 0x200000
    second_address = MAIN_RAM_BASE + second_offset
    second_record = _boxed_record(415, 0x55667788, "Mitsu")
    data[second_offset : second_offset + RECORD_SIZE] = second_record
    accumulator.expected_hits.append(
        {
            "offset": second_offset,
            "decoded": _decode_occupied(second_record, second_address),
        }
    )

    result = accumulator.finalize()

    assert result["pc_storage_resolver_status"] == "unresolved"
    assert result["session_pc_first_record_address"] is None
    assert "equally strong validation" in result["pc_storage_anchor_error"]


def test_session_layout_requires_party_range_and_full_ram_fit() -> None:
    no_party_range, _address, _data = _session_layout_accumulator()
    no_party_range.party_range_valid = False
    result = no_party_range.finalize()
    assert result["pc_storage_resolver_status"] == "unresolved"
    assert "party RAM range is unavailable" in result["pc_storage_anchor_error"]

    out_of_range, _address, data = _session_layout_accumulator()
    edge_offset = 0x400000 - RECORD_SIZE
    edge_address = MAIN_RAM_BASE + edge_offset
    edge_record = _boxed_record(415, 0x77889900, "Mitsu")
    data[0x80000 : 0x80000 + RECORD_SIZE] = bytes(RECORD_SIZE)
    data[edge_offset : edge_offset + RECORD_SIZE] = edge_record
    out_of_range.expected_hits = [
        {
            "offset": edge_offset,
            "decoded": _decode_occupied(edge_record, edge_address),
        }
    ]
    edge_result = out_of_range.finalize()
    assert edge_result["pc_storage_resolver_status"] == "unresolved"
    assert edge_result["session_pc_first_record_address"] is None


def test_session_layout_command_dispatch_and_lua_cache_use_run_id(tmp_path) -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket

    assert server.request_pc_storage_session_layout_discovery(415, "mitsu")
    assert command_socket.sent == [b"PC_DISCOVER_CURRENT_LAYOUT|415|6D69747375\n"]
    server._pc_discovery_progress = {"status": "idle"}
    assert server.request_pc_storage_session_layout_discovery(
        415, "mitsu", transport="tcp", lua_run_id="lua-run-current"
    )
    assert command_socket.sent[-1] == (
        b"PC_DISCOVER_CURRENT_LAYOUT|415|6D69747375|lua-run-current\n"
    )
    assert server.pc_storage_discovery_progress()["lua_run_id"] == "lua-run-current"
    assert server.pc_storage_discovery_progress()["mode"] == "session_layout"
    assert server.cache_session_pc_layout(
        "lua-run-current", MAIN_RAM_BASE + 0x80000, transport="tcp"
    )
    assert command_socket.sent[-1] == b"PC_CACHE_SESSION_PC|lua-run-current|02080000\n"
    assert not server.cache_session_pc_layout(
        "lua-run-current", MAIN_RAM_BASE + 0x400000 - RECORDS_SIZE + 1, transport="tcp"
    )

    file_server = BizHawkDebugServer(
        fallback_file=tmp_path / "ev_tracker_bizhawk.jsonl"
    )
    assert file_server.request_pc_storage_session_layout_discovery(
        415, "mitsu", transport="file"
    )
    assert file_server.command_file.read_text(encoding="ascii") == (
        "PC_DISCOVER_CURRENT_LAYOUT|415|6D69747375\n"
    )


def _waiting_pc_cache_server(tmp_path) -> tuple[BizHawkDebugServer, int]:
    address = MAIN_RAM_BASE + 0x80000
    server = BizHawkDebugServer(fallback_file=tmp_path / "events.jsonl")
    server._store_pc_discovery_result({
        "type": "pc_discovery_result",
        "pc_storage_session_layout_result": True,
        "pc_storage_resolver_status": "cache-pending",
        "session_lua_run_id": "lua-run-current",
        "session_pc_first_record_address": f"0x{address:08X}",
        "pc_storage_discovery_summary": {"mode": "session_layout"},
    }, "file")
    pending = {
        "lua_run_id": "lua-run-current",
        "address": f"0x{address:08X}",
        "command_sent": True,
        "lua_received": False,
        "lua_validation": "pending",
        "confirmation_received": False,
        "last_lua_run_id": None,
        "sent_at": time.monotonic(),
        "last_confirmation_at": None,
        "source": "file",
    }
    server._pc_cache_install = pending
    server._pc_discovery_progress = {
        "status": "cache-pending", "cache_install": dict(pending)
    }
    return server, address


def test_cache_ready_packet_reaches_handler_and_confirms_layout(tmp_path) -> None:
    server, address = _waiting_pc_cache_server(tmp_path)
    server._record_line(json.dumps({
        "type": "pc_storage_cache_ready",
        "lua_run_id": "lua-run-current",
        "session_pc_first_record_address": f"0x{address:08X}",
        "accepted": True,
        "occupied_records": 8,
        "empty_records": 532,
        "malformed_records": 0,
    }), "file")

    assert server.latest_pc_storage_discovery_payload().payload[
        "pc_storage_resolver_status"
    ] == "resolved for this session"
    progress = server.pc_storage_discovery_progress()
    assert progress["status"] == "completed"
    assert progress["cache_install"]["lua_received"] is True
    assert progress["cache_install"]["lua_validation"] == "pass"
    assert progress["cache_install"]["confirmation_received"] is True


def test_cache_rejection_from_new_lua_run_marks_resolver_stale(tmp_path) -> None:
    server, _address = _waiting_pc_cache_server(tmp_path)
    server._record_line(json.dumps({
        "type": "pc_storage_cache_rejected",
        "lua_run_id": "different-lua-run",
        "expected_lua_run_id": "lua-run-current",
        "accepted": False,
        "error": "Lua run ID mismatch",
    }), "file")

    result = server.latest_pc_storage_discovery_payload().payload
    assert result["pc_storage_resolver_status"] == "stale"
    assert "mismatch" in result["pc_storage_resolver_error"]
    assert server.pc_storage_discovery_progress()["cache_install"]["last_lua_run_id"] == (
        "different-lua-run"
    )


def test_transport_preserves_same_run_cache_and_discards_prior_run(tmp_path) -> None:
    server, _address = _waiting_pc_cache_server(tmp_path)
    server.reset_pc_discovery_for_lua_run("lua-run-current")
    assert server.pc_storage_discovery_progress()["status"] == "cache-pending"
    assert server.latest_pc_storage_discovery_payload() is not None

    server.reset_pc_discovery_for_lua_run("new-lua-run")
    assert server.pc_storage_discovery_progress() == {"status": "idle"}
    assert server.latest_pc_storage_discovery_payload() is None
    assert server._pc_cache_install is None


def test_transport_discards_queued_discovery_from_prior_lua_run(tmp_path) -> None:
    server = BizHawkDebugServer(fallback_file=tmp_path / "events.jsonl")
    server._pc_discovery_progress = {
        "status": "queued", "lua_run_id": "old-lua-run",
    }
    server.reset_pc_discovery_for_lua_run("new-lua-run")
    assert server.pc_storage_discovery_progress() == {"status": "idle"}


def test_cache_rejection_preserves_predicates_and_compares_anchor_bytes(tmp_path) -> None:
    server, address = _waiting_pc_cache_server(tmp_path)
    record = _boxed_record(415, 0x12345678, "Mitsu")
    current = server.latest_pc_storage_discovery_payload()
    server._store_pc_discovery_result({
        **current.payload,
        "expected_box1_slot1": {"nickname": "mitsu", "species_id": 415},
        "pc_storage_anchor_raw_hex": record.hex().upper(),
    }, "file")
    server._record_line(json.dumps({
        "type": "pc_storage_cache_rejected",
        "lua_run_id": "lua-run-current",
        "session_pc_first_record_address": f"0x{address:08X}",
        "accepted": False,
        "error": "Anchor species differs from expected Box 1 Slot 1 species.",
        "cache_validation": {
            "run_id_match": {"expected": "lua-run-current", "actual": "lua-run-current", "pass": True},
            "main_ram_range": {"domain_offset": "0x00080000", "pass": True},
            "anchor_record_ram_read": {
                "bytes_read": RECORD_SIZE,
                "first_64_bytes_hex": record[:64].hex().upper(),
                "raw_136_bytes_hex": record.hex().upper(),
                "pass": True,
            },
            "anchor_species": {"decoded_species_id": 414, "expected_species_id": 415, "pass": False},
            "anchor_nickname": {"checked_by_lua": False},
        },
    }), "file")

    result = server.latest_pc_storage_discovery_payload().payload
    assert result["pc_storage_resolver_status"] == "cache-confirmation-failed"
    validation = result["cache_validation"]
    assert validation["anchor_byte_comparison"]["bytes_match"] is True
    assert validation["python_anchor_decode"]["species_id"] == 415
    assert validation["python_anchor_decode"]["nickname"] == "Mitsu"
    assert validation["anchor_nickname"]["pass"] is True
    assert validation["anchor_species"]["decoded_species_name"] == "Combee"
    assert validation["anchor_species"]["pass"] is False
    assert server.pc_storage_discovery_progress()["cache_install"]["lua_validation"] == "fail"


def test_cache_confirmation_timeout_is_visible_and_retryable(tmp_path) -> None:
    server, address = _waiting_pc_cache_server(tmp_path)
    server._pc_cache_install["sent_at"] -= 4.0
    server._check_pc_cache_confirmation_timeout()

    progress = server.pc_storage_discovery_progress()
    assert progress["status"] == "failed"
    assert progress["pc_storage_resolver_status"] == "cache-confirmation-failed"
    assert "within 3s" in progress["error"]
    assert server.latest_pc_storage_discovery_payload().payload[
        "pc_storage_resolver_status"
    ] == "cache-confirmation-failed"

    assert server.retry_session_pc_cache_install()
    assert server.pc_storage_discovery_progress()["status"] == "cache-pending"
    assert server.command_file.read_text(encoding="ascii") == (
        f"PC_CACHE_SESSION_PC|lua-run-current|{address:08X}\n"
    )


def test_validated_pc_storage_packet_can_confirm_delayed_cache_ack(tmp_path) -> None:
    server, address = _waiting_pc_cache_server(tmp_path)
    server._record_line(json.dumps({
        "type": "pc_storage",
        "lua_run_id": "lua-run-current",
        "session_pc_first_record_address": f"0x{address:08X}",
        "pc_storage_resolver_kind": "session_pc_cache",
        "scan_ok": True,
        "pc_storage_acquisition_enabled": True,
        "occupied_records": 8,
        "empty_records": 532,
        "malformed_records": 0,
    }), "file")

    assert server.pc_storage_discovery_progress()["status"] == "completed"
    assert server.latest_pc_storage_discovery_payload().payload[
        "pc_storage_resolver_status"
    ] == "resolved for this session"


def test_fallback_cache_command_waits_behind_existing_command(tmp_path) -> None:
    server = BizHawkDebugServer(fallback_file=tmp_path / "events.jsonl")
    assert server._write_command_file("PC_SCAN_NOW\n")
    assert server.cache_session_pc_layout(
        "lua-run-current", MAIN_RAM_BASE + 0x80000, transport="file"
    )
    assert server.command_file.read_text(encoding="ascii") == "PC_SCAN_NOW\n"
    assert list(server._command_file_queue) == [
        "PC_CACHE_SESSION_PC|lua-run-current|02080000\n"
    ]
    server.command_file.unlink()
    server._flush_command_file_queue()
    assert server.command_file.read_text(encoding="ascii") == (
        "PC_CACHE_SESSION_PC|lua-run-current|02080000\n"
    )


def test_queued_cache_timeout_starts_after_command_is_published(tmp_path) -> None:
    server, address = _waiting_pc_cache_server(tmp_path)
    command = f"PC_CACHE_SESSION_PC|lua-run-current|{address:08X}\n"
    server._pc_cache_install.update({
        "command": command,
        "command_sent": False,
        "command_queued": True,
        "sent_at": None,
        "queued_at": time.monotonic(),
    })
    server._write_command_file("PC_SCAN_NOW\n")
    server._write_command_file(command)

    server._check_pc_cache_confirmation_timeout()
    assert server.pc_storage_discovery_progress()["status"] == "cache-pending"
    server.command_file.unlink()
    server._flush_command_file_queue()
    assert server._pc_cache_install["command_sent"] is True
    assert server._pc_cache_install["command_queued"] is False
    assert isinstance(server._pc_cache_install["sent_at"], float)


def test_fast_cache_ack_during_retry_is_not_reverted_to_pending(tmp_path, monkeypatch) -> None:
    server, address = _waiting_pc_cache_server(tmp_path)
    server._pc_discovery_progress["status"] = "failed"

    def acknowledge_immediately(_run, _address, *, transport):
        assert transport == "file"
        server._record_line(json.dumps({
            "type": "pc_storage_cache_ready",
            "lua_run_id": "lua-run-current",
            "session_pc_first_record_address": f"0x{address:08X}",
            "accepted": True,
            "occupied_records": 8,
            "empty_records": 532,
            "malformed_records": 0,
        }), "file")
        return True

    monkeypatch.setattr(server, "cache_session_pc_layout", acknowledge_immediately)
    assert server.retry_session_pc_cache_install()
    assert server.pc_storage_discovery_progress()["status"] == "completed"
    assert server.latest_pc_storage_discovery_payload().payload[
        "pc_storage_resolver_status"
    ] == "resolved for this session"


def test_session_layout_start_event_requires_identity_and_lua_run_id() -> None:
    server = BizHawkDebugServer()
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_started",
                "scan_id": "session-start-event",
                "mode": "session_layout",
                "start_address": f"0x{MAIN_RAM_BASE:08X}",
                "total_bytes": 0x400000,
                "estimated_chunk_count": 1024,
                "lua_run_id": "lua-run-current",
                "expected_species_id": 415,
                "expected_nickname_hex": "6D69747375",
                "party_records_address": f"0x{MAIN_RAM_BASE + 0x3F0000:08X}",
                "party_count": 0,
                "party_record_size": 236,
                "party_range_valid": True,
            }
        ),
        "tcp",
    )
    assert server._pc_discovery_scan is not None
    assert server._pc_discovery_scan.mode == "session_layout"
    assert server._pc_discovery_scan.expected_species_id == 415
    assert server._pc_discovery_scan.expected_nickname == "mitsu"
    assert server._pc_discovery_scan.lua_run_id == "lua-run-current"


def test_failed_runtime_relative_announcement_is_not_accepted() -> None:
    server = BizHawkDebugServer()
    header = MAIN_RAM_BASE + 0x10000
    server._record_line(
        json.dumps(
            {
                "type": "pc_discovery_started",
                "scan_id": "retired-runtime-relative-path",
                "mode": "runtime_relative",
                "start_address": f"0x{header:08X}",
                "total_bytes": 0x28 + RECORDS_SIZE,
                "anchor_address": f"0x{header + 0x28:08X}",
                "anchor_data_offset": 0x28,
            }
        ),
        "tcp",
    )

    assert server._pc_discovery_scan is None
    assert server.pc_storage_discovery_progress()["status"] == "failed"
    assert "invalid PC discovery scan range" in server.pc_storage_discovery_progress()[
        "error"
    ]


def test_anchor_region_overlapping_party_is_rejected() -> None:
    anchor = MAIN_RAM_BASE + 0x28000
    data = _anchor_memory()
    accumulator = PCStorageDiscoveryAccumulator(
        scan_id="anchor-party-overlap",
        mode="anchor",
        total_bytes=len(data),
        start_address=anchor - ANCHOR_CONTEXT_BYTES,
        anchor_address=anchor,
        anchor_data_offset=ANCHOR_CONTEXT_BYTES,
        party_records_address=anchor,
        party_count=1,
        party_record_size=236,
        party_range_valid=True,
    )
    for offset in range(0, len(data), 0x1000):
        accumulator.append_chunk(offset, data[offset : offset + 0x1000])

    result = accumulator.finalize()
    assert result["pc_storage_discovery_candidates"] == []
    assert result["pc_storage_anchor_result_ok"] is False
    assert result["pc_storage_anchor_error"] == (
        "Anchored 540-slot region overlaps active party RAM."
    )
    assert result["pc_storage_anchor_diagnostic"][
        "candidate_rejected_party_overlap"
    ] is True
    assert result["pc_storage_excluded_party_records"][0]["party_slot"] == 1


def _save_pc_segment(*, first_slot: bytes | None, second_slot: bytes | None = None) -> bytes:
    segment = bytearray(8 + RECORDS_SIZE)
    segment[:4] = (0).to_bytes(4, "little")
    if first_slot is not None:
        segment[4 : 4 + RECORD_SIZE] = first_slot
    if second_slot is not None:
        start = 4 + RECORD_SIZE
        segment[start : start + RECORD_SIZE] = second_slot
    return bytes(segment)


def _write_platinum_save_file(path: Path, segment: bytes) -> None:
    image = bytearray(0x80000)
    for record_offset in (0x0C104, 0x4C104):
        start = record_offset - 4
        image[start : start + len(segment)] = segment
    path.write_bytes(image)


def _send_pc_save_file_capture(
    server: BizHawkDebugServer, test_id: str
) -> None:
    directory = server._pc_save_offset_test_directory
    assert directory is not None
    save_files = list(directory.glob("*.SaveRAM"))
    for save_file in save_files:
        stat = save_file.stat()
        os.utime(save_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    server._record_line(
        json.dumps(
            {
                "type": "pc_save_offset_test_file_capture",
                "mode": "external_file",
                "test_id": test_id,
                "domains": [
                    {
                        "name": "ARM7 BIOS",
                        "size": 0x4000,
                        "readable": True,
                        "save_like": False,
                        "eligible_copy_count": 0,
                    }
                ],
                "save_ram_api_available": True,
                "save_ram_flush_call_ok": True,
                "lua_build_id": "pc-storage-v18-saveram-change-detection",
                "lua_game_id": "pokemon-platinum",
                "lua_game_version": "gen4-platinum-us",
                "lua_rom_name": "Pokemon Platinum",
                "lua_rom_hash": "platinum-hash",
                "lua_rom_path": None,
                "lua_rom_path_api": "not exposed",
                "lua_system_id": "NDS",
                "lua_configured_save_ram_path": "./SaveRAM",
                "save_ram_flush_semantics": "flushes emulator SaveRAM buffer to disk; does not invoke an in-game save",
                "lua_script_source": "@bizhawk/ev_tracker.lua",
                "lua_run_id": "save-offset-file-run",
                "frame": 12,
            }
        ),
        "tcp",
    )


def test_save_offset_external_file_reads_both_copies_and_detects_box_move(
    tmp_path,
) -> None:
    save_directory = tmp_path / "SaveRAM"
    save_directory.mkdir()
    save_file = save_directory / "platinum.SaveRAM"
    mitsu = _boxed_record(species_id=415, nickname="mitsu")
    _write_platinum_save_file(save_file, _save_pc_segment(first_slot=mitsu))
    (save_directory / "unrelated.txt").write_text("not a save", encoding="utf-8")
    server = BizHawkDebugServer(fallback_file=tmp_path / "tracker.jsonl")

    assert server.request_pc_save_offset_test(
        transport="file",
        save_ram_directory=save_directory,
        expected_nickname="mitsu",
        expected_species_id=415,
    )
    _send_pc_save_file_capture(server, "save-file-before-move")
    first = server.latest_pc_save_offset_test_payload()
    assert first is not None
    first_payload = first.payload
    assert first_payload["save_ram_file_path"] == str(save_file.resolve())
    assert first_payload["save_ram_detection_reason"] == "modified timestamp changed"
    assert first_payload["save_ram_filename_stem"] == "platinum"
    assert first_payload["lua_rom_path"] is None
    assert first_payload["lua_rom_path_api"] == "not exposed"
    assert first_payload["lua_system_id"] == "NDS"
    assert first_payload["save_ram_flush_semantics"].startswith("flushes emulator")
    assert len(first_payload["save_ram_directory_before"]) == 2
    assert len(first_payload["save_ram_directory_after"]) == 2
    assert first_payload["save_ram_file_changes"][0]["filename"] == "platinum.SaveRAM"
    assert first_payload["save_ram_api_available"] is True
    assert first_payload["save_ram_flush_call_ok"] is True
    assert len(first_payload["candidates"]) == 4
    first_record_candidates = [
        item
        for item in first_payload["candidates"]
        if item["layout_interpretation"] == "fixed offset is first Pokemon record"
    ]
    assert [item["save_copy_offset_tested"] for item in first_record_candidates] == [
        "0x0C104",
        "0x4C104",
    ]
    for candidate in first_record_candidates:
        assert candidate["box_1_slot_1"]["species"] == "Combee"
        assert candidate["box_1_slot_1"]["nickname"] == "mitsu"
        assert candidate["box_1_slot_1"]["checksum_valid"] is True
        assert candidate["expected_identity_match"] is True
        assert candidate["occupied_count"] == 1
        assert candidate["checksum_valid_count"] == 1
        assert candidate["empty_count"] == 539
        assert candidate["invalid_count"] == 0
    identity_search = first_payload["save_ram_identity_search"]
    assert identity_search["match_count"] == 2
    assert [item["offset"] for item in identity_search["matches"]] == [
        0x0C104,
        0x4C104,
    ]
    assert all(item["checksum_valid"] for item in identity_search["matches"])
    assert all(
        any(
            position["box"] == 1 and position["slot"] == 1
            and position["interpretation"] == "fixed offset is first record"
            for position in match["layout_positions"]
        )
        for match in identity_search["matches"]
    )

    _write_platinum_save_file(
        save_file,
        _save_pc_segment(first_slot=None, second_slot=mitsu),
    )
    assert server.request_pc_save_offset_test(
        transport="file",
        save_ram_directory=save_directory,
        expected_nickname="mitsu",
        expected_species_id=415,
    )
    _send_pc_save_file_capture(server, "save-file-after-move")
    moved = server.latest_pc_save_offset_test_payload()
    assert moved is not None
    moved_first_record = [
        item
        for item in moved.payload["candidates"]
        if item["layout_interpretation"] == "fixed offset is first Pokemon record"
    ]
    for candidate in moved_first_record:
        assert candidate["box_1_slot_1"]["empty"] is True
        assert candidate["box_1_slot_2"]["nickname"] == "mitsu"
        assert candidate["expected_identity_match"] is False
    changed = [
        item
        for item in moved.payload["comparison"]["targets"]
        if item["interpretation"] == "fixed offset is first Pokemon record"
    ]
    assert len(changed) == 2
    assert all(item["status"] == "changed" for item in changed)
    assert all(
        item["changed_slots"][:2]
        == [{"box": 1, "slot": 1}, {"box": 1, "slot": 2}]
        for item in changed
    )
    movement = moved.payload["save_ram_identity_search"]["file_comparison"]
    assert movement["changed"] is True
    assert movement["changed_byte_count"] > 0
    assert movement["old_matching_offsets"] == ["0x0C104", "0x4C104"]
    assert movement["new_matching_offsets"] == ["0x0C18C", "0x4C18C"]
    assert [item["delta"] for item in movement["movement_candidates"]] == [0x88, 0x88]
    assert movement["changed_ranges"]


def test_save_offset_flush_requires_unique_changed_file(tmp_path) -> None:
    save_directory = tmp_path / "SaveRAM"
    save_directory.mkdir()
    first = save_directory / "first.SaveRAM"
    second = save_directory / "second.SaveRAM"
    image = bytearray(0x80000)
    first.write_bytes(image)
    server = BizHawkDebugServer(fallback_file=tmp_path / "tracker.jsonl")
    assert server.request_pc_save_offset_test(
        transport="file", save_ram_directory=save_directory
    )
    for path in (first, second):
        path.write_bytes(image)
    _send_pc_save_file_capture(server, "ambiguous-flush")
    result = server.latest_pc_save_offset_test_payload()
    assert result is not None
    assert "Multiple SaveRAM files changed" in result.payload["error"]


def test_save_offset_flush_with_no_file_change_fails_without_guessing(tmp_path) -> None:
    save_directory = tmp_path / "SaveRAM"
    save_directory.mkdir()
    save_file = save_directory / "untouched.SaveRAM"
    _write_platinum_save_file(save_file, _save_pc_segment(first_slot=None))
    server = BizHawkDebugServer(fallback_file=tmp_path / "tracker.jsonl")
    assert server.request_pc_save_offset_test(
        transport="file", save_ram_directory=save_directory
    )
    # Deliver the Lua capture packet without simulating any filesystem write.
    server._record_line(
        json.dumps({
            "type": "pc_save_offset_test_file_capture",
            "test_id": "no-change",
            "save_ram_api_available": True,
            "save_ram_flush_call_ok": True,
            "domains": [],
            "lua_run_id": "no-change-run",
        }),
        "tcp",
    )
    result = server.latest_pc_save_offset_test_payload()
    assert result is not None
    assert "no file in the configured SaveRAM directory changed" in result.payload["error"]
    assert result.payload["save_ram_changed_candidate_count"] == 0


def _send_pc_save_capture(
    server: BizHawkDebugServer,
    test_id: str,
    segment: bytes,
) -> None:
    offsets = (0x0C104, 0x4C104)
    chunk_size = 0x1000
    segment_size = len(segment)
    targets = [
        {
            "domain": "SaveRAM",
            "domain_size": 0x80000,
            "record_offset": f"0x{record_offset:X}",
            "segment_offset": f"0x{record_offset - 4:X}",
            "segment_bytes": segment_size,
        }
        for record_offset in offsets
    ]
    total_bytes = len(offsets) * segment_size
    server._record_line(
        json.dumps(
            {
                "type": "pc_save_offset_test_started",
                "test_id": test_id,
                "domains": [
                    {
                        "name": "SaveRAM",
                        "size": 0x80000,
                        "readable": True,
                        "save_like": True,
                        "eligible_copy_count": 2,
                    },
                    {
                        "name": "Main RAM",
                        "size": 0x400000,
                        "readable": True,
                        "save_like": False,
                        "eligible_copy_count": 0,
                    },
                ],
                "targets": targets,
                "segment_count": len(offsets),
                "segment_bytes": segment_size,
                "total_bytes": total_bytes,
                "lua_build_id": "pc-storage-v16-save-offset-probe",
                "lua_game_id": "pokemon-platinum",
                "lua_game_version": "gen4-platinum-us",
                "lua_script_source": "@bizhawk/ev_tracker.lua",
                "lua_run_id": "save-offset-test-run",
            }
        ),
        "tcp",
    )
    for record_offset in offsets:
        for chunk_index, offset in enumerate(range(0, segment_size, chunk_size)):
            chunk = segment[offset : offset + chunk_size]
            server._record_line(
                json.dumps(
                    {
                        "type": "pc_save_offset_test_chunk",
                        "test_id": test_id,
                        "domain": "SaveRAM",
                        "record_offset": record_offset,
                        "segment_offset": offset,
                        "chunk_index": chunk_index,
                        "byte_count": len(chunk),
                        "data_encoding": "hex",
                        "data": chunk.hex().upper(),
                    }
                ),
                "tcp",
            )
    server._record_line(
        json.dumps(
            {
                "type": "pc_save_offset_test_end",
                "test_id": test_id,
                "segment_count": len(offsets),
                "total_bytes": total_bytes,
                "bytes_sent": total_bytes,
            }
        ),
        "tcp",
    )


def test_save_offset_test_decodes_copies_and_compares_unsaved_box_move() -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket
    assert server.request_pc_save_offset_test()
    assert command_socket.sent == [b"PC_TEST_SAVE_PC_OFFSETS\n"]

    mitsu = _boxed_record(species_id=415, nickname="mitsu")
    _send_pc_save_capture(
        server,
        "save-offset-before-move",
        _save_pc_segment(first_slot=mitsu),
    )
    first_capture = server.latest_pc_save_offset_test_payload()
    assert first_capture is not None
    assert server.pc_save_offset_test_status()["status"] == "completed"
    assert len(first_capture.payload["candidates"]) == 4
    first_record_candidates = [
        item
        for item in first_capture.payload["candidates"]
        if item["layout_interpretation"] == "fixed offset is first Pokemon record"
    ]
    assert [item["save_copy_offset_tested"] for item in first_record_candidates] == [
        "0x0C104",
        "0x4C104",
    ]
    for candidate in first_record_candidates:
        assert candidate["header_current_box"] == 0
        assert candidate["header_valid"] is True
        assert candidate["box_1_slot_1"]["species"] == "Combee"
        assert candidate["box_1_slot_1"]["nickname"] == "mitsu"
        assert candidate["box_1_slot_1"]["checksum_valid"] is True
        assert candidate["occupied_count"] == 1
        assert candidate["checksum_valid_count"] == 1
        assert candidate["empty_count"] == 539
        assert candidate["invalid_count"] == 0

    kaze = _boxed_record(species_id=187, nickname="kaze")
    _send_pc_save_capture(
        server,
        "save-offset-after-move",
        _save_pc_segment(first_slot=None, second_slot=kaze),
    )
    second_capture = server.latest_pc_save_offset_test_payload()
    assert second_capture is not None
    moved_candidates = [
        item
        for item in second_capture.payload["candidates"]
        if item["layout_interpretation"] == "fixed offset is first Pokemon record"
    ]
    for candidate in moved_candidates:
        assert candidate["box_1_slot_1"]["empty"] is True
        assert candidate["box_1_slot_2"]["species"] == "Hoppip"
        assert candidate["box_1_slot_2"]["nickname"] == "kaze"
    changed = [
        item
        for item in second_capture.payload["comparison"]["targets"]
        if item["interpretation"] == "fixed offset is first Pokemon record"
    ]
    assert len(changed) == 2
    assert all(item["status"] == "changed" for item in changed)
    assert all(
        item["changed_slots"][:2]
        == [{"box": 1, "slot": 1}, {"box": 1, "slot": 2}]
        for item in changed
    )


def test_save_offset_test_reports_missing_transport_chunk() -> None:
    server = BizHawkDebugServer()
    record_offset = 0x0C104
    segment_size = 8 + RECORDS_SIZE
    server._record_line(
        json.dumps(
            {
                "type": "pc_save_offset_test_started",
                "test_id": "save-offset-gap",
                "domains": [],
                "targets": [
                    {
                        "domain": "SaveRAM",
                        "domain_size": 0x80000,
                        "record_offset": f"0x{record_offset:X}",
                        "segment_offset": f"0x{record_offset - 4:X}",
                        "segment_bytes": segment_size,
                    }
                ],
                "segment_count": 1,
                "segment_bytes": segment_size,
                "total_bytes": segment_size,
            }
        ),
        "tcp",
    )
    server._record_line(
        json.dumps(
            {
                "type": "pc_save_offset_test_end",
                "test_id": "save-offset-gap",
                "segment_count": 1,
                "total_bytes": segment_size,
                "bytes_sent": segment_size,
            }
        ),
        "tcp",
    )
    assert server.pc_save_offset_test_status()["status"] == "failed"
    assert "missing chunk 0" in server.pc_save_offset_test_status()["error"]


def test_live_sram_search_finds_mitsu_in_both_copies_and_compares_slot_move() -> None:
    server = BizHawkDebugServer()
    command_socket = _CommandSocket()
    server._active_client = command_socket
    assert server.request_pc_sram_pokemon_search("mitsu", 415)
    assert command_socket.sent == [b"PC_SEARCH_SRAM_POKEMON\n"]

    first_copy = 0x1000
    second_copy = 0x41000
    image_a = bytearray(0x80000)
    mitsu = _boxed_record(species_id=415, nickname="mitsu")
    image_a[first_copy : first_copy + 136] = mitsu
    image_a[second_copy : second_copy + 136] = mitsu
    _send_live_sram_capture(server, bytes(image_a), scan_id="sram-capture-a")

    first_result = server.latest_pc_sram_search_payload()
    assert first_result is not None
    assert server.pc_sram_search_status()["status"] == "completed"
    assert first_result.payload["domain"] == "SRAM"
    assert first_result.payload["target_match_offsets"] == ["0x01000", "0x41000"]
    assert first_result.payload["target_match_count"] == 2
    assert first_result.payload["target_match_copy_deltas"][0]["delta"] == 0x40000
    assert first_result.payload["eligible_save_copy_count"] == 2
    assert first_result.payload["layout_strongly_validated"] is True
    for match in first_result.payload["target_matches"]:
        layout = match["layout_as_box_1_slot_1"]
        assert match["species_name"] == "Combee"
        assert match["nickname"] == "mitsu"
        assert match["checksum_valid"] is True
        assert len(match["surrounding_bytes"]["hex"]) > 272
        assert layout["valid_occupied_count"] == 1
        assert layout["empty_count"] == 539
        assert layout["invalid_count"] == 0
        assert layout["occupied_records"][0]["box"] == 1
        assert layout["occupied_records"][0]["slot"] == 1

    image_b = bytearray(image_a)
    for base in (first_copy, second_copy):
        image_b[base : base + 136] = bytes(136)
        image_b[base + 136 : base + 272] = mitsu
    assert server.request_pc_sram_pokemon_search("mitsu", 415)
    _send_live_sram_capture(server, bytes(image_b), scan_id="sram-capture-b")

    moved_result = server.latest_pc_sram_search_payload()
    assert moved_result is not None
    assert moved_result.payload["capture_number"] == 2
    assert moved_result.payload["target_match_offsets"] == ["0x01088", "0x41088"]
    comparison = moved_result.payload["previous_capture"]
    assert comparison["available"] is True
    assert comparison["in_game_save_invoked"] is False
    assert comparison["changed_byte_count"] > 0
    assert comparison["observed_plus_0x88"] is True
    assert [pair["delta"] for pair in comparison["movement_pairs"]] == [136, 136]
    assert all(pair["old_checksum_valid"] and pair["new_checksum_valid"] for pair in comparison["movement_pairs"])
    assert [pair["inferred_first_record_offset"] for pair in comparison["movement_pairs"]] == [
        0x1000,
        0x41000,
    ]
    assert "+0x88" in moved_result.payload["conclusion"]


def test_live_sram_search_fails_instead_of_analyzing_missing_chunks() -> None:
    server = BizHawkDebugServer()
    server._active_client = _CommandSocket()
    assert server.request_pc_sram_pokemon_search("mitsu", 415)
    total_bytes = 0x80000
    server._record_line(
        json.dumps(
            {
                "type": "pc_sram_search_started",
                "scan_id": "sram-gap",
                "domain": "SRAM",
                "domain_size": total_bytes,
                "chunk_bytes": 0x1000,
                "chunk_count": total_bytes // 0x1000,
                "lua_run_id": "live-sram-test-run",
            }
        ),
        "tcp",
    )
    server._record_line(
        json.dumps(
            {
                "type": "pc_sram_search_complete",
                "scan_id": "sram-gap",
                "chunk_count": total_bytes // 0x1000,
                "total_bytes": total_bytes,
                "bytes_scanned": total_bytes,
            }
        ),
        "tcp",
    )
    status = server.pc_sram_search_status()
    assert status["status"] == "failed"
    assert "Incomplete live SRAM transfer" in status["error"]
    assert "0/128 chunks" in status["error"]


def test_live_sram_search_uses_four_byte_record_start_alignment() -> None:
    image = bytearray(0x2000)
    record_offset = 0x124
    image[record_offset : record_offset + 136] = _boxed_record(
        species_id=415, nickname="mitsu"
    )
    result = analyze_sram_snapshot(
        bytes(image),
        expected_nickname="mitsu",
        expected_species_id=415,
        domain="SRAM",
        lua_run_id="alignment-test",
        scan_id="alignment-test",
    )
    assert result["target_match_offsets"] == ["0x00124"]
    assert result["target_matches"][0]["checksum_valid"] is True


def test_live_sram_search_rejects_non_sram_domain_and_file_reset_fails_active_scan() -> None:
    server = BizHawkDebugServer()
    server._active_client = _CommandSocket()
    assert server.request_pc_sram_pokemon_search("mitsu", 415)
    server._record_line(
        json.dumps(
            {
                "type": "pc_sram_search_started",
                "scan_id": "wrong-domain",
                "domain": "Main RAM",
                "domain_size": 0x80000,
                "chunk_bytes": 0x1000,
                "chunk_count": 128,
                "lua_run_id": "live-sram-test-run",
            }
        ),
        "tcp",
    )
    assert server.pc_sram_search_status()["status"] == "failed"
    assert "failed SRAM domain/dimension validation" in server.pc_sram_search_status()["error"]

    assert server.request_pc_sram_pokemon_search("mitsu", 415)
    server._record_line(
        json.dumps(
            {
                "type": "pc_sram_search_started",
                "scan_id": "reset-during-sram",
                "domain": "SRAM",
                "domain_size": 0x80000,
                "chunk_bytes": 0x1000,
                "chunk_count": 128,
                "lua_run_id": "live-sram-test-run",
            }
        ),
        "tcp",
    )
    server._handle_fallback_file_reset(
        0x10000, 0, "test truncation", "test"
    )
    assert server.pc_sram_search_status()["status"] == "failed"
    assert "transport reset during active SRAM scan" in server.pc_sram_search_status()["error"]
