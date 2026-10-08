"""D2 deterministic event/recovery traces; no emulator or asynchronous analysis."""

import argparse
import importlib.util
import json
import logging
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

from pokemon_ev_tracker.games.platinum.pc_discovery import PCStorageDiscoveryAccumulator
from pokemon_ev_tracker.pokemon.gen4.crypto import calculate_checksum, encrypt_box_data
from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer


def workflow(chunks, retry_heavy=False, finalize=False):
    server = BizHawkDebugServer()
    image = bytes(chunks * 512)
    if finalize:
        plain = (298).to_bytes(2, "little") + bytes(126)
        checksum = calculate_checksum(plain)
        record = (
            (0x12345678).to_bytes(4, "little") + bytes(2) + checksum.to_bytes(2, "little")
            + encrypt_box_data(plain, 0x12345678, checksum)
        )
        image = bytes(0x400) + record + bytes(540 * 136 - 136 + 0x400)
        chunks = (len(image) + 511) // 512
    scan = PCStorageDiscoveryAccumulator(
        "bench", "anchor" if finalize else "broad", len(image), 0x02000000,
        anchor_address=0x02000400 if finalize else None,
        anchor_data_offset=0x400 if finalize else 0,
        party_records_address=0x02300000, party_count=0, party_range_valid=True,
    )
    server._pc_discovery_scan = scan
    commands = []
    server._send_tcp_command = lambda command: commands.append(command) or True
    server._begin_pc_discovery_analysis_locked = lambda *args: None
    order = list(range(chunks))
    if retry_heavy:
        order = [index for pair in zip(order[1::2], order[::2]) for index in pair]
    for index in order:
        chunk = image[index * 512 : (index + 1) * 512]
        server._record_pc_discovery_event({
            "type": "pc_discovery_chunk", "scan_id": "bench", "chunk_index": index,
            "offset": index * 512, "byte_count": len(chunk),
            "data_encoding": "hex", "data": chunk.hex(),
        }, "tcp")
    server._record_pc_discovery_event({
        "type": "pc_discovery_end", "scan_id": "bench",
        "total_bytes": len(image), "chunk_count": chunks,
    }, "tcp")
    if finalize:
        server._finalize_pc_discovery(scan, "tcp", 42)
        result = server.latest_pc_storage_discovery_payload().payload
        assert not result["pc_storage_discovery_summary"].get("error")
        assert result["pc_storage_anchor_diagnostic"]["record_checksum_valid"]
        assert result["pc_storage_discovery_summary"]["valid_occupied_records"] == 1
    return server, scan, len(commands)


def main():
    global BizHawkDebugServer, PCStorageDiscoveryAccumulator
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-source", type=Path)
    parser.add_argument("--game-source", type=Path)
    args = parser.parse_args()
    if args.server_source and args.game_source:
        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module

        game = load("baseline_d2_game", args.game_source)
        server = load("baseline_d2_server", args.server_source)
        PCStorageDiscoveryAccumulator = game.PCStorageDiscoveryAccumulator
        server.PCStorageDiscoveryAccumulator = PCStorageDiscoveryAccumulator
        BizHawkDebugServer = server.BizHawkDebugServer
    logging.disable(logging.CRITICAL)
    results = {}
    for name, options in {
        "normal": (8, False, False), "large_stream": (256, False, False),
        "retry_heavy": (64, True, False), "finalization": (64, False, True),
    }.items():
        workflow(*options)
        rounds = []
        for _ in range(7):
            started = time.perf_counter()
            for _ in range(5):
                server, scan, commands = workflow(*options)
            rounds.append((time.perf_counter() - started) * 1000 / 5)
        del server, scan
        tracemalloc.start()
        server, scan, commands = workflow(*options)
        retained, peak = tracemalloc.get_traced_memory()
        server._pc_discovery_scan = None
        del scan
        released, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results[name] = {
            "median_ms": round(statistics.median(rounds), 4),
            "round_ms": [round(value, 4) for value in rounds], "commands": commands,
            "retained_bytes": retained, "peak_bytes": peak, "after_scan_release_bytes": released,
        }
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
