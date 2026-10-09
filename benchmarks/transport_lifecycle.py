"""D3 shutdown/resource measurements using controlled loops and local sockets."""

import argparse
import importlib.util
import json
import logging
import socket
import statistics
import sys
import threading
import time
import tracemalloc
from pathlib import Path

from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer


class Client:
    def __init__(self, sock):
        self.sock = sock
        self.ready = threading.Event()
        self.reads = 0

    def fileno(self):
        self.reads += 1
        if self.reads >= 2:
            self.ready.set()
        return self.sock.fileno()

    def recv(self, size):
        return self.sock.recv(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.sock.close()

    def shutdown(self, how):
        self.sock.shutdown(how)

    def close(self):
        self.sock.close()

    def makefile(self, *args, **kwargs):
        file = self.sock.makefile(*args, **kwargs)
        client = self

        class Reader:
            def readline(self):
                client.reads += 1
                if client.reads >= 2:
                    client.ready.set()
                return file.readline()

        return Reader()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    server_type = BizHawkDebugServer
    if args.source:
        spec = importlib.util.spec_from_file_location("d3_baseline", args.source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        server_type = module.BizHawkDebugServer
    logging.disable(logging.CRITICAL)
    times, alive = [], []
    for _ in range(20):
        server = server_type()
        receiver, sender = socket.socketpair()
        client = Client(receiver)
        if hasattr(server, "_launch_owned_thread"):
            worker = server._launch_owned_thread(
                server._handle_client, args=(client, ("local", 1)), name="bizhawk-ram-client",
            )
        else:
            worker = threading.Thread(target=server._handle_client, args=(client, ("local", 1)))
            worker.start()
        sender.sendall(b'{"type":"heartbeat"}\n')
        assert client.ready.wait(2)
        before = time.perf_counter()
        server.stop()
        times.append((time.perf_counter() - before) * 1000)
        alive.append(int(worker.is_alive()))
        sender.close()
        worker.join(2)
        assert not worker.is_alive()
    server = server_type()
    tracemalloc.start()
    cycle_times = []
    for _ in range(50):
        tcp, file = threading.Event(), threading.Event()

        def run(ready):
            event = server._stop_event
            ready.set()
            event.wait()

        server._serve_tcp = lambda ready=tcp: run(ready)
        server._poll_fallback_file = lambda ready=file: run(ready)
        server.start()
        assert tcp.wait(2) and file.wait(2)
        threads = (server._tcp_thread, server._file_thread)
        before = time.perf_counter()
        server.stop()
        cycle_times.append((time.perf_counter() - before) * 1000)
        for thread in threads:
            thread.join(2)
        assert all(not thread.is_alive() for thread in threads)
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result = {
        "tcp_shutdown_median_ms": round(statistics.median(times), 4),
        "tcp_shutdown_max_ms": round(max(times), 4),
        "tcp_readers_alive_at_stop_return": sum(alive), "tcp_cycles": 20,
        "controlled_cycle_shutdown_median_ms": round(statistics.median(cycle_times), 4),
        "restart_cycles": 50, "retained_bytes_after_cycles": retained, "peak_bytes": peak,
        "owned_threads_after_cleanup": len(server._owned_threads) if hasattr(server, "_owned_threads") else None,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
