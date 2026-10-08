"""Characterize existing receive/dispatch behavior before extracting protocol helpers."""

import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer


@pytest.mark.parametrize("message_type,handler", [
    ("pc_save_offset_test_started", "_record_pc_save_offset_test_event"),
    ("pc_sram_search_chunk", "_record_pc_sram_search_event"),
    ("pc_discovery_chunk", "_record_pc_discovery_event"),
    ("pc_storage_cache_ready", "_record_pc_discovery_event"),
    ("pc_storage_cache_rejected", "_record_pc_discovery_event"),
    ("pc_session_pc_resolver_cache_result", "_record_pc_discovery_event"),
    ("pc_pokemon_search_future", "_record_pc_pokemon_search_event"),
    ("pc_structure_pointer_search_future", "_record_pc_structure_pointer_search_event"),
])
def test_protocol_family_dispatch_keeps_payload_and_source(message_type, handler):
    server = BizHawkDebugServer()
    receive = Mock()
    setattr(server, handler, receive)
    payload = {"type": message_type, "run_id": "lua", "unknown_field": [1, 2]}
    server._record_line(json.dumps(payload), "tcp")
    receive.assert_called_once_with(payload, "tcp")
    assert server.latest_heartbeat() is None


@pytest.mark.parametrize("line", ["", " \r\n", "null", "[]", "42", '"text"', '{"type":'])
def test_empty_nonobject_and_malformed_do_not_publish(line):
    server = BizHawkDebugServer()
    server._record_line(line, "file")
    assert server.latest_heartbeat() is server.latest_party_payload() is None


def test_unicode_byte_count_unknown_messages_and_session_ack_fields():
    server = BizHawkDebugServer()
    payload = {"type": "party_memory", "run_id": "lua-a", "nickname": "é",
               "friendship_walk_ack_sequence": 123, "battle_battler_1_hp": 30}
    line = json.dumps(payload, ensure_ascii=False)
    server._record_line("  " + line + " \r\n", "tcp")
    first = server.latest_party_payload()
    assert first.bytes_received == len(line.encode("utf-8"))
    server._record_line('{"type":"future_message","run_id":"lua-b"}', "file")
    assert server.latest_party_payload() is first and server.latest_heartbeat() is None
    server._record_line(json.dumps({**payload, "run_id": "lua-b", "friendship_walk_ack_sequence": 124}), "file")
    assert server.latest_party_payload().payload["run_id"] == "lua-b"
    assert server.latest_party_payload().payload["friendship_walk_ack_sequence"] == 124


def test_malformed_logging_preserves_untrimmed_diagnostics(caplog):
    server = BizHawkDebugServer()
    server._record_line('  {"type":  \r\n', "tcp")
    assert "raw_line_length=12" in caplog.text
    assert "char_offset=8" in caplog.text


def test_coordinate_future_and_terminal_queue_order():
    server = BizHawkDebugServer()
    server._coordinate_file_capture_id = "capture"
    for kind in ("coordinate_scan_future", "coordinate_scan_chunk", "coordinate_scan_end"):
        server._record_line(json.dumps({"type": kind, "capture_id": "capture"}), "file")
    assert server._coordinate_file_capture_id is None
    assert [event["type"] for event in server.drain_coordinate_events()] == [
        "coordinate_scan_future", "coordinate_scan_chunk", "coordinate_scan_end"]


def test_tcp_partial_multiple_lines_disconnect_and_reconnect():
    server = BizHawkDebugServer()
    for session in ("one", "two"):
        receiver, sender = socket.socketpair()
        thread = threading.Thread(target=server._handle_client, args=(receiver, ("local", 1)))
        thread.start()
        try:
            sender.sendall(b'{"type":"party_')
            sender.sendall(f'memory","run_id":"{session}"}}\n{{"type":"heartbeat"}}\n'.encode())
            sender.shutdown(socket.SHUT_WR)
            thread.join(3)
            assert not thread.is_alive()
            assert server.latest_party_payload().payload["run_id"] == session
            assert server.latest_heartbeat().source == "tcp"
            assert server._active_client is None
        finally:
            sender.close()
            receiver.close()
            thread.join(3)


def test_concurrent_reception_keeps_each_producer_order():
    server = BizHawkDebugServer()
    def receive(source):
        for frame in range(30):
            server._record_line(json.dumps({"type": "coordinate_scan_chunk", "source": source, "frame": frame}), source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(receive, ("tcp", "file")))
    events = server.drain_coordinate_events()
    assert len(events) == 60
    for source in ("tcp", "file"):
        assert [event["frame"] for event in events if event["source"] == source] == list(range(30))


def test_unhashable_message_type_keeps_existing_exception_contract():
    # D1 does not silently harden/change existing message-field semantics.
    with pytest.raises(TypeError):
        BizHawkDebugServer()._record_line('{"type":[]}', "tcp")
