"""Validation and comparison helpers for live Platinum SRAM PC diagnostics."""

from __future__ import annotations

import hashlib
from itertools import pairwise
from typing import Any

from pokemon_ev_tracker.games.platinum.species import load_gen4_species
from pokemon_ev_tracker.pokemon.gen4.crypto import block_order, prng
from pokemon_ev_tracker.pokemon.gen4.structure import decode_box_pokemon

BOX_RECORD_SIZE = 136
BOX_COUNT = 18
SLOTS_PER_BOX = 30
TOTAL_BOX_SLOTS = BOX_COUNT * SLOTS_PER_BOX
PC_RECORDS_SIZE = BOX_RECORD_SIZE * TOTAL_BOX_SLOTS


def analyze_sram_snapshot(
    image: bytes,
    *,
    expected_nickname: str,
    expected_species_id: int,
    domain: str,
    lua_run_id: str | None,
    scan_id: str,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find real PK4 records and evaluate each target match as a box-array start."""
    valid_records: dict[int, dict[str, Any]] = {}
    target_matches: list[dict[str, Any]] = []
    expected_name = expected_nickname.casefold()
    species_names = {
        species.national_dex_number: species.name for species in load_gen4_species()
    }

    for offset in range(0, max(0, len(image) - BOX_RECORD_SIZE + 1), 4):
        record = image[offset : offset + BOX_RECORD_SIZE]
        if not any(record):
            continue
        pid = int.from_bytes(record[:4], "little")
        checksum = int.from_bytes(record[6:8], "little")
        species_block_index = block_order(pid).index("A")
        rng = prng(checksum)
        for _ in range(species_block_index * 16):
            next(rng)
        species_word = int.from_bytes(
            record[8 + species_block_index * 32 : 10 + species_block_index * 32],
            "little",
        )
        candidate_species = species_word ^ next(rng)
        if not 1 <= candidate_species <= 493:
            continue
        try:
            decoded = decode_box_pokemon(record, offset)
        except (IndexError, ValueError):
            continue
        nickname = decoded.nickname
        if (
            not decoded.checksum_valid
            or not 1 <= decoded.species_id <= 493
            or decoded.nickname_diagnostics.terminator_unit_index is None
            or nickname is None
        ):
            continue
        item = {
            "offset": offset,
            "offset_hex": f"0x{offset:05X}",
            "species_id": decoded.species_id,
            "species_name": species_names.get(decoded.species_id, "Unknown"),
            "nickname": nickname,
            "pid": f"0x{decoded.pid:08X}",
            "checksum": f"0x{decoded.checksum:04X}",
            "checksum_valid": True,
            "record_hex": record.hex().upper(),
        }
        valid_records[offset] = item
        if decoded.species_id == expected_species_id and nickname.casefold() == expected_name:
            target_matches.append(item)

    for match in target_matches:
        offset = match["offset"]
        match["surrounding_bytes"] = _surrounding_hex(image, offset)
        match["nearby_records"] = [
            {
                key: item[key]
                for key in ("offset_hex", "species_id", "species_name", "nickname", "pid", "checksum")
            }
            | {"delta": item["offset"] - offset}
            for item in sorted(
                (
                    record
                    for address, record in valid_records.items()
                    if address != offset and abs(address - offset) <= 0x10000
                ),
                key=lambda record: abs(record["offset"] - offset),
            )[:48]
        ]
        match["stride_neighbors"] = _stride_neighbors(image, offset, valid_records)
        match["stride_walk"] = _stride_walk(image, offset, valid_records)
        match["layout_as_box_1_slot_1"] = _evaluate_layout(image, offset, valid_records)

    prior_comparison = _compare_prior(
        image,
        target_matches,
        prior,
        lua_run_id=lua_run_id,
        domain=domain,
    )
    layouts = [match["layout_as_box_1_slot_1"] for match in target_matches]
    match_offsets = sorted(item["offset"] for item in target_matches)
    result = {
        "type": "pc_sram_search_result",
        "scan_id": scan_id,
        "lua_run_id": lua_run_id,
        "domain": domain,
        "domain_size": len(image),
        "expected_identity": {
            "nickname": expected_nickname,
            "species_id": expected_species_id,
        },
        "sha256": hashlib.sha256(image).hexdigest(),
        "capture_number": (prior or {}).get("capture_number", 0) + 1,
        "candidate_records_examined": max(0, (len(image) - BOX_RECORD_SIZE) // 4 + 1),
        "checksum_valid_record_count": len(valid_records),
        "target_match_count": len(target_matches),
        "target_match_offsets": [f"0x{offset:05X}" for offset in match_offsets],
        "target_match_copy_deltas": [
            {"from": f"0x{left:05X}", "to": f"0x{right:05X}", "delta": right - left}
            for left, right in pairwise(match_offsets)
        ],
        "target_matches": target_matches,
        "pc_layouts": layouts,
        "layout_strongly_validated": any(layout["plausible"] for layout in layouts),
        "previous_capture": prior_comparison,
        "conclusion": _conclusion(target_matches, layouts, prior_comparison),
    }
    return result


def _surrounding_hex(image: bytes, offset: int) -> dict[str, Any]:
    start = max(0, offset - 0x40)
    end = min(len(image), offset + BOX_RECORD_SIZE + 0x40)
    return {
        "start_offset": start,
        "start_offset_hex": f"0x{start:05X}",
        "bytes_before": min(0x40, offset),
        "hex": image[start:end].hex().upper(),
    }


def _classify_slot(
    image: bytes, offset: int, valid_records: dict[int, dict[str, Any]]
) -> tuple[str, dict[str, Any] | None]:
    if offset < 0 or offset + BOX_RECORD_SIZE > len(image):
        return "out_of_range", None
    record = image[offset : offset + BOX_RECORD_SIZE]
    if not any(record):
        return "empty", None
    decoded = valid_records.get(offset)
    if decoded is not None:
        return "occupied", decoded
    return "malformed", None


def _stride_neighbors(
    image: bytes, anchor: int, valid_records: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    neighbors = []
    for slot_delta in range(-4, 9):
        offset = anchor + slot_delta * BOX_RECORD_SIZE
        classification, decoded = _classify_slot(image, offset, valid_records)
        neighbors.append(
            {
                "slot_delta": slot_delta,
                "delta": slot_delta * BOX_RECORD_SIZE,
                "offset_hex": f"0x{offset:05X}",
                "classification": classification,
                "species_id": decoded["species_id"] if decoded else None,
                "species_name": decoded["species_name"] if decoded else None,
                "nickname": decoded["nickname"] if decoded else None,
            }
        )
    return neighbors


def _stride_walk(
    image: bytes, anchor: int, valid_records: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for direction, label in ((-1, "backward"), (1, "forward")):
        counts = {"occupied": 0, "empty": 0, "malformed": 0, "out_of_range": 0}
        occupied = []
        first_malformed = None
        for step in range(1, TOTAL_BOX_SLOTS + 1):
            slot_delta = direction * step
            offset = anchor + slot_delta * BOX_RECORD_SIZE
            classification, decoded = _classify_slot(image, offset, valid_records)
            key = classification if classification in counts else "malformed"
            counts[key] += 1
            if classification == "malformed" and first_malformed is None:
                first_malformed = slot_delta
            if decoded is not None:
                occupied.append(
                    {
                        "slot_delta": slot_delta,
                        "offset_hex": f"0x{offset:05X}",
                        "species_id": decoded["species_id"],
                        "species_name": decoded["species_name"],
                        "nickname": decoded["nickname"],
                        "checksum": decoded["checksum"],
                    }
                )
        summary[label] = {
            **counts,
            "first_malformed_slot_delta": first_malformed,
            "occupied_records": occupied,
        }
    return summary


def _evaluate_layout(
    image: bytes, first_record_offset: int, valid_records: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    region_fits = first_record_offset >= 0 and first_record_offset + PC_RECORDS_SIZE <= len(image)
    occupied = []
    empty_count = 0
    invalid_count = 0
    for index in range(TOTAL_BOX_SLOTS):
        offset = first_record_offset + index * BOX_RECORD_SIZE
        classification, decoded = _classify_slot(image, offset, valid_records)
        if classification == "empty":
            empty_count += 1
        elif classification == "occupied" and decoded is not None:
            box, slot = divmod(index, SLOTS_PER_BOX)
            occupied.append(
                {
                    "box": box + 1,
                    "slot": slot + 1,
                    "offset_hex": f"0x{offset:05X}",
                    "species_id": decoded["species_id"],
                    "species_name": decoded["species_name"],
                    "nickname": decoded["nickname"],
                    "pid": decoded["pid"],
                    "checksum": decoded["checksum"],
                    "checksum_valid": True,
                }
            )
        else:
            invalid_count += 1
    return {
        "first_record_offset": first_record_offset,
        "first_record_offset_hex": f"0x{first_record_offset:05X}",
        "record_size": BOX_RECORD_SIZE,
        "box_count": BOX_COUNT,
        "slots_per_box": SLOTS_PER_BOX,
        "total_slots": TOTAL_BOX_SLOTS,
        "region_fits_domain": region_fits,
        "valid_occupied_count": len(occupied),
        "empty_count": empty_count,
        "invalid_count": invalid_count,
        "occupied_records": occupied,
        "plausible": region_fits and invalid_count == 0 and empty_count + len(occupied) == TOTAL_BOX_SLOTS,
    }


def _compare_prior(
    image: bytes,
    matches: list[dict[str, Any]],
    prior: dict[str, Any] | None,
    *,
    lua_run_id: str | None,
    domain: str,
) -> dict[str, Any]:
    if prior is None:
        return {"available": False, "reason": "No baseline capture in this Lua run yet."}
    if prior.get("lua_run_id") != lua_run_id or prior.get("domain") != domain:
        return {"available": False, "reason": "Lua run or SRAM domain changed; baseline reset."}
    prior_image = prior.get("image")
    if not isinstance(prior_image, bytes) or len(prior_image) != len(image):
        return {"available": False, "reason": "Baseline SRAM image is unavailable or has a different size."}
    changed_offsets = [index for index, (old, new) in enumerate(zip(prior_image, image)) if old != new]
    ranges = _ranges(changed_offsets)
    prior_matches = prior.get("target_matches")
    if isinstance(prior_matches, list):
        old_matches = sorted(prior_matches, key=lambda item: int(item["offset"]))
    else:
        old_matches = [
            {"offset": int(item), "checksum_valid": True}
            for item in sorted(prior.get("target_offsets", []))
        ]
    new_matches = sorted(matches, key=lambda item: int(item["offset"]))
    pairs = []
    unmatched_new = list(new_matches)
    unmatched_old = []
    for old in old_matches:
        old_offset = int(old["offset"])
        same_pid = [item for item in unmatched_new if item.get("pid") == old.get("pid")]
        candidates = same_pid or unmatched_new
        if not candidates:
            unmatched_old.append(old)
            continue
        new = min(candidates, key=lambda item: abs(int(item["offset"]) - old_offset))
        new_offset = int(new["offset"])
        if abs(new_offset - old_offset) > PC_RECORDS_SIZE:
            unmatched_old.append(old)
            continue
        unmatched_new.remove(new)
        pairs.append(
            {
                "old_offset": f"0x{old_offset:05X}",
                "new_offset": f"0x{new_offset:05X}",
                "old_pid": old.get("pid"),
                "new_pid": new.get("pid"),
                "old_checksum": old.get("checksum"),
                "new_checksum": new.get("checksum"),
                "old_checksum_valid": old.get("checksum_valid", False),
                "new_checksum_valid": new.get("checksum_valid", False),
                "delta": new_offset - old_offset,
                "delta_hex": f"{new_offset - old_offset:+#x}",
                "one_box_slot_forward": new_offset - old_offset == BOX_RECORD_SIZE,
                "inferred_first_record_offset": (
                    old_offset if new_offset - old_offset == BOX_RECORD_SIZE else None
                ),
                "inferred_box": 1 if new_offset - old_offset == BOX_RECORD_SIZE else None,
                "inferred_slot": 2 if new_offset - old_offset == BOX_RECORD_SIZE else None,
                "inference_assumption": (
                    "Capture A target was Box 1 Slot 1"
                    if new_offset - old_offset == BOX_RECORD_SIZE
                    else None
                ),
            }
        )
    return {
        "available": True,
        "old_sha256": hashlib.sha256(prior_image).hexdigest(),
        "new_sha256": hashlib.sha256(image).hexdigest(),
        "changed_byte_count": len(changed_offsets),
        "changed_range_count": len(ranges),
        "changed_ranges": ranges[:128],
        "changed_ranges_truncated": len(ranges) > 128,
        "old_target_offsets": [f"0x{item['offset']:05X}" for item in old_matches],
        "new_target_offsets": [f"0x{item['offset']:05X}" for item in new_matches],
        "unmatched_old_target_offsets": [
            f"0x{item['offset']:05X}" for item in unmatched_old
        ],
        "unmatched_new_target_offsets": [
            f"0x{item['offset']:05X}" for item in unmatched_new
        ],
        "movement_pairs": pairs,
        "observed_plus_0x88": any(pair["one_box_slot_forward"] for pair in pairs),
        "in_game_save_invoked": False,
    }


def _ranges(offsets: list[int]) -> list[dict[str, Any]]:
    if not offsets:
        return []
    ranges = []
    start = previous = offsets[0]
    for offset in offsets[1:]:
        if offset != previous + 1:
            ranges.append(_range_item(start, previous))
            start = offset
        previous = offset
    ranges.append(_range_item(start, previous))
    return ranges


def _range_item(start: int, end: int) -> dict[str, Any]:
    return {
        "start_offset": f"0x{start:05X}",
        "end_offset": f"0x{end:05X}",
        "byte_count": end - start + 1,
    }


def _conclusion(
    matches: list[dict[str, Any]],
    layouts: list[dict[str, Any]],
    comparison: dict[str, Any],
) -> str:
    if not matches:
        return "No checksum-valid target Pokemon was found in live SRAM."
    if comparison.get("observed_plus_0x88"):
        return "The target moved by +0x88 in live SRAM without an in-game save; SRAM supports live PC movement monitoring."
    if any(layout["plausible"] for layout in layouts):
        return "Target found and a 540-slot layout is structurally valid; capture again after moving it to compare SRAM offsets."
    return "Target found in live SRAM, but no contiguous 540-slot layout validated from the known Box 1 Slot 1 anchor."
