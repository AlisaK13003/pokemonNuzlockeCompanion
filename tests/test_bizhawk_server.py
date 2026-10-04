from __future__ import annotations

import json
from pathlib import Path

from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer


class _CommandSocket:
    def __init__(self):
        self.sent = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


def test_default_fallback_file_uses_system_temp_not_working_directory(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)

    server = BizHawkDebugServer()

    assert server.fallback_file == tmp_path / "ev_tracker_bizhawk.jsonl"
    assert server.fallback_file.parent != Path.cwd()


def test_coordinate_capture_command_is_sent_only_for_valid_range() -> None:
    server = BizHawkDebugServer()
    fake_socket = _CommandSocket()
    server._active_client = fake_socket

    assert server.request_coordinate_capture("abc123", "right", 0, 0x400000)
    assert fake_socket.sent == [b"CAPTURE|abc123|right|0|4194304\n"]
    assert not server.request_coordinate_capture("abc123", "unknown", 0, 16)
    assert not server.request_coordinate_capture("abc123", "left", 0, 0x400001)


def test_manual_pc_scan_command_uses_requested_transport() -> None:
    server = BizHawkDebugServer()
    fake_socket = _CommandSocket()
    server._active_client = fake_socket

    assert server.request_pc_storage_scan()
    assert fake_socket.sent == [b"PC_SCAN_NOW\n"]


def test_manual_pc_discovery_command_uses_requested_transport() -> None:
    server = BizHawkDebugServer()
    fake_socket = _CommandSocket()
    server._active_client = fake_socket

    assert server.request_pc_storage_discovery()
    assert fake_socket.sent == [b"PC_DISCOVER_STORAGE\n"]


def test_manual_pc_anchor_discovery_command_contains_parsed_address() -> None:
    server = BizHawkDebugServer()
    fake_socket = _CommandSocket()
    server._active_client = fake_socket

    assert server.request_pc_storage_discovery(anchor_address=0x0227E550)
    assert fake_socket.sent == [b"PC_DISCOVER_STORAGE|BOX1_SLOT1|0227E550\n"]


def test_pc_discovery_payload_is_retained_across_automatic_scans() -> None:
    server = BizHawkDebugServer()
    anchor_payload = {
        "type": "pc_storage",
        "frame": 120,
        "pc_storage_discovery_requested": True,
        "pc_storage_anchor_result": True,
        "pc_storage_anchor_address": "0x0227E550",
    }
    server._record_line(json.dumps(anchor_payload), "file")
    discovery_result = server.latest_pc_storage_discovery_payload()

    server._record_line(
        json.dumps(
            {
                "type": "pc_storage",
                "frame": 180,
                "pc_storage_discovery_requested": False,
                "pc_storage_anchor_result": False,
            }
        ),
        "file",
    )

    assert server.latest_pc_storage_payload().payload["frame"] == 180
    assert server.latest_pc_storage_discovery_payload() is discovery_result
    assert discovery_result.payload["pc_storage_anchor_address"] == "0x0227E550"


def test_live_coordinate_preview_command_uses_domain_offsets() -> None:
    server = BizHawkDebugServer()
    fake_socket = _CommandSocket()
    server._active_client = fake_socket

    assert server.set_coordinate_preview(0x1234, 0x5678, "s16")
    assert server.set_coordinate_preview(None, None)
    assert fake_socket.sent == [b"PREVIEW|4660|22136|s16\n", b"CLEAR_PREVIEW\n"]


def test_friendship_walk_tcp_commands_are_high_level_and_validated() -> None:
    server = BizHawkDebugServer()
    fake_socket = _CommandSocket()
    server._active_client = fake_socket

    receipts = [
        server.send_friendship_walk_command("START", "horizontal"),
        server.send_friendship_walk_command("DIRECTION", "Right"),
        server.send_friendship_walk_command("PING"),
        server.send_friendship_walk_command("STOP"),
    ]
    assert all(receipt is not None for receipt in receipts)
    assert [receipt.sequence for receipt in receipts] == sorted(
        receipt.sequence for receipt in receipts
    )
    assert not server.send_friendship_walk_command("DIRECTION", "A")
    assert not server.send_friendship_walk_command("START", "diagonal")
    assert [command.split(b"|")[2].splitlines()[0] for command in fake_socket.sent] == [
        b"START",
        b"DIRECTION",
        b"PING",
        b"STOP",
    ]
    assert fake_socket.sent[0].endswith(b"|START|horizontal\n")
    assert fake_socket.sent[1].endswith(b"|DIRECTION|Right\n")


def test_friendship_walk_file_command_supports_fallback_transport(tmp_path) -> None:
    server = BizHawkDebugServer(fallback_file=tmp_path / "ram.jsonl")

    receipt = server.send_friendship_walk_command("START", "vertical", transport="file")

    assert receipt is not None
    assert server.command_file.read_text(encoding="ascii") == (
        f"WALK|{receipt.sequence}|START|vertical\n"
    )


def test_fallback_command_queue_does_not_replace_an_unconsumed_command(tmp_path) -> None:
    server = BizHawkDebugServer(fallback_file=tmp_path / "ram.jsonl")

    assert server._write_command_file("PC_DISCOVER_STORAGE\n")
    assert server._write_command_file("PC_RETRY_DISCOVERY_CHUNK|scan-1|7\n")
    assert server.command_file.read_text(encoding="ascii") == "PC_DISCOVER_STORAGE\n"
    assert list(server._command_file_queue) == ["PC_RETRY_DISCOVERY_CHUNK|scan-1|7\n"]

    server.command_file.unlink()
    server._flush_command_file_queue()
    assert server.command_file.read_text(encoding="ascii") == (
        "PC_RETRY_DISCOVERY_CHUNK|scan-1|7\n"
    )
    assert not server._command_file_queue


def test_pc_storage_payload_is_kept_separate_from_party_payload() -> None:
    server = BizHawkDebugServer()
    pc_payload = {"type": "pc_storage", "frame": 120, "records": []}
    party_payload = {"type": "party_memory", "frame": 120, "raw_party_hex": ""}

    server._record_line(json.dumps(pc_payload), "file")
    server._record_line(json.dumps(party_payload), "tcp")

    assert server.latest_pc_storage_payload().payload == pc_payload
    assert server.latest_pc_storage_payload().source == "file"
    assert server.latest_pc_storage_payload().bytes_received == len(
        json.dumps(pc_payload).encode("utf-8")
    )
    assert server.latest_party_payload().payload == party_payload
    assert server.latest_party_payload().source == "tcp"


def test_walk_stop_can_release_over_both_routes_with_one_sequence(tmp_path) -> None:
    server = BizHawkDebugServer(fallback_file=tmp_path / "ram.jsonl")
    fake_socket = _CommandSocket()
    server._active_client = fake_socket

    receipt = server.send_friendship_walk_command("STOP", transport=("file", "tcp"))

    assert receipt is not None
    assert receipt.transport == "file+tcp"
    assert server.command_file.read_text(encoding="ascii") == f"WALK|{receipt.sequence}|STOP\n"
    assert fake_socket.sent == [f"WALK|{receipt.sequence}|STOP\n".encode("ascii")]
