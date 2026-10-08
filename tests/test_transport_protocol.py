"""Pure protocol contracts independent of server, sockets, games and Qt."""

import json
from dataclasses import FrozenInstanceError

import pytest

from pokemon_ev_tracker.transport.protocol import (
    MalformedFamily,
    MessageRoute,
    classify_malformed_line,
    classify_message_type,
    parse_protocol_line,
    raw_json_integer,
)


@pytest.mark.parametrize("kind,route", [
    ("transport_file_reset", MessageRoute.FILE_RESET),
    ("coordinate_scan_future", MessageRoute.COORDINATE),
    ("pc_save_offset_test_future", MessageRoute.SAVE_OFFSET),
    ("pc_sram_search_future", MessageRoute.SRAM_SEARCH),
    ("pc_pokemon_search_future", MessageRoute.POKEMON_SEARCH),
    ("pc_structure_pointer_search_future", MessageRoute.POINTER_SEARCH),
    ("heartbeat", MessageRoute.HEARTBEAT),
    ("party_memory", MessageRoute.PARTY),
    ("pc_storage", MessageRoute.PC_STORAGE),
    ("pc_discovery_started", MessageRoute.PC_DISCOVERY),
    ("pc_discovery_chunk", MessageRoute.PC_DISCOVERY),
    ("pc_discovery_complete", MessageRoute.PC_DISCOVERY),
    ("pc_discovery_end", MessageRoute.PC_DISCOVERY),
    ("pc_discovery_cancelled", MessageRoute.PC_DISCOVERY),
    ("pc_discovery_failed", MessageRoute.PC_DISCOVERY),
    ("pc_session_pc_resolver_cache_result", MessageRoute.PC_DISCOVERY),
    ("pc_storage_cache_ready", MessageRoute.PC_DISCOVERY),
    ("pc_storage_cache_rejected", MessageRoute.PC_DISCOVERY),
    ("pc_discovery_future", MessageRoute.UNKNOWN),
    ("HEARTBEAT", MessageRoute.UNKNOWN),
    (None, MessageRoute.UNKNOWN),
    (False, MessageRoute.UNKNOWN),
    (4, MessageRoute.UNKNOWN),
])
def test_classification_contract(kind, route):
    assert classify_message_type(kind) is route
    result = parse_protocol_line(json.dumps({"type": kind, "frame": 12}))
    assert result.route is route and result.payload["frame"] == 12


@pytest.mark.parametrize("line,route", [
    ("", MessageRoute.EMPTY), (" \r\n", MessageRoute.EMPTY),
    ("null", MessageRoute.NON_OBJECT), ("[]", MessageRoute.NON_OBJECT),
    ('"text"', MessageRoute.NON_OBJECT), ("3", MessageRoute.NON_OBJECT),
    ("true", MessageRoute.NON_OBJECT),
])
def test_empty_nonobject_are_distinct(line, route):
    result = parse_protocol_line(line)
    assert result.route is route and result.payload is None and result.error is None


@pytest.mark.parametrize("line", ['{"type":', '\ufeff{"type":"heartbeat"}', '{"a":1,}', '{"text":"unterminated'])
def test_json_error_preserves_parser_message_offset_and_original_line(line):
    result = parse_protocol_line("  " + line + " \r\n")
    assert result.route is MessageRoute.MALFORMED and result.payload is None
    try:
        json.loads(line)
    except json.JSONDecodeError as expected:
        assert (result.error.msg, result.error.pos) == (expected.msg, expected.pos)
    assert result.raw_line == "  " + line + " " and result.line == line


def test_wrapper_frozen_payload_fields_not_normalized_or_copied():
    line = '{"type":"party_memory","run_id":"lua","ack":7,"text":"é","extra":NaN}'
    result = parse_protocol_line(line)
    assert result.payload["ack"] == 7 and result.payload["text"] == "é"
    assert result.payload["extra"] != result.payload["extra"]  # Standard json.loads accepts NaN.
    assert result.line == line
    with pytest.raises(FrozenInstanceError):
        result.route = MessageRoute.UNKNOWN


def test_duplicate_keys_keep_standard_json_last_value():
    result = parse_protocol_line('{"type":"heartbeat","type":"party_memory"}')
    assert result.route is MessageRoute.PARTY


@pytest.mark.parametrize("value", [[], {}])
def test_unhashable_fields_retain_legacy_exception(value):
    with pytest.raises(TypeError):
        parse_protocol_line(json.dumps({"type": value}))


@pytest.mark.parametrize("line,family", [
    ("pc_discovery_chunk pc_pokemon_search_end pc_save_offset_test_end pc_sram_search_end", MalformedFamily.PC_DISCOVERY),
    ("pc_pokemon_search_end pc_save_offset_test_end pc_sram_search_end", MalformedFamily.POKEMON_SEARCH),
    ("pc_save_offset_test_end pc_sram_search_end", MalformedFamily.SAVE_OFFSET),
    ("pc_sram_search_end", MalformedFamily.SRAM_SEARCH),
    ("coordinate_scan_end", MalformedFamily.UNKNOWN),
    ("PC_DISCOVERY_CHUNK", MalformedFamily.UNKNOWN),
])
def test_malformed_family_precedence_is_only_a_hint(line, family):
    assert classify_malformed_line(line) is family


@pytest.mark.parametrize("line,field,value", [
    ('{"chunk_index":12,', "chunk_index", 12),
    ('{"chunk_index":-3,', "chunk_index", -3),
    ('{"chunk_index":12.5,', "chunk_index", 12),
    ('{"chunk_index":"12",', "chunk_index", None),
    ('{"a.b": 4,', "a.b", 4),
    ('{"chunk_index":null}', "chunk_index", None),
])
def test_raw_integer_extraction_preserves_best_effort_semantics(line, field, value):
    assert raw_json_integer(line, field) == value
