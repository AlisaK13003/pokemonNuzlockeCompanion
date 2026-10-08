"""Characterize the raw-party lifecycle workflow before extracting its owner."""

from dataclasses import replace

import pytest
from test_party_snapshot_reuse import live as live_fixture
from test_party_snapshot_reuse import message, record
from test_party_snapshot_reuse import snapshot_window as window_fixture

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE

live = live_fixture
snapshot_window = window_fixture


@pytest.mark.parametrize("boundary", ["normal", "invalid", "stale", "disconnect", "restart", "rollback"])
def test_lifecycle_poll_transition_boundaries(live, snapshot_window, monkeypatch, boundary):
    source, server, clock, _ = live
    window = snapshot_window(AppSettings(prompt_for_nuzlocke_run=False, auto_pc_rediscovery=False))
    window.ram_data_source = source
    run = window.nuzlocke_view.store.create_run("lifecycle", PLATINUM_NUZLOCKE_PROFILE)
    run.starter_observation_complete = True
    events = []
    monkeypatch.setattr(window.nuzlocke_view, "receive_ram_death_candidates",
                        lambda run_id, deaths: events.append(("death", run_id, deaths[0].stable_id)))
    monkeypatch.setattr(window, "_handle_party_wipe",
                        lambda wipe: events.append(("wipe", wipe.run_id, wipe.members[0].stable_id)))
    try:
        server.sample = message(record(current_hp=20), frame=10)
        window._refresh_ram_backend_debug()
        server.sample = message(record(current_hp=0), frame=11)
        if boundary == "invalid":
            raw = bytearray.fromhex(server.sample.payload["raw_party_hex"])
            raw[12] ^= 1
            server.sample = replace(server.sample, payload={**server.sample.payload, "raw_party_hex": raw.hex()})
        elif boundary == "stale":
            clock[0] = 107
        elif boundary == "disconnect":
            server.connected = False
        elif boundary == "restart":
            server.sample = replace(server.sample, payload={**server.sample.payload, "run_id": "lua-b"})
        elif boundary == "rollback":
            server.sample = replace(server.sample, payload={**server.sample.payload, "frame": 1})
        window._refresh_ram_backend_debug()
        assert [event[0] for event in events] == (["death", "wipe"] if boundary == "normal" else [])
        assert all(event[1] == run.run_id for event in events)
        # Repeated zero HP, and recovery after an invalid sample, establish no new edge.
        server.connected = True
        server.sample = message(record(current_hp=0), received_at=clock[0], frame=12)
        window._refresh_ram_backend_debug()
        assert len(events) == (2 if boundary == "normal" else 0)
    finally:
        window.close()


def test_no_run_prompt_requires_progress_and_is_session_suppressed(live, snapshot_window, monkeypatch):
    source, server, _, _ = live
    window = snapshot_window(AppSettings(prompt_for_nuzlocke_run=True, auto_pc_rediscovery=False))
    window.ram_data_source = source
    prompts = []
    monkeypatch.setattr(window, "_prompt_create_run_without_active", lambda: prompts.append("prompt"))
    try:
        for frame in (10, 10, 11, 12):
            server.sample = message(record(current_hp=20), frame=frame)
            window._refresh_ram_backend_debug()
        assert prompts == []
        for frame in (13, 14, 15):
            server.sample = message(record(current_hp=20), frame=frame)
            window._refresh_ram_backend_debug()
        assert prompts == ["prompt"]
    finally:
        window.close()
