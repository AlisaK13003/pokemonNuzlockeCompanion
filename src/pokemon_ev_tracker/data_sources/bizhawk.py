"""BizHawk RAM data source diagnostics."""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from dataclasses import dataclass, replace

from pokemon_ev_tracker.data_sources.base import DataSourceSnapshot, GameDataSource
from pokemon_ev_tracker.games.platinum.battle import (
    BattleBattler,
    active_enemy_battlers,
    decode_battle_battlers,
)
from pokemon_ev_tracker.games.platinum.decoder import PartyState, valid_checksum_count
from pokemon_ev_tracker.games.platinum.pc_storage import (
    BoxedPokemon,
    PCStorageState,
    decode_pc_storage_payload,
)
from pokemon_ev_tracker.games.platinum.player_position import decode_player_position
from pokemon_ev_tracker.games.platinum.profile import PLATINUM_PROFILE
from pokemon_ev_tracker.transport.bizhawk_server import (
    BizHawkDebugServer,
    FriendshipWalkCommandReceipt,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoordinateCaptureRequest:
    accepted: bool
    source: str | None = None
    reason: str | None = None


def _boxed_monitor_identity(mon: BoxedPokemon) -> dict[str, object]:
    return {
        "stable_id": mon.stable_id,
        "box": mon.box_index,
        "slot": mon.slot_index,
        "species": mon.species_name,
        "nickname": mon.decoded.nickname,
        "pid": f"0x{mon.decoded.pid:08X}",
        "checksum": f"0x{mon.decoded.checksum:04X}",
    }


class BizHawkRamDataSource(GameDataSource):
    """Receives RAM-1 heartbeat messages from the EmuHawk Lua script."""

    def __init__(self, server: BizHawkDebugServer | None = None, profile=PLATINUM_PROFILE) -> None:
        self.server = server or BizHawkDebugServer()
        self.profile = profile
        self._last_good_party_by_pid = {}
        self._last_good_party_times: dict[int, float] = {}
        self._party_context = None
        self._party_profile = None
        self._party_decoder = None
        self._party_last_frame = None
        self._party_decode_key = object()
        self._party_decode_state: PartyState | None = None
        self._party_display_key = None
        self._party_display_state: PartyState | None = None
        self._battle_payload_cache = object()
        self._battle_battlers_cache: tuple[BattleBattler, ...] = ()
        self._pc_storage_payload_cache = object()
        self._pc_storage_state_cache: PCStorageState | None = None
        self._pc_storage_stabilizer_payload = object()
        self._pc_storage_stable_pokemon: tuple[BoxedPokemon, ...] = ()
        self._pc_storage_changes: tuple[BoxedPokemon, ...] = ()
        self._pc_storage_stream_identity = None
        self._pc_storage_last_frame: int | None = None
        self._pc_storage_stable_ids: set[str] = set()
        self._pc_storage_sightings: dict[str, tuple[int, int | None]] = {}
        self._pc_storage_last_occupied: dict[str, BoxedPokemon] = {}
        self._pc_storage_monitor_debug: dict[str, object] = {"status": "waiting"}

    @property
    def backend_name(self) -> str:
        return "BizHawk RAM"

    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        self.server.stop()
        self._reset_party_reuse()

    def _reset_party_reuse(self) -> None:
        """Retain at most one decode/display result and six last-good records."""
        self._party_decode_key = object()
        self._party_decode_state = None
        self._party_display_key = None
        self._party_display_state = None
        self._last_good_party_by_pid.clear()
        self._last_good_party_times.clear()

    def _prepare_party_context(self, party_payload) -> None:
        payload = party_payload.payload if party_payload is not None else {}
        context = (getattr(party_payload, "source", None), *(payload.get(key) for key in (
            "run_id", "lua_run_id", "rom_session_identity", "core", "domain", "pointer_value")))
        frame = payload.get("frame")
        rollback = (isinstance(frame, int) and isinstance(self._party_last_frame, int)
                    and frame < self._party_last_frame)
        decoder = self.profile.party_decoder
        if (context != self._party_context or self.profile is not self._party_profile
                or decoder != self._party_decoder or rollback or party_payload is None):
            self._reset_party_reuse()
        self._party_context = context
        self._party_profile = self.profile
        self._party_decoder = decoder
        self._party_last_frame = frame

    def request_coordinate_capture(
        self, capture_id: str, label: str, start_offset: int, length: int
    ) -> CoordinateCaptureRequest:
        party_payload = self.server.latest_party_payload()
        heartbeat = self.server.latest_heartbeat()
        if (
            not self.server.is_connected()
            or not self._party_payload_is_fresh(party_payload)
            or heartbeat is None
        ):
            return CoordinateCaptureRequest(
                False,
                reason="No fresh BizHawk RAM snapshot available.",
            )
        source = party_payload.source
        accepted = self.server.request_coordinate_capture(
            capture_id,
            label,
            start_offset,
            length,
            transport=source,
        )
        if not accepted:
            return CoordinateCaptureRequest(
                False,
                source=source,
                reason=f"Could not send a coordinate capture request through {source} transport.",
            )
        return CoordinateCaptureRequest(True, source=source)

    def set_coordinate_preview(
        self, x_offset: int | None, y_offset: int | None, data_type: str = "u16"
    ) -> bool:
        party_payload = self.server.latest_party_payload()
        if not self.server.is_connected() or not self._party_payload_is_fresh(party_payload):
            return False
        return self.server.set_coordinate_preview(
            x_offset,
            y_offset,
            data_type,
            transport=party_payload.source,
        )

    def drain_coordinate_events(self):
        return self.server.drain_coordinate_events()

    def send_friendship_walk_command(
        self, action: str, value: str | None = None
    ) -> FriendshipWalkCommandReceipt | None:
        if action != "STOP":
            party_payload = self.server.latest_party_payload()
            if not self._party_payload_is_fresh(party_payload):
                return None
            return self.server.send_friendship_walk_command(
                action, value, transport=party_payload.source
            )

        party_payload = self.server.latest_party_payload()
        source = getattr(party_payload, "source", None)
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("file", "tcp")
        )
        return self.server.send_friendship_walk_command("STOP", transport=transports)

    def request_pc_storage_scan(self) -> bool:
        party_payload = self.server.latest_party_payload()
        source = getattr(party_payload, "source", None)
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("tcp", "file")
        )
        return self.server.request_pc_storage_scan(transport=transports)

    def request_pc_save_offset_test(
        self,
        save_ram_directory: str | None = None,
        *,
        expected_nickname: str | None = None,
        expected_species_id: int | None = None,
    ) -> bool:
        party_payload = self.server.latest_party_payload()
        source = getattr(party_payload, "source", None)
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("tcp", "file")
        )
        request = getattr(self.server, "request_pc_save_offset_test", None)
        return bool(
            callable(request)
            and request(
                transport=transports,
                save_ram_directory=save_ram_directory,
                expected_nickname=expected_nickname,
                expected_species_id=expected_species_id,
            )
        )

    def request_pc_sram_pokemon_search(
        self, expected_nickname: str, expected_species_id: int
    ) -> bool:
        party_payload = self.server.latest_party_payload()
        source = getattr(party_payload, "source", None)
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("tcp", "file")
        )
        request = getattr(self.server, "request_pc_sram_pokemon_search", None)
        return bool(
            callable(request)
            and request(
                expected_nickname,
                expected_species_id,
                transport=transports,
            )
        )

    def request_pc_storage_discovery(self, anchor_address: int | None = None) -> bool:
        party_payload = self.server.latest_party_payload()
        source = getattr(party_payload, "source", None)
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("tcp", "file")
        )
        return self.server.request_pc_storage_discovery(
            transport=transports,
            anchor_address=anchor_address,
        )

    def request_pc_storage_session_layout_discovery(
        self, species_id: int, nickname: str | None
    ) -> bool:
        party_payload = self.server.latest_party_payload()
        source = getattr(party_payload, "source", None)
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("tcp", "file")
        )
        request = getattr(
            self.server, "request_pc_storage_session_layout_discovery", None
        )
        lua_run_id = (
            party_payload.payload.get("run_id")
            if party_payload is not None and isinstance(party_payload.payload, dict)
            else None
        )
        return bool(
            callable(request)
            and request(
                species_id, nickname, transport=transports, lua_run_id=lua_run_id
            )
        )

    def retry_pc_storage_session_cache_install(self) -> bool:
        request = getattr(self.server, "retry_session_pc_cache_install", None)
        return bool(callable(request) and request())

    def reset_pc_discovery_for_lua_run(self, lua_run_id: str) -> None:
        self.server.reset_pc_discovery_for_lua_run(lua_run_id)

    def request_pc_pokemon_inspection(self, anchor_address: int) -> bool:
        party_payload = self.server.latest_party_payload()
        source = getattr(party_payload, "source", None)
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("tcp", "file")
        )
        request = getattr(self.server, "request_pc_pokemon_inspection", None)
        return bool(
            callable(request)
            and request(anchor_address, transport=transports)
        )

    def request_pc_pokemon_search(self) -> bool:
        party_payload = self.server.latest_party_payload()
        source = getattr(party_payload, "source", None)
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("tcp", "file")
        )
        request = getattr(self.server, "request_pc_pokemon_search", None)
        return bool(callable(request) and request(transport=transports))

    def request_pc_structure_pointer_search(
        self, header_address: int, first_record_address: int
    ) -> bool:
        party_payload = self.server.latest_party_payload()
        source = getattr(party_payload, "source", None)
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("tcp", "file")
        )
        request = getattr(self.server, "request_pc_structure_pointer_search", None)
        return bool(
            callable(request)
            and request(
                header_address,
                first_record_address,
                transport=transports,
            )
        )

    def reset_pc_pokemon_inspection_baseline(self) -> None:
        reset = getattr(self.server, "reset_pc_pokemon_inspection_baseline", None)
        if callable(reset):
            reset()

    def request_pc_storage_discovery_cancel(self) -> bool:
        progress_reader = getattr(self.server, "pc_storage_discovery_progress", None)
        progress = progress_reader() if callable(progress_reader) else {}
        source = progress.get("source")
        transports = (
            (source, "file" if source == "tcp" else "tcp")
            if source in {"tcp", "file"}
            else ("tcp", "file")
        )
        request = getattr(self.server, "request_pc_storage_discovery_cancel", None)
        return bool(callable(request) and request(transport=transports))

    def snapshot(self) -> DataSourceSnapshot:
        heartbeat = self.server.latest_heartbeat()
        party_payload = self.server.latest_party_payload()
        latest_pc_storage = getattr(self.server, "latest_pc_storage_payload", None)
        pc_storage_payload = latest_pc_storage() if callable(latest_pc_storage) else None
        latest_pc_discovery = getattr(self.server, "latest_pc_storage_discovery_payload", None)
        pc_storage_discovery_payload = (
            latest_pc_discovery() if callable(latest_pc_discovery) else None
        )
        latest_pc_discovery_progress = getattr(
            self.server, "pc_storage_discovery_progress", None
        )
        pc_storage_discovery_progress = (
            latest_pc_discovery_progress()
            if callable(latest_pc_discovery_progress)
            else {"status": "idle"}
        )
        latest_save_test = getattr(
            self.server, "latest_pc_save_offset_test_payload", None
        )
        pc_save_offset_test_payload = (
            latest_save_test() if callable(latest_save_test) else None
        )
        save_test_status_reader = getattr(
            self.server, "pc_save_offset_test_status", None
        )
        pc_save_offset_test_status = (
            save_test_status_reader()
            if callable(save_test_status_reader)
            else {"status": "idle"}
        )
        latest_sram_search = getattr(self.server, "latest_pc_sram_search_payload", None)
        pc_sram_search_payload = (
            latest_sram_search() if callable(latest_sram_search) else None
        )
        sram_search_status_reader = getattr(self.server, "pc_sram_search_status", None)
        pc_sram_search_status = (
            sram_search_status_reader()
            if callable(sram_search_status_reader)
            else {"status": "idle"}
        )
        connected = self.server.is_connected()
        if not connected:
            self._reset_party_reuse()
        party_state = self._decode_party_payload(party_payload)
        display_party_state = self._stabilize_display_party(party_state, party_payload)
        battle_battlers = self._decode_battle_battlers(party_payload)
        pc_storage_state = self._decode_pc_storage_payload(pc_storage_payload)
        payload = party_payload.payload if party_payload is not None else {}
        player_position = decode_player_position(payload)
        party_payload_fresh = self._party_payload_is_fresh(party_payload)
        pc_storage_payload_fresh = self._pc_storage_payload_is_fresh(pc_storage_payload)
        stable_boxed_pokemon = self._stabilize_pc_storage_payload(
            pc_storage_state, pc_storage_payload
        )
        active_enemies = active_enemy_battlers(battle_battlers) if party_payload_fresh else ()
        enemy_summary = (
            ", ".join(f"{battler.species} Lv{battler.level}" for battler in active_enemies)
            or "(none)"
        )
        active_record_indices = [
            battler.battler_index for battler in battle_battlers if battler.active
        ]
        LOGGER.debug(
            "active_enemy_battlers produced: %d - %s; party payload fresh: %s; "
            "individually active battler indices: %s",
            len(active_enemies),
            enemy_summary,
            party_payload_fresh,
            active_record_indices,
        )
        return DataSourceSnapshot(
            backend_name=self.backend_name,
            connected=connected,
            details={
                "host": self.server.host,
                "port": self.server.port,
                "fallback_file": str(self.server.fallback_file),
                "heartbeat": heartbeat,
                "party_payload": party_payload,
                "pc_storage_payload": pc_storage_payload,
                "pc_storage_discovery_payload": pc_storage_discovery_payload,
                "pc_storage_discovery_progress": pc_storage_discovery_progress,
                "pc_save_offset_test_payload": pc_save_offset_test_payload,
                "pc_save_offset_test_status": pc_save_offset_test_status,
                "pc_sram_search_payload": pc_sram_search_payload,
                "pc_sram_search_status": pc_sram_search_status,
                "pc_storage_payload_fresh": pc_storage_payload_fresh,
                "pc_storage_state": pc_storage_state,
                "pc_storage_stable_pokemon": stable_boxed_pokemon,
                "pc_storage_changes": self._pc_storage_changes,
                "pc_storage_monitor_debug": self._pc_storage_monitor_debug,
                "party_payload_fresh": party_payload_fresh,
                "party_state": party_state,
                "display_party_state": display_party_state,
                "battle_battlers": battle_battlers,
                "active_enemy_battlers": active_enemies,
                "player_position": player_position,
                "friendship_walk_enabled": payload.get("friendship_walk_enabled"),
                "friendship_walk_mode": payload.get("friendship_walk_mode"),
                "friendship_walk_direction": payload.get("friendship_walk_direction"),
                "friendship_walk_requested_direction": payload.get(
                    "friendship_walk_requested_direction"
                ),
                "friendship_walk_injected_direction": payload.get(
                    "friendship_walk_injected_direction"
                ),
                "friendship_walk_b_injected": payload.get("friendship_walk_b_injected"),
                "friendship_walk_reversal_until_frame": payload.get(
                    "friendship_walk_reversal_until_frame"
                ),
                "friendship_walk_pause_reason": payload.get("friendship_walk_pause_reason"),
                "friendship_walk_ack_sequence": payload.get("friendship_walk_ack_sequence"),
                "friendship_walk_ack_frame": payload.get("friendship_walk_ack_frame"),
                "friendship_walk_ack_action": payload.get("friendship_walk_ack_action"),
                "ram_source": getattr(party_payload, "source", None),
                "ram_age_seconds": (
                    max(0.0, time.monotonic() - party_payload.received_at)
                    if party_payload is not None
                    else None
                ),
            },
        )

    def _party_payload_is_fresh(self, party_payload) -> bool:
        if party_payload is None:
            return False
        received_at = getattr(party_payload, "received_at", None)
        if not isinstance(received_at, (int, float)):
            return False
        stale_after = getattr(self.server, "stale_after_seconds", 5.0)
        return max(0.0, time.monotonic() - received_at) <= stale_after

    def _pc_storage_payload_is_fresh(self, pc_storage_payload) -> bool:
        if pc_storage_payload is None:
            return False
        received_at = getattr(pc_storage_payload, "received_at", None)
        if not isinstance(received_at, (int, float)):
            return False
        stale_after = getattr(self.server, "stale_after_seconds", 5.0)
        return max(0.0, time.monotonic() - received_at) <= stale_after

    def _decode_pc_storage_payload(self, pc_storage_payload) -> PCStorageState | None:
        if pc_storage_payload is self._pc_storage_payload_cache:
            return self._pc_storage_state_cache
        self._pc_storage_payload_cache = pc_storage_payload
        payload = pc_storage_payload.payload if pc_storage_payload is not None else None
        self._pc_storage_state_cache = decode_pc_storage_payload(payload)
        return self._pc_storage_state_cache

    def _stabilize_pc_storage_payload(
        self, state: PCStorageState | None, pc_storage_payload
    ) -> tuple[BoxedPokemon, ...]:
        if pc_storage_payload is self._pc_storage_stabilizer_payload:
            return self._pc_storage_stable_pokemon
        self._pc_storage_stabilizer_payload = pc_storage_payload
        self._pc_storage_changes = ()
        if state is None or not state.available:
            self._pc_storage_sightings.clear()
            self._pc_storage_stable_pokemon = ()
            self._pc_storage_monitor_debug = {
                "status": "invalid",
                "reason": getattr(state, "error", None) or "No validated PC snapshot",
                "previous": [
                    _boxed_monitor_identity(mon)
                    for mon in self._pc_storage_last_occupied.values()
                ],
                "current": [],
                "added": [],
                "removed": [],
                "stable_new": [],
            }
            return self._pc_storage_stable_pokemon

        payload = pc_storage_payload.payload if pc_storage_payload is not None else {}
        stream_identity = (
            (payload.get("lua_run_id") or payload.get("run_id"),
             payload.get("rom_session_identity"))
            if payload.get("pc_storage_resolver_kind") == "session_pc_cache"
            else (payload.get("run_id"), state.save_data_pointer)
        )
        current = {mon.stable_id: mon for mon in state.valid_pokemon}
        if stream_identity != self._pc_storage_stream_identity:
            self._pc_storage_stream_identity = stream_identity
            self._pc_storage_stable_ids = set(current)
            self._pc_storage_sightings.clear()
            self._pc_storage_last_frame = state.scan_frame
            self._pc_storage_stable_pokemon = state.valid_pokemon
            self._pc_storage_last_occupied = current
            self._pc_storage_monitor_debug = {
                "status": "baseline",
                "frame": state.scan_frame,
                "stream_identity": stream_identity,
                "previous": [],
                "current": [_boxed_monitor_identity(mon) for mon in current.values()],
                "added": [],
                "removed": [],
                "stable_new": [],
                "reason": "First validated PC snapshot for this Lua/ROM session",
            }
            LOGGER.debug(
                "PC monitoring ready: %d boxed Pokemon in the current snapshot.",
                len(current),
            )
            LOGGER.debug(
                "PC monitor baseline: frame=%s stream=%s occupied=%s",
                state.scan_frame, stream_identity, sorted(current),
            )
            return self._pc_storage_stable_pokemon

        if state.scan_frame == self._pc_storage_last_frame:
            return self._pc_storage_stable_pokemon

        previous_occupied = self._pc_storage_last_occupied
        added_ids = current.keys() - previous_occupied.keys()
        removed_ids = previous_occupied.keys() - current.keys()
        previous_sightings = self._pc_storage_sightings
        current_sightings: dict[str, tuple[int, int | None]] = {}
        stable: dict[str, BoxedPokemon] = {}
        changes = []
        for mon in state.valid_pokemon:
            if mon.stable_id in self._pc_storage_stable_ids:
                stable[mon.stable_id] = mon
                continue
            previous = previous_sightings.get(mon.stable_id)
            consecutive = (
                previous is not None
                and previous[0] == mon.decoded.checksum
                and state.scan_frame is not None
                and previous[1] is not None
                and state.scan_frame > previous[1]
            )
            if consecutive:
                self._pc_storage_stable_ids.add(mon.stable_id)
                stable[mon.stable_id] = mon
                changes.append(mon)
            else:
                current_sightings[mon.stable_id] = (mon.decoded.checksum, state.scan_frame)

        self._pc_storage_sightings = current_sightings
        self._pc_storage_last_frame = state.scan_frame
        self._pc_storage_stable_pokemon = tuple(stable.values())
        self._pc_storage_changes = tuple(changes)
        self._pc_storage_monitor_debug = {
            "status": "scanned",
            "frame": state.scan_frame,
            "stream_identity": stream_identity,
            "previous": [_boxed_monitor_identity(mon) for mon in previous_occupied.values()],
            "current": [_boxed_monitor_identity(mon) for mon in current.values()],
            "added": [_boxed_monitor_identity(current[identity]) for identity in sorted(added_ids)],
            "removed": [
                _boxed_monitor_identity(previous_occupied[identity])
                for identity in sorted(removed_ids)
            ],
            "stable_new": [_boxed_monitor_identity(mon) for mon in changes],
            "pending_stability": [
                _boxed_monitor_identity(current[identity])
                for identity in sorted(current_sightings)
            ],
        }
        LOGGER.debug(
            "PC monitor frame=%s stream=%s previous=%s current=%s added=%s removed=%s stable_new=%s pending=%s",
            state.scan_frame, stream_identity, sorted(previous_occupied), sorted(current),
            sorted(added_ids), sorted(removed_ids),
            [mon.stable_id for mon in changes], sorted(current_sightings),
        )
        for identity in sorted(added_ids):
            LOGGER.debug(
                "PC monitor newly occupied record frame=%s details=%s stable=%s",
                state.scan_frame, _boxed_monitor_identity(current[identity]),
                identity in self._pc_storage_stable_ids,
            )
        self._pc_storage_last_occupied = current
        return self._pc_storage_stable_pokemon

    def _decode_battle_battlers(self, party_payload) -> tuple[BattleBattler, ...]:
        if party_payload is self._battle_payload_cache:
            return self._battle_battlers_cache
        self._battle_payload_cache = party_payload
        payload = party_payload.payload if party_payload is not None else {}
        raw_records = {index: payload.get(f"battle_battler_{index}_raw_hex") for index in range(4)}
        self._battle_battlers_cache = decode_battle_battlers(
            raw_records,
            payload.get("pointer_value"),
            self.profile.memory_profile,
        )
        return self._battle_battlers_cache

    def _stabilize_display_party(
        self, party_state: PartyState | None, party_payload
    ) -> PartyState | None:
        self._prepare_party_context(party_payload)
        if party_state is None:
            return None

        now = time.monotonic()
        lifetime = getattr(self.server, "stale_after_seconds", 5.0)
        expired = [pid for pid, sampled_at in self._last_good_party_times.items()
                   if max(0.0, now - sampled_at) > lifetime]
        for pid in expired:
            self._last_good_party_times.pop(pid)
            self._last_good_party_by_pid.pop(pid, None)
        if expired:
            self._party_display_key = None
        received_at = getattr(party_payload, "received_at", None)
        sampled_at = received_at if isinstance(received_at, (int, float)) else now
        # Time is deliberately excluded from reuse, but expiration runs above on
        # every poll. Missing timestamps (legacy callers) cannot refresh old data
        # merely by polling the same state again.
        key = (party_state, received_at)
        if self._party_display_key is not None and (
                self._party_display_key[0] is party_state
                and self._party_display_key[1] == received_at):
            return self._party_display_state

        if not party_state.party_count_valid or party_state.party_count is None:
            return party_state

        checksummed_pids = {
            pokemon.decoded.diagnostics.pid
            for pokemon in party_state.pokemon
            if pokemon.checksum_valid
        }
        if all(pokemon.checksum_valid for pokemon in party_state.pokemon):
            self._last_good_party_by_pid = {
                pid: pokemon
                for pid, pokemon in self._last_good_party_by_pid.items()
                if pid in checksummed_pids
            }
            self._last_good_party_times = {
                pid: timestamp for pid, timestamp in self._last_good_party_times.items()
                if pid in checksummed_pids}

        display_members = []
        invalid_slots = []
        stale_slots = []
        invalid_battle_slots = []
        stale_battle_slots = []
        for pokemon in party_state.pokemon:
            pid = pokemon.decoded.diagnostics.pid
            previous = self._last_good_party_by_pid.get(pid)
            if not pokemon.checksum_valid:
                invalid_slots.append(pokemon.slot)
                if previous is not None:
                    display_members.append(replace(previous, slot=pokemon.slot, sample_stale=True))
                    stale_slots.append(pokemon.slot)
                continue

            current = (replace(pokemon, sample_stale=False, battle_stats_stale=False)
                       if pokemon.sample_stale or pokemon.battle_stats_stale else pokemon)
            if not pokemon.decoded.diagnostics.battle_stats_valid:
                invalid_battle_slots.append(pokemon.slot)
                has_sane_previous = (
                    previous is not None
                    and previous.level is not None
                    and previous.current_hp is not None
                    and previous.max_hp is not None
                    and previous.current_stats is not None
                )
                if has_sane_previous:
                    current = replace(
                        current,
                        level=previous.level,
                        current_hp=previous.current_hp,
                        max_hp=previous.max_hp,
                        current_stats=previous.current_stats,
                        battle_stats_stale=True,
                    )
                    stale_battle_slots.append(pokemon.slot)
                else:
                    current = replace(
                        current,
                        level=None,
                        current_hp=None,
                        max_hp=None,
                        current_stats=None,
                        battle_stats_stale=True,
                    )
                self._last_good_party_by_pid[pid] = current
            else:
                self._last_good_party_by_pid[pid] = current
                self._last_good_party_times[pid] = sampled_at
            display_members.append(current)

        # Invalid tails must never renew the lifetime of sane battle stats. A
        # checksum-valid record without sane stats is also bounded and expires.
        for pid in self._last_good_party_by_pid:
            self._last_good_party_times.setdefault(pid, sampled_at)
        while len(self._last_good_party_by_pid) > 6:
            oldest = min(self._last_good_party_times, key=self._last_good_party_times.get)
            self._last_good_party_times.pop(oldest)
            self._last_good_party_by_pid.pop(oldest)

        warnings = []
        if invalid_slots:
            slots = ", ".join(str(slot) for slot in invalid_slots)
            if stale_slots:
                warnings.append(
                    f"RAM checksum failed for slot(s) {slots}; showing the last valid matching-PID sample."
                )
            else:
                warnings.append(f"Waiting for checksum-valid RAM data in slot(s) {slots}.")
        if invalid_battle_slots:
            slots = ", ".join(str(slot) for slot in invalid_battle_slots)
            if stale_battle_slots:
                warnings.append(
                    f"Battle stats failed validation in slot(s) {slots}; keeping last sane level/HP and stats."
                )
            else:
                warnings.append(
                    f"Battle stats failed validation in slot(s) {slots}; level/HP withheld."
                )

        members = tuple(display_members)
        result = (party_state if not warnings and members == party_state.pokemon else replace(
            party_state, pokemon=members, live_read_warning=" ".join(warnings) or None))
        self._party_display_key = key
        self._party_display_state = result
        return result

    def _decode_party_payload(self, party_payload) -> PartyState | None:
        self._prepare_party_context(party_payload)
        if party_payload is None:
            return None
        payload = party_payload.payload
        candidates = payload.get("party_candidates", [])
        candidates = candidates if isinstance(candidates, (list, tuple)) else ()
        # Compare only decoder inputs, including all candidate addresses/counts.
        # Copy the small key so in-place edits to the protocol's mutable dict are
        # detectable. Raw hex strings themselves are immutable and not copied.
        key = (payload.get("raw_party_hex"), payload.get("party_address"),
               payload.get("party_count"), tuple(
                   (candidate.get("raw_party_hex"), candidate.get("address"),
                    candidate.get("party_count"))
                   for candidate in candidates if isinstance(candidate, dict)))
        if key == self._party_decode_key:
            return self._party_decode_state
        result = self._decode_party_inputs(payload, candidates)
        self._party_decode_key = deepcopy(key)
        self._party_decode_state = result
        return result

    def _decode_party_inputs(self, payload, candidates) -> PartyState | None:
        raw_hex = payload.get("raw_party_hex")
        decoded_primary = None
        if isinstance(raw_hex, str) and raw_hex:
            decoded_primary = self._decode_raw_party(
                raw_hex,
                _parse_hex_int(payload.get("party_address")),
                payload.get("party_count"),
            )

        decoded_candidates = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_raw = candidate.get("raw_party_hex")
            if not isinstance(candidate_raw, str) or not candidate_raw:
                continue
            decoded = self._decode_raw_party(
                candidate_raw,
                _parse_hex_int(candidate.get("address")),
                candidate.get("party_count"),
            )
            if decoded is not None:
                decoded_candidates.append((candidate, decoded))

        best = self._best_candidate(decoded_candidates)
        if best is not None:
            candidate, party = best
            verified = PartyState(
                party.party_count,
                party.party_count_valid,
                party.pokemon,
                error=party.error,
                candidate_count=len(decoded_candidates),
            )
            return verified
        if decoded_primary is not None:
            return PartyState(
                decoded_primary.party_count,
                decoded_primary.party_count_valid,
                decoded_primary.pokemon,
                decoded_primary.error,
                candidate_count=len(decoded_candidates),
            )
        if decoded_candidates:
            candidate, party = decoded_candidates[0]
            return PartyState(
                party.party_count,
                False,
                (),
                error=(
                    f"Scanned {len(decoded_candidates)} non-empty candidate(s), "
                    "but none had valid Pokemon checksums."
                ),
                candidate_count=len(decoded_candidates),
            )
        return decoded_primary

    def _decode_raw_party(
        self,
        raw_hex: str,
        base_address: int | None,
        fallback_count,
    ) -> PartyState | None:
        try:
            raw_party = bytes.fromhex(raw_hex)
        except ValueError as exc:
            return PartyState(None, False, (), f"Invalid raw party hex: {exc}")
        try:
            return self.profile.party_decoder(raw_party, base_address=base_address)
        except ValueError as exc:
            return PartyState(fallback_count, False, (), str(exc))

    def _best_candidate(
        self, candidates: list[tuple[dict, PartyState]]
    ) -> tuple[dict, PartyState] | None:
        viable = [
            (candidate, party)
            for candidate, party in candidates
            if party.party_count_valid
            and party.party_count
            and valid_checksum_count(party) == party.party_count
        ]
        if not viable:
            return None
        return max(
            viable,
            key=lambda item: (
                valid_checksum_count(item[1]),
                item[1].party_count or 0,
            ),
        )


def _parse_hex_int(value) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None
