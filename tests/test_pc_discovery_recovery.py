"""Recovery policy tests without Qt, sockets, a server or game decoding."""

from types import SimpleNamespace

import pytest

from pokemon_ev_tracker.core.pc_discovery_recovery import PCDiscoveryRecovery, RecoveryKind


def scan(**changes):
    values = {
        "scan_id": "scan", "source": "file", "last_chunk_received_at": 0.0,
        "end_received": False, "expected_chunk_count": None, "expected_byte_count": None,
        "total_bytes": 4, "data": bytearray(), "received_chunk_indexes": set(),
        "missing_chunk_indexes": set(), "first_missing_chunk_index": lambda: None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_request_commits_after_delivery_and_receipt_retains_attempt_history(monkeypatch):
    recovery = PCDiscoveryRecovery()
    monkeypatch.setattr("pokemon_ev_tracker.core.pc_discovery_recovery.time.monotonic", lambda: 10.0)

    def send(scan_id, index, source):
        assert (scan_id, index, source) == ("scan", 2, "tcp")
        assert recovery.attempts == {}
        assert recovery.in_flight == set()
        return "file"

    assert recovery.request_retry("scan", 2, "tcp", send)
    assert recovery.attempts == {2: 1}
    assert recovery.requested_at == {2: 10}
    assert recovery.request_retry("scan", 2, "tcp", lambda *args: pytest.fail("duplicate send"))
    recovery.received(2)
    assert recovery.attempts == {2: 1}
    assert not recovery.in_flight and not recovery.requested_at


@pytest.mark.parametrize("attempts", [0, 1, 2, 3, 4])
def test_failed_send_or_limit_does_not_spend_attempt(attempts):
    recovery = PCDiscoveryRecovery(attempts={0: attempts})
    sent = []
    assert not recovery.request_retry("scan", 0, "tcp", lambda *args: sent.append(args))
    assert len(sent) == (attempts < 3)
    assert recovery.attempts == {0: attempts}
    assert not recovery.in_flight and not recovery.requested_at


@pytest.mark.parametrize("now,due", [(1.99, False), (2.0, True), (2.01, True)])
def test_timeout_boundary(now, due):
    recovery = PCDiscoveryRecovery(attempts={0: 1}, in_flight={0}, requested_at={0: 0})
    sent = []
    events = list(recovery.expired_retries(scan(), now, lambda *args: sent.append(args) or "tcp"))
    assert len(events) == due
    assert len(sent) == due
    if due:
        assert events[0].kind is RecoveryKind.RETRYING
        assert recovery.attempts == {0: 2}


def test_missing_timestamp_is_unknown_not_expired():
    recovery = PCDiscoveryRecovery(in_flight={2})
    assert list(recovery.expired_retries(scan(), 100, lambda *args: pytest.fail("send"))) == []


@pytest.mark.parametrize("attempts,reason", [(2, "retry request could not be sent"), (3, "retransmission timed out")])
def test_expired_failure_stops_before_later_retry(attempts, reason):
    recovery = PCDiscoveryRecovery(
        attempts={0: attempts, 1: 1}, in_flight={0, 1}, requested_at={0: 0, 1: 0},
    )
    events = list(recovery.expired_retries(scan(), 2, lambda *args: None))
    assert len(events) == 1
    assert (events[0].kind, events[0].chunk_index, events[0].reason) == (
        RecoveryKind.MISSING_FAILED, 0, reason,
    )
    assert recovery.in_flight == {1}
    assert recovery.requested_at == {1: 0}


@pytest.mark.parametrize("end,in_flight,now,missing,kind", [
    (False, False, 1.99, None, None),
    (False, False, 2.0, None, RecoveryKind.STALLED),
    (True, False, 2.0, None, None),
    (False, True, 2.0, 0, None),
    (False, False, 2.0, 0, RecoveryKind.RETRYING),
    (True, False, 2.0, 3, RecoveryKind.RETRYING),
])
def test_stall_only_retries_proven_missing_chunks(end, in_flight, now, missing, kind):
    recovery = PCDiscoveryRecovery(in_flight={9} if in_flight else set())
    sent = []
    event = recovery.stalled(
        scan(end_received=end, first_missing_chunk_index=lambda: missing), now,
        lambda *args: sent.append(args) or "file",
    )
    assert (event.kind if event else None) is kind
    assert len(sent) == (kind is RecoveryKind.RETRYING)
    if sent:
        assert sent == [("scan", missing, "file")]


@pytest.mark.parametrize("end,count,indexes,size,expected,kind", [
    (False, 1, {0}, 4, 4, None),
    (True, None, {0}, 4, 4, None),
    (True, 1, {0}, 4, 4, RecoveryKind.READY),
    (True, 1, {0}, 3, 4, RecoveryKind.COUNTS_FAILED),
    (True, 1, {0}, 4, 3, RecoveryKind.COUNTS_FAILED),
    (True, 1, {0, 1}, 4, 4, RecoveryKind.COUNTS_FAILED),
    (True, 3, {1}, 1, 4, RecoveryKind.RETRYING),
])
def test_completion_gate(end, count, indexes, size, expected, kind):
    recovery = PCDiscoveryRecovery()
    observation = scan(
        end_received=end, expected_chunk_count=count, received_chunk_indexes=indexes,
        data=bytearray(size), expected_byte_count=expected,
    )
    sent = []
    event = recovery.completion(observation, "tcp", lambda *args: sent.append(args) or "tcp")
    assert (event.kind if event else None) is kind
    if kind is RecoveryKind.RETRYING:
        assert event.chunk_index == 0
        assert observation.missing_chunk_indexes == {0, 2}
        assert sent == [("scan", 0, "tcp")]
    else:
        assert sent == []


def test_replacement_recovery_is_isolated_and_has_no_ram_retention():
    old, new = PCDiscoveryRecovery(), PCDiscoveryRecovery()
    assert old.request_retry("old", 0, "file", lambda *args: "file")
    assert not new.attempts and not new.in_flight and not new.requested_at
    assert set(vars(old)) == {"attempts", "in_flight", "requested_at", "clock"}
