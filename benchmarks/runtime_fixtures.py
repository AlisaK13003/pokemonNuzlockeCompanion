"""Opt-in deterministic runtime measurements; no emulator or real user data.

Run from the repository root: python benchmarks/runtime_fixtures.py --output path.json
Requires the development environment and test fixtures. Timing assertions do not
belong in pytest. Compare medians on the same host with the same arguments.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import logging
import os
import platform
import statistics
import sys
import time
import tracemalloc
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "tests"))

from PySide6.QtWidgets import QApplication
from test_party_stats_view import _party_payload, _party_record
from test_pc_storage_discovery import _boxed_record
from test_training_integration import _party, _Source

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.config.training_preferences import EVTrainingPreferenceStore
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.core.save_backups import SaveBackupService
from pokemon_ev_tracker.data_sources.bizhawk import BizHawkRamDataSource
from pokemon_ev_tracker.games.platinum.pc_discovery import (
    MAIN_RAM_BASE,
    PCStorageDiscoveryAccumulator,
)
from pokemon_ev_tracker.games.platinum.profile import PLATINUM_PROFILE
from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer, BizHawkHeartbeat
from pokemon_ev_tracker.ui.main_window import MainWindow


def measure(callback, *, iterations=30, rounds=5):
    for _ in range(3):
        callback()
    timings = []
    for _ in range(rounds):
        started = time.perf_counter()
        for _ in range(iterations):
            callback()
        timings.append((time.perf_counter() - started) * 1000 / iterations)
    return {"median_ms": round(statistics.median(timings), 4),
            "round_ms": [round(value, 4) for value in timings],
            "iterations": iterations, "rounds": rounds}


def measure_party_snapshots(server, payload, window=None):
    """Same fixture and clock for both revisions; no sockets or source writes."""
    now = time.monotonic()
    valid = BizHawkHeartbeat(now, payload, "tcp")
    changed_raw = _party_payload(tuple(_party_record(i + 1, species, 46, attack=28)
                                     for i, species in enumerate((391, 388, 397, 404, 418, 93))))
    changed = BizHawkHeartbeat(now, {**payload, "raw_party_hex": changed_raw.hex()}, "tcp")
    invalid_raw = bytearray.fromhex(payload["raw_party_hex"])
    invalid_raw[12] ^= 1
    invalid = BizHawkHeartbeat(now, {**payload, "raw_party_hex": invalid_raw.hex()}, "tcp")
    source = BizHawkRamDataSource(server=server)
    def snapshot(message):
        server._party_payload = message
        server._heartbeat = message
        return source.snapshot()
    with patch("pokemon_ev_tracker.data_sources.bizhawk.time.monotonic", new=lambda: now), \
            patch.object(server, "is_connected", new=lambda: True):
        result = {"unchanged_snapshot": measure(lambda: snapshot(valid)),
                  "changed_pair": measure(lambda: (snapshot(valid), snapshot(changed))),
                  "invalid_valid_pair": measure(lambda: (snapshot(invalid), snapshot(valid)))}
        snapshot(valid)
        profile = cProfile.Profile()
        profile.enable()
        for _ in range(30):
            snapshot(valid)
        profile.disable()
        result["calls_30_unchanged"] = {
            name: sum(entry.callcount for entry in profile.getstats()
                      if hasattr(entry.code, "co_name") and entry.code.co_name == name)
            for name in ("_decode_raw_party", "decode_party_pokemon", "replace", "snapshot")}
        tracemalloc.start()
        for _ in range(300):
            snapshot(valid)
        retained, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        result["unchanged_traced_bytes"] = {"retained": retained, "peak": peak}
        result["last_good_entries"] = len(source._last_good_party_by_pid)
        for label, pair in (("changed_pairs", (valid, changed)),
                            ("invalid_valid_pairs", (invalid, valid))):
            profile = cProfile.Profile()
            profile.enable()
            for _ in range(30):
                for sample in pair:
                    snapshot(sample)
            profile.disable()
            result[f"decode_calls_30_{label}"] = sum(
                entry.callcount for entry in profile.getstats()
                if hasattr(entry.code, "co_name") and entry.code.co_name == "_decode_raw_party")
        tracemalloc.start()
        cold_source = BizHawkRamDataSource(server=server)
        cold_source.snapshot()
        retained, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        result["cold_snapshot_traced_bytes"] = {"retained": retained, "peak": peak}
        if window is not None:
            previous_source = window.ram_data_source
            window.ram_data_source = source
            try:
                result["full_ui_poll_unchanged"] = measure(window._refresh_ram_backend_debug)
            finally:
                window.ram_data_source = previous_source
    return result


def measure_ui_orchestration(window, source):
    """Active single/double battle calculations and the diagnostic text path."""
    from pokemon_ev_tracker.core.ev_training import EVTrainingPreference
    source.opponents(66, 74)
    double = source.battlers
    source.opponents(63)
    single = source.battlers
    source.battlers = double
    for index, pokemon in enumerate(source.party.pokemon):
        window.training_preference_store.set(pokemon.decoded.diagnostics.pid,
            EVTrainingPreference(frozenset({"attack" if index % 2 else "defense"})))
    window._refresh_ram_backend_debug()
    result = {"active_unchanged_poll": measure(window._refresh_ram_backend_debug)}
    profile = cProfile.Profile()
    profile.enable()
    for _ in range(30):
        window._refresh_ram_backend_debug()
    profile.disable()
    result["calls_30_active_polls"] = {
        name: sum(entry.callcount for entry in profile.getstats()
                  if hasattr(entry.code, "co_name") and entry.code.co_name == name)
        for name in ("_refresh_training_recommendations", "recommend_battle_evs",
                     "_refresh_ram_party_debug")}
    def changed_pair():
        source.battlers = single
        window._refresh_ram_backend_debug()
        source.battlers = double
        window._refresh_ram_backend_debug()
    result["changed_opponent_pair"] = measure(changed_pair)
    class TextSink:
        def setText(self, text):
            pass
    class DiagnosticHarness:
        _refresh_ram_party_debug = MainWindow._refresh_ram_party_debug
        _friendship_walk_transport_debug_lines = MainWindow._friendship_walk_transport_debug_lines
        provider = window.provider
        ram_party_summary_label = TextSink()
        def _set_ram_party_debug_text(self, text):
            self.text = text
    harness = DiagnosticHarness()
    empty = SimpleNamespace(party_count=0, party_count_valid=True, error=None,
                            candidate_count=0, pokemon=())
    sample = SimpleNamespace(source="file", payload={"pointer_value": "0x022711C8",
                                                    "frame": 123, "domain": "Main RAM"})
    from pokemon_ev_tracker.games.platinum.battle import active_enemy_battlers
    enemies = active_enemy_battlers(double)
    result["diagnostic_text_formatting"] = measure(
        lambda: harness._refresh_ram_party_debug(empty, sample, double, enemies))
    result["diagnostic_text"] = harness.text
    return result


def measure_pokemon_diagnostics():
    from test_pokemon_diagnostics import (
        DiagnosticHarness,
        box_cases,
        fixed_item_assets,
        inspection_cases,
        party_cases,
    )
    harness = DiagnosticHarness()
    party = party_cases()["valid"]
    box = box_cases()["valid"]
    state = SimpleNamespace(records=(box,), available=True, records_address=0x02280004,
        valid_pokemon=(box,), empty_records=539, checksum_failures=0)
    inspection = inspection_cases()["valid"]
    slug, path, exists = fixed_item_assets()
    with slug, path, exists:
        result = {"party_diagnostic_refresh": measure(lambda: harness._refresh_ram_party_debug(party, None))}
    result["boxed_diagnostic_refresh"] = measure(lambda: harness._pc_storage_debug_text(state, None, False, changes=(box,)))
    result["anchor_inspection_formatting"] = measure(lambda: harness._pc_pokemon_inspection_lines(inspection))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    app = QApplication.instance() or QApplication([])
    scratch = REPOSITORY / ".test-scratch"
    scratch.mkdir(exist_ok=True)
    results = {"python": platform.python_version(), "platform": platform.platform(),
               "qt_platform": os.environ["QT_QPA_PLATFORM"], "live_bizhawk": False}
    # Retain ignored scratch artifacts, as the test suite does. Windows sandbox
    # ACLs can prohibit tempfile's cleanup of directories created with mode 0700.
    with nullcontext(scratch / f"runtime-benchmark-{uuid4().hex}") as directory:
        root = Path(directory)
        root.mkdir()
        source = _Source()
        source.party = _party(*((i + 1, species) for i, species in enumerate((391, 388, 397, 404, 418, 93))))
        settings = AppSettings(auto_pc_rediscovery=False, prompt_for_nuzlocke_run=False,
                               remember_geometry=False)
        with patch.object(AppSettings, "save_default"), patch.object(SaveBackupService, "start"):
            started = time.perf_counter()
            window = MainWindow(settings, data_source=source,
                                nuzlocke_store=NuzlockeStore(root / "runs.json"),
                                training_preference_store=EVTrainingPreferenceStore(root / "training.json"))
            window.refresh_timer.stop()
            window.resize(1500, 1000)
            window.show()
            app.processEvents()
            results["startup_fixture_ms"] = round((time.perf_counter() - started) * 1000, 4)
            try:
                results["party_adapter_unchanged"] = measure(lambda: window._refresh_tracker_party(True, source.party))
                results["full_ui_poll_unchanged"] = measure(window._refresh_ram_backend_debug)
                profile = cProfile.Profile()
                profile.enable()
                for _ in range(30):
                    window._refresh_ram_backend_debug()
                profile.disable()
                results["profile_calls_30_polls"] = {
                    name: sum(entry.callcount for entry in profile.getstats()
                              if hasattr(entry.code, "co_name") and entry.code.co_name == name)
                    for name in ("resolve_move", "refresh_style", "_refresh_tracker_stats_metadata",
                                 "_refresh_ram_party_debug", "_refresh_training_recommendations")
                }
                server = BizHawkDebugServer(fallback_file=root / "transport.jsonl")
                raw = _party_payload(tuple(_party_record(i + 1, species, 47, attack=27)
                                           for i, species in enumerate((391, 388, 397, 404, 418, 93))))
                payload = {"type": "party_memory", "raw_party_hex": raw.hex(),
                           "party_address": "0x0227E20C", "party_count": 6}
                results["phase_b"] = measure_party_snapshots(server, payload, window)
                results["phase_c1"] = measure_ui_orchestration(window, source)
                results["phase_c2"] = measure_pokemon_diagnostics()
            finally:
                window.close()
                window.deleteLater()
                app.processEvents()
        data_source = BizHawkRamDataSource(server=server, profile=PLATINUM_PROFILE)
        heartbeat = BizHawkHeartbeat(time.monotonic(), payload, "tcp")
        results["party_decode_same_payload"] = measure(lambda: data_source._decode_party_payload(heartbeat))
        line = json.dumps(payload)
        results["transport_party_line"] = measure(lambda: server._record_line(line, "tcp"))
        image = bytearray(b"\xff" * 0x400000)
        record = _boxed_record()
        image[0x1000:0x1000 + len(record)] = record

        def scan():
            accumulator = PCStorageDiscoveryAccumulator("benchmark", "broad", len(image), MAIN_RAM_BASE)
            for offset in range(0, len(image), 0x1000):
                accumulator.append_chunk(offset, bytes(image[offset:offset + 0x1000]))
            assert accumulator.broad_valid_record_count >= 1
            return accumulator

        results["pc_broad_4mib"] = measure(scan, iterations=1, rounds=3)
        tracemalloc.start()
        scan()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results["pc_broad_peak_traced_bytes"] = peak
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
