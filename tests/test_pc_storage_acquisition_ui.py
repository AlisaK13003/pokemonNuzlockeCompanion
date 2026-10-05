from __future__ import annotations

import os
import time
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.core.ev_targets import EVTargetStore
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.data_sources.base import DataSourceSnapshot
from pokemon_ev_tracker.data_sources.bizhawk import BizHawkRamDataSource
from pokemon_ev_tracker.games.platinum.decoder import PartyState
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.games.platinum.pc_storage import PCStorageState
from pokemon_ev_tracker.games.platinum.profile import PLATINUM_PROFILE
from pokemon_ev_tracker.transport.bizhawk_server import BizHawkDebugServer
from pokemon_ev_tracker.ui.main_window import MainWindow


class _CommandSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


class _UiBizHawkSource(BizHawkRamDataSource):
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _SnapshotSource:
    profile = PLATINUM_PROFILE

    def __init__(self, snapshot: DataSourceSnapshot) -> None:
        self.current_snapshot = snapshot
        self.pc_scan_requests = 0
        self.pc_discovery_requests = 0
        self.pc_search_requests = 0
        self.pc_pointer_search_requests = []
        self.pc_layout_requests = []
        self.pc_cache_retry_requests = 0
        self.pc_save_offset_test_requests = []
        self.pc_sram_search_requests = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def snapshot(self) -> DataSourceSnapshot:
        return self.current_snapshot

    def request_pc_storage_scan(self) -> bool:
        self.pc_scan_requests += 1
        return True

    def request_pc_save_offset_test(
        self,
        save_ram_directory: str | None = None,
        *,
        expected_nickname: str | None = None,
        expected_species_id: int | None = None,
    ) -> bool:
        self.pc_save_offset_test_requests.append(
            (save_ram_directory, expected_nickname, expected_species_id)
        )
        return True

    def request_pc_sram_pokemon_search(
        self, expected_nickname: str, expected_species_id: int
    ) -> bool:
        self.pc_sram_search_requests.append((expected_nickname, expected_species_id))
        return True

    def request_pc_storage_discovery(self) -> bool:
        self.pc_discovery_requests += 1
        return True

    def request_pc_storage_session_layout_discovery(
        self, species_id: int, nickname: str | None
    ) -> bool:
        self.pc_layout_requests.append((species_id, nickname))
        return True

    def retry_pc_storage_session_cache_install(self) -> bool:
        self.pc_cache_retry_requests += 1
        return True

    def request_pc_pokemon_search(self) -> bool:
        self.pc_search_requests += 1
        return True

    def request_pc_structure_pointer_search(
        self, header_address: int, first_record_address: int
    ) -> bool:
        self.pc_pointer_search_requests.append((header_address, first_record_address))
        return True


def _box_mon(
    *,
    species_id: int = 63,
    species_name: str = "Abra",
    nickname: str = "abra",
    met_level: int = 6,
    pid: int = 0xABCDEF01,
    met_location_id: int = 0x12,
    met_location_name: str = "Route 203",
) -> SimpleNamespace:
    stable_id = f"pid:{pid:08X}:ot:1234:5678"
    decoded = SimpleNamespace(
        stable_id=stable_id,
        species_id=species_id,
        nickname=nickname,
        met_location_id=met_location_id,
        met_level=met_level,
        origin_game=12,
        egg_location_id=0,
        is_egg=False,
        pid=pid,
        checksum=0x1234,
        calculated_checksum=0x1234,
        checksum_valid=True,
    )
    return SimpleNamespace(
        decoded=decoded,
        stable_id=decoded.stable_id,
        species_id=decoded.species_id,
        species_name=species_name,
        nickname=decoded.nickname,
        met_location_name=met_location_name,
        met_level=decoded.met_level,
        level=decoded.met_level,
        checksum_valid=True,
        box_index=1,
        slot_index=1,
    )


def _storage_state(records=(), frame: int = 60) -> PCStorageState:
    return PCStorageState(
        available=True,
        error=None,
        save_data_pointer=0x020711C8,
        save_data_body_address=0x020711C8,
        save_data_struct_address_inferred=0x020711B4,
        page_info_address=0x02091428,
        page_location=0x8000,
        page_size=0x121D0,
        pc_boxes_address=0x020791C8,
        records_address=0x020791CC,
        records_offset=0x791CC,
        scan_frame=frame,
        scan_duration_ms=2,
        records=records,
        page_id=37,
        page_block_id=1,
        bytes_read=73_440,
        final_address_valid=True,
        pc_boxes_header_hex="00 " * 64,
    )


def _snapshot(
    boxed=(),
    *,
    frame: int = 60,
    party_state: PartyState | None = None,
    pc_payload_extra: dict | None = None,
    pc_discovery_progress: dict | None = None,
) -> DataSourceSnapshot:
    party_state = party_state or PartyState(0, True, ())
    party_payload = SimpleNamespace(
        source="file",
        received_at=time.monotonic(),
        payload={"run_id": "lua-session", "frame": frame, "domain": "Main RAM"},
    )
    pc_payload_data = {
        "run_id": "lua-session",
        "frame": frame,
        "lua_build_id": "pc-storage-v16-save-offset-probe",
        "lua_game_id": "pokemon-platinum",
        "lua_game_version": "gen4-platinum-us",
        "lua_script_source": "@C:/Workspace/Projects/evTracker/bizhawk/ev_tracker.lua",
        "pc_storage_reader_version": 4,
        "page_id_expected": 37,
        "pc_storage_resolver_status": "unresolved",
        "pc_storage_acquisition_enabled": False,
    }
    if pc_payload_extra:
        pc_payload_data.update(pc_payload_extra)
    pc_payload = SimpleNamespace(
        source="file",
        received_at=time.monotonic(),
        bytes_received=128,
        payload=pc_payload_data,
    )
    state = _storage_state(boxed, frame)
    return DataSourceSnapshot(
        backend_name="BizHawk RAM",
        connected=True,
        details={
            "heartbeat": None,
            "party_payload": party_payload,
            "party_payload_fresh": True,
            "party_state": party_state,
            "display_party_state": party_state,
            "battle_battlers": (),
            "active_enemy_battlers": (),
            "pc_storage_payload": pc_payload,
            "pc_storage_payload_fresh": True,
            "pc_storage_state": state,
            "pc_storage_stable_pokemon": boxed,
            "pc_storage_discovery_progress": pc_discovery_progress or {"status": "idle"},
        },
    )


def test_stable_boxed_catch_flows_from_ram_snapshot_into_nuzlocke(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    store = NuzlockeStore(tmp_path / "runs.json")
    store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    source = _SnapshotSource(_snapshot())
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=store,
    )
    window.refresh_timer.stop()

    window._refresh_ram_backend_debug()
    boxed = _box_mon()
    source.current_snapshot = _snapshot((boxed,), frame=180)
    window._refresh_ram_backend_debug()

    debug_text = window.pc_storage_details.toPlainText()
    assert "Storage source: file / Main RAM" in debug_text
    assert "Lua build ID: pc-storage-v16-save-offset-probe" in debug_text
    assert "Lua configured game: pokemon-platinum / gen4-platinum-us" in debug_text
    assert "Lua script source: @C:/Workspace/Projects/evTracker/bizhawk/ev_tracker.lua" in debug_text
    assert "Lua PC reader version: 4" in debug_text
    assert "SavePageInfo: not used by the session-local PC resolver" in debug_text
    assert "Resolved PC record array start: 0x020791C8" in debug_text
    assert "Record size: 0x88 (136 bytes)" in debug_text
    assert "Expected boxes: 18; slots per box: 30; total slots: 540" in debug_text
    assert "Box 1 Slot 1" in debug_text
    assert "Species: Abra (#63)" in debug_text
    assert "Checksum: VALID" in debug_text
    assert window.pc_storage_discovery_button.text() == "Discover PC Storage Address"
    assert not window.pc_storage_advanced_section.is_expanded
    assert not window.pc_storage_discovery_button.isVisible()
    assert window.pc_save_offset_test_button.text() == "Test Save-File PC Offsets (fallback)"
    assert window.pc_save_offset_test_status_label.text() == "idle"
    assert window.pc_sram_search_button.text() == "Search SRAM for Box Pokemon"
    assert window.pc_sram_search_status_label.text() == "idle"
    assert window.pc_save_offset_directory_edit.placeholderText().startswith(
        "Select BizHawk's NDS\\SaveRAM directory"
    )
    assert window.pc_discover_current_layout_button.text() == (
        "Rediscover PC Layout"
    )
    assert window.pc_layout_nickname_edit.text() == "mitsu"
    assert window.pc_layout_species_combo.currentData() == 415
    assert window.pc_storage_resolver_status_label.text() == "PC resolver: unresolved"
    assert window.pc_storage_discovery_status_label.text() == "idle"
    assert window.pc_storage_inspect_button.text() == "Inspect Pokemon Anchor"
    assert window.pc_storage_search_button.text() == "Search RAM for This Pokemon"
    assert window.pc_storage_inspection_status_label.text() == "idle"
    assert window.pc_storage_pointer_search_button.text() == "Search Structure Pointers"
    assert window.pc_storage_pointer_search_status_label.text() == "idle"
    assert window.pc_storage_anchor_address_edit.placeholderText() == "0x02200000"
    assert "No PC storage discovery scan has been run." in (
        window.pc_storage_discovery_details.toPlainText()
    )
    window.pc_storage_scan_button.click()
    assert source.pc_scan_requests == 1
    assert "Scan requested" in window.pc_storage_scan_status_label.text()
    window.pc_save_offset_directory_edit.setText(r"C:\BizHawk\NDS\SaveRAM")
    window.pc_save_offset_test_button.click()
    assert source.pc_save_offset_test_requests == [
        (r"C:\BizHawk\NDS\SaveRAM", "mitsu", 415)
    ]
    assert window.pc_save_offset_test_status_label.text().startswith("queued;")
    assert not window.pc_save_offset_test_button.isEnabled()
    window.pc_sram_search_button.click()
    assert source.pc_sram_search_requests == [("mitsu", 415)]
    assert window.pc_sram_search_status_label.text().startswith("queued;")
    assert not window.pc_sram_search_button.isEnabled()
    window.pc_discover_current_layout_button.click()
    assert source.pc_layout_requests == [(415, "mitsu")]
    assert window.pc_storage_resolver_status_label.text() == "PC resolver: discovering"
    window.pc_storage_discovery_button.click()
    assert source.pc_discovery_requests == 1
    assert window.pc_storage_discovery_status_label.text() == "scanning..."
    window.pc_storage_search_button.click()
    assert source.pc_search_requests == 1
    assert "searching Main RAM" in window.pc_storage_inspection_status_label.text()
    window.pc_storage_anchor_address_edit.setText("0x02028028")
    window.pc_storage_pointer_search_button.click()
    assert source.pc_pointer_search_requests == [(0x02028000, 0x02028028)]
    assert "searching for references" in window.pc_storage_pointer_search_status_label.text()
    pointer_result = SimpleNamespace(
        received_at=time.monotonic(),
        payload={
            "run_id": "pc-pointer-ui-result",
            "frame": 900,
            "pc_storage_discovery_requested": True,
            "pc_storage_discovery_result": True,
            "pc_storage_structure_pointer_search_result": True,
            "pc_storage_discovery_summary": {
                "mode": "structure_pointer_search",
                "bytes_scanned": 0x400000,
                "total_bytes": 0x400000,
                "reference_count": 1,
            },
            "pc_storage_pointer_search_header_address": "0x02028000",
            "pc_storage_pointer_search_first_record_address": "0x02028028",
            "pc_storage_pointer_references": [
                {
                    "reference_address": "0x02001000",
                    "target_address": "0x02028000",
                    "target": "header",
                }
            ],
        },
    )
    window._refresh_pc_storage_discovery_results(pointer_result)
    pointer_text = window.pc_storage_discovery_details.toPlainText()
    assert "PC Structure Pointer References" in pointer_text
    assert "0x02001000 -> 0x02028000 (header)" in pointer_text
    assert window.pc_storage_pointer_search_status_label.text() == "completed: 1 direct reference(s)"

    anchor = 0x02028028
    window._pc_discovery_requested_at = time.monotonic()
    window._pc_anchor_requested_at = time.monotonic()
    window._pc_anchor_requested_address = anchor
    anchor_result = SimpleNamespace(
        received_at=time.monotonic(),
        payload={
            "run_id": "pc-anchor-layout-ui-result",
            "frame": 901,
            "pc_storage_discovery_requested": True,
            "pc_storage_discovery_result": True,
            "pc_storage_anchor_result": True,
            "pc_storage_anchor_result_ok": True,
            "pc_storage_anchor_address": f"0x{anchor:08X}",
            "pc_storage_anchor_diagnostic": {
                "anchor_address": f"0x{anchor:08X}",
                "record_address_ram_valid": True,
                "record_checksum_valid": True,
                "species_id": 415,
                "candidate_pc_boxes_base": "0x02028000",
                "candidate_pc_boxes_base_ram_valid": True,
                "full_540_slot_region_fits_ram": True,
                "header_address": "0x02028000",
                "header_byte_count": 40,
                "header_bytes_hex": "00" * 40,
                "occupied_slots": [
                    {"box": 1, "slot": 1, "address": f"0x{anchor:08X}", "species_id": 415, "nickname": "mitsu"},
                    {"box": 1, "slot": 2, "address": f"0x{anchor + 136:08X}", "species_id": 187, "nickname": "petal"},
                    {"box": 18, "slot": 1, "address": f"0x{anchor + 510 * 136:08X}", "species_id": 258, "nickname": "sprout"},
                    {"box": 18, "slot": 2, "address": f"0x{anchor + 511 * 136:08X}", "species_id": 220, "nickname": "frost"},
                ],
                "invalid_slots": [],
                "pointer_references": [],
            },
            "pc_storage_discovery_summary": {
                "mode": "anchor",
                "box_count": 18,
                "slots_per_box": 30,
                "total_slots": 540,
                "record_size": 136,
                "valid_occupied_records": 4,
                "empty_records": 536,
                "invalid_records": 0,
                "party_range_available": True,
            },
            "pc_storage_discovery_candidates": [
                {
                    "candidate_pc_boxes_base": "0x02028000",
                    "first_box_pokemon_address": f"0x{anchor:08X}",
                    "valid_occupied_records": 4,
                    "empty_records": 536,
                    "invalid_records": 0,
                    "record_size": 136,
                    "sample_records": [],
                }
            ],
            "pc_storage_discovery_identity_matches": [],
            "pc_storage_excluded_party_records": [],
        },
    )
    window._refresh_pc_storage_discovery_results(anchor_result)
    anchor_text = window.pc_storage_discovery_details.toPlainText()
    assert f"Box 1 Slot 1 0x{anchor:08X}: mitsu (Combee, #415)" in anchor_text
    assert f"Box 18 Slot 1 0x{anchor + 510 * 136:08X}: sprout (Mudkip, #258)" in anchor_text
    assert f"Box 18 Slot 2 0x{anchor + 511 * 136:08X}: frost (Swinub, #220)" in anchor_text
    assert "invalid=0" in anchor_text
    search_anchor = anchor
    window._pc_search_requested_address = search_anchor
    search_payload = {
        "run_id": "search-result",
        "pc_storage_discovery_requested": True,
        "pc_storage_discovery_result": True,
        "pc_storage_pokemon_search_result": True,
        "pc_storage_inspection_anchor_address": f"0x{search_anchor:08X}",
        "pc_storage_discovery_summary": {
            "mode": "pokemon_search",
            "bytes_scanned": 0x400000,
            "total_bytes": 0x400000,
            "match_count": 1,
            "non_party_match_count": 1,
        },
        "pc_storage_search_identity": {
            "nickname": "Mitsu",
            "pid": "0x11223344",
            "checksum": "0x1234",
            "species_id": 415,
        },
        "pc_storage_search_matches": [
            {
                "address": f"0x{search_anchor + 136:08X}",
                "nickname": "Mitsu",
                "species_id": 415,
                "checksum_valid": True,
                "party_overlap": False,
                "party_slot": None,
            }
        ],
        "pc_storage_movement_comparison": {
            "baseline_addresses": [f"0x{search_anchor:08X}"],
            "current_addresses": [f"0x{search_anchor + 136:08X}"],
            "movement_detected": True,
            "movement_candidates": [
                {
                    "from_address": f"0x{search_anchor:08X}",
                    "to_address": f"0x{search_anchor + 136:08X}",
                    "delta_bytes": 136,
                    "delta_hex": "+0x88",
                    "old_checksum_valid": True,
                    "new_checksum_valid": True,
                    "old_party_overlap": False,
                    "new_party_overlap": False,
                }
            ],
        },
    }
    window._refresh_pc_storage_discovery_results(
        SimpleNamespace(payload=search_payload, received_at=time.monotonic())
    )
    search_text = window.pc_storage_discovery_details.toPlainText()
    assert "Search RAM for This Pokemon:" in search_text
    assert "Pokemon identity search result" in search_text
    assert "delta=136 bytes (+0x88)" in search_text
    assert window.pc_storage_inspection_status_label.text().startswith(
        "completed; matching Pokemon moved"
    )

    run = store.active_run
    assert run is not None
    assert len(run.acquisition_events) == 1
    event = run.acquisition_events[0]
    assert event.species_name == "Abra"
    assert event.level == 6
    assert event.suggested_location_name == "Route 203"
    assert event.source_location == "BOX"
    assert event.stable_id == boxed.stable_id
    window.close()
    assert app is not None


def test_session_discovered_pc_records_do_not_enable_nuzlocke_acquisition(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    store = NuzlockeStore(tmp_path / "runs.json")
    store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    boxed = _box_mon()
    source = _SnapshotSource(
        _snapshot(
            (boxed,),
            pc_payload_extra={
                "pc_storage_resolver_kind": "session_pc_cache",
                "pc_storage_resolver_status": "resolved for this session",
                "pc_storage_acquisition_enabled": False,
                "lua_run_id": "lua-session",
                "session_pc_run_id": "lua-session",
                "session_pc_first_record_address": "0x02080000",
            },
        )
    )
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=store,
    )
    window.refresh_timer.stop()

    window._refresh_ram_backend_debug()

    run = store.active_run
    assert run is not None
    assert run.acquisition_events == []
    window.close()
    assert app is not None


def test_session_pc_baselines_existing_then_detects_new_boxed_catch(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    store = NuzlockeStore(tmp_path / "runs.json")
    store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    existing = _box_mon(species_id=415, species_name="Combee", nickname="mitsu")
    caught = _box_mon(pid=0x11223344, nickname="newcatch")
    session = {
        "pc_storage_resolver_kind": "session_pc_cache",
        "pc_storage_resolver_status": "resolved for this session",
        "pc_storage_acquisition_enabled": True,
        "lua_run_id": "lua-session",
        "session_pc_run_id": "lua-session",
        "session_pc_first_record_address": "0x02080000",
    }
    source = _SnapshotSource(_snapshot((existing,), pc_payload_extra=session))
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=store,
    )
    window.refresh_timer.stop()

    window._refresh_ram_backend_debug()
    assert store.active_run.acquisition_events == []

    stale = _snapshot((existing,), frame=120, pc_payload_extra=session)
    stale.details["pc_storage_payload_fresh"] = False
    source.current_snapshot = replace(stale, connected=False)
    window._refresh_ram_backend_debug()

    source.current_snapshot = _snapshot(
        (existing, caught), frame=180, pc_payload_extra=session
    )
    window._refresh_ram_backend_debug()
    events = store.active_run.acquisition_events
    assert len(events) == 1
    assert events[0].stable_id == caught.stable_id
    assert events[0].source_location == "BOX"
    window.close()
    assert app is not None


def test_cache_pending_diagnostic_cannot_override_ready_eight_pokemon_baseline(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    store = NuzlockeStore(tmp_path / "runs.json")
    store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    existing = tuple(
        _box_mon(pid=0x10000000 + index, nickname=f"boxed{index}")
        for index in range(8)
    )
    for index, pokemon in enumerate(existing):
        pokemon.slot_index = index + 1
    session = {
        "pc_storage_resolver_kind": "session_pc_cache",
        "pc_storage_resolver_status": "resolved for this session",
        "pc_storage_acquisition_enabled": True,
        "lua_run_id": "lua-session",
        "session_pc_run_id": "lua-session",
        "session_pc_first_record_address": "0x0228B098",
    }
    old_discovery = SimpleNamespace(
        received_at=time.monotonic(),
        payload={
            "run_id": "discovery-result",
            "pc_storage_discovery_requested": True,
            "pc_storage_session_layout_result": True,
            "pc_storage_resolver_status": "cache-pending",
            "session_lua_run_id": "lua-session",
            "session_pc_first_record_address": "0x0228B098",
            "pc_storage_discovery_summary": {"mode": "session_layout"},
        },
    )
    initial = _snapshot(existing, pc_payload_extra=session)
    initial.details["pc_storage_discovery_payload"] = old_discovery
    source = _SnapshotSource(initial)
    window = MainWindow(
        AppSettings(), data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"), nuzlocke_store=store,
    )
    window.refresh_timer.stop()
    window._refresh_ram_backend_debug()

    assert store.active_run.acquisition_events == []
    assert window.pc_storage_resolver_status_label.text() == "PC resolver: baseline-ready"
    assert window.pc_storage_resolver_address_label.text() == "First record: 0x0228B098"
    assert window.pc_storage_baseline_count_label.text() == "Baseline occupied Pokemon: 8"
    assert window.pc_storage_monitoring_label.text() == (
        "Monitoring for new boxed Pokemon: yes"
    )
    assert "PC baseline established" in window.pc_storage_baseline_message_label.text()

    ninth = _box_mon(pid=0x20000000, nickname="newcatch")
    ninth.slot_index = 9
    later = _snapshot((*existing, ninth), frame=180, pc_payload_extra=session)
    later.details["pc_storage_discovery_payload"] = old_discovery
    source.current_snapshot = later
    window._refresh_ram_backend_debug()

    assert [event.stable_id for event in store.active_run.acquisition_events] == [
        ninth.stable_id
    ]
    assert store.active_run.acquisition_events[0].source_location == "BOX"
    assert "Additions since baseline: 1" in window.pc_storage_details.toPlainText()
    assert "Emitted BOX candidates: 1" in window.pc_storage_details.toPlainText()
    assert window.pc_storage_resolver_status_label.text() == "PC resolver: baseline-ready"
    window.close()
    assert app is not None


def test_cache_confirmation_failure_is_visible_and_retry_dispatches(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    progress = {
        "status": "failed",
        "cache_install": {
            "lua_run_id": "lua-session",
            "address": "0x0228B098",
            "command_sent": True,
            "command_queued": False,
            "lua_received": False,
            "lua_validation": "pending",
            "confirmation_received": False,
            "last_lua_run_id": None,
            "last_confirmation_at": None,
            "error": "No Lua cache confirmation within 3s",
        },
    }
    discovery = SimpleNamespace(
        received_at=time.monotonic(),
        payload={
            "run_id": "discovery-result",
            "pc_storage_discovery_requested": True,
            "pc_storage_session_layout_result": True,
            "pc_storage_resolver_status": "cache-confirmation-failed",
            "pc_storage_resolver_error": "No Lua cache confirmation within 3s",
            "session_lua_run_id": "lua-session",
            "session_pc_first_record_address": "0x0228B098",
            "pc_storage_discovery_summary": {"mode": "session_layout"},
            "cache_validation": {
                "run_id_match": {
                    "expected": "lua-session", "actual": "lua-session", "pass": True,
                },
                "main_ram_range": {
                    "first_record": "0x0228B098",
                    "domain_offset": "0x0028B098", "pass": True,
                },
                "anchor_species": {
                    "decoded_species_id": 414, "expected_species_id": 415,
                    "expected_species_name": "Combee", "pass": False,
                },
                "anchor_byte_comparison": {
                    "bytes_match": True,
                    "python_first_64_bytes_hex": "1234",
                    "lua_first_64_bytes_hex": "1234",
                },
            },
        },
    )
    snapshot = _snapshot(pc_payload_extra={
        "pc_storage_resolver_kind": "session_pc_cache",
        "pc_storage_resolver_status": "unresolved",
        "lua_run_id": "lua-session",
    }, pc_discovery_progress=progress)
    snapshot.details["pc_storage_discovery_payload"] = discovery
    source = _SnapshotSource(snapshot)
    window = MainWindow(
        AppSettings(), data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()
    window._refresh_ram_backend_debug()

    assert window.pc_storage_resolver_status_label.text() == (
        "PC resolver: cache-confirmation-failed"
    )
    assert "command sent=True" in window.pc_storage_cache_status_label.text()
    assert "Lua received=False" in window.pc_storage_cache_status_label.text()
    details = window.pc_storage_details.toPlainText()
    assert "PC Cache Validation" in details
    assert "domain_offset=0x0028B098" in details
    assert "expected_species_id=415" in details
    assert "bytes_match=True" in details
    assert window.pc_storage_retry_cache_button.isEnabled()
    window.pc_storage_retry_cache_button.click()
    assert source.pc_cache_retry_requests == 1
    assert window.pc_storage_resolver_status_label.text() == "PC resolver: cache-pending"
    window.close()
    assert app is not None


def _session_pc_snapshot(
    boxed=(), *, run_id="lua-session", resolver="unresolved", enabled=False,
    progress_status="idle", stale=False, frame=60,
) -> DataSourceSnapshot:
    snapshot = _snapshot(
        boxed, frame=frame,
        pc_payload_extra={
            "run_id": run_id,
            "lua_run_id": run_id,
            "pc_storage_resolver_kind": "session_pc_cache",
            "pc_storage_resolver_status": resolver,
            "pc_storage_acquisition_enabled": enabled,
            "session_pc_run_id": run_id if enabled else None,
            "session_pc_first_record_address": "0x0228B098" if enabled else None,
            "pc_storage_rediscovery_required": stale,
        },
        pc_discovery_progress={"status": progress_status, "progress_percent": 42},
    )
    snapshot.details["party_payload"].payload["run_id"] = run_id
    return snapshot


def test_session_pc_auto_discovery_once_and_new_run_restarts_it(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    source = _SnapshotSource(_session_pc_snapshot())
    window = MainWindow(
        AppSettings(), data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()

    window._refresh_ram_backend_debug()
    window._refresh_ram_backend_debug()
    assert source.pc_layout_requests == [(415, "mitsu")]
    assert window.pc_storage_status_label.text() == "Status: Discovering PC layout"
    assert window.pc_storage_progress_label.text() == "Progress: 42%"

    window._pc_storage_tracking_active = True
    window._pc_storage_baseline_ids.add("old-session-pokemon")
    source.current_snapshot = _session_pc_snapshot(run_id="new-lua-run")
    window._refresh_ram_backend_debug()
    window._refresh_ram_backend_debug()
    assert source.pc_layout_requests == [(415, "mitsu"), (415, "mitsu")]
    assert window._pc_lua_run_id == "new-lua-run"
    assert not window._pc_storage_tracking_active
    assert window._pc_storage_baseline_ids == set()
    window.close()
    assert app is not None


def test_session_pc_auto_baseline_monitor_stale_and_manual_recovery(
    tmp_path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    store = NuzlockeStore(tmp_path / "runs.json")
    store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    source = _SnapshotSource(_session_pc_snapshot())
    window = MainWindow(
        AppSettings(), data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=store,
    )
    window.refresh_timer.stop()
    window._refresh_ram_backend_debug()
    assert source.pc_layout_requests == [(415, "mitsu")]

    existing = _box_mon(species_id=415, species_name="Combee", nickname="mitsu")
    source.current_snapshot = _session_pc_snapshot(
        (existing,), resolver="resolved for this session", enabled=True,
        progress_status="completed", frame=120,
    )
    window._refresh_ram_backend_debug()
    assert window._pc_storage_baseline_ids == {existing.stable_id}
    assert window.pc_storage_status_label.text() == "Status: Monitoring"
    assert window.pc_storage_last_event_label.text() == (
        "Last PC event: Baseline established with 1 Pokemon."
    )
    assert window.pc_storage_layout_label.text() == "Box layout: Resolved"
    assert window.nuzlocke_view.detection_sources_label.text() == (
        "Automatic encounter detection: Party + Box"
    )
    assert store.active_run.acquisition_events == []
    assert not window.pc_storage_advanced_section.is_expanded
    assert not window.pc_storage_scan_button.isVisible()
    assert not window.pc_sram_search_button.isVisible()
    assert not window.pc_storage_anchor_recovery.isVisible()
    assert "Resolver" in window.pc_storage_advanced_summary_label.text()
    assert "Acquisition" in window.pc_storage_advanced_summary_label.text()
    window.pc_storage_advanced_section.toggle.click()
    assert window.pc_storage_advanced_section.is_expanded
    assert not window.pc_storage_raw_section.is_expanded
    assert "Lua run: lua-session" in window.pc_storage_advanced_summary_label.text()
    window.pc_storage_advanced_section.toggle.click()

    disconnected = replace(source.current_snapshot, connected=False)
    source.current_snapshot = disconnected
    window._refresh_ram_backend_debug()
    assert window._pc_storage_baseline_ids == {existing.stable_id}
    source.current_snapshot = _session_pc_snapshot(
        (existing,), resolver="resolved for this session", enabled=True,
        progress_status="completed", frame=150,
    )
    window._refresh_ram_backend_debug()
    assert source.pc_layout_requests == [(415, "mitsu")]
    assert window._pc_storage_baseline_ids == {existing.stable_id}

    boxed_catch = _box_mon(pid=0x10203040, nickname="newcatch")
    boxed_catch.slot_index = 2
    source.current_snapshot = _session_pc_snapshot(
        (existing, boxed_catch), resolver="resolved for this session", enabled=True,
        progress_status="completed", frame=180,
    )
    window._refresh_ram_backend_debug()
    assert [event.stable_id for event in store.active_run.acquisition_events] == [
        boxed_catch.stable_id
    ]
    assert "Box 1 Slot 2 - BOX acquisition emitted" in (
        window.pc_storage_last_event_label.text()
    )

    source.current_snapshot = _session_pc_snapshot(
        (existing, boxed_catch), resolver="stale", stale=True, frame=240,
    )
    window._refresh_ram_backend_debug()
    window._refresh_ram_backend_debug()
    assert source.pc_layout_requests == [(415, "mitsu"), (415, "mitsu")]
    assert not window._pc_storage_tracking_active
    assert window.pc_storage_status_label.text() == "Status: Discovering PC layout"

    source.current_snapshot = _session_pc_snapshot(
        (existing, boxed_catch), resolver="resolved for this session", enabled=True,
        progress_status="completed", frame=300,
    )
    window._refresh_ram_backend_debug()
    window.pc_discover_current_layout_button.click()
    assert source.pc_layout_requests[-1] == (415, "mitsu")
    assert len(source.pc_layout_requests) == 3
    window.close()
    assert app is not None


def test_failed_pc_anchor_reveals_recovery_fields(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    source = _SnapshotSource(_session_pc_snapshot())
    window = MainWindow(
        AppSettings(), data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()
    window._refresh_ram_backend_debug()
    failed = _session_pc_snapshot(progress_status="failed")
    failed.details["pc_storage_discovery_payload"] = SimpleNamespace(
        received_at=time.monotonic(),
        payload={
            "pc_storage_discovery_requested": True,
            "pc_storage_session_layout_result": True,
            "pc_storage_resolver_status": "unresolved",
            "pc_storage_discovery_summary": {
                "mode": "session_layout",
                "matching_box1_slot1_records_found": 0,
                "candidate_records_found": 0,
                "error": "No non-party Mitsu Box 1 Slot 1 candidate found.",
            },
        },
    )
    source.current_snapshot = failed
    window._refresh_ram_backend_debug()
    assert not window.pc_storage_anchor_recovery.isHidden()
    assert "Box 1 Slot 1 may be empty" in window.pc_storage_recovery_label.text()
    assert window.pc_storage_status_label.text() == "Status: Monitoring paused"
    window.pc_layout_nickname_edit.setText("newmon")
    window.pc_layout_retry_button.click()
    assert source.pc_layout_requests[-1] == (415, "newmon")
    assert window.settings.pc_box1_nickname == "newmon"
    window.close()
    assert app is not None


def test_pc_anchor_preference_survives_settings_reload(tmp_path) -> None:
    path = tmp_path / "settings.json"
    AppSettings(pc_box1_species_id=190, pc_box1_nickname="newmon").save(path)
    restored = AppSettings.load(path)
    assert restored.pc_box1_species_id == 190
    assert restored.pc_box1_nickname == "newmon"


def test_sram_ab_target_keeps_combee_species_until_reset(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    source = _SnapshotSource(_snapshot())
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()

    window.pc_sram_search_button.click()
    window.pc_layout_species_combo.setCurrentIndex(
        window.pc_layout_species_combo.findData(414)
    )
    window.pc_sram_search_button.setEnabled(True)
    window.pc_sram_search_button.click()
    assert source.pc_sram_search_requests == [("mitsu", 415), ("mitsu", 415)]

    window.pc_sram_reset_target_button.click()
    window.pc_sram_search_button.setEnabled(True)
    window.pc_sram_search_button.click()
    assert source.pc_sram_search_requests[-1] == ("mitsu", 414)
    window.close()
    assert app is not None


def test_stale_session_pc_resolver_requests_one_rediscovery(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    source = _SnapshotSource(_snapshot(pc_payload_extra={
        "pc_storage_resolver_kind": "session_pc_cache",
        "pc_storage_resolver_status": "stale",
        "pc_storage_acquisition_enabled": False,
        "pc_storage_rediscovery_required": True,
        "lua_run_id": "lua-session",
        "session_pc_first_record_address": "0x02080000",
    }))
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()

    window._refresh_ram_backend_debug()
    window._refresh_ram_backend_debug()

    assert source.pc_layout_requests == [(415, "mitsu")]
    window.close()
    assert app is not None


def test_session_layout_button_dispatches_expected_identity_through_bizhawk(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    server = BizHawkDebugServer()
    fake_socket = _CommandSocket()
    server._active_client = fake_socket
    source = _UiBizHawkSource(server=server)
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()

    window.pc_discover_current_layout_button.click()

    assert fake_socket.sent == [b"PC_DISCOVER_CURRENT_LAYOUT|415|6D69747375\n"]
    assert window.pc_storage_resolver_status_label.text() == "PC resolver: discovering"
    window.close()
    assert app is not None


def test_session_layout_result_renders_resolver_and_decoded_occupied_records(
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    window = MainWindow(
        AppSettings(),
        data_source=_SnapshotSource(_snapshot()),
        target_store=EVTargetStore(),
        nuzlocke_store=NuzlockeStore(),
    )
    window.refresh_timer.stop()
    result = SimpleNamespace(
        received_at=time.monotonic(),
        payload={
            "run_id": "session-layout-ui-result",
            "frame": 950,
            "pc_storage_discovery_requested": True,
            "pc_storage_discovery_result": True,
            "pc_storage_session_layout_result": True,
            "pc_storage_resolver_status": "resolved for this session",
            "session_lua_run_id": "lua-session",
            "session_pc_first_record_address": "0x02080000",
            "expected_box1_slot1": {"nickname": "mitsu", "species_id": 415},
            "pc_storage_discovery_summary": {
                "mode": "session_layout",
                "valid_occupied_records": 1,
                "empty_records": 539,
                "invalid_records": 0,
                "party_range_available": True,
                "party_range": {
                    "start_address": "0x023F0000",
                    "end_address_exclusive": "0x023F0000",
                    "record_size": 236,
                    "party_count": 0,
                },
            },
            "pc_storage_discovery_candidates": [
                {
                    "first_box_pokemon_address": "0x02080000",
                    "valid_occupied_records": 1,
                    "empty_records": 539,
                    "invalid_records": 0,
                    "party_region_overlap": False,
                    "plausible": True,
                }
            ],
            "pc_storage_decoded_occupied": [
                {
                    "box": 1,
                    "slot": 1,
                    "address": "0x02080000",
                    "nickname": "mitsu",
                    "species_id": 415,
                    "checksum_valid": True,
                }
            ],
        },
    )

    window._refresh_pc_storage_discovery_results(result)

    text = window.pc_storage_discovery_details.toPlainText()
    assert "Expected Box 1 Slot 1: mitsu / Combee" in text
    assert "Lua run: lua-session" in text
    assert "Resolver status: resolved for this session" in text
    assert "Resolved first record: 0x02080000" in text
    assert "Box 1 Slot 1 0x02080000: mitsu / Combee, checksum=True" in text
    assert window.pc_storage_resolver_status_label.text() == (
        "PC resolver: resolved-awaiting-baseline"
    )
    assert window.pc_storage_resolver_address_label.text() == "First record: 0x02080000"
    window.close()
    assert app is not None


def test_pc_storage_discovery_results_are_separate_and_persist(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    source = _SnapshotSource(
        _snapshot(
            frame=180,
            pc_payload_extra={
                "pc_storage_discovery_requested": True,
                "pc_storage_discovery_summary": {
                    "ram_bytes_read": 4_194_304,
                    "candidate_records_found": 1,
                    "candidate_bases_evaluated": 1,
                    "party_range_available": True,
                    "party_range": {
                        "start_address": "0x02270000",
                        "end_address_exclusive": "0x022700EC",
                        "record_size": 236,
                        "party_count": 1,
                    },
                },
                "pc_storage_excluded_party_records": [
                    {
                        "address": "0x02070000",
                        "party_slot": 1,
                        "species_id": 16,
                        "nickname": "Scout",
                        "checksum_valid": True,
                    }
                ],
                "pc_storage_discovery_identity_matches": [
                    {
                        "address": "0x02080100",
                        "species_id": 415,
                        "nickname": "Mitsu",
                        "checksum_valid": True,
                        "party_overlap": False,
                        "nearby_slot_layout_quality": {
                            "candidate_pc_boxes_base": "0x020800FC",
                            "valid_occupied_records": 1,
                            "empty_records": 539,
                            "invalid_records": 0,
                            "party_region_overlap": False,
                            "plausible": True,
                        },
                    }
                ],
                "pc_storage_discovery_candidates": [
                    {
                        "rank": 1,
                        "candidate_pc_boxes_base": "0x02080000",
                        "first_box_pokemon_address": "0x02080004",
                        "valid_occupied_records": 1,
                        "empty_records": 539,
                        "invalid_records": 0,
                        "confidence_score": 1839,
                        "reasons": ["contains checksum-valid Gen IV boxed Pokemon"],
                        "sample_records": [],
                    }
                ],
                "runtime_base_probe_rows": [
                    {
                        "relative_offset": "0x0000",
                    "address": "0x02070000",
                        "valid_ram": True,
                        "hex": "00" * 16,
                        "u32_le": ["0x00000000"] * 4,
                    }
                ],
            },
            pc_discovery_progress={"status": "completed"},
        )
    )
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()

    window._refresh_ram_backend_debug()
    discovery_text = window.pc_storage_discovery_details.toPlainText()
    assert window.pc_storage_discovery_status_label.text().startswith("completed in ")
    assert "PC Storage Discovery" in discovery_text
    assert "candidate_records_found=1" in discovery_text
    assert "PCBoxes=0x02080000" in discovery_text
    assert "Expected identity matches outside party RAM:" in discovery_text
    assert "0x02080100: Mitsu" in discovery_text
    assert "Excluded party records:" in discovery_text
    assert "0x02070000, party slot 1: Scout/Pidgey (#16)" in discovery_text
    assert "Runtime Pointer Probe" in discovery_text

    source.current_snapshot = _snapshot(frame=240)
    window._refresh_ram_backend_debug()
    assert window.pc_storage_discovery_details.toPlainText() == discovery_text
    window.close()
    assert app is not None


def test_pc_storage_discovery_explicitly_reports_missing_expected_identity(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    source = _SnapshotSource(
        _snapshot(
            frame=180,
            pc_payload_extra={
                "pc_storage_discovery_requested": True,
                "pc_storage_discovery_summary": {
                    "mode": "broad",
                    "party_range_available": True,
                    "party_range": {
                        "start_address": "0x02270000",
                        "end_address_exclusive": "0x022702CC",
                        "record_size": 236,
                        "party_count": 3,
                    },
                },
                "pc_storage_discovery_identity_matches": [],
                "pc_storage_excluded_party_records": [],
                "pc_storage_discovery_candidates": [],
            },
            pc_discovery_progress={"status": "completed"},
        )
    )
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()

    window._refresh_ram_backend_debug()
    discovery_text = window.pc_storage_discovery_details.toPlainText()
    assert "Active party RAM range: [0x02270000, 0x022702CC) (3 x 236 bytes)" in discovery_text
    assert "No matching expected-identity records were found." in discovery_text
    assert "Excluded party records:" in discovery_text
    window.close()
    assert app is not None


def test_pc_storage_discovery_pane_can_expand_and_restore(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    source = _SnapshotSource(_snapshot())
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()
    window.resize(1100, 900)
    window.set_route("diagnostics")
    window.diagnostics_view.set_section("advanced")
    window.show()
    app.processEvents()

    splitter = window.pc_storage_panes_splitter
    assert splitter.count() == 4
    assert splitter.widget(0) is window.pc_storage_details
    assert splitter.widget(2) is window.pc_save_offset_test_details
    assert splitter.widget(3) is window.pc_sram_search_details
    assert not window.pc_storage_discovery_expand_button.isVisible()
    window.pc_storage_advanced_section.toggle.click()
    window.pc_storage_raw_section.toggle.click()
    app.processEvents()
    assert window.pc_storage_discovery_expand_button.isVisible()
    before = splitter.sizes()
    assert before[1] > 0

    window.pc_storage_discovery_expand_button.click()
    app.processEvents()
    expanded = splitter.sizes()
    assert expanded[1] > before[1]
    assert window.pc_storage_discovery_expand_button.text() == "Restore"

    window.pc_storage_discovery_expand_button.click()
    app.processEvents()
    restored = splitter.sizes()
    assert restored[1] < expanded[1]
    assert window.pc_storage_discovery_expand_button.text() == "Expand"
    window.close()
    assert app is not None


def test_live_sram_search_results_render_capture_and_movement_details(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    window = MainWindow(
        AppSettings(),
        data_source=_SnapshotSource(_snapshot()),
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()
    result = {
        "type": "pc_sram_search_result",
        "scan_id": "sram-ui-test",
        "capture_number": 2,
        "lua_build_id": "pc-storage-v20-live-sram-search",
        "lua_run_id": "ui-run",
        "source": "tcp",
        "domain": "SRAM",
        "domain_size": 0x80000,
        "eligible_save_copy_count": 2,
        "expected_identity": {"nickname": "mitsu", "species_id": 415},
        "sha256": "1234",
        "candidate_records_examined": 100,
        "checksum_valid_record_count": 2,
        "target_match_count": 2,
        "target_match_offsets": ["0x01088", "0x41088"],
        "target_match_copy_deltas": [
            {"from": "0x01088", "to": "0x41088", "delta": 0x40000}
        ],
        "layout_strongly_validated": True,
        "conclusion": "The target moved by +0x88 in live SRAM.",
        "available_domains": [
            {
                "name": "SRAM",
                "size": 0x80000,
                "readable": True,
                "save_like": True,
                "eligible_save_copy_count": 2,
            }
        ],
        "target_matches": [
            {
                "offset_hex": "0x01088",
                "species_id": 415,
                "species_name": "Combee",
                "nickname": "mitsu",
                "pid": "0x12345678",
                "checksum": "0xABCD",
                "checksum_valid": True,
                "surrounding_bytes": {"hex": "00" * 40},
                "stride_neighbors": [],
                "nearby_records": [],
                "layout_as_box_1_slot_1": {
                    "first_record_offset_hex": "0x01088",
                    "valid_occupied_count": 1,
                    "empty_count": 539,
                    "invalid_count": 0,
                    "region_fits_domain": True,
                    "plausible": True,
                    "occupied_records": [
                        {
                            "box": 1,
                            "slot": 1,
                            "offset_hex": "0x01088",
                            "nickname": "mitsu",
                            "species_id": 415,
                            "species_name": "Combee",
                        }
                    ],
                },
            }
        ],
        "previous_capture": {
            "available": True,
            "changed_byte_count": 272,
            "changed_range_count": 4,
            "old_target_offsets": ["0x01000"],
            "new_target_offsets": ["0x01088"],
            "movement_pairs": [
                {
                    "old_offset": "0x01000",
                    "new_offset": "0x01088",
                    "delta_hex": "+0x88",
                    "one_box_slot_forward": True,
                }
            ],
            "changed_ranges": [],
        },
    }
    window._refresh_pc_sram_search(
        SimpleNamespace(payload=result),
        {"status": "completed", "capture_number": 2, "target_match_count": 2},
    )
    text = window.pc_sram_search_details.toPlainText()
    assert "SRAM domain: SRAM (524,288 bytes)" in text
    assert "offset candidates fit=2 (not validated)" in text
    assert "mitsu / Combee (#415)" in text
    assert "0x01000 -> 0x01088: delta +0x88" in text
    assert window.pc_sram_search_status_label.text() == "completed; capture 2; target matches 2"
    window.close()
    assert app is not None


def test_save_file_offset_results_render_and_survive_normal_refresh(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    source = _SnapshotSource(_snapshot())
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()
    result = SimpleNamespace(
        payload={
            "test_id": "save-ui-result",
            "capture_number": 2,
            "lua_build_id": "pc-storage-v16-save-offset-probe",
            "lua_game_id": "pokemon-platinum",
            "lua_game_version": "gen4-platinum-us",
            "lua_script_source": "@bizhawk/ev_tracker.lua",
            "lua_run_id": "lua-run-ui-test",
            "lua_rom_name": "Pokemon Platinum",
            "lua_rom_hash": "rom-hash",
            "lua_rom_path": None,
            "lua_rom_path_api": "not exposed",
            "lua_system_id": "NDS",
            "lua_configured_save_ram_path": "./SaveRAM",
            "save_ram_api_available": True,
            "save_ram_flush_call_ok": True,
            "save_ram_flush_semantics": "flushes emulator SaveRAM buffer to disk; does not invoke an in-game save",
            "save_ram_directory": r"C:\BizHawk\NDS\SaveRAM",
            "save_ram_file_path": r"C:\BizHawk\NDS\SaveRAM\platinum.SaveRAM",
            "save_ram_filename_stem": "platinum",
            "save_ram_detection_reason": "modified timestamp changed",
            "save_ram_directory_before": [{"filename": "platinum.SaveRAM"}],
            "save_ram_directory_after": [{"filename": "platinum.SaveRAM"}],
            "save_ram_file_changes": [
                {
                    "filename": "platinum.SaveRAM",
                    "reason": "modified timestamp changed",
                    "after": {"size": 0x80000, "modified_timestamp": "2026-10-02T12:00:00+00:00"},
                }
            ],
            "expected_box_1_slot_1": {"nickname": "mitsu", "species_id": 415},
            "domains": [
                {
                    "name": "SaveRAM",
                    "size": 0x80000,
                    "readable": True,
                    "save_like": True,
                    "eligible_copy_count": 2,
                }
            ],
            "expected": {"record_offsets": ["0x0C104", "0x4C104"]},
            "candidates": [
                {
                    "layout_interpretation": "fixed offset is first Pokemon record",
                    "domain": "SaveRAM",
                    "save_copy_offset_tested": "0x0C104",
                    "first_record_offset": "0x0C104",
                    "header_offset": "0x0C100",
                    "domain_size": 0x80000,
                    "record_region_fits_domain": True,
                    "header_current_box": 0,
                    "header_valid": True,
                    "box_1_slot_1": {
                        "box": 1,
                        "slot": 1,
                        "species_id": 415,
                        "species": "Combee",
                        "nickname": "mitsu",
                        "checksum_valid": True,
                    },
                    "box_1_slot_2": {"empty": True},
                    "occupied_count": 1,
                    "checksum_valid_count": 1,
                    "empty_count": 539,
                    "invalid_count": 0,
                    "occupied_records": [
                        {
                            "box": 1,
                            "slot": 1,
                            "species_id": 415,
                            "species": "Combee",
                            "nickname": "mitsu",
                            "checksum_valid": True,
                        }
                    ],
                    "invalid_records": [],
                }
            ],
            "comparison": {
                "has_previous_capture": True,
                "targets": [
                    {
                        "domain": "SaveRAM",
                        "record_offset": "0x0C104",
                        "interpretation": "fixed offset is first Pokemon record",
                        "status": "changed",
                        "changed_byte_count": 136,
                        "changed_slots": [
                            {"box": 1, "slot": 1},
                            {"box": 1, "slot": 2},
                        ],
                        "changed_slot_count": 2,
                    }
                ],
            },
            "save_ram_identity_search": {
                "identity": {"nickname": "mitsu", "species_id": 415},
                "searched_bytes": 0x80000,
                "alignment_bytes": 4,
                "match_count": 1,
                "matches": [
                    {
                        "offset_hex": "0x0C104",
                        "nickname": "mitsu",
                        "species": "Combee",
                        "species_id": 415,
                        "checksum_valid": True,
                    }
                ],
                "file_comparison": {
                    "has_previous_capture": True,
                    "changed": True,
                    "changed_byte_count": 136,
                    "changed_range_count": 2,
                    "old_matching_offsets": ["0x0C104"],
                    "new_matching_offsets": ["0x0C18C"],
                    "movement_candidates": [
                        {
                            "old_offset_hex": "0x0C104",
                            "new_offset_hex": "0x0C18C",
                            "delta_hex": "+0x88",
                            "checksum_valid_before": True,
                            "checksum_valid_after": True,
                        }
                    ],
                    "changed_ranges": [
                        {"start": "0x0C104", "end_exclusive": "0x0C18C", "length": 136},
                        {"start": "0x0C18C", "end_exclusive": "0x0C214", "length": 136},
                    ],
                },
            },
            "bytes_received": 146896,
        }
    )
    window._refresh_pc_save_offset_test(result, {"status": "completed"})
    text = window.pc_save_offset_test_details.toPlainText()
    assert "SaveRAM fixed offset 0x0C104" in text
    assert "SaveRAM API: client.saveram() available=true; flush call ok=true" in text
    assert r"Active SaveRAM candidate: C:\BizHawk\NDS\SaveRAM\platinum.SaveRAM" in text
    assert "Detection reason: modified timestamp changed" in text
    assert "Loaded ROM path: not exposed by this BizHawk Lua API" in text
    assert "System ID: NDS" in text
    assert "Full SaveRAM identity search:" in text
    assert "checksum-valid matches=1" in text
    assert "Movement: 0x0C104 -> 0x0C18C (+0x88 bytes)" in text
    assert "Capture comparison: changed=True; changed bytes=136" in text
    assert "Box 1 Slot 1: mitsu / Combee (#415), checksum=VALID" in text
    assert "changed bytes=136; changed slots=2" in text
    assert "Box 1 Slot 1 changed" in text
    assert window.pc_save_offset_test_status_label.text() == "completed"

    window._refresh_ram_backend_debug()
    assert window.pc_save_offset_test_details.toPlainText() == text
    window.close()
    assert app is not None


def test_pc_discovery_retry_status_shows_chunk_counts_and_missing_index(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    window = MainWindow(
        AppSettings(),
        data_source=_SnapshotSource(_snapshot()),
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()

    window._refresh_pc_storage_discovery_progress(
        {
            "status": "retrying",
            "progress_percent": 92,
            "received_chunk_count": 943,
            "expected_chunk_count": 1024,
            "missing_chunk_count": 1,
            "missing_chunk_index": 944,
        }
    )

    text = window.pc_storage_discovery_status_label.text()
    assert "scanning 92%" in text
    assert "chunks received: 943/1024" in text
    assert "proven missing: 1" in text
    assert "retrying chunk 944" in text

    window._refresh_pc_storage_discovery_progress(
        {
            "status": "stalled",
            "progress_percent": 88,
            "received_chunk_count": 902,
            "missing_chunk_count": 0,
            "producer_status": "stalled",
        }
    )
    stalled_text = window.pc_storage_discovery_status_label.text()
    assert "chunks received: 902" in stalled_text
    assert "proven missing: 0" in stalled_text
    assert "waiting for producer" in stalled_text
    window.close()
    assert app is not None


def test_pc_storage_discovery_button_dispatches_lua_transport_command(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    server = BizHawkDebugServer()
    fake_socket = _CommandSocket()
    server._active_client = fake_socket
    source = _UiBizHawkSource(server=server)
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()

    window.pc_storage_discovery_button.click()
    assert window.pc_storage_discovery_cancel_button.text() == "Cancel Discovery"
    assert window.pc_storage_discovery_cancel_button.isEnabled()
    window.pc_storage_anchor_address_edit.setText("0x0227E550")
    window.pc_storage_anchor_button.click()
    window.pc_storage_inspect_button.click()

    assert fake_socket.sent == [
        b"PC_DISCOVER_STORAGE\n",
        b"PC_DISCOVER_STORAGE|BOX1_SLOT1|0227E550\n",
        b"PC_INSPECT_POKEMON|0227E550\n",
    ]
    assert window.pc_storage_discovery_status_label.text() == "scanning..."
    assert window.pc_storage_anchor_status_label.text() == (
        "sent 0x0227E550; waiting for Lua response"
    )
    window.pc_storage_discovery_cancel_button.click()
    assert window.pc_storage_discovery_status_label.text() == "cancelling..."
    assert fake_socket.sent[-1] == b"PC_CANCEL_DISCOVERY\n"

    window.pc_storage_anchor_address_edit.setText("not-an-address")
    window.pc_storage_anchor_button.click()
    assert window.pc_storage_anchor_status_label.text().startswith(
        "failed: invalid address"
    )
    assert len(fake_socket.sent) == 4
    window.close()
    assert app is not None


def test_structure_pointer_button_dispatches_through_bizhawk_data_source(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    server = BizHawkDebugServer()
    fake_socket = _CommandSocket()
    server._active_client = fake_socket
    source = _UiBizHawkSource(server=server)
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"),
    )
    window.refresh_timer.stop()
    window.pc_storage_anchor_address_edit.setText("0x02028028")

    window.pc_storage_pointer_search_button.click()

    assert fake_socket.sent == [b"PC_SEARCH_PC_POINTERS|02028000|02028028\n"]
    assert "searching for references" in window.pc_storage_pointer_search_status_label.text()
    window.close()
    assert app is not None


def test_boxed_catch_is_observed_without_valid_party_snapshot(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda _self: None)
    store = NuzlockeStore(tmp_path / "runs.json")
    store.create_run("Platinum", PLATINUM_NUZLOCKE_PROFILE)
    invalid_party = PartyState(
        6,
        False,
        (),
        error="temporary party checksum failure",
    )
    session_pc = {
        "pc_storage_resolver_kind": "session_pc_cache",
        "pc_storage_resolver_status": "resolved for this session",
        "pc_storage_acquisition_enabled": True,
        "lua_run_id": "lua-session",
        "session_pc_run_id": "lua-session",
        "session_pc_first_record_address": "0x02080000",
    }
    source = _SnapshotSource(_snapshot(
        party_state=invalid_party, pc_payload_extra=session_pc,
    ))
    window = MainWindow(
        AppSettings(),
        data_source=source,
        target_store=EVTargetStore(tmp_path / "targets.json"),
        nuzlocke_store=store,
    )
    window.refresh_timer.stop()

    window._refresh_ram_backend_debug()
    skorupi = _box_mon(
        species_id=451,
        species_name="Skorupi",
        nickname="skorupi",
        met_level=20,
        pid=0x5C0A2F17,
        met_location_id=0x16,
        met_location_name="Route 207",
    )
    source.current_snapshot = _snapshot(
        (skorupi,),
        frame=180,
        party_state=invalid_party,
        pc_payload_extra=session_pc,
    )
    window._refresh_ram_backend_debug()

    run = store.active_run
    assert run is not None
    assert len(run.acquisition_events) == 1
    event = run.acquisition_events[0]
    assert event.species_name == "Skorupi"
    assert event.source_location == "BOX"
    assert event.suggested_location_name == "Route 207"
    assert event.stable_id == skorupi.stable_id
    assert "source: BOX" in window.nuzlocke_view.acquisition_status_label.text()
    assert "status: pending" in window.nuzlocke_view.acquisition_status_label.text()
    window.close()
    assert app is not None
