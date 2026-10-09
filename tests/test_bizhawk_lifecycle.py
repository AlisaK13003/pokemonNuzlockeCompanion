"""Deterministic transport resource/lifecycle tests without an emulator."""

import json
import socket
import threading
import time

import pytest

from pokemon_ev_tracker.games.platinum.pc_discovery import PCStorageDiscoveryAccumulator
from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer


def loops(server):
    tcp, file = threading.Event(), threading.Event()

    def run(ready):
        event = server._stop_event
        ready.set()
        event.wait()

    server._serve_tcp = lambda: run(tcp)
    server._poll_fallback_file = lambda: run(file)
    return tcp, file


def start(server, ready):
    server.start()
    assert all(event.wait(2) for event in ready)


def test_repeated_start_stop_and_restart_resets_runtime(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    for _ in range(3):
        ready = loops(server)
        start(server, ready)
        threads = set(server._owned_threads)
        assert len(threads) == 2
        server.start()
        assert server._owned_threads == threads
        server._record_line(json.dumps({"type": "party_memory", "friendship_walk_ack_sequence": 12}), "tcp")
        assert server.latest_party_payload() is not None
        server.stop()
        server.stop()
        assert not server._owned_threads
        assert all(not thread.is_alive() for thread in threads)
        assert server.latest_party_payload() is None
        assert server._party_payload is None
        assert not server.is_connected()


class Client:
    def __init__(self, sock):
        self.sock = sock
        self.ready = threading.Event()

    def fileno(self):
        self.ready.set()
        return self.sock.fileno()

    def recv(self, size):
        return self.sock.recv(size)

    def shutdown(self, how):
        self.sock.shutdown(how)

    def close(self):
        self.sock.close()


@pytest.mark.parametrize("partial", [b"", b'{"type":"party_memory"'], ids=["idle", "partial"])
def test_stop_wakes_blocked_tcp_reader_and_closes_socket(partial, tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    receiver, sender = socket.socketpair()
    client = Client(receiver)
    worker = server._launch_owned_thread(server._handle_client, args=(client, ("local", 1)), name="bizhawk-ram-client")
    try:
        assert client.ready.wait(2)
        if partial:
            sender.sendall(partial)
        server.stop()
        assert not worker.is_alive()
        assert receiver.fileno() == -1
        assert not server._clients and not server._client_threads
        assert server._active_client is None
        assert server.latest_party_payload() is None
    finally:
        sender.close()
        server.stop()


def test_replacement_wakes_old_reader_and_stop_closes_all_clients(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    senders = []
    clients, workers = [], []
    try:
        for index in range(2):
            receiver, sender = socket.socketpair()
            senders.append(sender)
            client = Client(receiver)
            clients.append(client)
            workers.append(server._launch_owned_thread(server._handle_client, args=(client, ("local", index)), name="bizhawk-ram-client"))
            assert client.ready.wait(2)
        workers[0].join(2)
        assert not workers[0].is_alive()
        assert server._active_client is clients[1]
        server.stop()
        assert all(not worker.is_alive() for worker in workers)
        assert all(client.sock.fileno() == -1 for client in clients)
    finally:
        for sender in senders:
            sender.close()
        server.stop()


def test_stop_during_file_callback_finishes_current_step_only(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    entered = threading.Event()
    later = []

    def poll(path):
        entered.set()
        server._stop_event.wait()
        server._record_line('{"type":"heartbeat"}', "file")

    server._poll_log_file = poll
    server._poll_coordinate_file = lambda: later.append(True)
    worker = server._launch_owned_thread(server._poll_fallback_file, name="bizhawk-ram-file-poller")
    assert entered.wait(2)
    server.stop()
    assert not worker.is_alive()
    assert not later
    assert server._heartbeat is None


def test_shutdown_analysis_timeout_blocks_restart_until_quiescent(monkeypatch, tmp_path):
    monkeypatch.setattr("pokemon_ev_tracker.transport.bizhawk_server.SHUTDOWN_JOIN_TIMEOUT_SECONDS", 0.02)
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    scan = PCStorageDiscoveryAccumulator("analysis", "broad", 512, 0x02000000)
    server._pc_discovery_scan = scan
    entered, release = threading.Event(), threading.Event()

    def finalize():
        entered.set()
        assert release.wait(5)
        return {"pc_storage_discovery_summary": {}}

    scan.finalize = finalize
    server._begin_pc_discovery_analysis_locked(scan, "tcp", 42)
    assert entered.wait(2)
    workers = tuple(server._owned_threads)
    try:
        before = time.monotonic()
        server.stop()
        assert time.monotonic() - before < 0.5
        assert len(server._owned_threads) == 1
        with pytest.raises(RuntimeError, match="still stopping"):
            server.start()
        server._store_pc_discovery_result({"old": True}, "tcp")
        assert server._pc_storage_discovery_payload is None
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
    assert not server._owned_threads
    ready = loops(server)
    start(server, ready)
    assert server.latest_pc_storage_discovery_payload() is None
    assert server._pc_discovery_scan is None
    server.stop()


def test_duplicate_analysis_launches_are_coalesced(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    scan = PCStorageDiscoveryAccumulator("analysis", "broad", 512, 0x02000000)
    entered, release = threading.Event(), threading.Event()
    server._finalize_pc_discovery = lambda *args: entered.set() or release.wait(5)
    server._begin_pc_discovery_analysis_locked(scan, "tcp", 42)
    assert entered.wait(2)
    for _ in range(10):
        server._begin_pc_discovery_analysis_locked(scan, "tcp", 42)
    assert len(server._owned_threads) == 1
    release.set()
    server.stop()
    assert not server._analysis_in_flight


def test_worker_exception_logs_and_releases_ownership(caplog, tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")

    def fail():
        raise ValueError("controlled failure")

    worker = server._launch_owned_thread(fail, name="test-worker")
    worker.join(2)
    assert not worker.is_alive()
    assert not server._owned_threads
    assert "controlled failure" in caplog.text
    server.stop()


def test_shutdown_promotes_queued_walk_stop_without_replaying_start(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    server.command_file.write_text("WALK|1|START|horizontal\n")
    server._command_file_queue.extend(("PC_SCAN_NOW\n", "WALK|2|STOP\n"))
    server.stop()
    assert server.command_file.read_text() == "WALK|2|STOP\n"
    assert not server._command_file_queue
    assert not server._write_command_file("WALK|3|START|horizontal\n")
    assert server._write_command_file("WALK|4|STOP\n")
    assert server.command_file.read_text() == "WALK|4|STOP\n"


def test_start_stop_race_during_reset_creates_no_threads(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    server._has_started = True
    entered, release = threading.Event(), threading.Event()
    server._reset_runtime_state = lambda: entered.set() or release.wait(5)
    starter = threading.Thread(target=server.start)
    starter.start()
    try:
        assert entered.wait(2)
        server.stop()
    finally:
        release.set()
        starter.join(2)
    assert not starter.is_alive()
    assert not server._owned_threads and not server._running


def test_failed_listener_does_not_duplicate_live_file_poller(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    file_ready = threading.Event()

    def fail():
        raise OSError("controlled bind failure")

    server._serve_tcp = fail
    server._poll_fallback_file = lambda: file_ready.set() or server._stop_event.wait()
    server.start()
    assert file_ready.wait(2)
    server._tcp_thread.join(2)
    poller = server._file_thread
    server.start()
    assert server._file_thread is poller
    server.stop()
    assert not poller.is_alive()


def test_concurrent_start_calls_launch_only_one_pair(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    ready = loops(server)
    gate = threading.Barrier(4)

    def call():
        gate.wait(2)
        server.start()

    callers = [threading.Thread(target=call) for _ in range(3)]
    for caller in callers:
        caller.start()
    gate.wait(2)
    for caller in callers:
        caller.join(2)
    assert all(event.wait(2) for event in ready)
    assert len(server._owned_threads) == 2
    server.stop()


def test_receiver_preserves_fragmented_utf8_order_and_final_eof_line(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    receiver, sender = socket.socketpair()
    worker = server._launch_owned_thread(server._handle_client, args=(receiver, ("local", 1)), name="bizhawk-ram-client")
    events = []
    original = server._record_line

    def record(line, source):
        events.append(json.loads(line))
        original(line, source)

    server._record_line = record
    try:
        first = json.dumps({"type": "party_memory", "nickname": "Pok\u00e9mon"}, ensure_ascii=False).encode()
        split = first.index(b"\xc3") + 1
        sender.sendall(first[:split])
        sender.sendall(first[split:] + b'\r\n{"type":"heartbeat"}\n{"type":"party_memory","frame":2}')
        sender.shutdown(socket.SHUT_WR)
        worker.join(2)
        assert not worker.is_alive()
        assert [event["type"] for event in events] == ["party_memory", "heartbeat", "party_memory"]
        assert events[0]["nickname"] == "Pok\u00e9mon"
        assert server.latest_party_payload().payload["frame"] == 2
    finally:
        sender.close()
        server.stop()


def test_real_listener_shutdown_and_rebind(monkeypatch, tmp_path):
    native_socket = socket.socket
    ready = threading.Event()

    class ListeningSocket:
        def __init__(self, *args, **kwargs):
            self.sock = native_socket(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.sock, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.sock.close()

        def listen(self, backlog):
            self.sock.listen(backlog)
            ready.set()

    monkeypatch.setattr("pokemon_ev_tracker.transport.bizhawk_server.socket.socket", ListeningSocket)
    server = BizHawkDebugServer(port=0, fallback_file=tmp_path / "fallback.jsonl")
    for _ in range(2):
        ready.clear()
        server.start()
        assert ready.wait(2)
        listener = server._server_socket
        port = listener.getsockname()[1]
        server.stop()
        assert listener.fileno() == -1
        assert server._server_socket is None
        assert not server._owned_threads
        server.port = port


def test_stop_preservation_failure_is_logged_and_queue_retained(monkeypatch, caplog, tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    server._command_file_queue.append("WALK|2|STOP\n")
    monkeypatch.setattr(server, "_publish_command_file_locked", lambda command: False)
    server.stop()
    assert list(server._command_file_queue) == ["WALK|2|STOP\n"]
    assert "Could not preserve queued WALK STOP" in caplog.text


def test_cancelled_analysis_cannot_publish(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    scan = PCStorageDiscoveryAccumulator("cancel", "broad", 512, 0x02000000)
    server._pc_discovery_scan = scan
    entered, release = threading.Event(), threading.Event()

    def finalize():
        entered.set()
        assert release.wait(5)
        return {"pc_storage_discovery_summary": {}}

    scan.finalize = finalize
    server._begin_pc_discovery_analysis_locked(scan, "tcp", 42)
    assert entered.wait(2)
    worker = next(iter(server._owned_threads))
    server._record_pc_discovery_event({"type": "pc_discovery_cancelled", "scan_id": "cancel"}, "tcp")
    cancelled = server.latest_pc_storage_discovery_payload()
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert server.latest_pc_storage_discovery_payload() is cancelled
    assert not server._analysis_in_flight
    server.stop()


def test_stop_closes_socket_before_waiting_for_send_lock(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    entered, closed = threading.Event(), threading.Event()

    class SendingSocket:
        def sendall(self, data):
            entered.set()
            assert closed.wait(2)
            raise OSError("socket closed")

        def shutdown(self, how):
            closed.set()

        def close(self):
            closed.set()

    server._active_client = SendingSocket()
    worker = server._launch_owned_thread(lambda: server._send_tcp_command("PC_SCAN_NOW\n"), name="send-test")
    assert entered.wait(2)
    server.stop()
    assert not worker.is_alive()
    assert server._active_client is None


def test_shutdown_gate_blocks_authoritative_publications(tmp_path):
    server = BizHawkDebugServer(fallback_file=tmp_path / "fallback.jsonl")
    server.stop()
    server._record_line('{"type":"party_memory","friendship_walk_ack_sequence":5}', "tcp")
    server._store_pc_discovery_result({"result": "old"}, "file")
    server._store_pc_sram_search_result({"result": "old"}, "file")
    server._store_pc_save_offset_test_result({"result": "old"}, "file")
    assert server._party_payload is None
    assert server._pc_storage_discovery_payload is None
    assert server._pc_sram_search_payload is None
    assert server._pc_save_offset_test_payload is None
