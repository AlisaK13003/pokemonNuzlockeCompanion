"""Time, content and session boundaries for party projection reuse."""
import gc
import weakref
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_gen4_party_ram import _party_record
from test_party_stats_view import _party_payload, make_window

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.data_sources.bizhawk import BizHawkRamDataSource
from pokemon_ev_tracker.games.platinum.decoder import decode_party
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.games.platinum.profile import PLATINUM_PROFILE
from pokemon_ev_tracker.games.provider import UnsupportedGameProvider
from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer, BizHawkHeartbeat

snapshot_window = make_window


def record(pid=1, species=387, **kwargs):
    return _party_record(pid, species, kwargs.pop("evs", (0, 0, 0, 0, 0, 0)), **kwargs)


def message(*records, received_at=100.0, **fields):
    return BizHawkHeartbeat(received_at, {
        "raw_party_hex": _party_payload(records or (record(),)).hex(),
        "party_address": "0x0227E20C", "party_count": len(records) or 1,
        "run_id": "lua-a", "core": "NDS", "domain": "Main RAM",
        "pointer_value": "0x02270000", "frame": 10, **fields}, "tcp")


def corrupt(sample):
    raw = bytearray.fromhex(sample.payload["raw_party_hex"])
    raw[12] ^= 1
    return replace(sample, payload={**sample.payload, "raw_party_hex": raw.hex()})


@pytest.fixture
def live(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("pokemon_ev_tracker.data_sources.bizhawk.time.monotonic", lambda: clock[0])
    server = BizHawkDebugServer(fallback_file=Path("unused"))
    server.connected = True
    server.sample = message()
    server.latest_party_payload = lambda: server.sample
    server.latest_heartbeat = lambda: server.sample
    server.is_connected = lambda: server.connected
    # UI shutdown sends STOP; keep this fixture completely outside real IPC.
    server.send_friendship_walk_command = lambda *_args, **_kwargs: None
    decoder = Mock(wraps=decode_party)
    source = BizHawkRamDataSource(server, replace(PLATINUM_PROFILE, party_decoder=decoder))
    return source, server, clock, decoder


def test_identical_contents_reuse_records_but_not_snapshot(live):
    source, server, _, decoder = live
    first = source.snapshot()
    server.sample = replace(server.sample, payload=dict(server.sample.payload))
    second = source.snapshot()
    assert first is not second and first.details is not second.details
    assert first.details["party_state"] is second.details["party_state"]
    assert first.details["display_party_state"] is second.details["display_party_state"]
    assert decoder.call_count == 1


@pytest.mark.parametrize("changes", [
    {"species": 388}, {"attack_stat": 55}, {"evs": (0, 12, 0, 0, 0, 0)},
    {"current_hp": 0}, {"friendship": 200}, {"moves": (33, 0, 0, 0)},
    {"move_pps": (12, 0, 0, 0)}, {"move_pp_ups": (1, 0, 0, 0)}, {"level": 12},
], ids=["evolution", "stat", "ev", "hp", "friendship", "move", "pp", "pp-up", "level"])
def test_same_pid_changed_bytes_are_redecoded(live, changes):
    source, server, _, decoder = live
    previous = source.snapshot().details["party_state"]
    changed = message(record(**changes))
    # Even callers editing the original dict in place cannot hide changed input.
    server.sample.payload.update(changed.payload)
    current = source.snapshot().details["party_state"]
    assert current is not previous
    assert current == decode_party(bytes.fromhex(changed.payload["raw_party_hex"]), 0x0227E20C)
    assert decoder.call_count == 2


def test_reorder_replacement_and_candidate_inputs(live):
    source, server, _, decoder = live
    server.sample = message(record(1), record(2, 390))
    first = source.snapshot().details["party_state"]
    server.sample = message(record(2, 390), record(1))
    reordered = source.snapshot().details["party_state"]
    assert [p.stable_id for p in reordered.pokemon] == [p.stable_id for p in reversed(first.pokemon)]
    assert [p.slot for p in reordered.pokemon] == [1, 2]
    server.sample = message(record(3, 393), record(1))
    assert source.snapshot().details["party_state"].pokemon[0].decoded.diagnostics.pid == 3
    candidate = {"raw_party_hex": message(record(4, 443)).payload["raw_party_hex"],
                 "address": "0x02280000", "party_count": 1}
    server.sample.payload["party_candidates"] = [candidate]
    assert source.snapshot().details["party_state"].pokemon[0].decoded.diagnostics.pid == 4
    candidate["raw_party_hex"] = message(record(5, 179)).payload["raw_party_hex"]
    assert source.snapshot().details["party_state"].pokemon[0].decoded.diagnostics.pid == 5
    assert decoder.call_count == 7


def test_invalid_current_display_fallback_expiry_and_recovery(live):
    source, server, clock, decoder = live
    valid = server.sample
    original = source.snapshot().details["party_state"]
    clock[0] = 101
    server.sample = corrupt(replace(valid, received_at=101))
    invalid = source.snapshot()
    assert not invalid.details["party_state"].pokemon[0].checksum_valid
    fallback = invalid.details["display_party_state"]
    assert fallback.pokemon[0].sample_stale
    assert fallback.pokemon[0].decoded is original.pokemon[0].decoded
    assert source.snapshot().details["display_party_state"] is fallback
    # Fresh invalid reads never renew the last-good lifetime.
    clock[0] = 106
    server.sample = replace(server.sample, received_at=106)
    expired = source.snapshot()
    assert expired.details["party_payload_fresh"]
    assert expired.details["display_party_state"].pokemon == ()
    clock[0] = 112
    stale = source.snapshot()
    assert not stale.details["party_payload_fresh"]
    assert stale.details["ram_age_seconds"] == 6
    assert stale.details["party_state"] is invalid.details["party_state"]
    server.sample = replace(valid, received_at=112)
    recovered = source.snapshot().details["display_party_state"]
    assert recovered.pokemon[0].checksum_valid and not recovered.pokemon[0].sample_stale
    assert recovered.live_read_warning is None
    assert decoder.call_count == 3


def test_invalid_battle_tail_cannot_extend_sane_stats_lifetime(live):
    source, server, clock, _ = live
    source.snapshot()
    clock[0] = 102
    server.sample = message(record(current_hp=65535), received_at=102)
    invalid = source.snapshot()
    assert invalid.details["party_state"].pokemon[0].current_hp == 65535
    assert not invalid.details["party_state"].pokemon[0].decoded.diagnostics.battle_stats_valid
    display = invalid.details["display_party_state"]
    assert display.pokemon[0].current_hp == 30
    clock[0] = 106
    expired = source.snapshot().details["display_party_state"]
    assert expired.pokemon[0].current_hp is None
    assert expired.pokemon[0].battle_stats_stale


def test_new_received_timestamp_renews_valid_sample_without_redecoding(live):
    source, server, clock, decoder = live
    initial = source.snapshot().details["party_state"]
    clock[0] = 104
    server.sample = replace(server.sample, received_at=104)
    assert source.snapshot().details["party_state"] is initial
    assert decoder.call_count == 1
    clock[0] = 106
    server.sample = corrupt(replace(server.sample, received_at=106))
    assert source.snapshot().details["display_party_state"].pokemon[0].sample_stale
    clock[0] = 110
    assert source.snapshot().details["display_party_state"].pokemon == ()


def test_invalid_reordered_slots_use_matching_pid_not_slot(live):
    source, server, _, _ = live
    server.sample = message(record(1, 387), record(2, 390))
    source.snapshot()
    raw = bytearray.fromhex(message(record(2, 390), record(1, 387)).payload["raw_party_hex"])
    raw[12] ^= 1
    server.sample = message(raw_party_hex=raw.hex())
    result = source.snapshot()
    raw_members = result.details["party_state"].pokemon
    shown = result.details["display_party_state"].pokemon
    assert not raw_members[0].checksum_valid
    assert [(p.species_id, p.slot) for p in shown] == [(390, 1), (387, 2)]
    assert shown[0].sample_stale and not shown[1].sample_stale


@pytest.mark.parametrize("field,value", [
    ("run_id", "lua-b"), ("lua_run_id", "lua-b"), ("rom_session_identity", "rom-b"),
    ("core", "new"), ("domain", "new"), ("pointer_value", "0x02300000"), ("frame", 1),
], ids=["lua-restart", "lua-session", "rom-session", "core", "domain", "pointer", "load-state"])
def test_session_change_or_frame_rollback_clears_fallback(live, field, value):
    source, server, _, decoder = live
    initial = source.snapshot().details["party_state"]
    server.sample = replace(server.sample, payload={**server.sample.payload, field: value})
    new = source.snapshot().details["party_state"]
    assert initial is not new and decoder.call_count == 2
    server.sample = corrupt(server.sample)
    server.sample.payload[field] = "another" if field != "frame" else 0
    assert source.snapshot().details["display_party_state"].pokemon == ()


def test_disconnect_missing_payload_and_reconnect(live):
    source, server, _, decoder = live
    valid = server.sample
    source.snapshot()
    server.connected = False
    server.sample = corrupt(valid)
    assert not source.snapshot().connected
    server.connected = True
    assert source.snapshot().details["display_party_state"].pokemon == ()
    server.sample = None
    assert source.snapshot().details["party_state"] is None
    server.sample = valid
    assert source.snapshot().details["display_party_state"].pokemon[0].checksum_valid
    assert decoder.call_count == 3


def test_profile_replacement_invalidates_and_unsupported_provider_stays_unavailable(live):
    source, _, _, decoder = live
    first = source.snapshot()
    other_decoder = Mock(wraps=decode_party)
    source.profile = replace(PLATINUM_PROFILE, game_id="test-profile", party_decoder=other_decoder)
    assert source.snapshot().details["party_state"] is not first.details["party_state"]
    assert decoder.call_count == other_decoder.call_count == 1
    provider = UnsupportedGameProvider("uninstalled")
    assert not provider.capabilities.live_party
    assert provider.make_data_source().snapshot().details == {}


def test_transport_change_invalidates_identical_decode_and_display(live):
    source, server, _, decoder = live
    first = source.snapshot().details["party_state"]
    server.sample = replace(server.sample, source="file")
    assert source.snapshot().details["party_state"] is not first
    assert decoder.call_count == 2


def test_addresses_and_candidate_counts_are_decoder_inputs(live):
    source, server, _, decoder = live
    source.snapshot()
    server.sample.payload["party_address"] = "0x02280000"
    changed = source.snapshot().details["party_state"]
    assert changed.pokemon[0].decoded.diagnostics.address == 0x02280004
    assert decoder.call_count == 2
    candidate = {"raw_party_hex": "00", "address": "0x02300000", "party_count": 1}
    server.sample.payload["party_candidates"] = [candidate]
    assert source.snapshot().details["party_state"].candidate_count == 1
    candidate["party_count"] = 2
    source.snapshot()
    assert decoder.call_count == 6


def test_previous_decode_results_are_not_retained(live):
    source, server, _, _ = live
    first = source.snapshot().details["party_state"]
    previous = weakref.ref(first)
    del first
    server.sample = message(record(2))
    source.snapshot()
    gc.collect()
    assert previous() is None
    assert len(source._last_good_party_by_pid) == 1


def test_malformed_and_empty_inputs_dont_reuse_valid_state(live):
    source, server, _, _ = live
    source.snapshot()
    for raw in ("garbage", "00", (7).to_bytes(4, "little").hex(), ""):
        server.sample = message(raw_party_hex=raw, party_candidates=None)
        current = source.snapshot().details["party_state"]
        assert current is None or not current.pokemon


def test_last_good_history_is_bounded_with_mixed_invalid_parties(live):
    source, server, _, _ = live
    bad = bytearray(record(999))
    bad[8] ^= 1
    for pid in range(1, 40):
        server.sample = message(record(pid), bytes(bad))
        source.snapshot()
        assert len(source._last_good_party_by_pid) <= 6
        assert len(source._last_good_party_times) <= 6


def test_ui_observers_keep_polling_raw_inputs_and_mutable_run_state(live, snapshot_window):
    source, server, clock, _ = live
    window = snapshot_window(AppSettings(prompt_for_nuzlocke_run=False, auto_pc_rediscovery=False))
    window.ram_data_source = source
    store = window.nuzlocke_view.store
    run = store.create_run("reuse", PLATINUM_NUZLOCKE_PROFILE)
    run.starter_observation_complete = True
    hp = Mock(wraps=window.party_lifecycle._hp.observe)
    wipe = Mock(wraps=window.party_lifecycle._wipe.observe)
    acquisitions = Mock(wraps=window.acquisitions._party.observe)
    window.party_lifecycle._hp.observe = hp
    window.party_lifecycle._wipe.observe = wipe
    window.acquisitions._party.observe = acquisitions
    try:
        window._refresh_ram_backend_debug()
        server.sample.payload["friendship_walk_ack_sequence"] = 9
        window._refresh_ram_backend_debug()
        assert hp.call_count == acquisitions.call_count == 2
        assert window._walk_last_ack_sequence == 9
        assert hp.call_args.kwargs["valid_snapshot"]
        server.sample = corrupt(message(record(current_hp=1)))
        window._refresh_ram_backend_debug()
        assert not hp.call_args.kwargs["valid_snapshot"]
        assert not acquisitions.call_args.kwargs["valid_snapshot"]
        # A display fallback must never enter the raw HP observer.
        assert hp.call_args.args[0][0].current_hp == 1
        clock[0] = 107
        window._refresh_ram_backend_debug()
        assert not hp.call_args.kwargs["valid_snapshot"]
        other = store.create_run("other", PLATINUM_NUZLOCKE_PROFILE)
        server.sample = message(received_at=107)
        window._refresh_ram_backend_debug()
        assert hp.call_args.kwargs["run_id"] == other.run_id
        assert hp.call_count == acquisitions.call_count == 5
        assert wipe.call_count == 5
    finally:
        window.close()
