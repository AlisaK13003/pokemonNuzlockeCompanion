"""Pure presentation of received PC anchor inspection results; no RAM decoding.

The caller owns species lookup, inspection state/baselines and command dispatch.
"""

from collections.abc import Mapping


def _parse_optional_int(value) -> int | None:
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


def format_pc_pokemon_inspection(payload: Mapping, species_names: Mapping[int, str]) -> list[str]:
    lines = ["", "Pokemon Anchor Inspection:"]
    anchor = payload.get("pc_storage_inspection_anchor_address", "--")
    record = payload.get("pc_storage_inspection_anchor_record")
    if isinstance(record, dict):
        species_id = _parse_optional_int(record.get("species_id"))
        species_name = species_names.get(
            species_id, f"Unknown #{species_id if species_id is not None else '--'}"
        )
        lines.append(
            f"  Anchor {anchor}: checksum-valid {record.get('nickname') or species_name} "
            f"({species_name}, #{species_id if species_id is not None else '--'}); "
            f"party overlap={record.get('party_overlap', '--')}; "
            f"PID={record.get('pid', '--')}; stable ID={record.get('stable_id', '--')}"
        )
    else:
        lines.append(
            f"  No checksum-valid Pokemon record starts exactly at {anchor}; "
            "the Main RAM identity search still ran."
        )

    nearby = payload.get("pc_storage_inspection_nearby_records")
    if isinstance(nearby, list):
        lines.extend(("  Nearby checksum-valid records (4-byte start alignment):",))
        names = species_names
        for item in nearby:
            if not isinstance(item, dict):
                continue
            species_id = _parse_optional_int(item.get("species_id"))
            species_name = names.get(
                species_id, f"Unknown #{species_id if species_id is not None else '--'}"
            )
            lines.append(
                f"    {item.get('address', '--')} delta={item.get('delta_hex', '--')}: "
                f"{item.get('nickname') or species_name}/{species_name} "
                f"(#{species_id if species_id is not None else '--'}), "
                f"checksum={item.get('checksum_valid', False)}, "
                f"party overlap={item.get('party_overlap', '--')}, "
                f"delta%136={item.get('delta_multiple_of_136', False)}, "
                f"delta%236={item.get('delta_multiple_of_236', False)}"
            )

    spacing = payload.get("pc_storage_inspection_spacing_summary")
    if isinstance(spacing, dict):
        lines.append(
            "  Repeated spacing: "
            f"adjacent pairs={spacing.get('adjacent_record_pairs', 0)}, "
            f"multiples of 136={spacing.get('pairs_multiple_of_136', 0)}, "
            f"multiples of 236={spacing.get('pairs_multiple_of_236', 0)}"
        )
        for item in spacing.get("repeated_neighbor_spacings", [])[:16]:
            lines.append(
                f"    {item.get('delta_bytes', '--')} bytes apart "
                f"({item.get('occurrences', 0)} adjacent pairs)"
            )

    copies = payload.get("pc_storage_discovery_identity_matches")
    lines.append("  Copies matching the inspected Pokemon identity:")
    if isinstance(copies, list) and copies:
        names = species_names
        for item in copies:
            if not isinstance(item, dict):
                continue
            species_id = _parse_optional_int(item.get("species_id"))
            species_name = names.get(
                species_id, f"Unknown #{species_id if species_id is not None else '--'}"
            )
            lines.append(
                f"    {item.get('address', '--')} delta={item.get('delta_hex', '--')}: "
                f"{item.get('nickname') or '--'} / {species_name} "
                f"(#{species_id if species_id is not None else '--'}); "
                f"checksum={item.get('checksum_valid', False)}, "
                f"party overlap={item.get('party_overlap', '--')}, "
                f"same anchor identity={item.get('same_as_anchor_identity', '--')}"
            )
            for neighbor in item.get("surrounding_valid_pokemon", [])[:24]:
                neighbor_id = _parse_optional_int(neighbor.get("species_id"))
                neighbor_name = names.get(
                    neighbor_id,
                    f"Unknown #{neighbor_id if neighbor_id is not None else '--'}",
                )
                lines.append(
                    f"      nearby {neighbor.get('address', '--')} "
                    f"delta={neighbor.get('delta_hex', '--')}: "
                    f"{neighbor.get('nickname') or neighbor_name}/{neighbor_name}; "
                    f"party overlap={neighbor.get('party_overlap', '--')}"
                )
            for reference in item.get("pointer_references", []):
                lines.append(
                    f"      pointer {reference.get('reference_address', '--')} -> "
                    f"{reference.get('target_address', '--')}"
                )
    else:
        lines.append("    No checksum-valid copies of the inspected identity were found.")

    lines.append("  Direct pointers to supplied address and address - 4:")
    anchor_references = payload.get("pc_storage_inspection_pointer_references")
    if isinstance(anchor_references, list) and anchor_references:
        for reference in anchor_references:
            lines.append(
                f"    {reference.get('reference_address', '--')} -> "
                f"{reference.get('target_address', '--')} "
                f"({', '.join(reference.get('target', []))})"
            )
    else:
        lines.append("    No direct 32-bit little-endian references found.")

    lines.extend(("  Immediate bytes around the anchor:",))
    adjacent_start = payload.get("pc_storage_inspection_adjacent_start", "--")
    adjacent_hex = payload.get("pc_storage_inspection_adjacent_hex")
    if isinstance(adjacent_hex, str):
        try:
            adjacent = bytes.fromhex(adjacent_hex)
            start_address = _parse_optional_int(adjacent_start) or 0
            for offset in range(0, len(adjacent), 16):
                lines.append(
                    f"    0x{start_address + offset:08X}: "
                    + adjacent[offset : offset + 16].hex(" ").upper()
                )
        except ValueError:
            lines.append("    Adjacent byte dump was malformed.")
    for item in payload.get("pc_storage_inspection_adjacent_u32", []):
        lines.append(
            f"    u32 {item.get('relative_offset', '--')} "
            f"{item.get('address', '--')} = {item.get('u32_le', '--')}"
        )

    comparison = payload.get("pc_storage_movement_comparison")
    if isinstance(comparison, dict):
        lines.extend(("  Movement experiment:", f"    Status: {comparison.get('status', '--')}"))
        lines.append(f"    Stable ID: {comparison.get('stable_id') or '--'}")
        if comparison.get("status") == "baseline captured":
            lines.append(
                "    Baseline addresses: " + ", ".join(comparison.get("baseline_addresses", []))
            )
        else:
            lines.append("    Before: " + ", ".join(comparison.get("baseline_addresses", [])))
            lines.append("    After: " + ", ".join(comparison.get("current_addresses", [])))
            for movement in comparison.get("movement_candidates", []):
                lines.append(
                    f"    Move {movement.get('from_address', '--')} -> "
                    f"{movement.get('to_address', '--')}: "
                    f"{movement.get('delta_bytes', '--')} bytes "
                    f"(136-stride={movement.get('delta_multiple_of_136', False)}, "
                    f"236-stride={movement.get('delta_multiple_of_236', False)})"
                )
            changes = comparison.get("changed_ram", {})
            if isinstance(changes, dict):
                lines.append(
                    f"    Changed RAM: {changes.get('changed_bytes', 0)} bytes in "
                    f"{changes.get('changed_region_count', 0)} regions; "
                    f"focus regions={changes.get('focus_region_count', 0)}"
                )
                for region in changes.get("focus_changed_regions", [])[:24]:
                    lines.append(
                        f"      {region.get('address_start', '--')}.."
                        f"{region.get('address_end_exclusive', '--')}: "
                        f"{region.get('changed_bytes', 0)} changed bytes"
                    )

    region_hex = payload.get("pc_storage_inspection_region_hex")
    region_start = _parse_optional_int(payload.get("pc_storage_inspection_region_start"))
    if isinstance(region_hex, str) and region_start is not None:
        lines.append(
            "  Raw anchor region "
            f"{payload.get('pc_storage_inspection_region_start', '--')}.."
            f"{payload.get('pc_storage_inspection_region_end_exclusive', '--')} "
            f"({len(region_hex) // 2} bytes; 0x4000 before, 0x20000 after):"
        )
        try:
            region = bytes.fromhex(region_hex)
            for offset in range(0, len(region), 16):
                lines.append(
                    f"    0x{region_start + offset:08X}: "
                    + region[offset : offset + 16].hex(" ").upper()
                )
        except ValueError:
            lines.append("    Anchor region dump was malformed.")
    return lines
