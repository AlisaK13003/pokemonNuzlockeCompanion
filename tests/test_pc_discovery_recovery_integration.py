"""Characterize the locked transport/recovery boundary before extraction."""

import threading

import pytest

from pokemon_ev_tracker.games.platinum.pc_discovery import PCStorageDiscoveryAccumulator
from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer


def scan_server():
    server = BizHawkDebugServer()
    scan = PCStorageDiscoveryAccumulator("recovery", "broad", 512, 0x02000000)
    server._pc_discovery_scan = scan
    return server, scan


@pytest.mark.parametrize("source,routes", [("tcp", ["tcp", "file"]), ("file", ["file", "tcp"])])
def test_retry_route_fallback_commits_only_after_delivery(source, routes):
    server, scan = scan_server()
    calls = []

    def deliver(route, command):
        assert not scan.retry_attempts
        calls.append((route, command))
        return len(calls) == 2

    server._send_tcp_command = lambda command: deliver("tcp", command)
    server._write_command_file = lambda command: deliver("file", command)
    assert server._request_pc_discovery_chunk_retry(scan, 0, source)
    assert calls == [(route, "PC_RETRY_DISCOVERY_CHUNK|recovery|0\n") for route in routes]
    assert scan.retry_attempts == {0: 1}
    assert scan.retry_in_flight == {0}
    assert server._request_pc_discovery_chunk_retry(scan, 0, source)
    assert len(calls) == 2


def test_unsent_retry_retains_no_attempt_or_deadline():
    server, scan = scan_server()
    server._send_tcp_command = lambda command: False
    server._write_command_file = lambda command: False
    assert not server._request_pc_discovery_chunk_retry(scan, 0, "tcp")
    assert (scan.retry_attempts, scan.retry_in_flight, scan.retry_requested_at) == ({}, set(), {})


@pytest.mark.parametrize("count,byte_count,data,indexes,ready", [
    (None, None, b"", set(), False),
    (1, 512, bytes(512), {0}, True),
    (1, 511, bytes(512), {0}, False),
    (1, 512, bytes(511), {0}, False),
    (1, 512, bytes(512), {0, 1}, False),
])
def test_analysis_gate_requires_exact_end_counts(count, byte_count, data, indexes, ready):
    server, scan = scan_server()
    scan.end_received = True
    scan.expected_chunk_count = count
    scan.expected_byte_count = byte_count
    scan.data = bytearray(data)
    scan.received_chunk_indexes = indexes
    scan.completion_source = "file"
    scan.completion_event = {"frame": 42}
    analyzed = []
    failed = []
    server._begin_pc_discovery_analysis_locked = lambda *args: analyzed.append(args)
    server._fail_active_pc_discovery_locked = lambda *args: failed.append(args)
    server._try_finish_pc_discovery_locked(scan, "tcp")
    assert bool(analyzed) is ready
    if ready:
        assert analyzed == [(scan, "file", 42)]
    elif count is not None:
        assert "end packet counts do not match" in failed[0][2]


def test_expired_retries_processed_in_index_order_and_stop_at_failure(monkeypatch):
    server, scan = scan_server()
    monkeypatch.setattr("pokemon_ev_tracker.transport.bizhawk_server.time.monotonic", lambda: 10.0)
    scan.last_transport_log_at = 10
    scan.last_chunk_received_at = 10
    scan.retry_in_flight.update((3, 1, 2))
    scan.retry_requested_at.update({1: 7, 2: 7, 3: 7})
    scan.retry_attempts.update({1: 1, 2: 3, 3: 1})
    sent = []
    failed = []
    server._send_tcp_command = lambda command: sent.append(command) or True
    server._fail_missing_pc_discovery_chunk_locked = lambda *args: failed.append(args)
    server._check_pc_discovery_retry_timeout()
    assert sent == ["PC_RETRY_DISCOVERY_CHUNK|recovery|1\n"]
    assert failed == [(scan, 2, "retransmission timed out")]
    assert scan.retry_in_flight == {1, 3}
    assert scan.retry_requested_at == {1: 10, 3: 7}


def test_session_reset_during_analysis_discards_result_and_recovery():
    server, scan = scan_server()
    scan.lua_run_id = "old-run"
    scan.retry_attempts[0] = 1
    started, release = threading.Event(), threading.Event()

    def analyze():
        started.set()
        assert release.wait(5)
        return {"pc_storage_discovery_summary": {}}

    scan.finalize = analyze
    worker = threading.Thread(target=server._finalize_pc_discovery, args=(scan, "tcp", 42))
    worker.start()
    try:
        assert started.wait(5)
        server.reset_pc_discovery_for_lua_run("new-run")
        assert server._pc_discovery_scan is None
        server._check_pc_discovery_retry_timeout()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert server.latest_pc_storage_discovery_payload() is None
    assert server.pc_storage_discovery_progress() == {"status": "idle"}


def test_chunk_receipt_clears_only_matching_retry_and_owner_aliases():
    server, scan = scan_server()
    scan.retry_in_flight.update((0, 1))
    scan.retry_requested_at.update({0: 1, 1: 1})
    scan.retry_attempts.update({0: 2, 1: 1})
    server._record_pc_discovery_event({
        "type": "pc_discovery_chunk", "scan_id": scan.scan_id, "chunk_index": 0,
        "offset": 0, "byte_count": 1, "data_encoding": "hex", "data": "00",
    }, "tcp")
    assert scan.retry_in_flight == {1}
    assert scan.retry_requested_at == {1: 1}
    assert scan.retry_attempts == {0: 2, 1: 1}
    assert scan.retry_in_flight is scan.recovery.in_flight
    assert scan.retry_attempts is scan.recovery.attempts
    assert scan.retry_requested_at is scan.recovery.requested_at
