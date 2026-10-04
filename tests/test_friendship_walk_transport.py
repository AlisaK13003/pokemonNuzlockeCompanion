from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pokemon_ev_tracker.data_sources.bizhawk import BizHawkRamDataSource
from pokemon_ev_tracker.transport.bizhawk_server import (
    BizHawkHeartbeat,
    FriendshipWalkCommandReceipt,
)


@pytest.mark.parametrize("transport", ["file", "tcp"])
def test_start_and_direction_follow_fresh_party_payload_transport(transport) -> None:
    server = SimpleNamespace(
        latest_party_payload=lambda: SimpleNamespace(
            received_at=time.monotonic(),
            source=transport,
            payload={"player_x": 10, "player_y": 4},
        ),
        is_connected=lambda: False,
        send_friendship_walk_command=Mock(
            side_effect=lambda action, value=None, *, transport: FriendshipWalkCommandReceipt(
                42, transport
            )
        ),
        stale_after_seconds=5.0,
    )
    source = BizHawkRamDataSource(server=server)

    assert source.send_friendship_walk_command("START", "horizontal")
    assert source.send_friendship_walk_command("DIRECTION", "Right")
    assert [
        call.kwargs["transport"] for call in server.send_friendship_walk_command.call_args_list
    ] == [transport, transport]


def test_walk_start_rejects_stale_file_payload() -> None:
    server = SimpleNamespace(
        latest_party_payload=lambda: SimpleNamespace(
            received_at=time.monotonic() - 20,
            source="file",
            payload={"player_x": 10, "player_y": 4},
        ),
        is_connected=lambda: True,
        send_friendship_walk_command=Mock(return_value=None),
        stale_after_seconds=5.0,
    )
    source = BizHawkRamDataSource(server=server)

    assert not source.send_friendship_walk_command("START", "horizontal")
    server.send_friendship_walk_command.assert_not_called()


def test_stop_can_be_sent_after_ram_connection_becomes_stale() -> None:
    server = SimpleNamespace(
        latest_party_payload=lambda: SimpleNamespace(source="file"),
        is_connected=lambda: False,
        send_friendship_walk_command=Mock(
            return_value=FriendshipWalkCommandReceipt(42, "file+tcp")
        ),
    )
    source = BizHawkRamDataSource(server=server)

    assert source.send_friendship_walk_command("STOP")
    server.send_friendship_walk_command.assert_called_once_with(
        "STOP", transport=("file", "tcp")
    )


def test_datasource_snapshot_exposes_canonical_player_position() -> None:
    party = BizHawkHeartbeat(
        time.monotonic(),
        {
            "frame": 456,
            "player_x": -12,
            "player_y": 38,
            "player_delta_x": -1,
            "player_delta_y": 0,
            "player_coordinates_validated": True,
            "friendship_walk_status": "Walking Left",
            "friendship_walk_enabled": True,
            "friendship_walk_mode": "horizontal",
            "friendship_walk_pause_reason": None,
            "friendship_walk_ack_sequence": 42,
            "friendship_walk_ack_frame": 456,
            "friendship_walk_requested_direction": "Left",
            "friendship_walk_injected_direction": "Left",
            "friendship_walk_b_injected": True,
        },
        "file",
    )
    server = SimpleNamespace(
        latest_party_payload=lambda: party,
        latest_heartbeat=lambda: None,
        is_connected=lambda: True,
        stale_after_seconds=5.0,
        host="127.0.0.1",
        port=46387,
        fallback_file="fallback.jsonl",
    )
    source = BizHawkRamDataSource(server=server)

    snapshot = source.snapshot()

    assert snapshot.details["party_payload_fresh"]
    assert snapshot.details["player_position"].x == -12
    assert snapshot.details["player_position"].y == 38
    assert snapshot.details["friendship_walk_enabled"] is True
    assert snapshot.details["friendship_walk_direction"] is None
    assert snapshot.details["friendship_walk_ack_sequence"] == 42
    assert snapshot.details["ram_source"] == "file"
    assert snapshot.details["friendship_walk_requested_direction"] == "Left"
    assert snapshot.details["friendship_walk_injected_direction"] == "Left"
    assert snapshot.details["friendship_walk_mode"] == "horizontal"
    assert snapshot.details["friendship_walk_b_injected"] is True


def test_lua_walk_watchdog_and_ack_use_consistent_wall_clock_and_sequences() -> None:
    script = Path("bizhawk/ev_tracker.lua").read_text(encoding="utf-8")

    assert 'CoordinateState.friendship_walk.last_command_time = os.time()' in script
    assert 'CoordinateState.friendship_walk.last_command_time = os.clock()' not in script
    assert 'line:match("^WALK|(%d+)|START|(horizontal)$")' in script
    assert '{"friendship_walk_ack_sequence", CoordinateState.friendship_walk.ack_sequence}' in script
    assert '{"friendship_walk_ack_frame", CoordinateState.friendship_walk.ack_frame}' in script
    assert "joypad.getimmediate" not in script
    assert "user_holds_direction" not in script
    assert 'joypad.set({Up = false, Down = false, Left = false, Right = false, B = false})' in script
    assert 'Left = direction == "Left"' in script
    assert 'Right = direction == "Right"' in script
    assert 'Up = direction == "Up"' in script
    assert 'Down = direction == "Down"' in script
    assert 'B = true' in script
    assert 'local WALK_REVERSAL_GRACE_FRAMES = 8' in script
    assert 'CoordinateState.friendship_walk.pending_direction = walk_direction' in script
    assert 'frame >= (CoordinateState.friendship_walk.reversal_until_frame or frame)' in script
    assert 'CoordinateState.friendship_walk.b_injected = true' in script
