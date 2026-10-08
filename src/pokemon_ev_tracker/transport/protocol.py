"""Pure NDJSON decoding/classification; no IO, runtime state or RAM interpretation."""

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MessageRoute(Enum):
    EMPTY = "empty"
    NON_OBJECT = "non_object"
    MALFORMED = "malformed"
    FILE_RESET = "file_reset"
    COORDINATE = "coordinate"
    SAVE_OFFSET = "save_offset"
    SRAM_SEARCH = "sram_search"
    PC_DISCOVERY = "pc_discovery"
    POKEMON_SEARCH = "pokemon_search"
    POINTER_SEARCH = "pointer_search"
    HEARTBEAT = "heartbeat"
    PARTY = "party"
    PC_STORAGE = "pc_storage"
    UNKNOWN = "unknown"


class MalformedFamily(Enum):
    PC_DISCOVERY = "pc_discovery"
    POKEMON_SEARCH = "pokemon_search"
    SAVE_OFFSET = "save_offset"
    SRAM_SEARCH = "sram_search"
    UNKNOWN = "unknown"


_DISCOVERY_TYPES = frozenset({
    "pc_discovery_started", "pc_discovery_chunk", "pc_discovery_complete",
    "pc_discovery_end", "pc_discovery_cancelled", "pc_discovery_failed",
    "pc_session_pc_resolver_cache_result", "pc_storage_cache_ready", "pc_storage_cache_rejected",
})


@dataclass(frozen=True, slots=True)
class ParsedLine:
    raw_line: str
    line: str
    route: MessageRoute
    payload: dict[str, Any] | None = None
    error: json.JSONDecodeError | None = None


def classify_message_type(message_type: object) -> MessageRoute:
    """Preserve exact family precedence and legacy message-field semantics."""
    if message_type == "transport_file_reset":
        return MessageRoute.FILE_RESET
    if isinstance(message_type, str) and message_type.startswith("coordinate_scan_"):
        return MessageRoute.COORDINATE
    if isinstance(message_type, str) and message_type.startswith("pc_save_offset_test_"):
        return MessageRoute.SAVE_OFFSET
    if isinstance(message_type, str) and message_type.startswith("pc_sram_search_"):
        return MessageRoute.SRAM_SEARCH
    # Keep the existing TypeError for unhashable fields rather than relaxing input.
    if message_type in _DISCOVERY_TYPES:
        return MessageRoute.PC_DISCOVERY
    if isinstance(message_type, str) and message_type.startswith("pc_pokemon_search_"):
        return MessageRoute.POKEMON_SEARCH
    if isinstance(message_type, str) and message_type.startswith("pc_structure_pointer_search_"):
        return MessageRoute.POINTER_SEARCH
    if message_type == "heartbeat":
        return MessageRoute.HEARTBEAT
    if message_type == "party_memory":
        return MessageRoute.PARTY
    if message_type == "pc_storage":
        return MessageRoute.PC_STORAGE
    return MessageRoute.UNKNOWN


def parse_protocol_line(line: str) -> ParsedLine:
    """Decode one line; stream framing and byte/timestamp publication stay in IO."""
    raw_line = line.rstrip("\r\n")
    normalized = raw_line.strip()
    if not normalized:
        return ParsedLine(raw_line, normalized, MessageRoute.EMPTY)
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as error:
        return ParsedLine(raw_line, normalized, MessageRoute.MALFORMED, error=error)
    if not isinstance(payload, dict):
        return ParsedLine(raw_line, normalized, MessageRoute.NON_OBJECT)
    return ParsedLine(raw_line, normalized, classify_message_type(payload.get("type")), payload)


def classify_malformed_line(line: str) -> MalformedFamily:
    """Best-effort family hints only; the server still validates active scan IDs."""
    if "pc_discovery_" in line:
        return MalformedFamily.PC_DISCOVERY
    if "pc_pokemon_search_" in line:
        return MalformedFamily.POKEMON_SEARCH
    if "pc_save_offset_test_" in line:
        return MalformedFamily.SAVE_OFFSET
    if "pc_sram_search_" in line:
        return MalformedFamily.SRAM_SEARCH
    return MalformedFamily.UNKNOWN


def raw_json_integer(line: str, field: str) -> int | None:
    """Legacy best-effort extraction used to retry malformed discovery chunks."""
    match = re.search(rf'"{re.escape(field)}"\s*:\s*(-?\d+)', line)
    return int(match.group(1)) if match else None
