"""Localhost NDJSON receiver for BizHawk Lua diagnostics."""

from __future__ import annotations

import hashlib
import heapq
import json
import logging
import os
import queue
import re
import secrets
import socket
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pokemon_ev_tracker.games.platinum.pc_discovery import (
    INSPECTION_AFTER_BYTES,
    INSPECTION_BEFORE_BYTES,
    PC_DISCOVERY_STALL_SECONDS,
    PLATINUM_PARTY_POINTER_ADDRESS,
    RECORDS_SIZE,
    PCStorageDiscoveryAccumulator,
)
from pokemon_ev_tracker.games.platinum.pc_sram_search import (
    analyze_sram_snapshot,
)
from pokemon_ev_tracker.games.platinum.species import load_gen4_species
from pokemon_ev_tracker.pokemon.gen4.structure import decode_box_pokemon

LOGGER = logging.getLogger(__name__)
NDS_MAIN_RAM_BASE = 0x02000000
NDS_MAIN_RAM_SIZE = 0x400000
PC_BOX_RECORD_SIZE = 136
PC_BOX_RECORDS_OFFSET = 4
PC_DISCOVERY_CHUNK_MAX = 0x1000
PC_DISCOVERY_RETRY_TIMEOUT_SECONDS = 2.0
PC_DISCOVERY_RETRY_LIMIT = 3
PC_DISCOVERY_STATUS_LOG_INTERVAL_SECONDS = 1.0
PC_CACHE_CONFIRMATION_TIMEOUT_SECONDS = 3.0
PC_SAVE_COPY_RECORD_OFFSETS = (0x0C104, 0x4C104)
PC_SAVE_HEADER_BYTES = 4
PC_SAVE_SEGMENT_PREFIX_BYTES = 4
PC_SAVE_SEGMENT_BYTES = 2 * PC_SAVE_SEGMENT_PREFIX_BYTES + RECORDS_SIZE
PC_SAVE_TEST_CHUNK_BYTES = 0x1000
PC_SAVE_FILE_MINIMUM_BYTES = max(
    offset - PC_SAVE_SEGMENT_PREFIX_BYTES + PC_SAVE_SEGMENT_BYTES
    for offset in PC_SAVE_COPY_RECORD_OFFSETS
)
PC_SRAM_SEARCH_CHUNK_MAX = 0x1000
PC_SRAM_SEARCH_MAX_BYTES = 0x1000000
PC_SRAM_SEARCH_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class BizHawkHeartbeat:
    """Latest diagnostic message received from BizHawk."""

    received_at: float
    payload: dict[str, Any]
    source: str
    bytes_received: int = 0


@dataclass(frozen=True)
class FriendshipWalkCommandReceipt:
    """Sequence and route used for a queued Lua walk command."""

    sequence: int
    transport: str


class BizHawkDebugServer:
    """Receives newline-delimited JSON from BizHawk over localhost.

    The matching Lua script tries TCP first. If LuaSocket is unavailable inside
    EmuHawk, it appends the same JSON lines to ``fallback_file`` and this class
    polls that file.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 46387,
        fallback_file: Path | None = None,
        stale_after_seconds: float = 5.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.fallback_file = (
            fallback_file
            or Path(
                os.environ.get("TEMP")
                or os.environ.get("TMP")
                or os.environ.get("TMPDIR")
                or tempfile.gettempdir()
            )
            / "ev_tracker_bizhawk.jsonl"
        )
        self.command_file = self.fallback_file.with_name("ev_tracker_bizhawk_command.txt")
        self.coordinate_file = self.fallback_file.with_name("ev_tracker_bizhawk_coordinates.jsonl")
        self.stale_after_seconds = stale_after_seconds
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._command_file_lock = threading.Lock()
        self._command_file_queue: deque[str] = deque()
        self._walk_command_lock = threading.Lock()
        self._walk_command_sequence = secrets.randbelow(8_000_000_000) + 1_000_000_000
        self._heartbeat: BizHawkHeartbeat | None = None
        self._party_payload: BizHawkHeartbeat | None = None
        self._pc_storage_payload: BizHawkHeartbeat | None = None
        self._pc_storage_discovery_payload: BizHawkHeartbeat | None = None
        self._pc_save_offset_test_payload: BizHawkHeartbeat | None = None
        self._pc_discovery_lock = threading.Lock()
        self._pc_save_test_lock = threading.Lock()
        self._pc_save_offset_test_active: dict[str, Any] | None = None
        self._pc_save_offset_test_baseline: dict[tuple[str, int], bytes] = {}
        self._pc_save_offset_test_baseline_run_id: str | None = None
        self._pc_save_offset_test_capture_count = 0
        self._pc_save_offset_test_status: dict[str, Any] = {"status": "idle"}
        self._pc_save_offset_test_directory: Path | None = None
        self._pc_save_offset_test_before_files: dict[str, dict[str, Any]] = {}
        self._pc_save_offset_test_expected_identity: dict[str, Any] | None = None
        self._pc_save_offset_test_image_baseline: dict[str, bytes] = {}
        self._pc_sram_search_lock = threading.Lock()
        self._pc_sram_search_payload: BizHawkHeartbeat | None = None
        self._pc_sram_search_pending: dict[str, Any] | None = None
        self._pc_sram_search_active: dict[str, Any] | None = None
        self._pc_sram_search_baselines: dict[tuple[str, str, int, str], dict[str, Any]] = {}
        self._pc_sram_search_status: dict[str, Any] = {"status": "idle"}
        self._pc_discovery_scan: PCStorageDiscoveryAccumulator | None = None
        self._pc_pokemon_search: dict[str, Any] | None = None
        self._pc_structure_pointer_search: dict[str, Any] | None = None
        self._pc_discovery_progress: dict[str, Any] = {"status": "idle"}
        self._pc_cache_install: dict[str, Any] | None = None
        self._pc_inspection_baseline: dict[str, Any] | None = None
        self._server_socket: socket.socket | None = None
        self._tcp_thread: threading.Thread | None = None
        self._file_thread: threading.Thread | None = None
        self._client_threads: list[threading.Thread] = []
        self._file_position: int | None = None
        self._file_size_seen: int | None = None
        self._client_lock = threading.Lock()
        self._active_client: socket.socket | None = None
        self._coordinate_events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=2048)
        self._coordinate_file_lock = threading.Lock()
        self._coordinate_file_capture_id: str | None = None
        self._coordinate_file_position = 0

    def start(self) -> None:
        if self._tcp_thread is not None and self._tcp_thread.is_alive():
            return
        self._stop_event.clear()
        self._tcp_thread = threading.Thread(
            target=self._serve_tcp,
            name="bizhawk-ram-tcp-server",
            daemon=True,
        )
        self._file_thread = threading.Thread(
            target=self._poll_fallback_file,
            name="bizhawk-ram-file-poller",
            daemon=True,
        )
        self._tcp_thread.start()
        self._file_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._client_lock:
            client = self._active_client
            self._active_client = None
        if client is not None:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass

    def latest_heartbeat(self) -> BizHawkHeartbeat | None:
        with self._lock:
            return self._heartbeat

    def latest_party_payload(self) -> BizHawkHeartbeat | None:
        with self._lock:
            return self._party_payload

    def latest_pc_storage_payload(self) -> BizHawkHeartbeat | None:
        with self._lock:
            return self._pc_storage_payload

    def latest_pc_storage_discovery_payload(self) -> BizHawkHeartbeat | None:
        with self._lock:
            return self._pc_storage_discovery_payload

    def latest_pc_save_offset_test_payload(self) -> BizHawkHeartbeat | None:
        with self._lock:
            return self._pc_save_offset_test_payload

    def latest_pc_sram_search_payload(self) -> BizHawkHeartbeat | None:
        with self._lock:
            return self._pc_sram_search_payload

    def pc_sram_search_status(self) -> dict[str, Any]:
        with self._pc_sram_search_lock:
            return dict(self._pc_sram_search_status)

    def pc_save_offset_test_status(self) -> dict[str, Any]:
        with self._pc_save_test_lock:
            return dict(self._pc_save_offset_test_status)

    def pc_storage_discovery_progress(self) -> dict[str, Any]:
        with self._pc_discovery_lock:
            pointer_search = self._pc_structure_pointer_search
            if pointer_search is not None:
                total_bytes = NDS_MAIN_RAM_SIZE
                bytes_scanned = int(pointer_search.get("bytes_scanned") or 0)
                now = time.monotonic()
                return {
                    "scan_id": pointer_search.get("scan_id"),
                    "mode": "structure_pointer_search",
                    "status": pointer_search.get("status", "queued"),
                    "progress_percent": min(100, bytes_scanned * 100 // total_bytes),
                    "bytes_scanned": bytes_scanned,
                    "total_bytes": total_bytes,
                    "current_address": pointer_search.get("current_address", "--"),
                    "matches_found": pointer_search.get("reference_count", 0),
                    "elapsed_seconds": max(
                        0.0, now - pointer_search.get("started_at", now)
                    ),
                    "source": pointer_search.get("source"),
                }
            search = self._pc_pokemon_search
            if search is not None:
                total_bytes = int(search.get("total_bytes") or NDS_MAIN_RAM_SIZE)
                bytes_scanned = int(search.get("bytes_scanned") or 0)
                now = time.monotonic()
                return {
                    "scan_id": search.get("scan_id"),
                    "mode": "pokemon_search",
                    "status": search.get("status", "queued"),
                    "progress_percent": min(100, bytes_scanned * 100 // max(1, total_bytes)),
                    "bytes_scanned": bytes_scanned,
                    "total_bytes": total_bytes,
                    "current_address": search.get("current_address", "--"),
                    "matches_found": len(search.get("matches", {})),
                    "elapsed_seconds": max(0.0, now - search.get("started_at", now)),
                    "anchor_address": f"0x{search['anchor_address']:08X}",
                    "source": search.get("source"),
                }
            accumulator = self._pc_discovery_scan
            if accumulator is not None:
                progress = accumulator.progress()
                stored_status = self._pc_discovery_progress
                if stored_status.get("status") == "retrying":
                    if progress["missing_chunk_count"]:
                        progress["status"] = "retrying"
                        progress["missing_chunk_index"] = min(
                            progress["proven_missing_chunks"]
                        )
                    elif stored_status.get("retry_reason") == "malformed_chunk":
                        progress["status"] = "retrying"
                        progress["missing_chunk_index"] = stored_status.get(
                            "missing_chunk_index"
                        )
                    elif progress["producer_status"] == "stalled":
                        progress["status"] = "stalled"
                    else:
                        progress["status"] = "scanning"
                elif progress["producer_status"] == "stalled":
                    progress["status"] = "stalled"
                return progress
            return dict(self._pc_discovery_progress)

    def request_coordinate_capture(
        self,
        capture_id: str,
        label: str,
        start_offset: int,
        length: int,
        *,
        transport: str = "tcp",
    ) -> FriendshipWalkCommandReceipt | None:
        if label not in {"baseline", "idle", "right", "left", "down", "up"}:
            return False
        if start_offset < 0 or length <= 0 or length > 0x400000:
            return False
        if transport not in {"tcp", "file"}:
            return False
        command = f"CAPTURE|{capture_id}|{label}|{start_offset}|{length}\n"
        if transport == "tcp":
            return self._send_tcp_command(command)
        with self._coordinate_file_lock:
            self._coordinate_file_capture_id = capture_id
            self._coordinate_file_position = 0
            try:
                self.coordinate_file.write_bytes(b"")
            except OSError:
                self._coordinate_file_capture_id = None
                return False
            if not self._write_command_file(command):
                self._coordinate_file_capture_id = None
                return False
        return True

    def send_friendship_walk_command(
        self,
        action: str,
        value: str | None = None,
        *,
        transport: str | tuple[str, ...] = "tcp",
    ) -> FriendshipWalkCommandReceipt | None:
        with self._walk_command_lock:
            self._walk_command_sequence += 1
            sequence = self._walk_command_sequence
        if action == "START" and value in {"horizontal", "vertical"}:
            command = f"WALK|{sequence}|START|{value}\n"
        elif action == "DIRECTION" and value in {"Left", "Right", "Up", "Down"}:
            command = f"WALK|{sequence}|DIRECTION|{value}\n"
        elif action == "STOP" and value is None:
            command = f"WALK|{sequence}|STOP\n"
        elif action == "PING" and value is None:
            command = f"WALK|{sequence}|PING\n"
        else:
            return None
        transports = (transport,) if isinstance(transport, str) else transport
        accepted = []
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                accepted.append(route)
        if not accepted:
            return None
        return FriendshipWalkCommandReceipt(sequence, "+".join(accepted))

    def set_coordinate_preview(
        self,
        x_offset: int | None,
        y_offset: int | None,
        data_type: str = "u16",
        *,
        transport: str = "tcp",
    ) -> bool:
        if x_offset is None or y_offset is None:
            command = "CLEAR_PREVIEW\n"
        else:
            if data_type not in {"u16", "s16"}:
                return False
            if not (0 <= x_offset <= 0x3FFFFE and 0 <= y_offset <= 0x3FFFFE):
                return False
            command = f"PREVIEW|{x_offset}|{y_offset}|{data_type}\n"
        if transport == "tcp":
            return self._send_tcp_command(command)
        if transport == "file":
            return self._write_command_file(command)
        return False

    def drain_coordinate_events(self, limit: int = 1024) -> tuple[dict[str, Any], ...]:
        events = []
        for _ in range(max(0, limit)):
            try:
                events.append(self._coordinate_events.get_nowait())
            except queue.Empty:
                break
        return tuple(events)

    def _send_tcp_command(self, command: str) -> bool:
        with self._client_lock:
            client = self._active_client
            if client is None:
                return False
            try:
                client.sendall(command.encode("ascii"))
            except OSError:
                self._active_client = None
                return False
        return True

    def _write_command_file(self, command: str) -> bool:
        with self._command_file_lock:
            if self.command_file.exists():
                self._command_file_queue.append(command)
                LOGGER.info(
                    "Queued BizHawk fallback command behind pending command: %s",
                    command.strip(),
                )
                return True
            return self._publish_command_file_locked(command)

    def _publish_command_file_locked(self, command: str) -> bool:
        temporary = self.command_file.with_name(self.command_file.name + ".tmp")
        try:
            temporary.write_text(command, encoding="ascii")
            os.replace(temporary, self.command_file)
        except OSError:
            LOGGER.exception("Could not write BizHawk Lua command file.")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    def _flush_command_file_queue(self) -> None:
        published = None
        with self._command_file_lock:
            if not self._command_file_queue or self.command_file.exists():
                return
            command = self._command_file_queue.popleft()
            if not self._publish_command_file_locked(command):
                self._command_file_queue.appendleft(command)
            else:
                published = command
                LOGGER.info(
                    "Published queued BizHawk fallback command: %s (remaining=%d)",
                    command.strip(), len(self._command_file_queue),
                )
        if published is not None:
            with self._pc_discovery_lock:
                pending = self._pc_cache_install
                if pending is not None and pending.get("command") == published:
                    pending.update({
                        "command_sent": True,
                        "command_queued": False,
                        "sent_at": time.monotonic(),
                    })
                    self._pc_discovery_progress = {
                        **self._pc_discovery_progress,
                        "cache_install": dict(pending),
                    }

    def request_pc_storage_scan(self, *, transport: str | tuple[str, ...] = "tcp") -> bool:
        transports = (transport,) if isinstance(transport, str) else transport
        command = "PC_SCAN_NOW\n"
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                return True
        return False

    def request_pc_sram_pokemon_search(
        self,
        expected_nickname: str,
        expected_species_id: int,
        *,
        transport: str | tuple[str, ...] = "tcp",
    ) -> bool:
        nickname = expected_nickname.strip() if isinstance(expected_nickname, str) else ""
        if (
            not nickname
            or isinstance(expected_species_id, bool)
            or not isinstance(expected_species_id, int)
            or not 1 <= expected_species_id <= 493
        ):
            with self._pc_sram_search_lock:
                self._pc_sram_search_status = {
                    "status": "failed",
                    "error": "Enter a nickname and a valid Gen IV species.",
                }
            return False
        with self._pc_sram_search_lock:
            if self._pc_sram_search_active is not None or self._pc_sram_search_pending is not None:
                return False
            self._pc_sram_search_pending = {
                "expected_nickname": nickname,
                "expected_species_id": expected_species_id,
                "requested_at": time.monotonic(),
            }
            self._pc_sram_search_status = {
                "status": "queued",
                "bytes_received": 0,
                "total_bytes": 0,
                "chunks_received": 0,
                "chunk_count": None,
            }
        transports = (transport,) if isinstance(transport, str) else transport
        command = "PC_SEARCH_SRAM_POKEMON\n"
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                with self._pc_sram_search_lock:
                    self._pc_sram_search_status["source"] = route
                LOGGER.info(
                    "Live SRAM Pokemon search dispatched via %s: nickname=%s species=%d",
                    route,
                    nickname,
                    expected_species_id,
                )
                return True
        with self._pc_sram_search_lock:
            self._pc_sram_search_pending = None
            self._pc_sram_search_status = {
                "status": "failed",
                "error": "BizHawk did not accept the live SRAM search command.",
            }
        LOGGER.error("Live SRAM Pokemon search dispatch failed.")
        return False

    def request_pc_save_offset_test(
        self,
        *,
        transport: str | tuple[str, ...] = "tcp",
        save_ram_directory: str | Path | None = None,
        expected_nickname: str | None = None,
        expected_species_id: int | None = None,
    ) -> bool:
        resolved_directory: Path | None = None
        before_files: dict[str, dict[str, Any]] = {}
        if save_ram_directory is not None:
            try:
                resolved_directory = Path(save_ram_directory).expanduser().resolve(strict=True)
                if not resolved_directory.is_dir():
                    raise ValueError("selected SaveRAM path is not a directory")
                before_files = self._snapshot_save_ram_directory(resolved_directory)
            except (OSError, RuntimeError, ValueError) as exc:
                error = f"Invalid BizHawk SaveRAM directory: {exc}"
                with self._pc_save_test_lock:
                    self._pc_save_offset_test_status = {
                        "status": "failed",
                        "error": error,
                    }
                LOGGER.error("PC save-offset test rejected selected file: %s", error)
                return False
        with self._pc_save_test_lock:
            if self._pc_save_offset_test_active is not None:
                return False
            self._pc_save_offset_test_directory = resolved_directory
            self._pc_save_offset_test_before_files = before_files
            self._pc_save_offset_test_expected_identity = (
                {
                    "nickname": expected_nickname.strip(),
                    "species_id": expected_species_id,
                }
                if isinstance(expected_nickname, str)
                and expected_nickname.strip()
                and isinstance(expected_species_id, int)
                and not isinstance(expected_species_id, bool)
                else None
            )
        transports = (transport,) if isinstance(transport, str) else transport
        command = "PC_TEST_SAVE_PC_OFFSETS\n"
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                with self._pc_save_test_lock:
                    self._pc_save_offset_test_status = {
                        "status": "queued",
                        "source": route,
                        "bytes_received": 0,
                        "total_bytes": 0,
                    }
                LOGGER.info("Save-file PC offset test dispatched via %s.", route)
                return True
        with self._pc_save_test_lock:
            self._pc_save_offset_test_directory = None
            self._pc_save_offset_test_before_files = {}
            self._pc_save_offset_test_expected_identity = None
        LOGGER.error("Save-file PC offset test dispatch failed.")
        return False

    @staticmethod
    def _snapshot_save_ram_directory(directory: Path) -> dict[str, dict[str, Any]]:
        """Capture per-file metadata; hash SaveRAM images to catch coarse timestamps."""
        snapshot: dict[str, dict[str, Any]] = {}
        for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            try:
                stat = path.stat()
                if not path.is_file():
                    continue
                entry: dict[str, Any] = {
                    "filename": path.name,
                    "path": str(path.resolve()),
                    "size": stat.st_size,
                    "modified_timestamp": datetime.fromtimestamp(
                        stat.st_mtime, UTC
                    ).isoformat(),
                    "modified_ns": stat.st_mtime_ns,
                }
                if path.suffix.casefold() in {".saveram", ".sav"}:
                    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                snapshot[str(path.resolve())] = entry
            except OSError as exc:
                snapshot[str(path)] = {
                    "filename": path.name,
                    "path": str(path),
                    "error": str(exc),
                }
        return snapshot

    @staticmethod
    def _save_ram_directory_changes(
        before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for path in sorted(set(before) | set(after)):
            old = before.get(path)
            new = after.get(path)
            reasons: list[str] = []
            if old is None:
                reasons.append("created")
            elif new is None:
                reasons.append("deleted")
            else:
                if old.get("size") != new.get("size"):
                    reasons.append("size changed")
                if old.get("modified_ns") != new.get("modified_ns"):
                    reasons.append("modified timestamp changed")
                if (
                    old.get("sha256") is not None
                    and new.get("sha256") is not None
                    and old["sha256"] != new["sha256"]
                ):
                    reasons.append("contents changed")
            if reasons:
                changes.append({
                    "path": path,
                    "filename": (new or old or {}).get("filename", Path(path).name),
                    "reason": ", ".join(reasons),
                    "before": old,
                    "after": new,
                })
        return changes

    def request_pc_storage_discovery(
        self,
        *,
        transport: str | tuple[str, ...] = "tcp",
        anchor_address: int | None = None,
    ) -> bool:
        with self._pc_discovery_lock:
            if self._pc_structure_pointer_search is not None:
                LOGGER.warning("PC storage discovery rejected while another diagnostic is active.")
                return False
        transports = (transport,) if isinstance(transport, str) else transport
        command = "PC_DISCOVER_STORAGE\n"
        mode = "broad"
        if anchor_address is not None:
            if isinstance(anchor_address, bool) or not isinstance(anchor_address, int):
                LOGGER.error(
                    "Rejected PC anchor command: address is not an integer: %r",
                    anchor_address,
                )
                return False
            if (
                anchor_address - PC_BOX_RECORDS_OFFSET < NDS_MAIN_RAM_BASE
                or anchor_address + PC_BOX_RECORD_SIZE > NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
            ):
                LOGGER.error(
                    "Rejected PC anchor command: address out of Main RAM: 0x%X",
                    anchor_address,
                )
                return False
            command = f"PC_DISCOVER_STORAGE|BOX1_SLOT1|{anchor_address:08X}\n"
            mode = "anchor"
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                with self._pc_discovery_lock:
                    self._pc_discovery_progress = {
                        "status": "queued",
                        "mode": mode,
                        "progress_percent": 0,
                        "candidates_found": 0,
                        "anchor_address": (
                            f"0x{anchor_address:08X}"
                            if anchor_address is not None
                            else None
                        ),
                        "source": route,
                    }
                LOGGER.info(
                    "PC storage discovery command dispatched via %s: %s",
                    route,
                    command.strip(),
                )
                return True
        LOGGER.error("PC storage discovery command dispatch failed: %s", command.strip())
        return False

    def request_pc_storage_session_layout_discovery(
        self,
        species_id: int,
        nickname: str | None,
        *,
        transport: str | tuple[str, ...] = "tcp",
        lua_run_id: str | None = None,
    ) -> bool:
        if isinstance(species_id, bool) or not isinstance(species_id, int) or not 1 <= species_id <= 493:
            LOGGER.error("Rejected session PC discovery: invalid expected species id %r", species_id)
            return False
        normalized_nickname = (nickname or "").strip()
        if len(normalized_nickname) > 10:
            LOGGER.error("Rejected session PC discovery: nickname exceeds 10 characters")
            return False
        try:
            nickname_hex = normalized_nickname.encode("ascii").hex().upper()
        except UnicodeEncodeError:
            LOGGER.error("Rejected session PC discovery: nickname must be ASCII")
            return False
        if any(ord(char) < 0x20 or ord(char) > 0x7E for char in normalized_nickname):
            LOGGER.error("Rejected session PC discovery: nickname contains non-printable text")
            return False
        with self._pc_discovery_lock:
            if (
                self._pc_discovery_scan is not None
                or self._pc_pokemon_search is not None
                or self._pc_structure_pointer_search is not None
                or self._pc_discovery_progress.get("status") in {
                    "queued", "scanning", "stalled", "retrying", "analyzing",
                    "cancel-requested", "cache-pending",
                }
            ):
                LOGGER.warning("Session PC layout discovery rejected while another PC diagnostic is active.")
                return False
        if lua_run_id is not None and re.fullmatch(r"[A-Za-z0-9-]{1,64}", lua_run_id) is None:
            return False
        command = f"PC_DISCOVER_CURRENT_LAYOUT|{species_id}|{nickname_hex}"
        if lua_run_id is not None:
            command += f"|{lua_run_id}"
        command += "\n"
        transports = (transport,) if isinstance(transport, str) else transport
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                with self._pc_discovery_lock:
                    self._pc_discovery_progress = {
                        "status": "queued",
                        "mode": "session_layout",
                        "progress_percent": 0,
                        "candidates_found": 0,
                        "expected_species_id": species_id,
                        "expected_nickname": normalized_nickname or None,
                        "lua_run_id": lua_run_id,
                        "source": route,
                    }
                LOGGER.info(
                    "Session PC layout discovery dispatched via %s: species=%d nickname=%r",
                    route,
                    species_id,
                    normalized_nickname or None,
                )
                return True
        LOGGER.error("Session PC layout discovery dispatch failed for %s.", command.strip())
        return False

    def reset_pc_discovery_for_lua_run(self, lua_run_id: str) -> None:
        """Drop only discovery work tied to a different Lua process/session."""
        with self._lock:
            result = self._pc_storage_discovery_payload
        result_run = (
            result.payload.get("session_lua_run_id")
            if result is not None and isinstance(result.payload, dict) else None
        )
        with self._pc_discovery_lock:
            active_run = getattr(self._pc_discovery_scan, "lua_run_id", None)
            pending_run = (self._pc_cache_install or {}).get("lua_run_id")
            progress_run = self._pc_discovery_progress.get("lua_run_id")
            if active_run not in {None, lua_run_id}:
                self._pc_discovery_scan = None
            if pending_run not in {None, lua_run_id}:
                self._pc_cache_install = None
            stale = any(run not in {None, lua_run_id} for run in (
                active_run, pending_run, result_run, progress_run
            ))
            if stale:
                self._pc_discovery_progress = {"status": "idle"}
        if stale:
            with self._lock:
                if self._pc_storage_discovery_payload is result:
                    self._pc_storage_discovery_payload = None
            LOGGER.info("Cleared PC discovery state from prior Lua run; new run=%s", lua_run_id)

    def cache_session_pc_layout(
        self,
        lua_run_id: str,
        first_record_address: int,
        *,
        transport: str,
    ) -> bool:
        if (
            not isinstance(lua_run_id, str)
            or re.fullmatch(r"[A-Za-z0-9-]{1,64}", lua_run_id) is None
            or isinstance(first_record_address, bool)
            or not isinstance(first_record_address, int)
            or not NDS_MAIN_RAM_BASE <= first_record_address
            or first_record_address + RECORDS_SIZE > NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
        ):
            LOGGER.error("Rejected invalid session PC cache request: run=%r address=%r", lua_run_id, first_record_address)
            return False
        command = f"PC_CACHE_SESSION_PC|{lua_run_id}|{first_record_address:08X}\n"
        LOGGER.info("Sending PC cache-install command: %s", command.strip())
        transports = (transport, "file" if transport == "tcp" else "tcp")
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                with self._command_file_lock:
                    queue_depth = len(self._command_file_queue)
                LOGGER.info(
                    "Session PC cache command dispatched via %s: run_id=%s first_record=0x%08X fallback_queue_depth=%d",
                    route,
                    lua_run_id,
                    first_record_address,
                    queue_depth,
                )
                return True
        LOGGER.error("Could not dispatch session PC cache command for run_id=%s", lua_run_id)
        return False

    def retry_session_pc_cache_install(self) -> bool:
        with self._pc_discovery_lock:
            pending = dict(self._pc_cache_install or {})
        if not pending or pending.get("confirmation_received") or pending.get("last_lua_run_id") not in {
            None, pending.get("lua_run_id")
        }:
            return False
        try:
            address = int(str(pending["address"]), 16)
        except (KeyError, TypeError, ValueError):
            return False
        command = f"PC_CACHE_SESSION_PC|{pending['lua_run_id']}|{address:08X}\n"
        with self._command_file_lock:
            already_queued = command in self._command_file_queue
        sent = already_queued or self.cache_session_pc_layout(
            str(pending["lua_run_id"]), address,
            transport=str(pending.get("source") or "file"),
        )
        if not sent:
            return False
        with self._command_file_lock:
            queued = command in self._command_file_queue
        with self._pc_discovery_lock:
            current = self._pc_cache_install
            retry_pending = current is not None and not current["confirmation_received"]
            if retry_pending:
                current.update({
                    "command_sent": not queued,
                    "command_queued": queued,
                    "sent_at": None if queued else time.monotonic(),
                    "queued_at": time.monotonic() if queued else None,
                    "lua_validation": "pending",
                    "error": None,
                })
                self._pc_discovery_progress = {
                    **self._pc_discovery_progress,
                    "status": "cache-pending",
                    "error": None,
                    "cache_install": dict(current),
                }
        LOGGER.info(
            "Retried PC cache installation: run=%s address=%s",
            pending["lua_run_id"], pending["address"],
        )
        return True

    def _check_pc_cache_confirmation_timeout(self) -> None:
        with self._pc_discovery_lock:
            pending = self._pc_cache_install
            if pending is None or pending["confirmation_received"]:
                return
            sent_at = pending.get("sent_at")
            queued_at = pending.get("queued_at")
            waiting_since = sent_at if isinstance(sent_at, (int, float)) else queued_at
            if not isinstance(waiting_since, (int, float)):
                return
            if self._pc_discovery_progress.get("status") != "cache-pending":
                return
            expected_run = pending["lua_run_id"]
            address = pending["address"]
        with self._lock:
            latest = max(
                (item for item in (self._pc_storage_payload, self._party_payload) if item),
                key=lambda item: item.received_at,
                default=None,
            )
            result_payload = self._pc_storage_discovery_payload
        observed_run = None
        if latest is not None and latest.received_at >= waiting_since:
            observed_run = latest.payload.get("lua_run_id") or latest.payload.get("run_id")
        mismatch = bool(observed_run and observed_run != expected_run)
        if not mismatch and time.monotonic() - waiting_since < PC_CACHE_CONFIRMATION_TIMEOUT_SECONDS:
            return
        reason = (
            f"Lua run changed during PC cache install: expected {expected_run}, "
            f"observed {observed_run}; rediscover the layout."
            if mismatch else
            f"Fallback command queue did not publish PC cache install within "
            f"{PC_CACHE_CONFIRMATION_TIMEOUT_SECONDS:g}s for {address} (run {expected_run})."
            if sent_at is None else
            f"No Lua cache confirmation within {PC_CACHE_CONFIRMATION_TIMEOUT_SECONDS:g}s "
            f"for {address} (run {expected_run})."
        )
        status = "stale" if mismatch else "cache-confirmation-failed"
        with self._pc_discovery_lock:
            pending = self._pc_cache_install
            if (
                pending is None or pending["confirmation_received"]
                or pending.get("sent_at") != sent_at
                or pending.get("queued_at") != queued_at
            ):
                return
            pending.update({"last_lua_run_id": observed_run, "error": reason})
            self._pc_discovery_progress = {
                **self._pc_discovery_progress,
                "status": "failed",
                "pc_storage_resolver_status": status,
                "error": reason,
                "cache_install": dict(pending),
            }
        LOGGER.error("PC cache confirmation failed: %s", reason)
        if result_payload is not None and isinstance(result_payload.payload, dict):
            result = dict(result_payload.payload)
            result["pc_storage_resolver_status"] = status
            result["pc_storage_resolver_error"] = reason
            result["pc_storage_rediscovery_required"] = mismatch
            self._store_pc_discovery_result(result, str(pending.get("source") or "file"))

    def request_pc_pokemon_inspection(
        self,
        anchor_address: int,
        *,
        transport: str | tuple[str, ...] = "tcp",
    ) -> bool:
        with self._pc_discovery_lock:
            if self._pc_structure_pointer_search is not None:
                LOGGER.warning("Pokemon inspection rejected while another diagnostic is active.")
                return False
        if isinstance(anchor_address, bool) or not isinstance(anchor_address, int):
            return False
        if (
            anchor_address < NDS_MAIN_RAM_BASE
            or anchor_address + PC_BOX_RECORD_SIZE > NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
        ):
            return False
        transports = (transport,) if isinstance(transport, str) else transport
        command = f"PC_INSPECT_POKEMON|{anchor_address:08X}\n"
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                with self._pc_discovery_lock:
                    self._pc_discovery_progress = {
                        "status": "queued",
                        "mode": "inspect",
                        "progress_percent": 0,
                        "candidates_found": 0,
                        "anchor_address": f"0x{anchor_address:08X}",
                        "source": route,
                    }
                LOGGER.info(
                    "Pokemon anchor inspection dispatched via %s: %s",
                    route,
                    command.strip(),
                )
                return True
        LOGGER.error("Pokemon anchor inspection dispatch failed: %s", command.strip())
        return False

    def request_pc_pokemon_search(
        self, *, transport: str | tuple[str, ...] = "tcp"
    ) -> bool:
        with self._pc_discovery_lock:
            if (
                self._pc_discovery_scan is not None
                or self._pc_pokemon_search is not None
                or self._pc_structure_pointer_search is not None
            ):
                LOGGER.warning("Pokemon RAM search rejected while another PC diagnostic is active.")
                return False
            baseline = self._pc_inspection_baseline
            identity = dict(baseline.get("identity") or {}) if baseline else {}
            anchor_address = baseline.get("anchor_address") if baseline else None
            if identity and anchor_address is not None:
                identity["anchor_address"] = anchor_address
        try:
            pid = int(str(identity.get("pid")), 0)
            checksum = int(str(identity.get("checksum")), 0)
            species_id = int(identity.get("species_id"))
        except (TypeError, ValueError):
            LOGGER.error("Pokemon RAM search rejected: no valid inspection identity baseline.")
            return False
        if (
            anchor_address is None
            or not NDS_MAIN_RAM_BASE <= anchor_address
            or anchor_address + PC_BOX_RECORD_SIZE > NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
            or not 1 <= species_id <= 493
            or not 0 <= pid <= 0xFFFFFFFF
            or not 0 <= checksum <= 0xFFFF
        ):
            return False
        command = (
            f"PC_SEARCH_POKEMON|{pid:08X}|{checksum:04X}|{species_id:03X}|"
            f"{anchor_address:08X}\n"
        )
        transports = (transport,) if isinstance(transport, str) else transport
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                with self._pc_discovery_lock:
                    self._pc_pokemon_search = {
                        "scan_id": None,
                        "identity": identity,
                        "baseline_scan_id": baseline.get("scan_id") if baseline else None,
                        "baseline_addresses": list(
                            baseline.get("identity_addresses", []) if baseline else []
                        ),
                        "anchor_address": anchor_address,
                        "status": "queued",
                        "bytes_scanned": 0,
                        "total_bytes": NDS_MAIN_RAM_SIZE,
                        "matches": {},
                        "source": route,
                        "started_at": time.monotonic(),
                    }
                    self._pc_discovery_progress = {
                        "status": "queued",
                        "mode": "pokemon_search",
                        "progress_percent": 0,
                        "anchor_address": f"0x{anchor_address:08X}",
                        "source": route,
                    }
                LOGGER.info(
                    "Pokemon identity search dispatched via %s: pid=0x%08X "
                    "checksum=0x%04X species=%d anchor=0x%08X",
                    route,
                    pid,
                    checksum,
                    species_id,
                    anchor_address,
                )
                return True
        LOGGER.error("Pokemon identity search dispatch failed.")
        return False

    def request_pc_structure_pointer_search(
        self,
        header_address: int,
        first_record_address: int,
        *,
        transport: str | tuple[str, ...] = "tcp",
    ) -> bool:
        if (
            isinstance(header_address, bool)
            or not isinstance(header_address, int)
            or isinstance(first_record_address, bool)
            or not isinstance(first_record_address, int)
            or first_record_address - header_address != 0x28
            or header_address < NDS_MAIN_RAM_BASE
            or first_record_address + PC_BOX_RECORD_SIZE
            > NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
        ):
            return False
        with self._pc_discovery_lock:
            if (
                self._pc_discovery_scan is not None
                or self._pc_pokemon_search is not None
                or self._pc_structure_pointer_search is not None
                or self._pc_discovery_progress.get("status") in {
                    "queued", "scanning", "stalled", "retrying", "analyzing", "cancel-requested"
                }
            ):
                LOGGER.warning("Structure pointer search rejected while another PC diagnostic is active.")
                return False
        command = (
            f"PC_SEARCH_PC_POINTERS|{header_address:08X}|"
            f"{first_record_address:08X}\n"
        )
        transports = (transport,) if isinstance(transport, str) else transport
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                with self._pc_discovery_lock:
                    self._pc_structure_pointer_search = {
                        "scan_id": None,
                        "header_address": header_address,
                        "first_record_address": first_record_address,
                        "status": "queued",
                        "bytes_scanned": 0,
                        "reference_count": 0,
                        "references": [],
                        "source": route,
                        "started_at": time.monotonic(),
                    }
                    self._pc_discovery_progress = {
                        "status": "queued",
                        "mode": "structure_pointer_search",
                        "progress_percent": 0,
                        "anchor_address": f"0x{first_record_address:08X}",
                        "source": route,
                    }
                LOGGER.info(
                    "PC structure pointer search dispatched via %s: header=0x%08X "
                    "first_record=0x%08X",
                    route,
                    header_address,
                    first_record_address,
                )
                return True
        LOGGER.error("PC structure pointer search dispatch failed.")
        return False

    def reset_pc_pokemon_inspection_baseline(self) -> None:
        with self._pc_discovery_lock:
            self._pc_inspection_baseline = None

    def request_pc_storage_discovery_cancel(
        self, *, transport: str | tuple[str, ...] = "tcp"
    ) -> bool:
        with self._pc_discovery_lock:
            if self._pc_discovery_progress.get("status") not in {
                "queued",
                "scanning",
                "stalled",
                "retrying",
                "analyzing",
                "cancel-requested",
            }:
                return False
        transports = (transport,) if isinstance(transport, str) else transport
        command = "PC_CANCEL_DISCOVERY\n"
        for route in transports:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                with self._pc_discovery_lock:
                    self._pc_discovery_progress = {
                        **self._pc_discovery_progress,
                        "status": "cancel-requested",
                        "source": route,
                    }
                LOGGER.info("PC discovery cancellation dispatched via %s", route)
                return True
        LOGGER.error("PC discovery cancellation dispatch failed.")
        return False

    def is_connected(self) -> bool:
        heartbeat = self.latest_heartbeat()
        if heartbeat is None:
            return False
        return time.monotonic() - heartbeat.received_at <= self.stale_after_seconds

    def _serve_tcp(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                self._server_socket = server
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.host, self.port))
                server.listen(1)
                server.settimeout(0.5)
                LOGGER.info("BizHawk RAM server listening on %s:%s", self.host, self.port)
                while not self._stop_event.is_set():
                    try:
                        client, address = server.accept()
                    except TimeoutError:
                        continue
                    except OSError:
                        break
                    thread = threading.Thread(
                        target=self._handle_client,
                        args=(client, address),
                        name="bizhawk-ram-client",
                        daemon=True,
                    )
                    self._client_threads.append(thread)
                    thread.start()
        except OSError:
            LOGGER.exception("BizHawk RAM TCP server failed.")
        finally:
            self._server_socket = None

    def _handle_client(self, client: socket.socket, address: tuple[str, int]) -> None:
        LOGGER.info("BizHawk RAM Lua client connected from %s:%s", *address)
        with self._client_lock:
            previous = self._active_client
            self._active_client = client
        if previous is not None and previous is not client:
            try:
                previous.close()
            except OSError:
                pass
        with client:
            file = client.makefile("r", encoding="utf-8", newline="\n")
            while not self._stop_event.is_set():
                try:
                    line = file.readline()
                except OSError:
                    break
                if not line:
                    break
                self._record_line(line, "tcp")
        with self._client_lock:
            if self._active_client is client:
                self._active_client = None

    def _poll_fallback_file(self) -> None:
        while not self._stop_event.wait(0.1):
            try:
                self._poll_log_file(self.fallback_file)
                self._poll_coordinate_file()
                self._flush_command_file_queue()
                self._check_pc_discovery_retry_timeout()
                self._check_pc_cache_confirmation_timeout()
                self._check_pc_save_offset_test_timeout()
                self._check_pc_sram_search_timeout()
            except OSError:
                LOGGER.exception("Could not poll BizHawk fallback file.")

    def _handle_fallback_file_reset(
        self,
        old_size: int,
        new_size: int,
        reason: str,
        initiator: str,
    ) -> None:
        with self._pc_sram_search_lock:
            sram_active = self._pc_sram_search_active
        with self._pc_discovery_lock:
            accumulator = self._pc_discovery_scan
            search = self._pc_pokemon_search
            pointer_search = self._pc_structure_pointer_search
            active_scan_id = (
                accumulator.scan_id
                if accumulator is not None
                else search.get("scan_id")
                if search is not None
                else pointer_search.get("scan_id")
                if pointer_search is not None
                else sram_active.get("scan_id")
                if sram_active is not None
                else None
            )
            discovery_active = (
                accumulator is not None
                or search is not None
                or pointer_search is not None
                or sram_active is not None
            )
            LOGGER.error(
                "BizHawk output transport reset: old_size=%d new_size=%d reason=%s "
                "initiator=%s discovery_active=%s active_scan_id=%s",
                old_size,
                new_size,
                reason,
                initiator,
                discovery_active,
                active_scan_id,
            )
            if accumulator is not None:
                error = (
                    "Discovery transport reset during active scan "
                    f"(old_size={old_size}, new_size={new_size}, reason={reason}, "
                    f"initiator={initiator})."
                )
                self._fail_active_pc_discovery_locked(
                    accumulator, "file", error
                )
            elif search is not None:
                self._fail_pc_pokemon_search_locked(
                    search,
                    "Discovery transport reset during active scan "
                    f"(old_size={old_size}, new_size={new_size}, reason={reason}, "
                    f"initiator={initiator}).",
                )
            elif pointer_search is not None:
                self._fail_pc_structure_pointer_search_locked(
                    pointer_search,
                    "Discovery transport reset during active scan "
                    f"(old_size={old_size}, new_size={new_size}, reason={reason}, "
                    f"initiator={initiator}).",
                )
        if sram_active is not None:
            self._fail_pc_sram_search(
                "Discovery transport reset during active SRAM scan "
                f"(old_size={old_size}, new_size={new_size}, reason={reason}, "
                f"initiator={initiator}).",
                "file",
                scan_id=sram_active.get("scan_id"),
            )

    def _poll_log_file(self, path: Path) -> None:
        if not path.exists():
            if self._file_position is not None and self._file_position > 0:
                old_size = self._file_size_seen or self._file_position
                self._handle_fallback_file_reset(
                    old_size,
                    0,
                    "output file disappeared",
                    "external or unknown writer",
                )
                self._file_position = 0
                self._file_size_seen = 0
            return
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if self._file_position is None:
                self._file_size_seen = size
                if size == 0:
                    self._file_position = 0
                    return
                handle.seek(size - 1)
                if handle.read(1) == b"\n":
                    self._file_position = size
                    return
                boundary = size
                partial_start = 0
                while boundary > 0:
                    block_start = max(0, boundary - 4096)
                    handle.seek(block_start)
                    block = handle.read(boundary - block_start)
                    last_newline = block.rfind(b"\n")
                    if last_newline >= 0:
                        partial_start = block_start + last_newline + 1
                        break
                    boundary = block_start
                self._file_position = partial_start
                LOGGER.info(
                    "BizHawk fallback reader started at byte %d to retain a trailing partial line.",
                    partial_start,
                )
                return
            old_size = self._file_size_seen or self._file_position
            if size < self._file_position or size < old_size:
                self._handle_fallback_file_reset(
                    old_size,
                    size,
                    "output file was truncated or rotated",
                    "external or unknown writer",
                )
                self._file_position = 0
            handle.seek(self._file_position)
            next_position = self._file_position
            while True:
                line_start = handle.tell()
                raw_line = handle.readline()
                if not raw_line:
                    next_position = handle.tell()
                    break
                if not raw_line.endswith(b"\n"):
                    handle.seek(line_start)
                    next_position = line_start
                    break
                next_position = handle.tell()
                self._record_line(
                    raw_line.decode("utf-8", errors="replace").rstrip("\r\n"),
                    "file",
                )
            self._file_position = next_position
            self._file_size_seen = size

    def _poll_coordinate_file(self) -> None:
        with self._coordinate_file_lock:
            capture_id = self._coordinate_file_capture_id
            position = self._coordinate_file_position
        if capture_id is None or not self.coordinate_file.exists():
            return
        size = self.coordinate_file.stat().st_size
        if size < position:
            position = 0
        with self.coordinate_file.open("rb") as handle:
            handle.seek(position)
            data = handle.read()
        complete_length = data.rfind(b"\n") + 1
        if complete_length == 0:
            return
        text = data[:complete_length].decode("utf-8", errors="replace")
        completed = False
        for line in text.splitlines():
            self._record_line(line, "file")
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                event.get("type") in {"coordinate_scan_end", "coordinate_scan_error"}
                and event.get("capture_id") == capture_id
            ):
                completed = True
        with self._coordinate_file_lock:
            if self._coordinate_file_capture_id == capture_id:
                self._coordinate_file_position = position + complete_length
                if completed:
                    self._coordinate_file_capture_id = None

    def _record_line(self, line: str, source: str) -> None:
        raw_line = line.rstrip("\r\n")
        line = raw_line.strip()
        if not line:
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            LOGGER.warning(
                "Invalid BizHawk JSON line: source=%s raw_line_length=%d "
                "json_error=%s char_offset=%d prefix=%r suffix=%r",
                source,
                len(raw_line),
                exc.msg,
                exc.pos,
                raw_line[:200],
                raw_line[-200:],
            )
            if "pc_discovery_" in line:
                with self._pc_discovery_lock:
                    active = self._pc_discovery_scan
                    if active is not None and active.scan_id in line:
                        chunk_index = (
                            self._raw_json_integer(line, "chunk_index")
                            if "pc_discovery_chunk" in line
                            else None
                        )
                        if chunk_index is not None:
                            active.retry_in_flight.discard(chunk_index)
                        if chunk_index is not None and self._request_pc_discovery_chunk_retry(
                            active, chunk_index, source
                        ):
                            self._pc_discovery_progress = {
                                **active.progress(),
                                "status": "retrying",
                                "missing_chunk_index": chunk_index,
                                "retry_reason": "malformed_chunk",
                            }
                        else:
                            if chunk_index is not None:
                                self._fail_missing_pc_discovery_chunk_locked(
                                    active,
                                    chunk_index,
                                    "malformed JSON persisted after retries "
                                    f"({exc.msg} at character {exc.pos})",
                                )
                            else:
                                error = (
                                    "Discovery transport error: malformed JSON for an active "
                                    f"scan event; {exc.msg} at character {exc.pos}."
                                )
                                self._fail_active_pc_discovery_locked(
                                    active, source, error
                                )
            elif "pc_pokemon_search_" in line:
                with self._pc_discovery_lock:
                    search = self._pc_pokemon_search
                    if (
                        search is not None
                        and isinstance(search.get("scan_id"), str)
                        and search["scan_id"] in line
                    ):
                        self._fail_pc_pokemon_search_locked(
                            search,
                            "Discovery transport error: malformed identity-search event; "
                            f"{exc.msg} at character {exc.pos}.",
                            source,
                        )
            elif "pc_save_offset_test_" in line:
                with self._pc_save_test_lock:
                    active = self._pc_save_offset_test_active
                    if active is not None and active["test_id"] in line:
                        self._fail_pc_save_offset_test_locked(
                            active["test_id"],
                            "Save-offset transport error: malformed JSON packet; "
                            f"{exc.msg} at character {exc.pos}.",
                            source,
                        )
            elif "pc_sram_search_" in line:
                with self._pc_sram_search_lock:
                    active = self._pc_sram_search_active
                    if active is not None and active["scan_id"] in line:
                        self._fail_pc_sram_search_locked(
                            active,
                            "Live SRAM transport error: malformed JSON packet; "
                            f"{exc.msg} at character {exc.pos}.",
                            source,
                        )
                    elif self._pc_sram_search_pending is not None:
                        error = (
                            "Live SRAM transport error: malformed JSON before the scan "
                            f"started; {exc.msg} at character {exc.pos}."
                        )
                        self._pc_sram_search_pending = None
                        self._pc_sram_search_status = {
                            "status": "failed",
                            "error": error,
                        }
                        self._store_pc_sram_search_result(
                            {"type": "pc_sram_search_result", "error": error}, source
                        )
            return
        if not isinstance(payload, dict):
            return
        message_type = payload.get("type")
        if message_type == "transport_file_reset":
            try:
                old_size = int(payload.get("old_size") or 0)
                new_size = int(payload.get("new_size") or 0)
            except (TypeError, ValueError):
                old_size = 0
                new_size = 0
            self._handle_fallback_file_reset(
                old_size,
                new_size,
                str(payload.get("reason") or "reset marker received"),
                str(payload.get("initiator") or "Lua fallback writer"),
            )
            return
        if isinstance(message_type, str) and message_type.startswith("coordinate_scan_"):
            if message_type in {"coordinate_scan_end", "coordinate_scan_error"}:
                with self._coordinate_file_lock:
                    if payload.get("capture_id") == self._coordinate_file_capture_id:
                        self._coordinate_file_capture_id = None
            try:
                self._coordinate_events.put_nowait(payload)
            except queue.Full:
                LOGGER.warning("Dropping coordinate discovery event because its queue is full.")
            return
        if isinstance(message_type, str) and message_type.startswith(
            "pc_save_offset_test_"
        ):
            self._record_pc_save_offset_test_event(payload, source)
            return
        if isinstance(message_type, str) and message_type.startswith("pc_sram_search_"):
            self._record_pc_sram_search_event(payload, source)
            return
        if message_type in {
            "pc_discovery_started",
            "pc_discovery_chunk",
            "pc_discovery_complete",
            "pc_discovery_end",
            "pc_discovery_cancelled",
            "pc_discovery_failed",
            "pc_session_pc_resolver_cache_result",
            "pc_storage_cache_ready",
            "pc_storage_cache_rejected",
        }:
            self._record_pc_discovery_event(payload, source)
            return
        if isinstance(message_type, str) and message_type.startswith("pc_pokemon_search_"):
            self._record_pc_pokemon_search_event(payload, source)
            return
        if isinstance(message_type, str) and message_type.startswith(
            "pc_structure_pointer_search_"
        ):
            self._record_pc_structure_pointer_search_event(payload, source)
            return
        with self._lock:
            received = BizHawkHeartbeat(
                received_at=time.monotonic(),
                payload=payload,
                source=source,
                bytes_received=len(line.encode("utf-8")),
            )
            if message_type == "heartbeat":
                self._heartbeat = received
            elif message_type == "party_memory":
                self._party_payload = received
            elif message_type == "pc_storage":
                self._pc_storage_payload = received
                if (
                    payload.get("pc_storage_discovery_requested") is True
                    or payload.get("pc_storage_anchor_result") is True
                ):
                    self._pc_storage_discovery_payload = received
                    LOGGER.info(
                        "Python received PC discovery result: frame=%s requested=%s "
                        "anchor_result=%s anchor=%s",
                        payload.get("frame"),
                        payload.get("pc_storage_discovery_requested"),
                        payload.get("pc_storage_anchor_result"),
                        payload.get("pc_storage_anchor_address"),
                    )
        if message_type == "pc_storage":
            self._confirm_pc_cache_from_storage(payload, source)

    def _confirm_pc_cache_from_storage(self, payload: dict[str, Any], source: str) -> None:
        with self._pc_discovery_lock:
            pending = self._pc_cache_install
            if pending is None or pending["confirmation_received"]:
                return
            expected_run = pending["lua_run_id"]
            expected_address = pending["address"]
        if not (
            payload.get("lua_run_id") == expected_run
            and payload.get("session_pc_first_record_address") == expected_address
            and payload.get("pc_storage_resolver_kind") == "session_pc_cache"
            and payload.get("scan_ok") is True
            and payload.get("pc_storage_acquisition_enabled") is True
            and payload.get("malformed_records") == 0
            and payload.get("occupied_records", 0) + payload.get("empty_records", 0) == 540
        ):
            return
        LOGGER.info(
            "Lua production PC scan confirms cache: run=%s address=%s occupied=%s "
            "empty=%s invalid=0 (explicit ACK may be delayed)",
            expected_run, expected_address, payload.get("occupied_records"),
            payload.get("empty_records"),
        )
        self._record_session_pc_cache_result({
            "type": "pc_storage_cache_ready",
            "lua_run_id": expected_run,
            "session_pc_first_record_address": expected_address,
            "accepted": True,
            "occupied_records": payload.get("occupied_records"),
            "empty_records": payload.get("empty_records"),
            "malformed_records": 0,
            "confirmation_source": "validated pc_storage scan",
        }, source)

    def _record_pc_sram_search_event(
        self, payload: dict[str, Any], source: str
    ) -> None:
        message_type = payload.get("type")
        scan_id = payload.get("scan_id")
        if message_type == "pc_sram_search_started":
            try:
                total_bytes = int(payload.get("domain_size"))
                chunk_bytes = int(payload.get("chunk_bytes"))
                chunk_count = int(payload.get("chunk_count"))
            except (TypeError, ValueError):
                self._fail_pc_sram_search(
                    "Lua start packet contains invalid SRAM dimensions.", source
                )
                return
            domain = payload.get("domain")
            lua_run_id = payload.get("lua_run_id") or payload.get("run_id")
            expected_count = (total_bytes + chunk_bytes - 1) // chunk_bytes if chunk_bytes else 0
            if (
                not isinstance(scan_id, str)
                or not scan_id
                or not isinstance(domain, str)
                or "sram" not in domain.casefold()
                or not isinstance(lua_run_id, str)
                or not 0 < total_bytes <= PC_SRAM_SEARCH_MAX_BYTES
                or chunk_bytes != PC_SRAM_SEARCH_CHUNK_MAX
                or chunk_count != expected_count
            ):
                self._fail_pc_sram_search(
                    "Lua start packet failed SRAM domain/dimension validation.", source
                )
                return
            with self._pc_sram_search_lock:
                pending = self._pc_sram_search_pending
                if pending is None:
                    LOGGER.warning("Ignoring unsolicited live SRAM search start: %s", scan_id)
                    return
                for baseline_key in tuple(self._pc_sram_search_baselines):
                    if baseline_key[0] != lua_run_id:
                        del self._pc_sram_search_baselines[baseline_key]
                self._pc_sram_search_active = {
                    "scan_id": scan_id,
                    "domain": domain,
                    "total_bytes": total_bytes,
                    "chunk_bytes": chunk_bytes,
                    "chunk_count": chunk_count,
                    "received_chunks": set(),
                    "image": bytearray(total_bytes),
                    "bytes_received": 0,
                    "last_chunk_at": time.monotonic(),
                    "started_at": time.monotonic(),
                    "lua_run_id": lua_run_id,
                    "lua_build_id": payload.get("lua_build_id"),
                    "domains": payload.get("domains", []),
                    "eligible_save_copy_count": payload.get("eligible_save_copy_count", 0),
                    "expected_nickname": pending["expected_nickname"],
                    "expected_species_id": pending["expected_species_id"],
                    "source": source,
                    "finalizing": False,
                }
                self._pc_sram_search_status = {
                    "status": "scanning",
                    "scan_id": scan_id,
                    "domain": domain,
                    "bytes_received": 0,
                    "total_bytes": total_bytes,
                    "chunks_received": 0,
                    "chunk_count": chunk_count,
                    "progress_percent": 0,
                    "source": source,
                    "last_chunk_at": time.monotonic(),
                }
                LOGGER.info(
                    "Live SRAM search started: scan_id=%s domain=%s size=0x%X chunks=%d "
                    "target=%s/%d lua=%s",
                    scan_id,
                    domain,
                    total_bytes,
                    chunk_count,
                    pending["expected_nickname"],
                    pending["expected_species_id"],
                    lua_run_id,
                )
            return

        if not isinstance(scan_id, str):
            return
        if message_type == "pc_sram_search_failed":
            error = str(payload.get("error") or "Lua reported a live SRAM search failure.")
            self._fail_pc_sram_search(error, source, scan_id=scan_id)
            return
        if message_type == "pc_sram_search_chunk":
            try:
                chunk_index = int(payload.get("chunk_index"))
                offset = int(payload.get("offset"))
                byte_count = int(payload.get("byte_count"))
                encoded = payload.get("data")
                if payload.get("data_encoding") != "hex" or not isinstance(encoded, str):
                    raise ValueError("chunk is not JSON-safe hexadecimal data")
                chunk = bytes.fromhex(encoded)
            except (TypeError, ValueError) as exc:
                self._fail_pc_sram_search(
                    f"Invalid SRAM chunk packet: {exc}", source, scan_id=scan_id
                )
                return
            with self._pc_sram_search_lock:
                active = self._pc_sram_search_active
                if active is None or active["scan_id"] != scan_id or active["finalizing"]:
                    return
                if payload.get("lua_run_id") not in {None, active["lua_run_id"]}:
                    self._fail_pc_sram_search_locked(
                        active,
                        "Lua run ID changed during the live SRAM transfer.",
                        source,
                    )
                    return
                expected_offset = chunk_index * active["chunk_bytes"]
                expected_count = min(
                    active["chunk_bytes"], active["total_bytes"] - expected_offset
                )
                if (
                    chunk_index < 0
                    or chunk_index >= active["chunk_count"]
                    or offset != expected_offset
                    or byte_count != expected_count
                    or len(chunk) != byte_count
                    or byte_count <= 0
                    or byte_count > PC_SRAM_SEARCH_CHUNK_MAX
                ):
                    self._fail_pc_sram_search_locked(
                        active,
                        f"Invalid SRAM chunk {chunk_index}: offset/length does not match the scan.",
                        source,
                    )
                    return
                if chunk_index in active["received_chunks"]:
                    existing = bytes(active["image"][offset : offset + byte_count])
                    if existing != chunk:
                        self._fail_pc_sram_search_locked(
                            active,
                            f"Duplicate SRAM chunk {chunk_index} contained different bytes.",
                            source,
                        )
                        return
                else:
                    active["image"][offset : offset + byte_count] = chunk
                    active["received_chunks"].add(chunk_index)
                    active["bytes_received"] += byte_count
                active["last_chunk_at"] = time.monotonic()
                received = active["bytes_received"]
                total = active["total_bytes"]
                count = len(active["received_chunks"])
                chunks = active["chunk_count"]
                self._pc_sram_search_status = {
                    **self._pc_sram_search_status,
                    "status": "scanning",
                    "bytes_received": received,
                    "total_bytes": total,
                    "chunks_received": count,
                    "chunk_count": chunks,
                    "progress_percent": min(100, received * 100 // total),
                    "last_chunk_at": active["last_chunk_at"],
                }
            return

        if message_type == "pc_sram_search_complete":
            with self._pc_sram_search_lock:
                active = self._pc_sram_search_active
                if active is None or active["scan_id"] != scan_id or active["finalizing"]:
                    return
                if payload.get("lua_run_id") not in {None, active["lua_run_id"]}:
                    self._fail_pc_sram_search_locked(
                        active,
                        "Lua run ID changed before the live SRAM scan completed.",
                        source,
                    )
                    return
                try:
                    end_chunks = int(payload.get("chunk_count"))
                    end_bytes = int(payload.get("total_bytes"))
                    bytes_scanned = int(payload.get("bytes_scanned"))
                except (TypeError, ValueError):
                    self._fail_pc_sram_search_locked(
                        active, "Lua end packet contains invalid scan dimensions.", source
                    )
                    return
                missing = sorted(set(range(active["chunk_count"])) - active["received_chunks"])
                if (
                    end_chunks != active["chunk_count"]
                    or end_bytes != active["total_bytes"]
                    or bytes_scanned != active["total_bytes"]
                    or missing
                    or active["bytes_received"] != active["total_bytes"]
                ):
                    missing_text = ", ".join(str(index) for index in missing[:12]) or "none"
                    self._fail_pc_sram_search_locked(
                        active,
                        "Incomplete live SRAM transfer: "
                        f"received {len(active['received_chunks'])}/{active['chunk_count']} chunks, "
                        f"missing indexes {missing_text}.",
                        source,
                    )
                    return
                active["finalizing"] = True
                image = bytes(active["image"])
                run_id = active["lua_run_id"]
                domain = active["domain"]
                identity = (
                    run_id,
                    domain,
                    active["expected_species_id"],
                    active["expected_nickname"].casefold(),
                )
                prior = self._pc_sram_search_baselines.get(identity)
                self._pc_sram_search_status = {
                    **self._pc_sram_search_status,
                    "status": "analyzing",
                    "progress_percent": 100,
                }
                analysis_context = dict(active)

            try:
                result = analyze_sram_snapshot(
                    image,
                    expected_nickname=analysis_context["expected_nickname"],
                    expected_species_id=analysis_context["expected_species_id"],
                    domain=domain,
                    lua_run_id=run_id,
                    scan_id=scan_id,
                    prior=prior,
                )
            except Exception as exc:
                LOGGER.exception("Live SRAM Pokemon analysis failed for scan %s.", scan_id)
                self._fail_pc_sram_search(
                    f"Python could not analyze the live SRAM snapshot: {type(exc).__name__}: {exc}",
                    source,
                    scan_id=scan_id,
                )
                return

            result["lua_build_id"] = analysis_context.get("lua_build_id")
            result["available_domains"] = analysis_context.get("domains", [])
            result["eligible_save_copy_count"] = analysis_context.get(
                "eligible_save_copy_count", 0
            )
            result["source"] = source
            result["chunk_count"] = analysis_context["chunk_count"]
            result["transfer_bytes"] = analysis_context["bytes_received"]
            result["elapsed_seconds"] = round(
                time.monotonic() - analysis_context["started_at"], 3
            )
            with self._pc_sram_search_lock:
                self._pc_sram_search_baselines[identity] = {
                    "image": image,
                    "target_offsets": [item["offset"] for item in result["target_matches"]],
                    "target_matches": [
                        {
                            "offset": item["offset"],
                            "pid": item["pid"],
                            "checksum": item["checksum"],
                            "checksum_valid": item["checksum_valid"],
                        }
                        for item in result["target_matches"]
                    ],
                    "lua_run_id": run_id,
                    "domain": domain,
                    "capture_number": result["capture_number"],
                }
                self._pc_sram_search_active = None
                self._pc_sram_search_pending = None
                self._pc_sram_search_status = {
                    "status": "completed",
                    "scan_id": scan_id,
                    "domain": domain,
                    "bytes_received": len(image),
                    "total_bytes": len(image),
                    "chunks_received": analysis_context["chunk_count"],
                    "chunk_count": analysis_context["chunk_count"],
                    "progress_percent": 100,
                    "target_match_count": result["target_match_count"],
                    "capture_number": result["capture_number"],
                    "source": source,
                }
            self._store_pc_sram_search_result(result, source)
            LOGGER.info(
                "Live SRAM Pokemon search complete: scan_id=%s valid_records=%d "
                "target_matches=%d layouts=%d capture=%d",
                scan_id,
                result["checksum_valid_record_count"],
                result["target_match_count"],
                len(result["pc_layouts"]),
                result["capture_number"],
            )

    def _check_pc_sram_search_timeout(self) -> None:
        with self._pc_sram_search_lock:
            active = self._pc_sram_search_active
            if active is None or active["finalizing"]:
                return
            elapsed = time.monotonic() - active["last_chunk_at"]
            if elapsed <= PC_SRAM_SEARCH_TIMEOUT_SECONDS:
                return
            self._fail_pc_sram_search_locked(
                active,
                f"No SRAM chunk arrived for {elapsed:.1f}s; transfer timed out.",
                active.get("source", "unknown"),
            )

    def _fail_pc_sram_search(
        self, message: str, source: str, *, scan_id: str | None = None
    ) -> None:
        with self._pc_sram_search_lock:
            active = self._pc_sram_search_active
            if active is not None and (scan_id is None or active["scan_id"] == scan_id):
                self._fail_pc_sram_search_locked(active, message, source)
                return
            self._pc_sram_search_pending = None
            self._pc_sram_search_status = {"status": "failed", "error": message}
            result = {"type": "pc_sram_search_result", "error": message}
        self._store_pc_sram_search_result(result, source)

    def _fail_pc_sram_search_locked(
        self, active: dict[str, Any], message: str, source: str
    ) -> None:
        if self._pc_sram_search_active is active:
            self._pc_sram_search_active = None
        self._pc_sram_search_pending = None
        self._pc_sram_search_status = {
            "status": "failed",
            "scan_id": active.get("scan_id"),
            "error": message,
            "bytes_received": active.get("bytes_received", 0),
            "total_bytes": active.get("total_bytes", 0),
            "chunks_received": len(active.get("received_chunks", ())),
            "chunk_count": active.get("chunk_count"),
            "source": source,
        }
        self._store_pc_sram_search_result(
            {
                "type": "pc_sram_search_result",
                "scan_id": active.get("scan_id"),
                "domain": active.get("domain"),
                "error": message,
                "expected_identity": {
                    "nickname": active.get("expected_nickname"),
                    "species_id": active.get("expected_species_id"),
                },
            },
            source,
        )
        LOGGER.error("Live SRAM Pokemon search failed: scan=%s: %s", active.get("scan_id"), message)

    def _store_pc_sram_search_result(
        self, payload: dict[str, Any], source: str
    ) -> None:
        line = json.dumps(payload, separators=(",", ":"))
        received = BizHawkHeartbeat(
            received_at=time.monotonic(),
            payload=payload,
            source=source,
            bytes_received=len(line.encode("utf-8")),
        )
        with self._lock:
            self._pc_sram_search_payload = received

    def _record_pc_save_offset_test_event(
        self, payload: dict[str, Any], source: str
    ) -> None:
        message_type = payload.get("type")
        test_id = payload.get("test_id")
        if message_type == "pc_save_offset_test_file_capture":
            self._record_pc_save_offset_test_file_capture(payload, source)
            return
        if message_type == "pc_save_offset_test_started":
            if not isinstance(test_id, str) or not test_id:
                self._fail_pc_save_offset_test(
                    None, "Lua started a save-offset test without a test ID.", source
                )
                return
            raw_targets = payload.get("targets")
            raw_domains = payload.get("domains")
            if not isinstance(raw_targets, list) or not isinstance(raw_domains, list):
                self._fail_pc_save_offset_test(
                    test_id, "Lua start packet omitted domain or target metadata.", source
                )
                return
            try:
                segment_bytes = int(payload.get("segment_bytes"))
                expected_segments = int(payload.get("segment_count"))
                total_bytes = int(payload.get("total_bytes"))
            except (TypeError, ValueError):
                self._fail_pc_save_offset_test(
                    test_id, "Lua start packet contains invalid segment dimensions.", source
                )
                return
            if (
                segment_bytes != PC_SAVE_SEGMENT_BYTES
                or expected_segments != len(raw_targets)
                or total_bytes != expected_segments * PC_SAVE_SEGMENT_BYTES
            ):
                self._fail_pc_save_offset_test(
                    test_id, "Lua start packet has inconsistent PC save dimensions.", source
                )
                return

            targets: dict[tuple[str, int], dict[str, Any]] = {}
            for raw_target in raw_targets:
                if not isinstance(raw_target, dict):
                    self._fail_pc_save_offset_test(
                        test_id, "Lua start packet contains an invalid save target.", source
                    )
                    return
                domain = raw_target.get("domain")
                record_offset = self._event_address(raw_target.get("record_offset"))
                segment_offset = self._event_address(raw_target.get("segment_offset"))
                try:
                    target_size = int(raw_target.get("segment_bytes"))
                except (TypeError, ValueError):
                    target_size = -1
                if (
                    not isinstance(domain, str)
                    or record_offset not in PC_SAVE_COPY_RECORD_OFFSETS
                    or segment_offset != record_offset - PC_SAVE_SEGMENT_PREFIX_BYTES
                    or target_size != PC_SAVE_SEGMENT_BYTES
                ):
                    self._fail_pc_save_offset_test(
                        test_id, "Lua start packet contains an unsafe save target.", source
                    )
                    return
                key = (domain, record_offset)
                if key in targets:
                    self._fail_pc_save_offset_test(
                        test_id, "Lua start packet duplicated a save target.", source
                    )
                    return
                targets[key] = {
                    "domain": domain,
                    "record_offset": record_offset,
                    "domain_size": raw_target.get("domain_size"),
                    "segment_offset": segment_offset,
                    "data": bytearray(PC_SAVE_SEGMENT_BYTES),
                    "chunks": {},
                }

            with self._pc_save_test_lock:
                if self._pc_save_offset_test_active is not None:
                    LOGGER.warning(
                        "Ignoring overlapping PC save-offset test start: test=%s active=%s",
                        test_id,
                        self._pc_save_offset_test_active.get("test_id"),
                    )
                    return
                self._pc_save_offset_test_active = {
                    "test_id": test_id,
                    "domains": raw_domains,
                    "targets": targets,
                    "expected_segments": expected_segments,
                    "total_bytes": total_bytes,
                    "bytes_received": 0,
                    "source": source,
                    "metadata": {
                        key: payload.get(key)
                        for key in (
                            "lua_build_id",
                            "lua_game_id",
                            "lua_game_version",
                            "lua_script_source",
                            "lua_run_id",
                            "frame",
                        )
                    },
                    "started_at": time.monotonic(),
                    "last_event_at": time.monotonic(),
                }
                self._pc_save_offset_test_status = {
                    "test_id": test_id,
                    "status": "scanning",
                    "source": source,
                    "bytes_received": 0,
                    "total_bytes": total_bytes,
                    "segments_received": 0,
                    "segment_count": expected_segments,
                }
            LOGGER.info(
                "Python received PC save-offset test start: test=%s domains=%d "
                "segments=%d bytes=%d source=%s",
                test_id,
                len(raw_domains),
                expected_segments,
                total_bytes,
                source,
            )
            return

        if message_type == "pc_save_offset_test_failed":
            self._fail_pc_save_offset_test(
                test_id if isinstance(test_id, str) else None,
                str(payload.get("error") or "Lua reported a save-offset test failure."),
                source,
                lua_metadata=payload,
            )
            return

        if message_type == "pc_save_offset_test_chunk":
            with self._pc_save_test_lock:
                active = self._pc_save_offset_test_active
                if active is None or active.get("test_id") != test_id:
                    LOGGER.warning(
                        "Ignoring PC save-offset chunk for inactive test: test=%s",
                        test_id,
                    )
                    return
                domain = payload.get("domain")
                record_offset = self._event_address(payload.get("record_offset"))
                key = (domain, record_offset)
                target = active["targets"].get(key)
                try:
                    chunk_index = int(payload.get("chunk_index"))
                    segment_offset = int(payload.get("segment_offset"))
                    byte_count = int(payload.get("byte_count"))
                except (TypeError, ValueError):
                    target = None
                    chunk_index = segment_offset = byte_count = -1
                encoded = payload.get("data")
                if (
                    target is None
                    or payload.get("data_encoding") != "hex"
                    or not isinstance(encoded, str)
                    or byte_count <= 0
                    or byte_count > PC_SAVE_TEST_CHUNK_BYTES
                    or chunk_index < 0
                    or segment_offset != chunk_index * PC_SAVE_TEST_CHUNK_BYTES
                    or segment_offset + byte_count > PC_SAVE_SEGMENT_BYTES
                ):
                    error = "Lua sent a malformed PC save-offset data chunk."
                    self._fail_pc_save_offset_test_locked(test_id, error, source)
                    return
                expected_bytes = min(
                    PC_SAVE_TEST_CHUNK_BYTES,
                    PC_SAVE_SEGMENT_BYTES - segment_offset,
                )
                try:
                    chunk = bytes.fromhex(encoded)
                except ValueError:
                    chunk = b""
                if byte_count != expected_bytes or len(chunk) != byte_count:
                    self._fail_pc_save_offset_test_locked(
                        test_id,
                        f"Invalid save-offset chunk {chunk_index} length or hex data.",
                        source,
                    )
                    return
                previous = target["chunks"].get(chunk_index)
                if previous is not None:
                    if previous != chunk:
                        self._fail_pc_save_offset_test_locked(
                            test_id,
                            f"Conflicting duplicate save-offset chunk {chunk_index}.",
                            source,
                        )
                        return
                    return
                target["data"][segment_offset : segment_offset + byte_count] = chunk
                target["chunks"][chunk_index] = chunk
                active["bytes_received"] += byte_count
                active["last_event_at"] = time.monotonic()
                received_chunks = sum(
                    len(item["chunks"]) for item in active["targets"].values()
                )
                expected_chunks = sum(
                    (PC_SAVE_SEGMENT_BYTES + PC_SAVE_TEST_CHUNK_BYTES - 1)
                    // PC_SAVE_TEST_CHUNK_BYTES
                    for _ in active["targets"]
                )
                self._pc_save_offset_test_status = {
                    "test_id": test_id,
                    "status": "scanning",
                    "source": source,
                    "bytes_received": active["bytes_received"],
                    "total_bytes": active["total_bytes"],
                    "segments_received": sum(
                        len(item["chunks"])
                        == (PC_SAVE_SEGMENT_BYTES + PC_SAVE_TEST_CHUNK_BYTES - 1)
                        // PC_SAVE_TEST_CHUNK_BYTES
                        for item in active["targets"].values()
                    ),
                    "segment_count": active["expected_segments"],
                    "chunks_received": received_chunks,
                    "chunk_count": expected_chunks,
                }
            return

        if message_type == "pc_save_offset_test_end":
            with self._pc_save_test_lock:
                active = self._pc_save_offset_test_active
                if active is None or active.get("test_id") != test_id:
                    LOGGER.warning(
                        "Ignoring PC save-offset end packet for inactive test: test=%s",
                        test_id,
                    )
                    return
                try:
                    reported_segments = int(payload.get("segment_count"))
                    reported_bytes = int(payload.get("total_bytes"))
                    sent_bytes = int(payload.get("bytes_sent"))
                except (TypeError, ValueError):
                    reported_segments = reported_bytes = sent_bytes = -1
                missing = []
                for key, target in active["targets"].items():
                    expected_chunks = (
                        PC_SAVE_SEGMENT_BYTES + PC_SAVE_TEST_CHUNK_BYTES - 1
                    ) // PC_SAVE_TEST_CHUNK_BYTES
                    missing.extend(
                        (key, index)
                        for index in range(expected_chunks)
                        if index not in target["chunks"]
                    )
                if (
                    reported_segments != active["expected_segments"]
                    or reported_bytes != active["total_bytes"]
                    or sent_bytes != active["total_bytes"]
                    or missing
                ):
                    if missing:
                        key, index = missing[0]
                        error = (
                            "Save-offset capture incomplete: missing chunk "
                            f"{index} for {key[0]} at record offset 0x{key[1]:X}."
                        )
                    else:
                        error = "Lua end packet totals do not match received save data."
                    self._fail_pc_save_offset_test_locked(test_id, error, source)
                    return
                result = self._finish_pc_save_offset_test_locked(active)
                self._pc_save_offset_test_active = None
                self._pc_save_offset_test_status = {
                    "test_id": test_id,
                    "status": "completed",
                    "source": source,
                    "bytes_received": active["bytes_received"],
                    "total_bytes": active["total_bytes"],
                    "progress_percent": 100,
                    "segments_received": active["expected_segments"],
                    "segment_count": active["expected_segments"],
                }
                self._pc_save_offset_test_directory = None
                self._pc_save_offset_test_before_files = {}
                self._pc_save_offset_test_expected_identity = None
            self._store_pc_save_offset_test_result(result, source)
            LOGGER.info(
                "Python completed PC save-offset test: test=%s candidates=%d bytes=%d",
                test_id,
                len(result["candidates"]),
                sent_bytes,
            )

    def _record_pc_save_offset_test_file_capture(
        self, payload: dict[str, Any], source: str
    ) -> None:
        test_id = payload.get("test_id")
        if not isinstance(test_id, str) or not test_id:
            self._fail_pc_save_offset_test(
                None, "Lua requested a SaveRAM file capture without a test ID.", source,
                lua_metadata=payload,
            )
            return
        if payload.get("save_ram_api_available") is not True:
            self._fail_pc_save_offset_test(
                test_id,
                "No SaveRAM memory domain is available and client.saveram() is not exposed.",
                source,
                lua_metadata=payload,
            )
            return
        if payload.get("save_ram_flush_call_ok") is not True:
            self._fail_pc_save_offset_test(
                test_id,
                "client.saveram() failed: "
                + str(payload.get("save_ram_flush_error") or "unknown Lua error"),
                source,
                lua_metadata=payload,
            )
            return

        with self._pc_save_test_lock:
            directory = self._pc_save_offset_test_directory
            before_files = dict(self._pc_save_offset_test_before_files)
            expected_identity = self._pc_save_offset_test_expected_identity
        if directory is None:
            self._fail_pc_save_offset_test(
                test_id,
                "SaveRAM was flushed, but no BizHawk SaveRAM directory is configured in PC Storage Debug.",
                source,
                lua_metadata=payload,
            )
            return

        try:
            after_files = self._snapshot_save_ram_directory(directory)
        except OSError as exc:
            self._fail_pc_save_offset_test(
                test_id,
                f"Could not rescan BizHawk SaveRAM directory {directory}: {exc}",
                source,
                lua_metadata=payload,
            )
            return
        changes = self._save_ram_directory_changes(before_files, after_files)
        changed_after = [item for item in changes if item.get("after") is not None]
        save_like_changes = [
            item for item in changed_after
            if Path(str(item.get("path", ""))).suffix.casefold()
            in {".saveram", ".sav"}
        ]
        active_candidates = save_like_changes or changed_after
        capture_metadata = {
            "save_ram_directory": str(directory),
            "save_ram_directory_before": list(before_files.values()),
            "save_ram_directory_after": list(after_files.values()),
            "save_ram_file_changes": changes,
            "save_ram_changed_candidate_count": len(active_candidates),
            "save_ram_flush_semantics": payload.get(
                "save_ram_flush_semantics",
                "client.saveram() flushes emulator SaveRAM to disk; it does not perform an in-game save",
            ),
        }
        if len(active_candidates) != 1:
            if not active_candidates:
                error = (
                    "client.saveram() completed, but no file in the configured SaveRAM "
                    "directory changed; the active save file cannot be identified from this flush."
                )
            else:
                names = ", ".join(
                    str(item.get("filename", "?")) for item in active_candidates
                )
                error = (
                    "Multiple SaveRAM files changed during client.saveram(); refusing to "
                    f"guess which is active: {names}"
                )
            self._fail_pc_save_offset_test(
                test_id,
                error,
                source,
                lua_metadata=payload,
                capture_metadata=capture_metadata,
            )
            return

        changed_file = active_candidates[0]
        file_path = Path(str(changed_file["path"]))
        if not file_path.is_file():
            self._fail_pc_save_offset_test(
                test_id,
                f"The SaveRAM file changed during flush but is no longer present: {file_path}",
                source,
                lua_metadata=payload,
                capture_metadata=capture_metadata,
            )
            return
        try:
            save_image = file_path.read_bytes()
        except OSError as exc:
            self._fail_pc_save_offset_test(
                test_id,
                f"Could not read detected BizHawk SaveRAM file {file_path}: {exc}",
                source,
                lua_metadata=payload,
                capture_metadata=capture_metadata,
            )
            return
        if len(save_image) < PC_SAVE_FILE_MINIMUM_BYTES:
            self._fail_pc_save_offset_test(
                test_id,
                f"BizHawk SaveRAM file is too short ({len(save_image):,} bytes); "
                f"both offsets require at least {PC_SAVE_FILE_MINIMUM_BYTES:,} bytes.",
                source,
                lua_metadata=payload,
                capture_metadata=capture_metadata,
            )
            return

        targets: dict[tuple[str, int], dict[str, Any]] = {}
        for record_offset in PC_SAVE_COPY_RECORD_OFFSETS:
            segment_offset = record_offset - PC_SAVE_SEGMENT_PREFIX_BYTES
            segment = save_image[segment_offset : segment_offset + PC_SAVE_SEGMENT_BYTES]
            if len(segment) != PC_SAVE_SEGMENT_BYTES:
                self._fail_pc_save_offset_test(
                    test_id,
                    f"SaveRAM copy at 0x{record_offset:05X} extends past end of file.",
                    source,
                    lua_metadata=payload,
                )
                return
            key = (str(file_path), record_offset)
            targets[key] = {
                "domain": "BizHawk SaveRAM file",
                "source_file_path": str(file_path),
                "record_offset": record_offset,
                "domain_size": len(save_image),
                "segment_offset": segment_offset,
                "data": bytearray(segment),
                "chunks": {},
            }

        metadata = {
            key: payload.get(key)
            for key in (
                "lua_build_id",
                "lua_game_id",
                "lua_game_version",
                "lua_script_source",
                "lua_run_id",
                "lua_rom_name",
                "lua_rom_hash",
                "lua_rom_path",
                "lua_rom_path_api",
                "lua_system_id",
                "lua_configured_save_ram_path",
                "frame",
                "save_ram_api_available",
                "save_ram_flush_call_ok",
                "save_ram_flush_error",
                "save_ram_flush_semantics",
            )
        }
        metadata.update(capture_metadata)
        metadata["save_ram_file_path"] = str(file_path)
        metadata["save_ram_filename_stem"] = file_path.stem
        metadata["save_ram_detection_reason"] = changed_file["reason"]
        metadata["save_ram_image_sha256"] = hashlib.sha256(save_image).hexdigest()
        metadata["expected_box_1_slot_1"] = expected_identity
        active = {
            "test_id": test_id,
            "domains": payload.get("domains", []),
            "targets": targets,
            "expected_segments": len(targets),
            "total_bytes": len(targets) * PC_SAVE_SEGMENT_BYTES,
            "bytes_received": len(targets) * PC_SAVE_SEGMENT_BYTES,
            "source": source,
            "metadata": metadata,
            "save_ram_image": save_image,
            "started_at": time.monotonic(),
            "last_event_at": time.monotonic(),
        }
        with self._pc_save_test_lock:
            if self._pc_save_offset_test_active is not None:
                self._fail_pc_save_offset_test_locked(
                    test_id, "Another PC save-offset test is already active.", source,
                    lua_metadata=payload,
                )
                return
            result = self._finish_pc_save_offset_test_locked(active)
            self._pc_save_offset_test_active = None
            self._pc_save_offset_test_status = {
                "test_id": test_id,
                "status": "completed",
                "source": source,
                "bytes_received": active["bytes_received"],
                "total_bytes": active["total_bytes"],
                "segments_received": len(targets),
                "segment_count": len(targets),
            }
            self._pc_save_offset_test_directory = None
            self._pc_save_offset_test_before_files = {}
            self._pc_save_offset_test_expected_identity = None
        LOGGER.info(
            "Python read BizHawk SaveRAM file: test=%s path=%s bytes=%d copies=%d",
            test_id,
            file_path,
            len(save_image),
            len(targets),
        )
        self._store_pc_save_offset_test_result(result, source)

    def _check_pc_save_offset_test_timeout(self) -> None:
        with self._pc_save_test_lock:
            active = self._pc_save_offset_test_active
            if active is None:
                return
            idle_seconds = time.monotonic() - active.get(
                "last_event_at", active.get("started_at", time.monotonic())
            )
            if idle_seconds <= 10.0:
                return
            self._fail_pc_save_offset_test_locked(
                active["test_id"],
                f"Save-offset capture timed out after {idle_seconds:.1f}s without data.",
                active["source"],
            )

    def _fail_pc_save_offset_test(
        self,
        test_id: str | None,
        error: str,
        source: str,
        *,
        lua_metadata: dict[str, Any] | None = None,
        capture_metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._pc_save_test_lock:
            self._fail_pc_save_offset_test_locked(
                test_id,
                error,
                source,
                lua_metadata=lua_metadata,
                capture_metadata=capture_metadata,
            )

    def _fail_pc_save_offset_test_locked(
        self,
        test_id: str | None,
        error: str,
        source: str,
        *,
        lua_metadata: dict[str, Any] | None = None,
        capture_metadata: dict[str, Any] | None = None,
    ) -> None:
        active = self._pc_save_offset_test_active
        if active is not None and test_id is not None and active.get("test_id") != test_id:
            return
        metadata = dict(active.get("metadata", {})) if active is not None else {}
        if self._pc_save_offset_test_directory is not None:
            metadata.setdefault(
                "save_ram_directory", str(self._pc_save_offset_test_directory)
            )
            metadata.setdefault(
                "save_ram_directory_before",
                list(self._pc_save_offset_test_before_files.values()),
            )
        if self._pc_save_offset_test_expected_identity is not None:
            metadata.setdefault(
                "expected_box_1_slot_1",
                self._pc_save_offset_test_expected_identity,
            )
        if lua_metadata:
            metadata = {
                **metadata,
                **{
                    key: lua_metadata.get(key)
                    for key in (
                        "lua_build_id",
                        "lua_game_id",
                        "lua_game_version",
                        "lua_script_source",
                        "lua_run_id",
                        "lua_rom_name",
                        "lua_rom_hash",
                        "lua_rom_path",
                        "lua_rom_path_api",
                        "lua_system_id",
                        "lua_configured_save_ram_path",
                        "frame",
                        "save_ram_api_available",
                        "save_ram_flush_call_ok",
                        "save_ram_flush_error",
                        "save_ram_flush_semantics",
                    )
                },
            }
            if lua_metadata.get("domains") is not None:
                metadata["domains"] = lua_metadata.get("domains")
        if capture_metadata:
            metadata.update(capture_metadata)
        result = {
            "type": "pc_save_offset_test_result",
            "test_id": test_id or (active.get("test_id") if active else None),
            "error": error,
            "domains": active.get("domains", []) if active else metadata.get("domains", []),
            "candidates": [],
            "comparison": None,
            **metadata,
        }
        self._pc_save_offset_test_active = None
        self._pc_save_offset_test_directory = None
        self._pc_save_offset_test_before_files = {}
        self._pc_save_offset_test_expected_identity = None
        self._pc_save_offset_test_status = {
            "test_id": result["test_id"],
            "status": "failed",
            "error": error,
            "source": source,
        }
        self._store_pc_save_offset_test_result(result, source)
        LOGGER.error("PC save-offset test failed: test=%s error=%s", result["test_id"], error)

    def _finish_pc_save_offset_test_locked(
        self, active: dict[str, Any]
    ) -> dict[str, Any]:
        lua_run_id = active.get("metadata", {}).get("lua_run_id")
        if (
            self._pc_save_offset_test_baseline_run_id is not None
            and lua_run_id != self._pc_save_offset_test_baseline_run_id
        ):
            self._pc_save_offset_test_baseline = {}
            self._pc_save_offset_test_image_baseline = {}
        species_names = {
            species.national_dex_number: species.name
            for species in load_gen4_species()
        }
        candidates = []
        next_baseline: dict[tuple[str, int], bytes] = {}
        comparisons = []
        for key, target in active["targets"].items():
            data = bytes(target["data"])
            prior = self._pc_save_offset_test_baseline.get(key)
            for interpretation, header_index, records_index in (
                ("fixed offset is first Pokemon record", 0, 4),
                ("fixed offset is PCBoxes header", 4, 8),
            ):
                candidate = self._decode_pc_save_segment(
                    target,
                    data,
                    species_names,
                    interpretation=interpretation,
                    header_index=header_index,
                    records_index=records_index,
                )
                candidate["source_file_path"] = target.get("source_file_path")
                expected_identity = active.get("metadata", {}).get(
                    "expected_box_1_slot_1"
                )
                if isinstance(expected_identity, dict):
                    slot = candidate.get("box_1_slot_1", {})
                    candidate["expected_box_1_slot_1"] = expected_identity
                    candidate["expected_identity_match"] = bool(
                        slot.get("checksum_valid") is True
                        and slot.get("species_id") == expected_identity.get("species_id")
                        and isinstance(slot.get("nickname"), str)
                        and slot["nickname"].casefold()
                        == str(expected_identity.get("nickname", "")).casefold()
                    )
                if prior is None:
                    candidate["comparison_to_previous"] = "no previous capture for this target"
                    comparisons.append({
                        "domain": target.get("source_file_path") or key[0],
                        "record_offset": f"0x{key[1]:05X}",
                        "interpretation": interpretation,
                        "status": "first capture",
                        "changed_byte_count": None,
                        "changed_slots": [],
                    })
                else:
                    changed_byte_count = sum(
                        left != right for left, right in zip(prior, data)
                    )
                    changed_slots = [
                        {"box": index // 30 + 1, "slot": index % 30 + 1}
                        for index in range(540)
                        if prior[
                            records_index + index * PC_BOX_RECORD_SIZE :
                            records_index + (index + 1) * PC_BOX_RECORD_SIZE
                        ]
                        != data[
                            records_index + index * PC_BOX_RECORD_SIZE :
                            records_index + (index + 1) * PC_BOX_RECORD_SIZE
                        ]
                    ]
                    comparison_status = "unchanged" if changed_byte_count == 0 else "changed"
                    candidate["comparison_to_previous"] = comparison_status
                    comparisons.append({
                        "domain": target.get("source_file_path") or key[0],
                        "record_offset": f"0x{key[1]:05X}",
                        "interpretation": interpretation,
                        "status": comparison_status,
                        "changed_byte_count": changed_byte_count,
                        "changed_slots": changed_slots[:40],
                        "changed_slot_count": len(changed_slots),
                        "header_changed": (
                            prior[header_index : header_index + 4]
                            != data[header_index : header_index + 4]
                        ),
                    })
                candidates.append(candidate)
            next_baseline[key] = data
        had_baseline = any(
            key in self._pc_save_offset_test_baseline for key in active["targets"]
        )
        self._pc_save_offset_test_baseline = next_baseline
        self._pc_save_offset_test_baseline_run_id = lua_run_id
        image = active.get("save_ram_image")
        file_path = active.get("metadata", {}).get("save_ram_file_path")
        identity = active.get("metadata", {}).get("expected_box_1_slot_1")
        identity_matches = self._find_save_ram_identity_records(image, identity)
        previous_image = self._pc_save_offset_test_image_baseline.get(str(file_path))
        file_comparison = self._compare_save_ram_images(
            previous_image.get("image") if previous_image else None,
            image,
            previous_image.get("identity_matches", []) if previous_image else [],
            identity_matches,
        )
        self._pc_save_offset_test_image_baseline[str(file_path)] = {
            "image": image,
            "identity_matches": identity_matches,
        }
        self._pc_save_offset_test_capture_count = getattr(
            self, "_pc_save_offset_test_capture_count", 0
        ) + 1
        return {
            "type": "pc_save_offset_test_result",
            "test_id": active["test_id"],
            "capture_number": self._pc_save_offset_test_capture_count,
            "domains": active["domains"],
            "candidates": candidates,
            "comparison": {
                "has_previous_capture": had_baseline,
                "targets": comparisons,
                "unchanged_note": (
                    "No bytes changed from the prior manual capture. This domain may "
                    "represent persisted save data rather than unsaved live box state."
                    if had_baseline and comparisons and all(
                        item["status"] == "unchanged" for item in comparisons
                    )
                    else None
                ),
            },
            "save_ram_identity_search": {
                "identity": identity,
                "searched_bytes": len(image) if isinstance(image, bytes) else 0,
                "alignment_bytes": 4,
                "match_count": len(identity_matches),
                "matches": identity_matches,
                "file_comparison": file_comparison,
            },
            "expected": {
                "box_count": 18,
                "slots_per_box": 30,
                "record_size": PC_BOX_RECORD_SIZE,
                "total_slots": 540,
                "record_offsets": [f"0x{offset:05X}" for offset in PC_SAVE_COPY_RECORD_OFFSETS],
                "header_bytes": PC_SAVE_HEADER_BYTES,
                "layout_hypotheses": [
                    "fixed offset is first Pokemon record; header is 4 bytes before",
                    "fixed offset is PCBoxes header; first record is 4 bytes after",
                ],
            },
            "bytes_received": active["bytes_received"],
            **active["metadata"],
        }

    @staticmethod
    def _find_save_ram_identity_records(
        image: bytes | None, identity: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        if not isinstance(image, bytes) or not isinstance(identity, dict):
            return []
        wanted_species = identity.get("species_id")
        wanted_nickname = str(identity.get("nickname", "")).casefold()
        if not isinstance(wanted_species, int) or not wanted_nickname:
            return []
        matches: list[dict[str, Any]] = []
        species_name = next(
            (
                species.name
                for species in load_gen4_species()
                if species.national_dex_number == wanted_species
            ),
            f"Unknown #{wanted_species}",
        )
        last_offset = len(image) - PC_BOX_RECORD_SIZE
        for offset in range(0, last_offset + 1, 4):
            record = image[offset : offset + PC_BOX_RECORD_SIZE]
            if not any(record):
                continue
            try:
                decoded = decode_box_pokemon(record, offset)
            except (ValueError, IndexError, OverflowError):
                continue
            if (
                decoded.checksum_valid
                and decoded.species_id == wanted_species
                and decoded.nickname.casefold() == wanted_nickname
            ):
                layout_positions = []
                for copy_record_offset in PC_SAVE_COPY_RECORD_OFFSETS:
                    for interpretation, first_record_offset in (
                        ("fixed offset is first record", copy_record_offset),
                        ("fixed offset is header", copy_record_offset + 4),
                    ):
                        delta = offset - first_record_offset
                        if 0 <= delta < RECORDS_SIZE and delta % PC_BOX_RECORD_SIZE == 0:
                            slot_index = delta // PC_BOX_RECORD_SIZE
                            layout_positions.append({
                                "copy_offset": f"0x{copy_record_offset:05X}",
                                "interpretation": interpretation,
                                "box": slot_index // 30 + 1,
                                "slot": slot_index % 30 + 1,
                            })
                matches.append({
                    "offset": offset,
                    "offset_hex": f"0x{offset:05X}",
                    "species_id": decoded.species_id,
                    "species": species_name,
                    "nickname": decoded.nickname,
                    "checksum": f"0x{decoded.checksum:04X}",
                    "calculated_checksum": f"0x{decoded.calculated_checksum:04X}",
                    "checksum_valid": True,
                    "layout_positions": layout_positions,
                })
        return matches

    @staticmethod
    def _compare_save_ram_images(
        previous: bytes | None,
        current: bytes | None,
        previous_matches: list[dict[str, Any]],
        current_matches: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(current, bytes):
            return {"has_previous_capture": previous is not None}
        if not isinstance(previous, bytes):
            return {
                "has_previous_capture": False,
                "changed": None,
                "changed_byte_count": None,
                "changed_ranges": [],
                "old_matching_offsets": [],
                "new_matching_offsets": [item["offset_hex"] for item in current_matches],
                "movement_candidates": [],
            }

        shared_length = min(len(previous), len(current))
        changed_indices = [
            index
            for index in range(shared_length)
            if previous[index] != current[index]
        ]
        changed_indices.extend(range(shared_length, max(len(previous), len(current))))
        changed_ranges: list[dict[str, Any]] = []
        if changed_indices:
            start = prior = changed_indices[0]
            for index in changed_indices[1:]:
                if index != prior + 1:
                    changed_ranges.append({
                        "start": f"0x{start:05X}",
                        "end_exclusive": f"0x{prior + 1:05X}",
                        "length": prior - start + 1,
                    })
                    start = index
                prior = index
            changed_ranges.append({
                "start": f"0x{start:05X}",
                "end_exclusive": f"0x{prior + 1:05X}",
                "length": prior - start + 1,
            })
        old_offsets = [item["offset"] for item in previous_matches]
        new_offsets = [item["offset"] for item in current_matches]
        movement = []
        for old, new in zip(old_offsets, new_offsets):
            movement.append({
                "old_offset": old,
                "old_offset_hex": f"0x{old:05X}",
                "new_offset": new,
                "new_offset_hex": f"0x{new:05X}",
                "delta": new - old,
                "delta_hex": f"{new - old:+#x}",
                "checksum_valid_before": True,
                "checksum_valid_after": True,
            })
        return {
            "has_previous_capture": True,
            "changed": bool(changed_indices) or len(previous) != len(current),
            "changed_byte_count": len(changed_indices),
            "changed_range_count": len(changed_ranges),
            "changed_ranges": changed_ranges[:64],
            "changed_ranges_truncated": len(changed_ranges) > 64,
            "old_matching_offsets": [f"0x{offset:05X}" for offset in old_offsets],
            "new_matching_offsets": [f"0x{offset:05X}" for offset in new_offsets],
            "movement_candidates": movement,
        }

    @staticmethod
    def _decode_pc_save_segment(
        target: dict[str, Any],
        data: bytes,
        species_names: dict[int, str],
        *,
        interpretation: str,
        header_index: int,
        records_index: int,
    ) -> dict[str, Any]:
        current_box = int.from_bytes(data[header_index : header_index + 4], "little")
        occupied_records = []
        invalid_records = []
        checksum_valid_count = 0
        empty_count = 0
        box_one_slots: dict[int, dict[str, Any]] = {}
        for index in range(540):
            start = records_index + index * PC_BOX_RECORD_SIZE
            record = data[start : start + PC_BOX_RECORD_SIZE]
            box = index // 30 + 1
            slot = index % 30 + 1
            if not any(record):
                empty_count += 1
                if box == 1:
                    box_one_slots[slot] = {
                        "empty": True,
                        "box": box,
                        "slot": slot,
                    }
                continue
            try:
                decoded = decode_box_pokemon(record)
            except (ValueError, IndexError) as exc:
                invalid_records.append({
                    "box": box,
                    "slot": slot,
                    "error": str(exc),
                })
                if box == 1:
                    box_one_slots[slot] = {
                        "empty": False,
                        "box": box,
                        "slot": slot,
                        "checksum_valid": False,
                        "error": str(exc),
                    }
                continue
            valid_species = 1 <= decoded.species_id <= 493
            checksum_valid = bool(decoded.checksum_valid)
            if checksum_valid:
                checksum_valid_count += 1
            if not checksum_valid or not valid_species:
                invalid_records.append({
                    "box": box,
                    "slot": slot,
                    "species_id": decoded.species_id,
                    "checksum_valid": checksum_valid,
                    "error": "invalid checksum or species",
                })
                if box == 1:
                    box_one_slots[slot] = {
                        "empty": False,
                        "box": box,
                        "slot": slot,
                        "species_id": decoded.species_id,
                        "checksum_valid": checksum_valid,
                        "error": "invalid checksum or species",
                    }
                continue
            item = {
                "box": box,
                "slot": slot,
                "species_id": decoded.species_id,
                "species": species_names.get(
                    decoded.species_id, f"Unknown #{decoded.species_id}"
                ),
                "nickname": decoded.nickname,
                "checksum_valid": True,
                "pid": f"0x{decoded.pid:08X}",
                "checksum": f"0x{decoded.checksum:04X}",
            }
            occupied_records.append(item)
            if box == 1:
                box_one_slots[slot] = item
        record_offset = target["record_offset"]
        first_record_offset = record_offset - 4 + records_index
        header_offset = record_offset - 4 + header_index
        domain_size = target.get("domain_size")
        region_fits = (
            isinstance(domain_size, int)
            and first_record_offset + RECORDS_SIZE <= domain_size
        )
        return {
            "layout_interpretation": interpretation,
            "domain": target["domain"],
            "domain_size": domain_size,
            "source_file_path": target.get("source_file_path"),
            "save_copy_offset_tested": f"0x{record_offset:05X}",
            "first_record_offset": f"0x{first_record_offset:05X}",
            "header_offset": f"0x{header_offset:05X}",
            "header_current_box": current_box,
            "header_valid": 0 <= current_box < 18,
            "record_region_fits_domain": region_fits,
            "structure_valid": (
                0 <= current_box < 18
                and region_fits
                and len(occupied_records) + empty_count + len(invalid_records) == 540
                and len(invalid_records) == 0
            ),
            "box_count": 18,
            "slots_per_box": 30,
            "record_size": PC_BOX_RECORD_SIZE,
            "total_slots": 540,
            "occupied_count": len(occupied_records) + len(invalid_records),
            "empty_count": empty_count,
            "checksum_valid_count": checksum_valid_count,
            "valid_occupied_count": len(occupied_records),
            "invalid_count": len(invalid_records),
            "box_1_slot_1": box_one_slots.get(1, {"empty": True, "box": 1, "slot": 1}),
            "box_1_slot_2": box_one_slots.get(2, {"empty": True, "box": 1, "slot": 2}),
            "occupied_records": occupied_records,
            "invalid_records": invalid_records[:20],
        }

    def _store_pc_save_offset_test_result(
        self, payload: dict[str, Any], source: str
    ) -> None:
        line = json.dumps(payload, separators=(",", ":"))
        received = BizHawkHeartbeat(
            received_at=time.monotonic(),
            payload=payload,
            source=source,
            bytes_received=len(line.encode("utf-8")),
        )
        with self._lock:
            self._pc_save_offset_test_payload = received

    @staticmethod
    def _event_address(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value, 0)
            except ValueError:
                try:
                    return int(value, 16)
                except ValueError:
                    return None
        return None

    def _request_pc_discovery_chunk_retry(
        self,
        accumulator: PCStorageDiscoveryAccumulator,
        chunk_index: int,
        source: str,
    ) -> bool:
        if chunk_index in accumulator.retry_in_flight:
            return True
        attempts = accumulator.retry_attempts.get(chunk_index, 0)
        if attempts >= PC_DISCOVERY_RETRY_LIMIT:
            return False
        command = f"PC_RETRY_DISCOVERY_CHUNK|{accumulator.scan_id}|{chunk_index}\n"
        routes = (source, "file" if source == "tcp" else "tcp")
        route_used = None
        for route in routes:
            sent = (
                route == "tcp" and self._send_tcp_command(command)
            ) or (route == "file" and self._write_command_file(command))
            if sent:
                route_used = route
                break
        if route_used is None:
            return False
        accumulator.retry_attempts[chunk_index] = attempts + 1
        accumulator.retry_in_flight.add(chunk_index)
        accumulator.retry_requested_at[chunk_index] = time.monotonic()
        LOGGER.warning(
            "Requesting retransmission of PC discovery chunk: scan=%s chunk=%d attempt=%d via=%s",
            accumulator.scan_id,
            chunk_index,
            attempts + 1,
            route_used,
        )
        return True

    def _acknowledge_pc_discovery_scan(self, scan_id: str, source: str) -> None:
        command = f"PC_ACK_DISCOVERY_SCAN|{scan_id}\n"
        routes = (source, "file" if source == "tcp" else "tcp")
        for route in routes:
            sent = (route == "tcp" and self._send_tcp_command(command)) or (
                route == "file" and self._write_command_file(command)
            )
            if sent:
                LOGGER.debug("Acknowledged PC discovery cache: scan=%s via=%s", scan_id, route)
                return
        LOGGER.warning("Could not acknowledge PC discovery cache: scan=%s", scan_id)

    def _acknowledge_pc_pokemon_search(self, scan_id: str, source: str) -> None:
        command = f"PC_ACK_POKEMON_SEARCH|{scan_id}\n"
        routes = (source, "file" if source == "tcp" else "tcp")
        for route in routes:
            sent = (route == "tcp" and self._send_tcp_command(command)) or (
                route == "file" and self._write_command_file(command)
            )
            if sent:
                LOGGER.debug("Acknowledged Pokemon search result: scan=%s via=%s", scan_id, route)
                return
        LOGGER.warning("Could not acknowledge Pokemon search result: scan=%s", scan_id)

    def _check_pc_discovery_retry_timeout(self) -> None:
        with self._pc_discovery_lock:
            accumulator = self._pc_discovery_scan
            if accumulator is None:
                return
            now = time.monotonic()
            if (
                now - accumulator.last_transport_log_at
                >= PC_DISCOVERY_STATUS_LOG_INTERVAL_SECONDS
            ):
                progress = accumulator.progress()
                missing = progress["missing_chunk_indexes"]
                LOGGER.info(
                    "PC discovery transport: scan_id=%s received=%d/%s highest_chunk=%d "
                    "next_unproduced=%d missing_count=%d oldest_missing_chunk=%s "
                    "seconds_since_last_chunk=%.1f end_received=%s producer_status=%s",
                    accumulator.scan_id,
                    progress["received_chunk_count"],
                    progress["expected_chunk_count"]
                    if progress["expected_chunk_count"] is not None
                    else progress["estimated_chunk_count"] or "?",
                    progress["highest_chunk_seen"],
                    progress["next_unproduced_chunk"],
                    len(missing),
                    min(missing) if missing else "none",
                    progress["seconds_since_last_chunk"],
                    progress["end_received"],
                    progress["producer_status"],
                )
                accumulator.last_transport_log_at = now
            expired = [
                index
                for index in accumulator.retry_in_flight
                if now - accumulator.retry_requested_at.get(index, now)
                >= PC_DISCOVERY_RETRY_TIMEOUT_SECONDS
            ]
            for chunk_index in sorted(expired):
                accumulator.retry_in_flight.discard(chunk_index)
                accumulator.retry_requested_at.pop(chunk_index, None)
                if accumulator.retry_attempts.get(chunk_index, 0) >= PC_DISCOVERY_RETRY_LIMIT:
                    self._fail_missing_pc_discovery_chunk_locked(
                        accumulator, chunk_index, "retransmission timed out"
                    )
                    return
                if self._request_pc_discovery_chunk_retry(
                    accumulator, chunk_index, accumulator.source
                ):
                    self._set_pc_discovery_retry_progress(accumulator, chunk_index)
                else:
                    self._fail_missing_pc_discovery_chunk_locked(
                        accumulator, chunk_index, "retry request could not be sent"
                    )
                    return

            if self._pc_discovery_scan is not accumulator:
                return
            retry_active = bool(accumulator.retry_in_flight)
            stalled_for = now - accumulator.last_chunk_received_at
            if not retry_active and stalled_for >= PC_DISCOVERY_STALL_SECONDS:
                missing_index = accumulator.first_missing_chunk_index()
                if missing_index is not None:
                    if self._request_pc_discovery_chunk_retry(
                        accumulator, missing_index, accumulator.source
                    ):
                        self._set_pc_discovery_retry_progress(accumulator, missing_index)
                    else:
                        self._fail_missing_pc_discovery_chunk_locked(
                            accumulator,
                            missing_index,
                            "retry limit reached or request failed",
                        )
                elif not accumulator.end_received:
                    self._pc_discovery_progress = {
                        **accumulator.progress(),
                        "status": "stalled",
                    }

    def _set_pc_discovery_retry_progress(
        self, accumulator: PCStorageDiscoveryAccumulator, chunk_index: int
    ) -> None:
        progress = accumulator.progress()
        self._pc_discovery_progress = {
            **progress,
            "status": "retrying",
            "missing_chunk_index": chunk_index,
            **(
                {"retry_reason": "malformed_chunk"}
                if chunk_index not in progress["proven_missing_chunks"]
                else {}
            ),
        }

    def _fail_missing_pc_discovery_chunk_locked(
        self,
        accumulator: PCStorageDiscoveryAccumulator,
        chunk_index: int,
        reason: str,
    ) -> None:
        address = accumulator.start_address + len(accumulator.data)
        self._fail_active_pc_discovery_locked(
            accumulator,
            accumulator.source,
            f"Discovery failed: missing chunk {chunk_index} at 0x{address:08X}; {reason}.",
        )

    @staticmethod
    def _raw_json_integer(line: str, field: str) -> int | None:
        match = re.search(rf'"{re.escape(field)}"\s*:\s*(-?\d+)', line)
        return int(match.group(1)) if match else None

    def _fail_active_pc_discovery_locked(
        self,
        accumulator: PCStorageDiscoveryAccumulator,
        source: str,
        error: str,
        frame: Any = None,
    ) -> None:
        self._pc_discovery_scan = None
        self._pc_discovery_progress = {
            **self._pc_discovery_progress,
            "scan_id": accumulator.scan_id,
            "mode": accumulator.mode,
            "status": "failed",
            "error": error,
            "source": source,
        }
        self._store_pc_discovery_result(
            self._failed_result(accumulator, error, frame), source
        )
        self._acknowledge_pc_discovery_scan(accumulator.scan_id, source)

    def _begin_pc_discovery_analysis_locked(
        self,
        accumulator: PCStorageDiscoveryAccumulator,
        source: str,
        frame: Any,
    ) -> None:
        self._pc_discovery_progress = {
            **accumulator.progress(),
            "status": "analyzing",
            "progress_percent": 100,
            "current_address": f"0x{accumulator.start_address + accumulator.total_bytes:08X}",
        }
        thread = threading.Thread(
            target=self._finalize_pc_discovery,
            args=(accumulator, source, frame),
            name="pc-storage-discovery-analysis",
            daemon=True,
        )
        thread.start()

    def _try_finish_pc_discovery_locked(
        self,
        accumulator: PCStorageDiscoveryAccumulator,
        source: str,
    ) -> None:
        expected_count = accumulator.expected_chunk_count
        expected_bytes = accumulator.expected_byte_count
        if not accumulator.end_received or expected_count is None:
            return
        missing = sorted(
            set(range(expected_count)) - accumulator.received_chunk_indexes
        )
        accumulator.missing_chunk_indexes = set(missing)
        if missing:
            missing_index = missing[0]
            if self._request_pc_discovery_chunk_retry(
                accumulator, missing_index, source
            ):
                self._set_pc_discovery_retry_progress(accumulator, missing_index)
            else:
                self._fail_missing_pc_discovery_chunk_locked(
                    accumulator, missing_index, "retry limit reached or request failed"
                )
            return
        if (
            accumulator.received_chunk_indexes != set(range(expected_count))
            or expected_bytes != accumulator.total_bytes
            or len(accumulator.data) != expected_bytes
        ):
            self._fail_active_pc_discovery_locked(
                accumulator,
                source,
                "Discovery transport error: end packet counts do not match the "
                "received chunk indexes and bytes.",
                (accumulator.completion_event or {}).get("frame"),
            )
            return
        self._begin_pc_discovery_analysis_locked(
            accumulator,
            accumulator.completion_source or source,
            (accumulator.completion_event or {}).get("frame"),
        )

    def _record_pc_discovery_event(self, payload: dict[str, Any], source: str) -> None:
        message_type = payload.get("type")
        if message_type in {
            "pc_session_pc_resolver_cache_result",
            "pc_storage_cache_ready",
            "pc_storage_cache_rejected",
        }:
            self._record_session_pc_cache_result(payload, source)
            return
        scan_id = str(payload.get("scan_id") or "")
        if message_type == "pc_discovery_started":
            mode = payload.get("mode")
            total_bytes = payload.get("total_bytes")
            start_address = self._event_address(payload.get("start_address"))
            anchor_address = self._event_address(payload.get("anchor_address"))
            runtime_base = self._event_address(payload.get("runtime_base_address"))
            runtime_pointer_slot = self._event_address(payload.get("runtime_pointer_slot"))
            runtime_header_offset = self._event_address(payload.get("runtime_header_offset"))
            runtime_record_offset = self._event_address(payload.get("runtime_record_offset"))
            predicted_header = self._event_address(
                payload.get("predicted_pc_header_address")
            )
            predicted_record = self._event_address(
                payload.get("predicted_first_box_record_address")
            )
            runtime_pointer_semantics = payload.get("runtime_pointer_semantics")
            lua_run_id = str(payload.get("lua_run_id") or "")
            expected_species_id = None
            expected_nickname = None
            if mode == "session_layout":
                try:
                    expected_species_id = int(payload.get("expected_species_id"))
                    nickname_bytes = bytes.fromhex(
                        str(payload.get("expected_nickname_hex") or "")
                    )
                    expected_nickname = nickname_bytes.decode("ascii") or None
                except (TypeError, ValueError, UnicodeDecodeError):
                    expected_species_id = None
                    expected_nickname = None
            party_records = self._event_address(payload.get("party_records_address"))
            party_range_valid = payload.get("party_range_valid") is True
            try:
                party_record_size = int(payload.get("party_record_size") or 0)
            except (TypeError, ValueError):
                party_record_size = 0
            try:
                total_bytes = int(total_bytes)
            except (TypeError, ValueError):
                total_bytes = 0
            try:
                anchor_data_offset = int(payload.get("anchor_data_offset") or 0)
            except (TypeError, ValueError):
                anchor_data_offset = -1
            try:
                estimated_chunk_count = int(
                    payload.get("estimated_chunk_count")
                    or (total_bytes + PC_DISCOVERY_CHUNK_MAX - 1)
                    // PC_DISCOVERY_CHUNK_MAX
                )
            except (TypeError, ValueError):
                estimated_chunk_count = 0
            if mode in {"broad", "session_layout"}:
                valid_range = (
                    total_bytes == NDS_MAIN_RAM_SIZE
                    and start_address == NDS_MAIN_RAM_BASE
                )
                if mode == "session_layout":
                    valid_range = (
                        valid_range
                        and 1 <= (expected_species_id or 0) <= 493
                        and len(str(payload.get("expected_nickname_hex") or "")) <= 20
                        and bool(lua_run_id)
                    )
            elif mode == "inspect":
                expected_start = max(
                    NDS_MAIN_RAM_BASE,
                    (anchor_address or 0) - INSPECTION_BEFORE_BYTES,
                )
                expected_end = min(
                    NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE,
                    (anchor_address or 0) + INSPECTION_AFTER_BYTES,
                )
                valid_range = (
                    anchor_address is not None
                    and NDS_MAIN_RAM_BASE <= anchor_address
                    and anchor_address + PC_BOX_RECORD_SIZE
                    <= NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
                    and start_address == expected_start
                    and total_bytes == expected_end - expected_start
                    and anchor_data_offset == anchor_address - expected_start
                )
            elif mode == "anchor":
                valid_range = (
                    anchor_address is not None
                    and start_address == anchor_address - 0x400
                    and total_bytes == 0x400 + 18 * 30 * PC_BOX_RECORD_SIZE + 0x400
                    and anchor_data_offset == 0x400
                    and start_address >= NDS_MAIN_RAM_BASE
                    and start_address + total_bytes <= NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
                )
            elif mode == "runtime_relative":
                # Historical resolver announcements are intentionally unsupported:
                # the runtime-base-relative offset failed cross-session validation.
                valid_range = False
            else:
                valid_range = False
            if not scan_id or not valid_range:
                self._set_pc_discovery_failure(
                    scan_id,
                    source,
                    "Lua announced an invalid PC discovery scan range.",
                    mode=mode,
                    anchor_address=anchor_address,
                    resolution_payload=payload,
                )
                return
            try:
                party_count = int(payload.get("party_count"))
            except (TypeError, ValueError):
                party_count = -1
            if not 0 <= party_count <= 6:
                party_range_valid = False
                party_count = 0
            accumulator = PCStorageDiscoveryAccumulator(
                scan_id=scan_id,
                mode=mode,
                total_bytes=total_bytes,
                start_address=start_address,
                anchor_address=anchor_address,
                anchor_data_offset=anchor_data_offset,
                expected_species_id=expected_species_id,
                expected_nickname=expected_nickname,
                lua_run_id=lua_run_id or None,
                runtime_base_address=runtime_base,
                runtime_pointer_slot=runtime_pointer_slot,
                runtime_pointer_semantics=runtime_pointer_semantics,
                runtime_header_offset=runtime_header_offset,
                runtime_record_offset=runtime_record_offset,
                predicted_pc_header_address=predicted_header,
                predicted_first_box_record_address=predicted_record,
                party_records_address=party_records,
                party_count=party_count,
                party_record_size=party_record_size,
                party_range_valid=party_range_valid,
                source=source,
                estimated_chunk_count=(
                    estimated_chunk_count
                    if 0 < estimated_chunk_count <= 8192
                    else None
                ),
            )
            with self._pc_discovery_lock:
                self._pc_discovery_scan = accumulator
                self._pc_discovery_progress = accumulator.progress()
            LOGGER.info(
                "Python received PC discovery start: id=%s mode=%s bytes=%d anchor=%s "
                "party_range_valid=%s party_records=%s party_count=%d party_record_size=%d",
                scan_id,
                mode,
                total_bytes,
                f"0x{anchor_address:08X}" if anchor_address is not None else None,
                party_range_valid,
                f"0x{party_records:08X}" if party_records is not None else None,
                party_count,
                party_record_size,
            )
            return

        if message_type == "pc_discovery_failed":
            with self._pc_discovery_lock:
                active = self._pc_discovery_scan
            if active is None or (scan_id and scan_id != active.scan_id):
                self._set_pc_discovery_failure(
                    scan_id,
                    source,
                    str(payload.get("error") or "Lua PC discovery failed."),
                    mode=payload.get("mode"),
                    anchor_address=self._event_address(payload.get("anchor_address")),
                    resolution_payload=payload,
                )
                return

        if message_type == "pc_discovery_cancelled":
            with self._pc_discovery_lock:
                active = self._pc_discovery_scan
            if active is not None and not scan_id:
                scan_id = active.scan_id
            if active is None:
                with self._pc_discovery_lock:
                    queued_progress = dict(self._pc_discovery_progress)
                cancelled_mode = payload.get("mode") or queued_progress.get("mode")
                cancelled_anchor = queued_progress.get("anchor_address")
                cancelled = {
                    "type": "pc_storage_discovery_result",
                    "run_id": scan_id or f"cancelled-{time.monotonic_ns()}",
                    "frame": payload.get("frame"),
                    "pc_storage_discovery_requested": True,
                    "pc_storage_discovery_result": True,
                    "pc_storage_anchor_result": cancelled_mode == "anchor",
                    "pc_storage_runtime_relative_result": cancelled_mode == "runtime_relative",
                    "pc_storage_runtime_relative_result_ok": False,
                    "pc_storage_pokemon_inspection_result": cancelled_mode == "inspect",
                    "pc_storage_anchor_address": cancelled_anchor,
                    "pc_storage_inspection_anchor_address": (
                        cancelled_anchor if cancelled_mode == "inspect" else None
                    ),
                    "pc_storage_discovery_summary": {
                        "cancelled": True,
                        "bytes_scanned": int(payload.get("bytes_scanned") or 0),
                    },
                    "pc_storage_discovery_candidates": [],
                    "pc_storage_anchor_diagnostic": None,
                }
                with self._pc_discovery_lock:
                    self._pc_discovery_progress = {
                        "scan_id": scan_id,
                        "mode": cancelled_mode,
                        "status": "cancelled",
                        "progress_percent": 0,
                        "candidates_found": 0,
                        "source": source,
                    }
                self._store_pc_discovery_result(cancelled, source)
                return

        with self._pc_discovery_lock:
            accumulator = self._pc_discovery_scan
            if accumulator is None or scan_id != accumulator.scan_id:
                LOGGER.warning(
                    "Ignoring PC discovery event for inactive scan: type=%s id=%s",
                    message_type,
                    scan_id,
                )
                return
            if message_type == "pc_discovery_chunk":
                try:
                    offset = int(payload.get("offset"))
                    encoded = payload.get("data")
                    encoding = payload.get("data_encoding")
                    if encoded is None and payload.get("data_hex") is not None:
                        encoded = payload.get("data_hex")
                        encoding = "hex"
                    if not isinstance(encoded, str):
                        raise TypeError("Discovery transport error: chunk omitted encoded RAM bytes.")
                    if encoding != "hex":
                        raise ValueError(
                            f"Discovery transport error: unsupported chunk encoding {encoding!r}."
                        )
                    if len(encoded) > PC_DISCOVERY_CHUNK_MAX * 2:
                        raise ValueError("Discovery transport error: oversized hexadecimal chunk.")
                    chunk = bytes.fromhex(encoded)
                    if len(chunk) > PC_DISCOVERY_CHUNK_MAX:
                        raise ValueError("Discovery transport error: chunk exceeded 4 KB.")
                    raw_index = payload.get("chunk_index")
                    chunk_index = (
                        int(raw_index)
                        if raw_index is not None
                        else accumulator.received_chunk_count
                    )
                    raw_byte_count = payload.get("byte_count")
                    byte_count = int(raw_byte_count) if raw_byte_count is not None else len(chunk)
                    chunk_state = accumulator.queue_chunk(
                        offset,
                        chunk,
                        chunk_index,
                        byte_count,
                    )
                except (TypeError, ValueError) as exc:
                    LOGGER.error("Rejecting invalid PC discovery chunk: %s", exc)
                    self._fail_active_pc_discovery_locked(
                        accumulator, source, str(exc), payload.get("frame")
                    )
                    return
                if chunk_state == "duplicate":
                    LOGGER.debug(
                        "Ignoring duplicate PC discovery chunk: id=%s index=%s",
                        scan_id,
                        chunk_index,
                    )
                    return
                if chunk_state == "gap":
                    missing_index = accumulator.first_missing_chunk_index()
                    if missing_index is None:
                        self._pc_discovery_progress = accumulator.progress()
                        return
                    if self._request_pc_discovery_chunk_retry(accumulator, missing_index, source):
                        self._set_pc_discovery_retry_progress(
                            accumulator, missing_index
                        )
                        return
                    self._fail_missing_pc_discovery_chunk_locked(
                        accumulator,
                        missing_index,
                        "retry limit reached or request failed",
                    )
                    return
                self._pc_discovery_progress = accumulator.progress()
                progress = dict(self._pc_discovery_progress)
                LOGGER.debug(
                    "PC discovery chunk received: id=%s bytes=%s/%s candidates=%s",
                    scan_id,
                    progress["bytes_scanned"],
                    progress["total_bytes"],
                    progress["candidates_found"],
                )
                self._try_finish_pc_discovery_locked(accumulator, source)
                return
            if message_type == "pc_discovery_cancelled":
                self._pc_discovery_scan = None
                cancelled = {
                    "type": "pc_storage_discovery_result",
                    "run_id": scan_id,
                    "frame": payload.get("frame"),
                    "pc_storage_discovery_requested": True,
                    "pc_storage_discovery_result": True,
                    "pc_storage_anchor_result": accumulator.mode == "anchor",
                    "pc_storage_runtime_relative_result": accumulator.mode == "runtime_relative",
                    "pc_storage_runtime_relative_result_ok": False,
                    "pc_storage_pokemon_inspection_result": accumulator.mode == "inspect",
                    "pc_storage_anchor_address": (
                        f"0x{accumulator.anchor_address:08X}"
                        if accumulator.anchor_address is not None
                        else None
                    ),
                    "pc_storage_inspection_anchor_address": (
                        f"0x{accumulator.anchor_address:08X}"
                        if accumulator.mode == "inspect"
                        and accumulator.anchor_address is not None
                        else None
                    ),
                    "pc_storage_discovery_summary": {
                        "mode": accumulator.mode,
                        "cancelled": True,
                        "bytes_scanned": len(accumulator.data),
                    },
                    "pc_storage_discovery_candidates": [],
                    "pc_storage_anchor_diagnostic": None,
                }
                self._pc_discovery_progress = {
                    "scan_id": scan_id,
                    "mode": accumulator.mode,
                    "status": "cancelled",
                    "progress_percent": int(len(accumulator.data) * 100 / accumulator.total_bytes),
                    "bytes_scanned": len(accumulator.data),
                    "total_bytes": accumulator.total_bytes,
                    "candidates_found": accumulator.candidates_found,
                    "source": source,
                }
                self._store_pc_discovery_result(cancelled, source)
                LOGGER.info("PC discovery cancelled by Lua: id=%s", scan_id)
                return
            if message_type == "pc_discovery_failed":
                self._pc_discovery_scan = None
                error = str(payload.get("error") or "Lua PC discovery failed.")
                missing_index = self._pc_discovery_progress.get("missing_chunk_index")
                if isinstance(missing_index, int):
                    missing_address = accumulator.start_address + len(accumulator.data)
                    error = (
                        "Discovery failed: missing chunk "
                        f"{missing_index} at 0x{missing_address:08X}; retransmission failed: "
                        f"{error}"
                    )
                self._pc_discovery_progress = {
                    "scan_id": scan_id,
                    "mode": accumulator.mode,
                    "status": "failed",
                    "error": error,
                    "source": source,
                }
                self._store_pc_discovery_result(
                    self._failed_result(accumulator, error, payload.get("frame")), source
                )
                return
            if message_type in {"pc_discovery_complete", "pc_discovery_end"}:
                try:
                    expected_byte_count = int(
                        payload.get(
                            "total_bytes",
                            payload.get("expected_byte_count", accumulator.total_bytes),
                        )
                    )
                    expected_chunk_count = int(
                        payload.get(
                            "chunk_count",
                            payload.get(
                                "expected_chunk_count", accumulator.received_chunk_count
                            ),
                        )
                    )
                except (TypeError, ValueError):
                    expected_byte_count = -1
                    expected_chunk_count = -1
                max_chunks = (accumulator.total_bytes + 0x1FF) // 0x200
                if (
                    expected_chunk_count <= 0
                    or expected_chunk_count > max_chunks
                    or expected_byte_count != accumulator.total_bytes
                ):
                    self._fail_active_pc_discovery_locked(
                        accumulator,
                        source,
                        "Discovery transport error: invalid end packet counts "
                        f"(chunks={expected_chunk_count}, bytes={expected_byte_count}).",
                        payload.get("frame"),
                    )
                    return
                accumulator.end_received = True
                accumulator.expected_chunk_count = expected_chunk_count
                accumulator.expected_byte_count = expected_byte_count
                accumulator.completion_event = payload
                accumulator.completion_source = source
                accumulator._refresh_missing_chunk_indexes()
                self._pc_discovery_progress = accumulator.progress()
                self._try_finish_pc_discovery_locked(accumulator, source)

    def _failed_result(
        self,
        accumulator: PCStorageDiscoveryAccumulator,
        error: str,
        frame: Any = None,
    ) -> dict[str, Any]:
        return {
            "type": "pc_storage_discovery_result",
            "run_id": accumulator.scan_id,
            "frame": frame,
            "pc_storage_discovery_requested": True,
            "pc_storage_discovery_result": True,
            "pc_storage_anchor_result": accumulator.mode == "anchor",
            "pc_storage_runtime_relative_result": accumulator.mode == "runtime_relative",
            "pc_storage_runtime_relative_result_ok": False,
            "pc_storage_pokemon_inspection_result": accumulator.mode == "inspect",
            "pc_storage_anchor_address": (
                f"0x{accumulator.anchor_address:08X}"
                if accumulator.anchor_address is not None
                else None
            ),
            "pc_storage_inspection_anchor_address": (
                f"0x{accumulator.anchor_address:08X}"
                if accumulator.mode == "inspect" and accumulator.anchor_address is not None
                else None
            ),
            "pc_storage_anchor_error": error,
            "pc_storage_discovery_summary": {"mode": accumulator.mode, "error": error},
            "pc_storage_discovery_candidates": [],
            "pc_storage_anchor_diagnostic": None,
            "runtime_pointer_slot": (
                f"0x{accumulator.runtime_pointer_slot:08X}"
                if accumulator.runtime_pointer_slot is not None
                else None
            ),
            "runtime_pointer_semantics": accumulator.runtime_pointer_semantics,
            "runtime_base_address": (
                f"0x{accumulator.runtime_base_address:08X}"
                if accumulator.runtime_base_address is not None
                else None
            ),
            "runtime_header_offset": accumulator.runtime_header_offset,
            "runtime_record_offset": accumulator.runtime_record_offset,
            "predicted_pc_header_address": (
                f"0x{accumulator.predicted_pc_header_address:08X}"
                if accumulator.predicted_pc_header_address is not None
                else None
            ),
            "predicted_first_box_record_address": (
                f"0x{accumulator.predicted_first_box_record_address:08X}"
                if accumulator.predicted_first_box_record_address is not None
                else None
            ),
        }

    def _set_pc_discovery_failure(
        self,
        scan_id: str,
        source: str,
        error: str,
        *,
        mode: str | None = None,
        anchor_address: int | None = None,
        resolution_payload: dict[str, Any] | None = None,
    ) -> None:
        failed = {
            "type": "pc_storage_discovery_result",
            "run_id": scan_id,
            "pc_storage_discovery_requested": True,
            "pc_storage_discovery_result": True,
            "pc_storage_anchor_result": mode == "anchor",
            "pc_storage_runtime_relative_result": mode == "runtime_relative",
            "pc_storage_runtime_relative_result_ok": False,
            "pc_storage_pokemon_inspection_result": mode == "inspect",
            "pc_storage_anchor_address": (
                f"0x{anchor_address:08X}" if anchor_address is not None else None
            ),
            "pc_storage_inspection_anchor_address": (
                f"0x{anchor_address:08X}"
                if mode == "inspect" and anchor_address is not None
                else None
            ),
            "pc_storage_anchor_error": error,
            "pc_storage_discovery_summary": {"mode": mode, "error": error},
            "pc_storage_discovery_candidates": [],
            "pc_storage_anchor_diagnostic": None,
        }
        if mode == "runtime_relative" and resolution_payload is not None:
            resolution_keys = (
                "runtime_pointer_slot",
                "runtime_pointer_semantics",
                "runtime_base_address",
                "runtime_header_offset",
                "runtime_record_offset",
                "predicted_pc_header_address",
                "predicted_first_box_record_address",
            )
            for key in resolution_keys:
                failed[key] = resolution_payload.get(key)
            failed["pc_storage_discovery_summary"].update(
                {key: failed[key] for key in resolution_keys}
            )
        with self._pc_discovery_lock:
            self._pc_discovery_scan = None
            self._pc_discovery_progress = {
                "scan_id": scan_id,
                "mode": mode,
                "status": "failed",
                "error": error,
                "source": source,
            }
        self._store_pc_discovery_result(failed, source)

    def _finalize_pc_discovery(
        self,
        accumulator: PCStorageDiscoveryAccumulator,
        source: str,
        frame: Any,
    ) -> None:
        try:
            result = accumulator.finalize()
            result["frame"] = frame
            if accumulator.mode == "inspect":
                result["pc_storage_movement_comparison"] = (
                    self._compare_and_advance_inspection_baseline(accumulator, result)
                )
            summary = result.get("pc_storage_discovery_summary", {})
            error = summary.get("error") if isinstance(summary, dict) else None
            status = "failed" if error else "completed"
        except Exception as exc:
            LOGGER.exception("PC storage discovery analysis failed.")
            result = self._failed_result(accumulator, str(exc), frame)
            status = "failed"
            error = str(exc)
        with self._pc_discovery_lock:
            if self._pc_discovery_scan is not accumulator:
                LOGGER.info(
                    "Discarding superseded PC discovery analysis: id=%s",
                    accumulator.scan_id,
                )
                return
            self._pc_discovery_scan = None
            self._pc_discovery_progress = {
                "scan_id": accumulator.scan_id,
                "mode": accumulator.mode,
                "status": status,
                "progress_percent": 100,
                "current_address": f"0x{accumulator.start_address + accumulator.total_bytes:08X}",
                "bytes_scanned": accumulator.total_bytes,
                "total_bytes": accumulator.total_bytes,
                "candidates_found": accumulator.candidates_found,
                "elapsed_seconds": max(0.0, time.monotonic() - accumulator.started_at),
                "error": error,
                "source": source,
            }
        self._store_pc_discovery_result(result, source)
        if accumulator.mode == "session_layout" and result.get(
            "pc_storage_resolver_status"
        ) == "cache-pending":
            try:
                first_record_address = int(
                    str(result["session_pc_first_record_address"]), 16
                )
            except (KeyError, TypeError, ValueError):
                first_record_address = None
            lua_run_id = result.get("session_lua_run_id")
            LOGGER.info(
                "PC resolver discovery succeeded: scan=%s address=%s Lua run=%s "
                "occupied=%s empty=%s invalid=%s",
                accumulator.scan_id, result.get("session_pc_first_record_address"),
                lua_run_id, result.get("pc_storage_discovery_summary", {}).get("valid_occupied_records"),
                result.get("pc_storage_discovery_summary", {}).get("empty_records"),
                result.get("pc_storage_discovery_summary", {}).get("invalid_records"),
            )
            with self._pc_discovery_lock:
                self._pc_cache_install = {
                    "lua_run_id": lua_run_id,
                    "address": result.get("session_pc_first_record_address"),
                    "command": (
                        f"PC_CACHE_SESSION_PC|{lua_run_id}|{first_record_address:08X}\n"
                        if first_record_address is not None else None
                    ),
                    "command_sent": False,
                    "command_queued": False,
                    "lua_received": False,
                    "lua_validation": "pending",
                    "confirmation_received": False,
                    "last_lua_run_id": None,
                    "sent_at": None,
                    "queued_at": None,
                    "last_confirmation_at": None,
                    "source": source,
                }
            cache_sent = (
                first_record_address is not None
                and isinstance(lua_run_id, str)
                and self.cache_session_pc_layout(
                    lua_run_id,
                    first_record_address,
                    transport=source,
                )
            )
            if not cache_sent:
                error = "Layout validated, but Lua did not accept the session cache command."
                result["pc_storage_resolver_status"] = "unresolved"
                result["pc_storage_anchor_error"] = error
                result["pc_storage_discovery_summary"]["error"] = error
                with self._pc_discovery_lock:
                    self._pc_cache_install = None
                    self._pc_discovery_progress = {
                        **self._pc_discovery_progress,
                        "status": "failed",
                        "error": error,
                    }
                self._store_pc_discovery_result(result, source)
            else:
                with self._command_file_lock:
                    queued = self._pc_cache_install["command"] in self._command_file_queue
                with self._pc_discovery_lock:
                    pending = self._pc_cache_install
                    if pending is not None and not pending["confirmation_received"]:
                        if pending["sent_at"] is None:
                            pending["command_sent"] = not queued
                            pending["command_queued"] = queued
                            pending["sent_at"] = None if queued else time.monotonic()
                            pending["queued_at"] = time.monotonic() if queued else None
                        self._pc_discovery_progress = {
                            **self._pc_discovery_progress,
                            "status": "cache-pending",
                            "session_pc_first_record_address": pending["address"],
                            "cache_install": dict(pending),
                        }
        self._acknowledge_pc_discovery_scan(accumulator.scan_id, source)
        LOGGER.info(
            "PC discovery analysis %s: id=%s mode=%s candidates=%s",
            status,
            accumulator.scan_id,
            accumulator.mode,
            result.get("pc_storage_discovery_summary", {}).get("candidate_records_found"),
        )

    def _record_session_pc_cache_result(
        self, payload: dict[str, Any], source: str
    ) -> None:
        lua_run_id = str(payload.get("lua_run_id") or "")
        accepted = payload.get("accepted") is True
        address = payload.get("session_pc_first_record_address")
        occupied = payload.get("occupied_records")
        empty = payload.get("empty_records")
        malformed = payload.get("malformed_records")
        LOGGER.info(
            "Python received PC cache confirmation: type=%s run=%s accepted=%s "
            "address=%s valid=%s empty=%s invalid=%s source=%s",
            payload.get("type"), lua_run_id, accepted, address,
            payload.get("occupied_records"), payload.get("empty_records"),
            payload.get("malformed_records"), source,
        )
        with self._lock:
            current = self._pc_storage_discovery_payload
        if current is None or not isinstance(current.payload, dict):
            LOGGER.warning("Received session PC cache result without a stored layout result.")
            return
        if accepted and not (
            type(occupied) is int and type(empty) is int and type(malformed) is int
            and occupied >= 0 and empty >= 0 and malformed == 0
            and occupied + empty == 540
        ):
            LOGGER.warning(
                "Ignoring incomplete PC cache acknowledgement: run=%s address=%s "
                "occupied=%s empty=%s malformed=%s; awaiting validated PC scan",
                lua_run_id, address, occupied, empty, malformed,
            )
            return
        result = dict(current.payload)
        validation = payload.get("cache_validation")
        if isinstance(validation, dict):
            validation = dict(validation)
            raw_read = validation.get("anchor_record_ram_read")
            python_hex = result.get("pc_storage_anchor_raw_hex")
            lua_hex = raw_read.get("raw_136_bytes_hex") if isinstance(raw_read, dict) else None
            if isinstance(python_hex, str) and isinstance(lua_hex, str):
                validation["anchor_byte_comparison"] = {
                    "bytes_match": python_hex.upper() == lua_hex.upper(),
                    "python_first_64_bytes_hex": python_hex[:128],
                    "lua_first_64_bytes_hex": lua_hex[:128],
                }
            if isinstance(lua_hex, str):
                try:
                    decoded = decode_box_pokemon(bytes.fromhex(lua_hex))
                except (ValueError, IndexError) as exc:
                    validation["python_decode_error"] = str(exc)
                else:
                    expected = result.get("expected_box1_slot1") or {}
                    names = load_gen4_species()
                    species_name = (
                        names[decoded.species_id - 1].name
                        if 1 <= decoded.species_id <= len(names) else None
                    )
                    expected_id = expected.get("species_id")
                    expected_name = (
                        names[expected_id - 1].name
                        if type(expected_id) is int and 1 <= expected_id <= len(names)
                        else None
                    )
                    validation["python_anchor_decode"] = {
                        "species_id": decoded.species_id,
                        "species_name": species_name,
                        "nickname": decoded.nickname,
                        "checksum_valid": decoded.checksum_valid,
                        "stored_checksum": decoded.checksum,
                        "calculated_checksum": decoded.calculated_checksum,
                        "shuffle_index": decoded.shuffle_index,
                    }
                    species_check = validation.get("anchor_species")
                    if isinstance(species_check, dict):
                        validation["anchor_species"] = {
                            **species_check,
                            "decoded_species_name": species_name,
                            "expected_species_name": expected_name,
                        }
                    nickname_check = validation.get("anchor_nickname")
                    if isinstance(nickname_check, dict):
                        expected_nickname = expected.get("nickname")
                        validation["anchor_nickname"] = {
                            **nickname_check,
                            "decoded_nickname": decoded.nickname,
                            "expected_nickname": expected_nickname,
                            "decoded_by": "Python Gen IV parser over Lua-read bytes",
                            "pass": (
                                decoded.nickname.casefold() == expected_nickname.casefold()
                                if isinstance(decoded.nickname, str)
                                and isinstance(expected_nickname, str)
                                else expected_nickname is None
                            ),
                        }
            result["cache_validation"] = validation
            LOGGER.info("PC cache validation details: %s", validation)
        if result.get("session_lua_run_id") != lua_run_id:
            LOGGER.warning(
                "Session PC cache rejected after Lua run changed: expected=%s observed=%s",
                result.get("session_lua_run_id"),
                lua_run_id,
            )
            accepted = False
            payload = {
                **payload,
                "error": payload.get("error") or "Lua run ID changed before cache confirmation.",
            }
        if accepted and address != result.get("session_pc_first_record_address"):
            accepted = False
            payload = {**payload, "error": "Lua confirmed a different PC base address."}
        if accepted:
            result["pc_storage_resolver_status"] = "resolved for this session"
            result["pc_storage_resolver_error"] = None
        else:
            error = str(payload.get("error") or "Lua rejected the session PC cache.")
            result["pc_storage_resolver_status"] = (
                "stale" if result.get("session_lua_run_id") != lua_run_id
                else "cache-confirmation-failed"
            )
            result["pc_storage_resolver_error"] = error
            result["pc_storage_rediscovery_required"] = (
                result["pc_storage_resolver_status"] == "stale"
            )
            summary = result.setdefault("pc_storage_discovery_summary", {})
            if isinstance(summary, dict):
                summary["resolver_error"] = error
        self._store_pc_discovery_result(result, source)
        with self._pc_discovery_lock:
            pending = self._pc_cache_install
            if pending is not None:
                pending.update({
                    "lua_received": True,
                    "lua_validation": "pass" if accepted else "fail",
                    "confirmation_received": True,
                    "last_lua_run_id": lua_run_id,
                    "last_confirmation_at": time.monotonic(),
                    "error": None if accepted else result.get("pc_storage_resolver_error"),
                })
            self._pc_discovery_progress = {
                **self._pc_discovery_progress,
                "status": "completed" if accepted else "failed",
                "pc_storage_resolver_status": result["pc_storage_resolver_status"],
                "session_pc_first_record_address": result.get(
                    "session_pc_first_record_address"
                ),
                "error": None if accepted else result.get("pc_storage_resolver_error"),
                "cache_install": dict(pending) if pending is not None else None,
            }
        LOGGER.info(
            "Lua session PC cache acknowledgement: run_id=%s accepted=%s address=%s",
            lua_run_id,
            accepted,
            address,
        )

    @staticmethod
    def _record_addresses_for_identity(
        records: list[dict[str, Any]], stable_id: str | None
    ) -> list[dict[str, Any]]:
        if not stable_id:
            return []
        return [record for record in records if record.get("stable_id") == stable_id]

    @staticmethod
    def _inspection_address(record: dict[str, Any]) -> int | None:
        value = record.get("address")
        if not isinstance(value, str):
            return None
        try:
            return int(value, 0)
        except ValueError:
            return None

    @classmethod
    def _ram_change_summary(
        cls,
        before: bytes,
        after: bytes,
        focus_addresses: list[int],
        *,
        max_regions: int = 48,
    ) -> dict[str, Any]:
        if len(before) != len(after):
            return {"error": "Inspection snapshots have different RAM lengths."}
        focus_ranges = [
            (max(NDS_MAIN_RAM_BASE, address - 0x1000),
             min(NDS_MAIN_RAM_BASE + len(before), address + 0x10000))
            for address in focus_addresses
        ]
        total_changed = 0
        sequence = 0
        top_heap: list[tuple[int, int, int, dict[str, Any]]] = []
        focus_regions: list[dict[str, Any]] = []
        focus_region_count = 0
        run_start: int | None = None
        last_changed: int | None = None
        run_changed = 0

        def retain_region(start: int, last: int, changed: int) -> None:
            nonlocal sequence, focus_region_count
            absolute_start = NDS_MAIN_RAM_BASE + start
            absolute_end = NDS_MAIN_RAM_BASE + last + 1
            region = {
                "address_start": f"0x{absolute_start:08X}",
                "address_end_exclusive": f"0x{absolute_end:08X}",
                "changed_bytes": changed,
                "span_bytes": last + 1 - start,
            }
            sequence += 1
            item = (changed, last + 1 - start, sequence, region)
            if len(top_heap) < max_regions:
                heapq.heappush(top_heap, item)
            elif item[:2] > top_heap[0][:2]:
                heapq.heapreplace(top_heap, item)
            if any(
                absolute_start < focus_end and absolute_end > focus_start
                for focus_start, focus_end in focus_ranges
            ):
                focus_region_count += 1
                if len(focus_regions) < max_regions:
                    focus_regions.append(region)
                else:
                    smallest = min(
                        range(len(focus_regions)),
                        key=lambda index: focus_regions[index]["changed_bytes"],
                    )
                    if changed > focus_regions[smallest]["changed_bytes"]:
                        focus_regions[smallest] = region

        for offset, (old_byte, new_byte) in enumerate(zip(before, after)):
            if old_byte == new_byte:
                continue
            total_changed += 1
            if run_start is None:
                run_start = offset
                run_changed = 0
            elif last_changed is not None and offset - last_changed > 64:
                retain_region(run_start, last_changed, run_changed)
                run_start = offset
                run_changed = 0
            last_changed = offset
            run_changed += 1
        if run_start is not None and last_changed is not None:
            retain_region(run_start, last_changed, run_changed)

        regions = [item[3] for item in sorted(top_heap, reverse=True)]
        focus_regions.sort(key=lambda region: int(region["address_start"], 16))
        return {
            "changed_bytes": total_changed,
            "changed_region_count": sequence,
            "largest_changed_regions": regions,
            "focus_region_count": focus_region_count,
            "focus_changed_regions": focus_regions,
        }

    def _compare_and_advance_inspection_baseline(
        self,
        accumulator: PCStorageDiscoveryAccumulator,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        current_records = result.get("pc_storage_inspection_valid_record_index", [])
        if not isinstance(current_records, list):
            current_records = []
        current_identity = result.get("pc_storage_inspection_primary_stable_id")
        identity = result.get("pc_storage_inspection_identity")
        if not isinstance(identity, dict):
            identity = {}
        current_anchor = accumulator.anchor_address
        current_matches = [
            record
            for record in self._record_addresses_for_identity(
                current_records, current_identity
            )
            if record.get("party_overlap") is False
        ]
        with self._pc_discovery_lock:
            baseline = self._pc_inspection_baseline

        if baseline is None:
            with self._pc_discovery_lock:
                self._pc_inspection_baseline = {
                    "scan_id": accumulator.scan_id,
                    "stable_id": current_identity,
                    "identity": identity,
                    "identity_addresses": current_matches,
                    "anchor_address": current_anchor,
                }
            return {
                "status": "baseline captured",
                "baseline_scan_id": accumulator.scan_id,
                "stable_id": current_identity,
                "baseline_addresses": [
                    record.get("address") for record in current_matches
                ],
                "movement_candidates": [],
            }

        stable_id = baseline.get("stable_id") or current_identity
        previous_matches = [
            record
            for record in baseline.get("identity_addresses", [])
            if record.get("stable_id") == stable_id
            and record.get("party_overlap") is False
        ]
        previous_addresses = [
            self._inspection_address(record)
            for record in previous_matches
            if self._inspection_address(record) is not None
        ]
        current_addresses = [
            self._inspection_address(record)
            for record in current_matches
            if self._inspection_address(record) is not None
        ]
        movement_candidates = []
        for old_address in previous_addresses:
            for new_address in current_addresses:
                delta = new_address - old_address
                movement_candidates.append(
                    {
                        "from_address": f"0x{old_address:08X}",
                        "to_address": f"0x{new_address:08X}",
                        "delta_bytes": delta,
                        "delta_hex": f"{delta:+#x}",
                        "delta_multiple_of_136": delta % PC_BOX_RECORD_SIZE == 0,
                        "delta_multiple_of_236": delta % 236 == 0,
                    }
                )
        comparison = {
            "status": "compared",
            "baseline_scan_id": baseline.get("scan_id"),
            "current_scan_id": accumulator.scan_id,
            "stable_id": stable_id,
            "baseline_addresses": [f"0x{address:08X}" for address in previous_addresses],
            "current_addresses": [f"0x{address:08X}" for address in current_addresses],
            "movement_detected": set(previous_addresses) != set(current_addresses),
            "movement_candidates": movement_candidates[:64],
            "baseline_advanced": True,
        }
        with self._pc_discovery_lock:
            self._pc_inspection_baseline = {
                "scan_id": accumulator.scan_id,
                "stable_id": stable_id,
                "identity": identity or baseline.get("identity", {}),
                "identity_addresses": current_matches,
                "anchor_address": current_anchor,
            }
        return comparison

    def _fail_pc_pokemon_search_locked(
        self,
        search: dict[str, Any],
        error: str,
        source: str = "file",
    ) -> None:
        if self._pc_pokemon_search is not search:
            return
        self._pc_pokemon_search = None
        scan_id = str(search.get("scan_id") or f"search-failed-{time.monotonic_ns()}")
        anchor = int(search.get("anchor_address") or 0)
        result = {
            "type": "pc_storage_discovery_result",
            "run_id": scan_id,
            "pc_storage_discovery_requested": True,
            "pc_storage_discovery_result": True,
            "pc_storage_pokemon_search_result": True,
            "pc_storage_pokemon_inspection_result": False,
            "pc_storage_inspection_anchor_address": f"0x{anchor:08X}" if anchor else None,
            "pc_storage_anchor_error": error,
            "pc_storage_discovery_summary": {"mode": "pokemon_search", "error": error},
            "pc_storage_search_matches": [],
            "pc_storage_movement_comparison": None,
        }
        self._pc_discovery_progress = {
            "scan_id": search.get("scan_id"),
            "mode": "pokemon_search",
            "status": "failed",
            "error": error,
            "source": source,
        }
        self._store_pc_discovery_result(result, source)
        if search.get("scan_id"):
            self._acknowledge_pc_pokemon_search(str(search["scan_id"]), source)

    def _record_pc_pokemon_search_event(
        self, payload: dict[str, Any], source: str
    ) -> None:
        message_type = payload.get("type")
        scan_id = str(payload.get("scan_id") or "")
        with self._pc_discovery_lock:
            search = self._pc_pokemon_search
            if search is None:
                LOGGER.warning("Ignoring Pokemon search event without an active search: %s", message_type)
                return
            if search.get("scan_id") is None and scan_id:
                search["scan_id"] = scan_id
            if search.get("scan_id") not in (None, scan_id):
                LOGGER.warning(
                    "Ignoring Pokemon search event for inactive scan: type=%s id=%s active=%s",
                    message_type,
                    scan_id,
                    search.get("scan_id"),
                )
                return

            if message_type == "pc_pokemon_search_started":
                identity = search.get("identity", {})
                try:
                    total_bytes = int(payload.get("total_bytes") or 0)
                    event_identity = (
                        self._event_address(payload.get("pid")),
                        self._event_address(payload.get("checksum")),
                        int(payload.get("species_id") or 0),
                    )
                    expected_identity = (
                        int(str(identity.get("pid")), 0),
                        int(str(identity.get("checksum")), 0),
                        int(identity.get("species_id") or 0),
                    )
                except (TypeError, ValueError):
                    self._fail_pc_pokemon_search_locked(
                        search, "Lua announced an invalid Pokemon search identity.", source
                    )
                    return
                if total_bytes != NDS_MAIN_RAM_SIZE or event_identity != expected_identity:
                    self._fail_pc_pokemon_search_locked(
                        search,
                        "Lua announced an invalid Pokemon identity search range or identity.",
                        source,
                    )
                    return
                party_count = int(payload.get("party_count") or 0)
                party_records = self._event_address(payload.get("party_records_address"))
                party_valid = payload.get("party_range_valid") is True
                record_size = int(payload.get("party_record_size") or 0)
                if (
                    party_count < 0
                    or party_count > 6
                    or record_size != 236
                    or party_valid
                    and (
                        party_records is None
                        or party_records < NDS_MAIN_RAM_BASE
                        or party_records + party_count * record_size
                        > NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
                    )
                ):
                    party_valid = False
                    party_records = None
                    party_count = 0
                search.update(
                    {
                        "scan_id": scan_id,
                        "status": "scanning",
                        "total_bytes": total_bytes,
                        "party_records_address": party_records,
                        "party_count": party_count,
                        "party_range_valid": party_valid,
                        "source": source,
                    }
                )
                self._pc_discovery_progress = {
                    "scan_id": scan_id,
                    "mode": "pokemon_search",
                    "status": "scanning",
                    "progress_percent": 0,
                    "bytes_scanned": 0,
                    "total_bytes": total_bytes,
                    "current_address": f"0x{NDS_MAIN_RAM_BASE:08X}",
                    "matches_found": 0,
                    "source": source,
                }
                LOGGER.info(
                    "Python received Pokemon identity search start: scan=%s anchor=0x%08X "
                    "party_range_valid=%s",
                    scan_id,
                    search["anchor_address"],
                    party_valid,
                )
                return

            if message_type == "pc_pokemon_search_progress":
                try:
                    total_bytes = int(payload.get("total_bytes") or 0)
                    bytes_scanned = int(payload.get("bytes_scanned") or 0)
                except (TypeError, ValueError):
                    self._fail_pc_pokemon_search_locked(
                        search, "Lua reported malformed Pokemon search progress.", source
                    )
                    return
                if (
                    total_bytes != NDS_MAIN_RAM_SIZE
                    or not 0 <= bytes_scanned <= total_bytes
                ):
                    self._fail_pc_pokemon_search_locked(
                        search, "Lua reported invalid Pokemon search progress.", source
                    )
                    return
                search.update(
                    {
                        "status": "scanning",
                        "bytes_scanned": bytes_scanned,
                        "current_address": str(payload.get("current_address") or "--"),
                    }
                )
                self._pc_discovery_progress = {
                    "scan_id": scan_id,
                    "mode": "pokemon_search",
                    "status": "scanning",
                    "progress_percent": min(100, bytes_scanned * 100 // total_bytes),
                    "bytes_scanned": bytes_scanned,
                    "total_bytes": total_bytes,
                    "current_address": search["current_address"],
                    "matches_found": len(search["matches"]),
                    "elapsed_seconds": max(0.0, time.monotonic() - search["started_at"]),
                    "source": source,
                }
                return

            if message_type == "pc_pokemon_search_match":
                try:
                    address = self._event_address(payload.get("address"))
                    record_hex = payload.get("record_hex")
                    if (
                        address is None
                        or address < NDS_MAIN_RAM_BASE
                        or address + PC_BOX_RECORD_SIZE > NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
                        or not isinstance(record_hex, str)
                        or len(record_hex) != PC_BOX_RECORD_SIZE * 2
                    ):
                        raise ValueError("Lua match omitted a valid address or 136-byte record.")
                    record_bytes = bytes.fromhex(record_hex)
                    decoded = decode_box_pokemon(record_bytes, address)
                    identity = search["identity"]
                    if (
                        not decoded.checksum_valid
                        or decoded.pid != int(str(identity["pid"]), 0)
                        or decoded.checksum != int(str(identity["checksum"]), 0)
                        or decoded.species_id != int(identity["species_id"])
                    ):
                        raise ValueError("Lua candidate did not match the anchored Pokemon identity.")
                except (TypeError, ValueError) as exc:
                    self._fail_pc_pokemon_search_locked(
                        search, f"Discovery match validation failed: {exc}", source
                    )
                    return
                party_slot = None
                party_start = search.get("party_records_address")
                if search.get("party_range_valid") and party_start is not None:
                    for index in range(search.get("party_count", 0)):
                        slot_start = party_start + index * 236
                        if address < slot_start + 236 and address + PC_BOX_RECORD_SIZE > slot_start:
                            party_slot = index + 1
                            break
                matches = search["matches"]
                matches[address] = {
                    "address": f"0x{address:08X}",
                    "pid": f"0x{decoded.pid:08X}",
                    "checksum": f"0x{decoded.checksum:04X}",
                    "species_id": decoded.species_id,
                    "nickname": decoded.nickname,
                    "stable_id": decoded.stable_id,
                    "checksum_valid": True,
                    "party_overlap": party_slot is not None if search.get("party_range_valid") else None,
                    "party_slot": party_slot,
                }
                return

            if message_type == "pc_pokemon_search_cancelled":
                self._pc_pokemon_search = None
                result = {
                    "type": "pc_storage_discovery_result",
                    "run_id": scan_id,
                    "pc_storage_discovery_requested": True,
                    "pc_storage_discovery_result": True,
                    "pc_storage_pokemon_search_result": True,
                    "pc_storage_discovery_summary": {
                        "mode": "pokemon_search",
                        "cancelled": True,
                        "bytes_scanned": int(payload.get("bytes_scanned") or 0),
                    },
                    "pc_storage_search_matches": [],
                    "pc_storage_inspection_anchor_address": f"0x{search['anchor_address']:08X}",
                }
                self._pc_discovery_progress = {
                    "scan_id": scan_id,
                    "mode": "pokemon_search",
                    "status": "cancelled",
                    "progress_percent": 0,
                    "source": source,
                }
                self._store_pc_discovery_result(result, source)
                if scan_id:
                    self._acknowledge_pc_pokemon_search(scan_id, source)
                return

            if message_type == "pc_pokemon_search_failed":
                self._fail_pc_pokemon_search_locked(
                    search,
                    str(payload.get("error") or "Lua Pokemon identity search failed."),
                    source,
                )
                return

            if message_type != "pc_pokemon_search_end":
                return
            try:
                total_bytes = int(payload.get("total_bytes") or 0)
                bytes_scanned = int(payload.get("bytes_scanned") or 0)
                expected_matches = int(payload.get("match_count") or 0)
            except (TypeError, ValueError):
                self._fail_pc_pokemon_search_locked(
                    search,
                    "Discovery transport error: malformed Pokemon search end counts.",
                    source,
                )
                return
            if (
                total_bytes != NDS_MAIN_RAM_SIZE
                or bytes_scanned != total_bytes
                or expected_matches != len(search["matches"])
            ):
                self._fail_pc_pokemon_search_locked(
                    search,
                    "Discovery transport error: Pokemon search end counts do not match received matches.",
                    source,
                )
                return

            matches = sorted(search["matches"].values(), key=lambda item: int(item["address"], 16))
            non_party_matches = [item for item in matches if item.get("party_overlap") is False]
            with_baseline = search.get("baseline_addresses", [])
            old_addresses = sorted(
                {
                    int(item.get("address"), 16)
                    for item in with_baseline
                    if isinstance(item, dict) and isinstance(item.get("address"), str)
                }
            )
            new_addresses = sorted(int(item["address"], 16) for item in non_party_matches)
            movement_candidates = []
            new_match_by_address = {
                int(item["address"], 16): item for item in non_party_matches
            }
            for old in old_addresses:
                for new in new_addresses:
                    movement_candidates.append(
                        {
                            "from_address": f"0x{old:08X}",
                            "to_address": f"0x{new:08X}",
                            "delta_bytes": new - old,
                            "delta_hex": f"{new - old:+#x}",
                            "delta_multiple_of_136": (new - old) % PC_BOX_RECORD_SIZE == 0,
                            "old_checksum_valid": True,
                            "new_checksum_valid": True,
                            "old_party_overlap": False,
                            "new_party_overlap": new_match_by_address[new].get(
                                "party_overlap"
                            ),
                        }
                    )
                    if len(movement_candidates) >= 64:
                        break
                if len(movement_candidates) >= 64:
                    break
            identity = search["identity"]
            party_range = None
            if search.get("party_range_valid") and search.get("party_records_address") is not None:
                party_start = search["party_records_address"]
                party_range = {
                    "start_address": f"0x{party_start:08X}",
                    "end_address_exclusive": f"0x{party_start + search['party_count'] * 236:08X}",
                    "record_size": 236,
                    "party_count": search["party_count"],
                }
            result = {
                "type": "pc_storage_discovery_result",
                "run_id": scan_id,
                "frame": payload.get("frame"),
                "pc_storage_discovery_requested": True,
                "pc_storage_discovery_result": True,
                "pc_storage_pokemon_search_result": True,
                "pc_storage_pokemon_inspection_result": False,
                "pc_storage_inspection_anchor_address": f"0x{search['anchor_address']:08X}",
                "pc_storage_discovery_summary": {
                    "mode": "pokemon_search",
                    "bytes_scanned": bytes_scanned,
                    "total_bytes": total_bytes,
                    "match_count": len(matches),
                    "non_party_match_count": len(non_party_matches),
                    "elapsed_ms": int(payload.get("elapsed_ms") or 0),
                    "party_range_available": party_range is not None,
                    "party_range": party_range,
                },
                "pc_storage_search_identity": identity,
                "pc_storage_search_matches": matches,
                "pc_storage_movement_comparison": {
                    "status": "compared",
                    "baseline_scan_id": search.get("baseline_scan_id"),
                    "current_scan_id": scan_id,
                    "stable_id": identity.get("stable_id"),
                    "baseline_addresses": [f"0x{address:08X}" for address in old_addresses],
                    "current_addresses": [f"0x{address:08X}" for address in new_addresses],
                    "movement_detected": set(old_addresses) != set(new_addresses),
                    "movement_candidates": movement_candidates[:64],
                },
            }
            self._pc_pokemon_search = None
            self._pc_inspection_baseline = {
                **(self._pc_inspection_baseline or {}),
                "scan_id": scan_id,
                "identity_addresses": non_party_matches,
            }
            self._pc_discovery_progress = {
                "scan_id": scan_id,
                "mode": "pokemon_search",
                "status": "completed",
                "progress_percent": 100,
                "bytes_scanned": bytes_scanned,
                "total_bytes": total_bytes,
                "matches_found": len(matches),
                "source": source,
            }
        LOGGER.info(
            "Python completed Pokemon identity search: scan=%s bytes=%d matches=%d non_party=%d movement=%s",
            scan_id,
            bytes_scanned,
            len(matches),
            len(non_party_matches),
            result["pc_storage_movement_comparison"]["movement_detected"],
        )
        self._store_pc_discovery_result(result, source)
        self._acknowledge_pc_pokemon_search(scan_id, source)

    def _fail_pc_structure_pointer_search_locked(
        self, search: dict[str, Any], error: str, source: str = "file"
    ) -> None:
        if self._pc_structure_pointer_search is not search:
            return
        self._pc_structure_pointer_search = None
        scan_id = str(search.get("scan_id") or f"pointer-search-failed-{time.monotonic_ns()}")
        result = {
            "type": "pc_storage_discovery_result",
            "run_id": scan_id,
            "pc_storage_discovery_requested": True,
            "pc_storage_discovery_result": True,
            "pc_storage_structure_pointer_search_result": True,
            "pc_storage_discovery_summary": {
                "mode": "structure_pointer_search",
                "error": error,
            },
            "pc_storage_pointer_search_header_address": f"0x{search['header_address']:08X}",
            "pc_storage_pointer_search_first_record_address": f"0x{search['first_record_address']:08X}",
            "pc_storage_pointer_references": [],
            "pc_storage_anchor_error": error,
        }
        self._pc_discovery_progress = {
            "scan_id": search.get("scan_id"),
            "mode": "structure_pointer_search",
            "status": "failed",
            "error": error,
            "source": source,
        }
        self._store_pc_discovery_result(result, source)

    def _record_pc_structure_pointer_search_event(
        self, payload: dict[str, Any], source: str
    ) -> None:
        message_type = payload.get("type")
        scan_id = str(payload.get("scan_id") or "")
        result: dict[str, Any] | None = None
        error: str | None = None
        with self._pc_discovery_lock:
            search = self._pc_structure_pointer_search
            if search is None:
                LOGGER.warning(
                    "Ignoring structure pointer event without an active scan: %s",
                    message_type,
                )
                return
            if search.get("scan_id") is None and scan_id:
                search["scan_id"] = scan_id
            if search.get("scan_id") not in (None, scan_id):
                LOGGER.warning(
                    "Ignoring structure pointer event for inactive scan: %s",
                    scan_id,
                )
                return

            if message_type == "pc_structure_pointer_search_started":
                header = self._event_address(payload.get("header_address"))
                first_record = self._event_address(payload.get("first_record_address"))
                runtime_pointer_slot = self._event_address(
                    payload.get("runtime_pointer_slot")
                )
                runtime_base = self._event_address(payload.get("runtime_base_address"))
                try:
                    total_bytes = int(payload.get("total_bytes") or 0)
                except (TypeError, ValueError):
                    total_bytes = 0
                if (
                    total_bytes != NDS_MAIN_RAM_SIZE
                    or header != search["header_address"]
                    or first_record != search["first_record_address"]
                    or runtime_pointer_slot != PLATINUM_PARTY_POINTER_ADDRESS
                    or runtime_base is not None
                    and not NDS_MAIN_RAM_BASE
                    <= runtime_base
                    < NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
                ):
                    error = "Lua announced a different pointer-search range or target."
                else:
                    now = time.monotonic()
                    search.update(
                        {
                            "status": "scanning",
                            "bytes_scanned": 0,
                            "current_address": f"0x{NDS_MAIN_RAM_BASE:08X}",
                            "started_at": now,
                            "source": source,
                            "runtime_pointer_slot": runtime_pointer_slot,
                            "runtime_base_address": runtime_base,
                        }
                    )
                    self._pc_discovery_progress = {
                        "scan_id": scan_id,
                        "mode": "structure_pointer_search",
                        "status": "scanning",
                        "progress_percent": 0,
                        "bytes_scanned": 0,
                        "total_bytes": total_bytes,
                        "current_address": f"0x{NDS_MAIN_RAM_BASE:08X}",
                        "matches_found": 0,
                        "source": source,
                    }
                    LOGGER.info(
                        "Python received PC structure pointer scan start: scan=%s "
                        "header=0x%08X first_record=0x%08X",
                        scan_id,
                        header,
                        first_record,
                    )
            elif message_type == "pc_structure_pointer_search_progress":
                try:
                    total_bytes = int(payload.get("total_bytes") or 0)
                    bytes_scanned = int(payload.get("bytes_scanned") or 0)
                    reference_count = int(payload.get("reference_count") or 0)
                except (TypeError, ValueError):
                    total_bytes = bytes_scanned = 0
                    reference_count = -1
                if (
                    total_bytes != NDS_MAIN_RAM_SIZE
                    or not 0 <= bytes_scanned <= total_bytes
                    or reference_count < 0
                ):
                    error = "Lua reported invalid pointer-search progress."
                else:
                    search.update(
                        {
                            "status": "scanning",
                            "bytes_scanned": bytes_scanned,
                            "current_address": str(payload.get("current_address") or "--"),
                            "reference_count": reference_count,
                        }
                    )
                    self._pc_discovery_progress = {
                        "scan_id": scan_id,
                        "mode": "structure_pointer_search",
                        "status": "scanning",
                        "progress_percent": min(100, bytes_scanned * 100 // total_bytes),
                        "bytes_scanned": bytes_scanned,
                        "total_bytes": total_bytes,
                        "current_address": search["current_address"],
                        "matches_found": reference_count,
                        "elapsed_seconds": max(
                            0.0, time.monotonic() - search["started_at"]
                        ),
                        "source": source,
                    }
            elif message_type in {
                "pc_structure_pointer_search_failed",
                "pc_structure_pointer_search_cancelled",
            }:
                cancelled = message_type.endswith("_cancelled")
                try:
                    bytes_scanned = int(payload.get("bytes_scanned") or 0)
                except (TypeError, ValueError):
                    bytes_scanned = 0
                summary = {
                    "mode": "structure_pointer_search",
                    "bytes_scanned": bytes_scanned,
                    "total_bytes": NDS_MAIN_RAM_SIZE,
                    "cancelled": cancelled,
                }
                if not cancelled:
                    error = str(payload.get("error") or "Lua pointer search failed.")
                    summary["error"] = error
                result = {
                    "type": "pc_storage_discovery_result",
                    "run_id": scan_id or f"pointer-search-{time.monotonic_ns()}",
                    "frame": payload.get("frame"),
                    "pc_storage_discovery_requested": True,
                    "pc_storage_discovery_result": True,
                    "pc_storage_structure_pointer_search_result": True,
                    "pc_storage_discovery_summary": summary,
                    "pc_storage_pointer_search_header_address": f"0x{search['header_address']:08X}",
                    "pc_storage_pointer_search_first_record_address": f"0x{search['first_record_address']:08X}",
                    "pc_storage_pointer_references": [],
                    "pc_storage_anchor_error": error,
                }
                self._pc_structure_pointer_search = None
                self._pc_discovery_progress = {
                    "scan_id": scan_id,
                    "mode": "structure_pointer_search",
                    "status": "cancelled" if cancelled else "failed",
                    "progress_percent": min(100, bytes_scanned * 100 // NDS_MAIN_RAM_SIZE),
                    "error": error,
                    "source": source,
                }
            elif message_type == "pc_structure_pointer_search_end":
                try:
                    total_bytes = int(payload.get("total_bytes") or 0)
                    bytes_scanned = int(payload.get("bytes_scanned") or 0)
                    reference_count = int(payload.get("reference_count") or 0)
                    references = payload.get("references")
                    header = self._event_address(payload.get("header_address"))
                    first_record = self._event_address(payload.get("first_record_address"))
                    runtime_pointer_slot = self._event_address(
                        payload.get("runtime_pointer_slot")
                    )
                    runtime_base = self._event_address(
                        payload.get("runtime_base_address")
                    )
                    if not isinstance(references, list):
                        raise TypeError("Lua omitted the pointer reference array.")
                    normalized = []
                    allowed_targets = {
                        search["header_address"],
                        search["first_record_address"],
                    }
                    for reference in references:
                        if not isinstance(reference, dict):
                            raise TypeError("Lua returned a malformed pointer reference.")
                        ref_address = self._event_address(reference.get("reference_address"))
                        target_address = self._event_address(reference.get("target_address"))
                        if (
                            ref_address is None
                            or ref_address < NDS_MAIN_RAM_BASE
                            or ref_address + 4 > NDS_MAIN_RAM_BASE + NDS_MAIN_RAM_SIZE
                            or ref_address % 4 != 0
                            or target_address not in allowed_targets
                        ):
                            raise ValueError("Lua returned an invalid or unrelated pointer address.")
                        normalized.append(
                            {
                                "reference_address": f"0x{ref_address:08X}",
                                "target_address": f"0x{target_address:08X}",
                                "target": "header"
                                if target_address == search["header_address"]
                                else "first_record",
                            }
                        )
                except (TypeError, ValueError) as exc:
                    error = f"Pointer search result validation failed: {exc}"
                else:
                    if (
                        total_bytes != NDS_MAIN_RAM_SIZE
                        or bytes_scanned != total_bytes
                        or reference_count < len(normalized)
                        or header != search["header_address"]
                        or first_record != search["first_record_address"]
                        or runtime_pointer_slot != PLATINUM_PARTY_POINTER_ADDRESS
                        or runtime_base != search.get("runtime_base_address")
                    ):
                        error = "Pointer search end counts or targets did not match the request."
                    else:
                        result = {
                            "type": "pc_storage_discovery_result",
                            "run_id": scan_id,
                            "frame": payload.get("frame"),
                            "pc_storage_discovery_requested": True,
                            "pc_storage_discovery_result": True,
                            "pc_storage_structure_pointer_search_result": True,
                            "pc_storage_discovery_summary": {
                                "mode": "structure_pointer_search",
                                "bytes_scanned": bytes_scanned,
                                "total_bytes": total_bytes,
                                "reference_count": reference_count,
                                "reported_reference_count": len(normalized),
                                "references_truncated": reference_count > len(normalized),
                                "elapsed_ms": int(payload.get("elapsed_ms") or 0),
                                "runtime_pointer_slot": f"0x{runtime_pointer_slot:08X}",
                                "runtime_base_address": (
                                    f"0x{runtime_base:08X}"
                                    if runtime_base is not None
                                    else None
                                ),
                                "header_offset_from_runtime_base": (
                                    header - runtime_base
                                    if runtime_base is not None
                                    else None
                                ),
                                "first_record_offset_from_runtime_base": (
                                    first_record - runtime_base
                                    if runtime_base is not None
                                    else None
                                ),
                            },
                            "pc_storage_pointer_search_header_address": f"0x{header:08X}",
                            "pc_storage_pointer_search_first_record_address": f"0x{first_record:08X}",
                            "pc_storage_pointer_references": normalized,
                            "pc_storage_anchor_error": None,
                        }
                        self._pc_structure_pointer_search = None
                        self._pc_discovery_progress = {
                            "scan_id": scan_id,
                            "mode": "structure_pointer_search",
                            "status": "completed",
                            "progress_percent": 100,
                            "bytes_scanned": bytes_scanned,
                            "total_bytes": total_bytes,
                            "matches_found": reference_count,
                            "source": source,
                        }
            else:
                return

            if error is not None and self._pc_structure_pointer_search is search:
                self._fail_pc_structure_pointer_search_locked(search, error, source)
                return
        if result is not None:
            LOGGER.info(
                "Python completed PC structure pointer search: scan=%s references=%d",
                result.get("run_id"),
                result.get("pc_storage_discovery_summary", {}).get("reference_count", 0),
            )
            self._store_pc_discovery_result(result, source)

    def _store_pc_discovery_result(self, payload: dict[str, Any], source: str) -> None:
        line = json.dumps(payload, separators=(",", ":"))
        received = BizHawkHeartbeat(
            received_at=time.monotonic(),
            payload=payload,
            source=source,
            bytes_received=len(line.encode("utf-8")),
        )
        with self._lock:
            self._pc_storage_discovery_payload = received
