"""D1 receive-line parsing/dispatch comparisons without sockets or emulator state."""

import argparse
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer


def measure(callback):
    for _ in range(20):
        callback()
    rounds = []
    for _ in range(7):
        started = time.perf_counter()
        for _ in range(2000):
            callback()
        rounds.append((time.perf_counter() - started) * 1_000_000 / 2000)
    return {"median_us": round(statistics.median(rounds), 3),
            "round_us": [round(value, 3) for value in rounds], "iterations": 2000}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    server_type = BizHawkDebugServer
    if args.source:
        spec = importlib.util.spec_from_file_location("baseline_transport", args.source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        server_type = module.BizHawkDebugServer
    server = server_type()
    server._record_pc_discovery_event = lambda payload, source: None
    lines = {
        "heartbeat": json.dumps({"type": "heartbeat", "run_id": "lua", "frame": 120}),
        "party": json.dumps({"type": "party_memory", "run_id": "lua", "raw_party_hex": "AB" * 1420}),
        "pc_chunk_stubbed_handler": json.dumps({"type": "pc_discovery_chunk", "scan_id": "scan", "data": "AB" * 4096}),
    }
    result = {name: measure(lambda line=line: server._record_line(line, "tcp"))
              for name, line in lines.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
