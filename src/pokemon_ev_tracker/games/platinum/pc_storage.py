"""Runtime PC-box location and boxed-PK4 decoding for Pokemon Platinum."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pokemon_ev_tracker.games.platinum.locations import platinum_met_location_name
from pokemon_ev_tracker.games.platinum.species import load_gen4_species
from pokemon_ev_tracker.pokemon.gen4.structure import DecodedBoxPokemon, decode_box_pokemon

PC_BOX_COUNT = 18
PC_BOX_SLOTS = 30
BOX_POKEMON_SIZE = 0x88
PC_BOX_RECORDS_SIZE = PC_BOX_COUNT * PC_BOX_SLOTS * BOX_POKEMON_SIZE
PC_BOXES_PAGE_ID = 37
PC_BOXES_PAGE_SIZE = 0x121D0
PC_BOXES_PAGE_INFO_OFFSET = 0x20010
SAVE_PAGE_INFO_SIZE = 0x10
SAVE_DATA_STRUCT_TO_BODY_OFFSET = 0x14
PC_BOX_RECORDS_OFFSET = 4
SAVE_DATA_BODY_SIZE = 0x20000
MAIN_RAM_BASE = 0x02000000
SAVE_DATA_POINTER_ADDRESS = 0x02101D2C


@dataclass(frozen=True)
class BoxedPokemon:
    box_index: int
    slot_index: int
    species_name: str
    decoded: DecodedBoxPokemon
    raw_hex: str = ""

    @property
    def species_id(self) -> int:
        return self.decoded.species_id

    @property
    def stable_id(self) -> str:
        return self.decoded.stable_id

    @property
    def level(self) -> int | None:
        return self.decoded.met_level

    @property
    def met_location_name(self) -> str | None:
        return platinum_met_location_name(self.decoded.met_location_id)

    @property
    def checksum_valid(self) -> bool:
        return self.decoded.checksum_valid


@dataclass(frozen=True)
class PCStorageState:
    available: bool
    error: str | None
    save_data_pointer: int | None
    save_data_body_address: int | None
    save_data_struct_address_inferred: int | None
    page_info_address: int | None
    page_location: int | None
    page_size: int | None
    pc_boxes_address: int | None
    records_address: int | None
    records_offset: int | None
    scan_frame: int | None
    scan_duration_ms: float | None
    records: tuple[BoxedPokemon, ...]
    page_id: int | None = None
    page_block_id: int | None = None
    bytes_read: int = 0
    malformed_records: int = 0
    final_address_valid: bool = False
    pc_boxes_header_hex: str | None = None
    diagnostic_candidates: tuple[dict[str, Any], ...] = ()
    box_count: int = PC_BOX_COUNT
    slots_per_box: int = PC_BOX_SLOTS
    record_stride: int = BOX_POKEMON_SIZE
    records_scanned: int = PC_BOX_COUNT * PC_BOX_SLOTS
    resolver_status: str = "unresolved"
    session_pc_first_record_address: int | None = None
    lua_run_id: str | None = None

    @property
    def valid_pokemon(self) -> tuple[BoxedPokemon, ...]:
        return tuple(
            mon
            for mon in self.records
            if mon.checksum_valid and 1 <= mon.species_id <= 493
        )

    @property
    def checksum_failures(self) -> int:
        return sum(not mon.checksum_valid for mon in self.records)

    @property
    def empty_records(self) -> int | None:
        if not self.available:
            return None
        return max(0, self.records_scanned - len(self.records) - self.malformed_records)


def decode_pc_storage_payload(payload: dict[str, Any] | None) -> PCStorageState | None:
    if not isinstance(payload, dict):
        return None

    save_data_pointer = _parse_int(payload.get("save_data_pointer"))
    save_data_body_address = _parse_int(payload.get("save_data_body_address"))
    if save_data_body_address is None:
        save_data_body_address = save_data_pointer
    save_data_struct_address_inferred = _parse_int(payload.get("save_data_struct_address_inferred"))
    page_info_address = _parse_int(payload.get("page_info_address"))
    page_location = _parse_int(payload.get("page_location"))
    page_size = _parse_int(payload.get("page_size"))
    pc_boxes_address = _parse_int(payload.get("pc_boxes_address"))
    records_address = _parse_int(payload.get("records_address"))
    records_offset = _parse_int(payload.get("records_offset"))
    frame = _parse_int(payload.get("frame"))
    page_id = _parse_int(payload.get("page_id"))
    block_id = _parse_int(payload.get("page_block_id"))
    bytes_read = _parse_int(payload.get("bytes_read")) or 0
    final_address_valid = payload.get("final_address_valid") is True
    pc_boxes_header_hex = (
        str(payload["pc_boxes_header_hex"])
        if payload.get("pc_boxes_header_hex") is not None
        else None
    )
    resolver_kind = str(payload.get("pc_storage_resolver_kind") or "")
    resolver_status = str(payload.get("pc_storage_resolver_status") or "unresolved")
    session_pc_first_record_address = _parse_int(
        payload.get("session_pc_first_record_address")
    )
    lua_run_id = str(payload.get("lua_run_id") or "") or None
    session_pc_run_id = str(payload.get("session_pc_run_id") or "") or None
    raw_diagnostic_candidates = payload.get("diagnostic_candidates", ())
    diagnostic_candidates = (
        tuple(item for item in raw_diagnostic_candidates if isinstance(item, dict))
        if isinstance(raw_diagnostic_candidates, list)
        else ()
    )
    scan_duration = payload.get("scan_duration_ms")
    scan_duration_ms = (
        float(scan_duration)
        if isinstance(scan_duration, (int, float)) and not isinstance(scan_duration, bool)
        else None
    )
    if payload.get("scan_ok") is not True:
        return PCStorageState(
            available=False,
            error=str(payload.get("scan_error") or "PC storage resolver was not validated."),
            save_data_pointer=save_data_pointer,
            save_data_body_address=save_data_body_address,
            save_data_struct_address_inferred=save_data_struct_address_inferred,
            page_info_address=page_info_address,
            page_location=page_location,
            page_size=page_size,
            pc_boxes_address=pc_boxes_address,
            records_address=records_address,
            records_offset=records_offset,
            scan_frame=frame,
            scan_duration_ms=scan_duration_ms,
            records=(),
            page_id=page_id,
            page_block_id=block_id,
            bytes_read=bytes_read,
            final_address_valid=final_address_valid,
            pc_boxes_header_hex=pc_boxes_header_hex,
            diagnostic_candidates=diagnostic_candidates,
            resolver_status=resolver_status,
            session_pc_first_record_address=session_pc_first_record_address,
            lua_run_id=lua_run_id,
        )

    box_count = _parse_int(payload.get("box_count"))
    slots_per_box = _parse_int(payload.get("slots_per_box"))
    record_stride = _parse_int(payload.get("record_stride"))
    records_scanned = _parse_int(payload.get("records_scanned"))
    expected_page_info = (
        save_data_body_address + PC_BOXES_PAGE_INFO_OFFSET + PC_BOXES_PAGE_ID * SAVE_PAGE_INFO_SIZE
        if save_data_body_address is not None
        else None
    )
    expected_pc_boxes_address = (
        save_data_body_address + page_location
        if save_data_body_address is not None and page_location is not None
        else None
    )
    expected_records_address = (
        expected_pc_boxes_address + PC_BOX_RECORDS_OFFSET
        if expected_pc_boxes_address is not None
        else None
    )
    if resolver_kind == "session_pc_cache":
        metadata_valid = (
            resolver_status == "resolved for this session"
            and lua_run_id is not None
            and lua_run_id == session_pc_run_id
            and session_pc_first_record_address is not None
            and records_address == session_pc_first_record_address
            and pc_boxes_address == session_pc_first_record_address
            and records_offset == session_pc_first_record_address - MAIN_RAM_BASE
            and final_address_valid
            and MAIN_RAM_BASE <= session_pc_first_record_address
            and session_pc_first_record_address + PC_BOX_RECORDS_SIZE
            <= MAIN_RAM_BASE + 0x400000
            and box_count == PC_BOX_COUNT
            and slots_per_box == PC_BOX_SLOTS
            and record_stride == BOX_POKEMON_SIZE
            and records_scanned == PC_BOX_COUNT * PC_BOX_SLOTS
            and bytes_read == PC_BOX_RECORDS_SIZE
            and (_parse_int(payload.get("malformed_records")) or 0) == 0
        )
    else:
        metadata_valid = (
            records_address is not None
            and records_offset is not None
            and pc_boxes_address is not None
            and page_id == PC_BOXES_PAGE_ID
            and block_id == 1
            and page_size == PC_BOXES_PAGE_SIZE
            and page_location is not None
            and 0 < page_location <= SAVE_DATA_BODY_SIZE - page_size
            and box_count == PC_BOX_COUNT
            and slots_per_box == PC_BOX_SLOTS
            and record_stride == BOX_POKEMON_SIZE
            and records_scanned == PC_BOX_COUNT * PC_BOX_SLOTS
            and page_info_address == expected_page_info
            and pc_boxes_address == expected_pc_boxes_address
            and records_address == expected_records_address
            and records_offset == records_address - MAIN_RAM_BASE
        )
    if not metadata_valid:
        return PCStorageState(
            available=False,
            error=(
                "Session PC resolver metadata is invalid."
                if resolver_kind == "session_pc_cache"
                else "PC storage runtime page metadata does not match the validated Platinum layout."
            ),
            save_data_pointer=save_data_pointer,
            save_data_body_address=save_data_body_address,
            save_data_struct_address_inferred=save_data_struct_address_inferred,
            page_info_address=page_info_address,
            page_location=page_location,
            page_size=page_size,
            pc_boxes_address=pc_boxes_address,
            records_address=records_address,
            records_offset=records_offset,
            scan_frame=frame,
            scan_duration_ms=scan_duration_ms,
            records=(),
            page_id=page_id,
            page_block_id=block_id,
            bytes_read=bytes_read,
            final_address_valid=final_address_valid,
            pc_boxes_header_hex=pc_boxes_header_hex,
            diagnostic_candidates=diagnostic_candidates,
            resolver_status=resolver_status,
            session_pc_first_record_address=session_pc_first_record_address,
            lua_run_id=lua_run_id,
        )

    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        return PCStorageState(
            available=False,
            error="PC storage scan did not include a record list.",
            save_data_pointer=save_data_pointer,
            save_data_body_address=save_data_body_address,
            save_data_struct_address_inferred=save_data_struct_address_inferred,
            page_info_address=page_info_address,
            page_location=page_location,
            page_size=page_size,
            pc_boxes_address=pc_boxes_address,
            records_address=records_address,
            records_offset=records_offset,
            scan_frame=frame,
            scan_duration_ms=scan_duration_ms,
            records=(),
            page_id=page_id,
            page_block_id=block_id,
            bytes_read=bytes_read,
            final_address_valid=final_address_valid,
            pc_boxes_header_hex=pc_boxes_header_hex,
            diagnostic_candidates=diagnostic_candidates,
            resolver_status=resolver_status,
            session_pc_first_record_address=session_pc_first_record_address,
            lua_run_id=lua_run_id,
        )

    names = _species_names()
    records = []
    record_layout_error = False
    record_integrity_error = False
    malformed_records = 0
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            malformed_records += 1
            continue
        try:
            box_index = int(raw_record["box"])
            slot_index = int(raw_record["slot"])
            raw = bytes.fromhex(str(raw_record["raw_hex"]))
        except (KeyError, TypeError, ValueError):
            malformed_records += 1
            continue
        if not (1 <= box_index <= PC_BOX_COUNT and 1 <= slot_index <= PC_BOX_SLOTS):
            malformed_records += 1
            continue
        if len(raw) != BOX_POKEMON_SIZE:
            malformed_records += 1
            continue
        if raw[:8] == bytes(8):
            continue
        address = _parse_int(raw_record.get("address"))
        expected_address = records_address + (
            (box_index - 1) * PC_BOX_SLOTS + slot_index - 1
        ) * BOX_POKEMON_SIZE
        if address != expected_address:
            record_layout_error = True
            malformed_records += 1
            continue
        decoded = decode_box_pokemon(raw, address)
        if resolver_kind == "session_pc_cache" and (
            not decoded.checksum_valid or not 1 <= decoded.species_id <= 493
        ):
            record_integrity_error = True
            malformed_records += 1
            continue
        records.append(
            BoxedPokemon(
                box_index=box_index,
                slot_index=slot_index,
                species_name=names.get(decoded.species_id, f"Unknown #{decoded.species_id}"),
                decoded=decoded,
                raw_hex=raw.hex(" ").upper(),
            )
        )

    if record_layout_error or record_integrity_error:
        return PCStorageState(
            available=False,
            error=(
                "PC storage record addresses do not match the validated box layout."
                if record_layout_error
                else "Cached PC storage contains a malformed occupied record."
            ),
            save_data_pointer=save_data_pointer,
            save_data_body_address=save_data_body_address,
            save_data_struct_address_inferred=save_data_struct_address_inferred,
            page_info_address=page_info_address,
            page_location=page_location,
            page_size=page_size,
            pc_boxes_address=pc_boxes_address,
            records_address=records_address,
            records_offset=records_offset,
            scan_frame=frame,
            scan_duration_ms=scan_duration_ms,
            records=(),
            page_id=page_id,
            page_block_id=block_id,
            bytes_read=bytes_read,
            malformed_records=malformed_records,
            final_address_valid=final_address_valid,
            pc_boxes_header_hex=pc_boxes_header_hex,
            diagnostic_candidates=diagnostic_candidates,
            resolver_status=resolver_status,
            session_pc_first_record_address=session_pc_first_record_address,
            lua_run_id=lua_run_id,
        )

    return PCStorageState(
        available=True,
        error=None,
        save_data_pointer=save_data_pointer,
        save_data_body_address=save_data_body_address,
        save_data_struct_address_inferred=save_data_struct_address_inferred,
        page_info_address=page_info_address,
        page_location=page_location,
        page_size=page_size,
        pc_boxes_address=pc_boxes_address,
        records_address=records_address,
        records_offset=records_offset,
        scan_frame=frame,
        scan_duration_ms=scan_duration_ms,
        records=tuple(records),
        page_id=page_id,
        page_block_id=block_id,
        bytes_read=bytes_read,
        malformed_records=malformed_records,
        final_address_valid=final_address_valid,
        pc_boxes_header_hex=pc_boxes_header_hex,
        diagnostic_candidates=diagnostic_candidates,
        resolver_status=resolver_status,
        session_pc_first_record_address=session_pc_first_record_address,
        lua_run_id=lua_run_id,
    )


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


@lru_cache(maxsize=1)
def _species_names() -> dict[int, str]:
    return {species.national_dex_number: species.name for species in load_gen4_species()}
