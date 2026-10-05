"""Application window composing the BizHawk-backed Platinum tracker."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QMovie, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QWidget,
)

from pokemon_ev_tracker.config.training_preferences import EVTrainingPreferenceStore
from pokemon_ev_tracker.core.automation.friendship_walk import (
    FriendshipWalkController,
    WalkAxis,
    WalkStatus,
)
from pokemon_ev_tracker.core.ev_changes import EVChangeTracker
from pokemon_ev_tracker.core.ev_targets import EVTargetStore
from pokemon_ev_tracker.core.ev_training import recommend_battle_evs
from pokemon_ev_tracker.core.friendship_training import (
    ETAStatus,
    FriendshipETA,
    FriendshipGoal,
    FriendshipWalkSessionStats,
)
from pokemon_ev_tracker.core.moves import PokemonMoveState, resolve_move
from pokemon_ev_tracker.core.nuzlocke.acquisition import (
    PartyAcquisitionObserver,
)
from pokemon_ev_tracker.core.nuzlocke.death_detection import PartyHpObserver, PartyHpSample
from pokemon_ev_tracker.core.nuzlocke.models import PartyLevel
from pokemon_ev_tracker.core.nuzlocke.run_prompt import NoRunPromptObserver
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.core.nuzlocke.wipe_detection import PartyWipeCandidate, PartyWipeObserver
from pokemon_ev_tracker.data_sources.base import GameDataSource
from pokemon_ev_tracker.games.provider import GameProvider, GameSession
from pokemon_ev_tracker.games.registry import (
    DEFAULT_GAME_ID,
    default_game_provider,
    get_game_provider,
)
from pokemon_ev_tracker.pokemon.gen4.friendship import friendship_label
from pokemon_ev_tracker.pokemon.gen4.structure import (
    HELD_ITEM_BOX_DATA_OFFSET,
    HELD_ITEM_RECORD_OFFSET,
    IVS_BOX_DATA_OFFSET,
    IVS_RECORD_OFFSET,
)
from pokemon_ev_tracker.transport.bizhawk_server import FriendshipWalkCommandReceipt
from pokemon_ev_tracker.ui.app_shell import AppShell
from pokemon_ev_tracker.ui.backend_status import BackendStatusWidget
from pokemon_ev_tracker.ui.compact_nuzlocke import CompactNuzlockeView
from pokemon_ev_tracker.ui.diagnostics_tools import build_advanced_diagnostics
from pokemon_ev_tracker.ui.diagnostics_view import DiagnosticsView
from pokemon_ev_tracker.ui.ev_change_log import EvChangeLogWidget
from pokemon_ev_tracker.ui.ev_target_dialog import EVTargetDialog
from pokemon_ev_tracker.ui.friendship_panel import FriendshipWalkPanel
from pokemon_ev_tracker.ui.item_sprite_loader import (
    get_item_sprite,
    item_sprite_exists,
    item_sprite_path,
    item_sprite_slug,
)
from pokemon_ev_tracker.ui.nuzlocke_view import NuzlockeView
from pokemon_ev_tracker.ui.opponent_panel import CurrentOpponentPanel
from pokemon_ev_tracker.ui.party_card import PartyCard
from pokemon_ev_tracker.ui.sprite_loader import (
    get_animated_sprite_size,
    get_static_sprite,
    resolve_sprite_asset,
)
from pokemon_ev_tracker.ui.theme import apply_theme, refresh_style
from pokemon_ev_tracker.ui.tracker_views import (
    CompactPartyStatsView,
    CompactTrainingView,
    PartyStatsView,
    RecommendationPanel,
    TrainingView,
)
from pokemon_ev_tracker.ui.training_focus_dialog import TrainingFocusDialog

LOGGER = logging.getLogger(__name__)


class _CoordinateAnalysisSignals(QObject):
    completed = Signal(object)


class _NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


def _format_optional_hex(value: int | None) -> str:
    return f"0x{value:08X}" if value is not None else "--"


def _parse_optional_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


def _valid_acquisition_snapshot(party_state) -> bool:
    if (
        party_state is None
        or not party_state.party_count_valid
        or party_state.error
        or getattr(party_state, "live_read_warning", None)
        or party_state.party_count != len(party_state.pokemon)
    ):
        return False
    return all(
        pokemon.checksum_valid and not getattr(pokemon, "sample_stale", False)
        for pokemon in party_state.pokemon
    )


def _valid_death_snapshot(party_state, party_payload, stale_after: float) -> bool:
    if not _valid_acquisition_snapshot(party_state) or party_payload is None:
        return False
    received_at = getattr(party_payload, "received_at", None)
    if not isinstance(received_at, (int, float)) or time.monotonic() - received_at > stale_after:
        return False
    return all(
        getattr(pokemon, "current_stats", None) is not None
        and isinstance(getattr(pokemon, "current_hp", None), int)
        and not isinstance(getattr(pokemon, "current_hp", None), bool)
        and isinstance(getattr(pokemon, "max_hp", None), int)
        and 1 <= pokemon.max_hp <= 714
        and 0 <= pokemon.current_hp <= pokemon.max_hp
        for pokemon in party_state.pokemon
    )


def _frame_number(payload) -> int | None:
    value = payload.get("frame") if isinstance(payload, dict) else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class _ElidedStatusLabel(QLabel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._compact_text: str | None = None
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Application status")

    def set_full_text(self, text: str, compact_text: str | None = None) -> None:
        self._full_text = text
        self._compact_text = compact_text
        self._update_elision()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_elision()

    def _update_elision(self) -> None:
        width = max(0, self.contentsRect().width() - 4)
        candidate = self._full_text
        if (
            self._compact_text
            and self.fontMetrics().horizontalAdvance(candidate) > width
        ):
            candidate = self._compact_text
        rendered = self.fontMetrics().elidedText(
            candidate, Qt.TextElideMode.ElideRight, width
        )
        self.setText(rendered)
        self.setToolTip(self._full_text if rendered != self._full_text else "")


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings,
        data_source: GameDataSource | None = None,
        target_store: EVTargetStore | None = None,
        nuzlocke_store: NuzlockeStore | None = None,
        training_preference_store: EVTrainingPreferenceStore | None = None,
        provider: GameProvider | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        source_profile = getattr(data_source, "profile", None)
        game_id = getattr(source_profile, "game_id", DEFAULT_GAME_ID)
        self.provider = provider or get_game_provider(game_id)
        self.profile = source_profile or self.provider.profile
        self.ram_data_source = data_source or self.provider.make_data_source()
        self.game_session = GameSession(self.provider, self.ram_data_source)
        # Retain explicit legacy-store injection for compatibility, but never load,
        # migrate, or rewrite numeric targets as part of the new Training workflow.
        self.ev_target_store = target_store
        self.training_preference_store = training_preference_store or EVTrainingPreferenceStore()
        self._training_opponent_yields = ()
        self._training_battle_state = "idle"
        self._training_party_connected = False
        self.training_recommendations = {}
        self.nuzlocke_view = NuzlockeView(
            store=nuzlocke_store,
            profiles=((self.provider.nuzlocke_profile,)
                      if self.provider.capabilities.nuzlocke
                      and self.provider.nuzlocke_profile is not None else ()),
            settings=settings,
        )
        self._acquisition_observer = PartyAcquisitionObserver()
        self._pc_acquisition_observer = PartyAcquisitionObserver(reset_on_disconnect=False)
        self._pc_storage_tracking_active = False
        self._pc_storage_baseline_key = None
        self._pc_storage_baseline_ids: set[str] = set()
        self._pc_storage_post_baseline_ids: set[str] = set()
        self._pc_storage_emitted_box_ids: set[str] = set()
        self._pc_resolver_lifecycle = "unresolved"
        self._pc_monitoring_ready = False
        self._pc_lua_run_id: str | None = None
        self._pc_auto_discovery_requested_runs: set[str] = set()
        self._pc_last_event = "No PC events yet."
        self._last_acquisition_trace_timestamp = 0.0
        self._pc_scan_requested_at: float | None = None
        self._pc_discovery_requested_at: float | None = None
        self._pc_anchor_requested_at: float | None = None
        self._pc_anchor_requested_address: int | None = None
        self._pc_layout_requested_at: float | None = None
        self._pc_auto_rediscovery_key = None
        self._pc_inspection_requested_at: float | None = None
        self._pc_inspection_requested_address: int | None = None
        self._pc_search_requested_at: float | None = None
        self._pc_search_requested_address: int | None = None
        self._pc_pointer_search_requested_at: float | None = None
        self._pc_save_offset_test_requested_at: float | None = None
        self._last_pc_save_offset_test_key = None
        self._last_pc_save_offset_test_text = (
            "No save-file PC offset test has been run."
        )
        self._pc_sram_search_requested_at: float | None = None
        self._pc_sram_search_target: tuple[str, int] | None = None
        self._last_pc_sram_search_key = None
        self._last_pc_sram_search_text = (
            "Search live SRAM once, move the target Pokemon without using the in-game Save command, then search again."
        )
        self._pc_last_inspected_address: int | None = None
        self._last_pc_storage_discovery_text = "No PC storage discovery scan has been run."
        self._last_pc_storage_discovery_key = None
        self._last_pc_storage_scan_frame: int | None = None
        self._party_hp_observer = PartyHpObserver()
        self._party_wipe_observer = PartyWipeObserver()
        self._no_run_prompt_observer = NoRunPromptObserver()
        self.compact_mode = False
        self.tracker_view = "training"
        self._compact_member_count = -1
        self._compact_opponent_count = -1
        self._party_layout_slots: tuple[int, ...] = ()
        self._ev_history_records: list[tuple[str, str]] = []
        self._compact_return_route = None
        self._suspend_geometry_save = True
        self._changing_mode = False
        self._ev_change_tracker = EVChangeTracker()
        self._ev_history_limit = 200
        self.friendship_walk = FriendshipWalkController()
        self._walk_session: FriendshipWalkSessionStats | None = None
        self._walk_session_identity: str | None = None
        self._walk_target_reached = False
        self._walk_tracked_missing = False
        self._walk_session_ended = True
        self._friendship_walk_start_frame: int | None = None
        self._friendship_walk_pending_sequence: int | None = None
        self._friendship_walk_pending_axis: str | None = None
        self._friendship_walk_ack_deadline = 0.0
        self._last_walk_ping_at = 0.0
        self._walk_last_command_sequence: int | None = None
        self._walk_command_transport: str | None = None
        self._walk_last_ack_sequence: int | None = None
        self._walk_last_ack_frame: int | None = None
        self._walk_last_ack_received_at: float | None = None
        self._walk_unacked_since: float | None = None
        self._walk_command_state = "UNKNOWN"
        self._remote_walk_stop_requested = False
        self.coordinate_discovery = (
            self.provider.coordinate_discovery_type()
            if self.provider.capabilities.player_position else None)
        self._coordinate_capture_id: str | None = None
        self._coordinate_capture_label: str | None = None
        self._coordinate_capture_source: str | None = None
        self._coordinate_capture_busy = False
        self._coordinate_analysis_signals = _CoordinateAnalysisSignals(self)
        self._coordinate_analysis_signals.completed.connect(self._finish_coordinate_analysis)

        self.setWindowTitle(f"{self.profile.display_name} Companion")
        self.setMinimumSize(
            QSize(320, 300) if settings.compact_mode else QSize(620, 480)
        )
        self.resize(1100, 760)
        self._build_ui()
        self._apply_game_capabilities()
        apply_theme(self)
        self.set_tracker_view(settings.tracker_view, persist=False)
        self.set_route(settings.workspace_view or settings.tracker_view, persist=False)
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(450)
        self._geometry_save_timer.timeout.connect(self._save_window_state)

        saved_geometry = (
            settings.window_geometry
            if settings.compact_mode
            else settings.normal_window_geometry or settings.window_geometry
        )
        if saved_geometry is not None:
            self.setGeometry(*saved_geometry)
        self._set_compact_mode(settings.compact_mode, persist=False, initial=True)
        self._suspend_geometry_save = False

        if self.provider.capabilities.live_party:
            self.ram_data_source.start()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_ram_backend_debug)
        self.refresh_timer.setInterval(250)
        if self.provider.capabilities.live_party:
            self.refresh_timer.start()
            self._refresh_ram_backend_debug()
        else:
            self._show_unsupported_game_state()

    def _build_ui(self) -> None:
        self.backend_status = BackendStatusWidget()
        self.backend_status.details_widget.hide()
        self.compact_status_label = self.backend_status.compact_label
        self.tracker_backend_group = self.backend_status
        self.tracker_status_labels = {
            "backend": self.backend_status.details["backend"],
            "connection": self.backend_status.details["connection"],
            "memory_domain": self.backend_status.details["domain"],
            "last_update": self.backend_status.details["last_update"],
            "frame": self.backend_status.details["frame"],
        }
        self.shell = AppShell(
            self.provider.display_name, self.backend_status,
            generation=self.provider.generation, emulator=self.provider.emulator)
        self.shell.setProperty("gameMotif", self.provider.theme.motif_id)
        self.shell.setProperty("gameAccentPrimary", self.provider.theme.accent_primary)
        self.shell.setProperty("gameAccentSecondary", self.provider.theme.accent_secondary)
        self.setCentralWidget(self.shell)
        self._root_layout = self.shell.root_layout
        self.tracker_title = self.shell.brand
        self.compact_mode_button = self.shell.compact_button
        self.tracker_view_buttons = {key: self.shell.nav_buttons[key] for key in ("training", "stats")}
        self.shell.route_requested.connect(self.set_route)
        self.shell.compact_requested.connect(self.set_compact_mode)
        self.shell.notification_requested.connect(self._review_detection)
        self.compact_shortcut = QShortcut(QKeySequence("Ctrl+Shift+C"), self)
        self.compact_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.compact_shortcut.activated.connect(self.compact_mode_button.toggle)

        # Transitional formatting adapter: the proven render methods populate these
        # fields. The six legacy cards are never mounted in the visible workspace.
        self._party_adapter_host = QWidget(self)
        self._party_adapter_host.hide()
        self.tracker_party_cards = {}
        self.party_card_widgets = {}
        for slot in range(1, 7):
            card = PartyCard(slot, self._party_adapter_host)
            self.tracker_party_cards[slot] = card.fields
            self.party_card_widgets[slot] = card
            card.card_size_changed.connect(self._refresh_card_sprite_for_size)
        self.training_view = TrainingView()
        self.party_stats_view = PartyStatsView()
        self.compact_training_view = CompactTrainingView()
        self.compact_party_stats_view = CompactPartyStatsView()
        self._party_views = (self.training_view, self.party_stats_view,
                             self.compact_training_view, self.compact_party_stats_view)
        for view in self._party_views:
            view.selected_slot_changed.connect(self._select_party_slot)
        for view in (self.training_view, self.compact_training_view):
            view.edit_focus_requested.connect(self._edit_training_focus)
            view.clear_focus_requested.connect(self._clear_training_focus)
        self.nuzlocke_view.presentation_changed.connect(self._refresh_fight_summaries)
        self._refresh_fight_summaries()
        self.current_opponent_panel = CurrentOpponentPanel(
            ev_yield_summary=self.provider.format_ev_yield_summary)
        if not self.provider.capabilities.live_opponents:
            self.current_opponent_panel.set_unavailable()
        self.recommendation_panel = RecommendationPanel()
        self.ev_change_log = EvChangeLogWidget(self._clear_ev_log)
        self.ev_change_list = self.ev_change_log.list
        self.clear_ev_log_button = self.ev_change_log.clear_button
        self.ev_history_group = self.ev_change_log
        self._build_friendship_walk(None)
        self.training_view.set_runtime_widgets(
            opponent=self.current_opponent_panel, recommendations=self.recommendation_panel,
            history=self.ev_change_log, friendship=self.friendship_walk_group)
        self.tracker_scroll_area = self.training_view.scroll

        advanced = build_advanced_diagnostics(self)
        self.diagnostics_view = DiagnosticsView(advanced, self._rediscover_pc_layout)
        self.ram_debug_scroll_area = self.diagnostics_view.advanced_scroll_area
        self.nuzlocke_view.rediscover_pc_requested.connect(self._rediscover_pc_layout)
        self.compact_nuzlocke_view = CompactNuzlockeView(self.nuzlocke_view)
        self.compact_nuzlocke_view.review_requested.connect(self._expand_nuzlocke)
        self.compact_nuzlocke_view.rediscover_pc_requested.connect(self._rediscover_pc_layout)
        self.nuzlocke_scroll_area = self._scroll_page(self.nuzlocke_view)
        for key, page in (
            ("training", self.training_view), ("stats", self.party_stats_view),
            ("nuzlocke", self.nuzlocke_scroll_area), ("diagnostics", self.diagnostics_view),
            ("compact_training", self.compact_training_view),
            ("compact_stats", self.compact_party_stats_view),
            ("compact_nuzlocke", self.compact_nuzlocke_view),
        ):
            self.shell.add_page(key, page)
        self.route = "training"
        self.shell.show_route(self.route)

        status_bar = QStatusBar(self)
        status_bar.setObjectName("statusBar")
        status_bar.setSizeGripEnabled(False)
        status_bar.setFixedHeight(26)
        self.setStatusBar(status_bar)
        self.tracker_status_message = _ElidedStatusLabel(status_bar)
        status_bar.addPermanentWidget(self.tracker_status_message, 1)
        self._set_bottom_status(connected=False, info="Waiting for BizHawk / EmuHawk RAM connection...")

    def _apply_game_capabilities(self) -> None:
        caps = self.provider.capabilities
        self.friendship_walk_group.setVisible(caps.friendship_walk)
        self.start_friendship_walk_button.setEnabled(caps.friendship_walk)
        self.stop_friendship_walk_button.setEnabled(caps.friendship_walk)
        self.friendship_walk_shortcut.setEnabled(caps.friendship_walk)
        if not (caps.live_opponents and caps.ev_yields):
            self.recommendation_panel.empty.setText(
                "Live battle EV recommendations unavailable for this game")
        self.pc_storage_section.setVisible(caps.pc_storage)
        self.coordinate_discovery_group.setVisible(caps.player_position)
        self.nuzlocke_view.rediscover_pc_button.setVisible(caps.pc_storage)
        self.nuzlocke_view.pc_panel.setVisible(caps.pc_storage)
        self.compact_nuzlocke_view.rediscover_button.setVisible(caps.pc_storage)
        self.compact_nuzlocke_view.pc_panel.setVisible(caps.pc_storage)
        self.capability_status_label.setText(
            f"PC Storage: {'Supported' if caps.pc_storage else 'Not available for this integration'}"
            f" · Friendship Walk: {'Supported' if caps.friendship_walk else 'Not available'}"
            f" · Battle opponent reader: {'Supported' if caps.live_opponents else 'Not available'}"
        )

    def _show_unsupported_game_state(self) -> None:
        message = "Unsupported game — the companion could not identify a supported Pokémon game."
        self._set_bottom_status(connected=False, info=message)
        self.shell.system_label.setText(message)
        self.current_opponent_panel.set_unavailable()

    def _select_party_slot(self, slot: int) -> None:
        for view in self._party_views:
            view.blockSignals(True)
            view.set_selected_slot(slot)
            view.blockSignals(False)

    def _refresh_party_workspaces(self, connected=None) -> None:
        if connected is None:
            connected = self._training_party_connected
        for card in self.tracker_party_cards.values():
            pid = card.get("pid")
            pokemon = card.get("pokemon")
            decoded = getattr(pokemon, "decoded", None)
            definition_for_id = (
                self.provider.move_catalog
                if self.provider.capabilities.move_metadata else None)
            if pokemon is not None and pokemon.checksum_valid and decoded is not None:
                move_ids = getattr(decoded, "move_ids", ())
                current_pps = getattr(decoded, "move_current_pps", ())
                pp_ups = getattr(decoded, "move_pp_ups", ())
                card["move_displays"] = tuple(
                    resolve_move(
                        PokemonMoveState(move_id, current_pps[index], pp_ups[index]),
                        definition_for_id or (lambda _move_id: None),
                    ) if index < len(current_pps) and index < len(pp_ups) else None
                    for index, move_id in enumerate(move_ids[:4])
                )
            else:
                card["move_displays"] = ()
            card["training_preference"] = (
                self.training_preference_store.get(pid)
                if pid is not None and card.get("pokemon") is not None else None
            )
        for view in self._party_views:
            view.blockSignals(True)
            view.refresh(self.tracker_party_cards, connected=connected)
            view.blockSignals(False)
        self._select_party_slot(self.training_view.selected_slot)
        self.friendship_walk_group.refresh_party(self.tracker_party_cards)
        self._render_friendship_walk()
        self._refresh_training_recommendations(() if not connected else None)

    def _refresh_fight_summaries(self) -> None:
        timeline = self.nuzlocke_view.fight_timeline()
        self.training_view.set_fight_timeline(timeline)
        self.compact_training_view.set_fight_timeline(timeline)

    def _handle_party_wipe(self, candidate: PartyWipeCandidate) -> None:
        run = self.nuzlocke_view.store.active_run
        if run is None or run.run_id != candidate.run_id:
            return
        action = self.settings.nuzlocke_wipe_action
        if action == "IGNORE":
            return
        if action == "ASK ME":
            box = QMessageBox(self)
            box.setWindowTitle("Party Wipe Detected")
            box.setText(f"All {len(candidate.members)} current party Pokémon fainted.")
            box.setInformativeText("\n".join(
                f"{member.nickname or member.species} · {member.species} · "
                f"Lv. {member.level if member.level is not None else '—'}"
                for member in candidate.members
            ))
            end_button = box.addButton("End Run as Wiped", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Keep Current Run", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Dismiss", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not end_button:
                return
        if not self.nuzlocke_view.end_run_wiped(candidate):
            return
        self._party_hp_observer.reset()
        self._party_wipe_observer.reset()
        self._no_run_prompt_observer.suppress_for_session()
        box = QMessageBox(self)
        box.setWindowTitle("Run Ended")
        box.setText(f'"{run.name}" was marked WIPED. Start the next run?')
        create_button = box.addButton("Create New Run", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is create_button:
            self.nuzlocke_view._create_run()

    def _prompt_create_run_without_active(self) -> None:
        if self.nuzlocke_view.store.active_run is not None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("No Active Nuzlocke Run")
        box.setText("You're playing without an active tracked Nuzlocke run.")
        create_button = box.addButton("Create Run", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        never_button = box.addButton("Don't Ask Again", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        if box.clickedButton() is never_button:
            self.settings.prompt_for_nuzlocke_run = False
            self.settings.save_default()
            self.nuzlocke_view.no_run_prompt_input.setChecked(False)
        elif box.clickedButton() is create_button:
            self.nuzlocke_view._create_run()

    def _refresh_training_recommendations(self, opponents=None) -> None:
        if not (self.provider.capabilities.live_opponents and self.provider.capabilities.ev_yields):
            self._training_opponent_yields = ()
            self._training_battle_state = "unavailable"
            self.training_recommendations = {}
            self.recommendation_panel.refresh(
                self.tracker_party_cards, {}, battle_state="unavailable")
            return
        if opponents is not None:
            yields = tuple(self.provider.get_training_ev_yield(enemy.species_id) for enemy in opponents)
            self._training_opponent_yields = yields
            self._training_battle_state = (
                "unavailable" if any(value is None for value in yields) else
                "active" if any(any(value.as_mapping().values()) for value in yields) else "idle"
            )
        self.training_recommendations = {
            slot: recommend_battle_evs(card.get("training_preference"), self._training_opponent_yields)
            for slot, card in self.tracker_party_cards.items()
            if self._training_battle_state == "active"
            and (pokemon := card.get("pokemon")) is not None
            and pokemon.checksum_valid
        }
        self.recommendation_panel.refresh(
            self.tracker_party_cards, self.training_recommendations,
            battle_state=self._training_battle_state,
        )

    def _edit_training_focus(self, slot: int) -> None:
        card = self.tracker_party_cards.get(slot, {})
        pokemon, pid = card.get("pokemon"), card.get("pid")
        if pokemon is None or pid is None or not pokemon.checksum_valid:
            return
        title = (pokemon.species if self.provider.nickname_is_default(pokemon.nickname, pokemon.species)
                 else f"{pokemon.nickname} ({pokemon.species})")
        # Qt continues polling during the dialog. Save the captured identity, then
        # project preferences onto the current party, which may have reordered.
        dialog = TrainingFocusDialog(title, self.training_preference_store.get(pid), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.training_preference_store.set(pid, dialog.preference())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could not save training focus", str(error))
            return
        self._refresh_party_workspaces()

    def _clear_training_focus(self, slot: int) -> None:
        card = self.tracker_party_cards.get(slot, {})
        pokemon, pid = card.get("pokemon"), card.get("pid")
        if pokemon is None or pid is None or not pokemon.checksum_valid:
            return
        try:
            self.training_preference_store.clear(pid)
        except OSError as error:
            QMessageBox.warning(self, "Could not clear training focus", str(error))
            return
        self._refresh_party_workspaces()

    def set_route(self, route: str, persist: bool = True) -> None:
        if route not in {"training", "stats", "nuzlocke", "diagnostics"}:
            raise ValueError(f"Unknown workspace: {route}")
        if self.compact_mode and route == "diagnostics":
            route = self.tracker_view
        if persist and self.compact_mode:
            self._compact_return_route = None
        self.route = route
        if route in {"training", "stats"}:
            self.tracker_view = route
            if persist:
                self.settings.tracker_view = route
        self.shell.show_route(route)
        self.ev_change_log.setVisible(route == "training")
        if persist:
            self.settings.workspace_view = route
            self.settings.save_default()

    def _expand_nuzlocke(self, section: str) -> None:
        self.set_compact_mode(False)
        self.set_route("nuzlocke")
        self.nuzlocke_view.show_section(section)

    def _review_detection(self) -> None:
        self._expand_nuzlocke(
            "Deaths" if not self.nuzlocke_view.ram_death_group.isHidden() else "Encounters")

    @staticmethod
    def _scroll_page(content: QWidget) -> QScrollArea:
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        scroll = QScrollArea()
        scroll.setObjectName("mainPageScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(content)
        return scroll

    def _build_friendship_walk(self, parent) -> None:
        panel = FriendshipWalkPanel(parent)
        self.friendship_walk_group = panel
        self.friendship_walk_axis = panel.axis
        self.friendship_walk_axis.setCurrentIndex(
            max(0, panel.axis.findData(self.settings.friendship_walk_axis)))
        self.friendship_walk_axis.currentIndexChanged.connect(self._friendship_walk_axis_changed)
        self.start_friendship_walk_button = panel.start
        self.start_friendship_walk_button.clicked.connect(self._start_friendship_walk)
        self.stop_friendship_walk_button = panel.stop
        self.stop_friendship_walk_button.clicked.connect(self._stop_friendship_walk)
        self.friendship_walk_status_label = panel.status
        self.friendship_walk_moves_label = panel.moves
        panel.tracked_changed.connect(self._friendship_tracked_changed)
        panel.goal_changed.connect(self._friendship_goal_changed)
        self.friendship_walk_shortcut = QShortcut(QKeySequence("Ctrl+Shift+W"), self)
        self.friendship_walk_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.friendship_walk_shortcut.activated.connect(self._stop_friendship_walk)

    def _friendship_walk_axis_changed(self, _index: int) -> None:
        axis = self.friendship_walk_axis.currentData()
        if axis not in {WalkAxis.HORIZONTAL.value, WalkAxis.VERTICAL.value}:
            return
        self.settings.friendship_walk_axis = axis
        self.settings.save_default()

    def _friendship_tracked_changed(self, identity) -> None:
        if identity is None:
            return
        rules = self.provider.friendship_rules
        goal = self.settings.friendship_goals.get(
            str(identity), rules.default_goal if rules else 160,
        )
        self.friendship_walk_group.set_goal(
            goal, evolution_goal=rules.evolution_goal if rules else 220,
        )
        pokemon = self.friendship_walk_group.selected_pokemon
        if self._walk_session is not None and self._walk_session_identity != identity:
            self._walk_session_identity = identity
            if pokemon is not None and pokemon.checksum_valid:
                self._walk_session.retarget(pokemon.friendship)
        self._walk_tracked_missing = False
        self._walk_target_reached = False
        if (pokemon is not None and pokemon.checksum_valid
                and pokemon.friendship >= goal
                and (self.friendship_walk.active or self._friendship_walk_pending_sequence is not None)):
            self._complete_friendship_goal()
            return
        self._render_friendship_walk()

    def _friendship_goal_changed(self, target: int) -> None:
        identity = self.friendship_walk_group.selected_identity
        if identity is not None:
            self.settings.friendship_goals[str(identity)] = FriendshipGoal(target).target
            self.settings.save_default()
        pokemon = self.friendship_walk_group.selected_pokemon
        if (pokemon is not None and pokemon.checksum_valid
                and pokemon.friendship >= target
                and (self.friendship_walk.active or self._friendship_walk_pending_sequence is not None)):
            self._complete_friendship_goal()
        else:
            self._walk_target_reached = False
            self._render_friendship_walk()
            if pokemon is not None and pokemon.checksum_valid and pokemon.friendship < target:
                self.start_friendship_walk_button.setEnabled(True)

    def _complete_friendship_goal(self) -> None:
        if self._walk_target_reached:
            return
        if self.friendship_walk.active or self._friendship_walk_pending_sequence is not None:
            self._stop_friendship_walk()
        self._walk_target_reached = True
        self._render_friendship_walk()

    def _start_friendship_walk(self) -> None:
        if self.friendship_walk.active or self._friendship_walk_pending_sequence is not None:
            return
        pokemon = self.friendship_walk_group.selected_pokemon
        goal = self.friendship_walk_group.goal
        if pokemon is not None and pokemon.checksum_valid and pokemon.friendship >= goal:
            self._walk_target_reached = True
            self._render_friendship_walk()
            return
        snapshot = self.ram_data_source.snapshot()
        party_payload = snapshot.details.get("party_payload")
        payload = party_payload.payload if party_payload is not None else {}
        position = self.provider.decode_player_position(payload)
        frame = _frame_number(payload)
        active_enemies = snapshot.details.get("active_enemy_battlers", ())
        if party_payload is None or not snapshot.details.get("party_payload_fresh", False):
            self.friendship_walk.pause(WalkStatus.PAUSED_RAM)
            self._render_friendship_walk()
            return
        if position is None or not position.validated_offsets or frame is None:
            self.friendship_walk.pause(WalkStatus.PAUSED_COORDINATES)
            self._render_friendship_walk()
            return
        if active_enemies:
            self.friendship_walk.pause(WalkStatus.PAUSED_BATTLE)
            self._render_friendship_walk()
            return

        axis = self.friendship_walk_axis.currentData() or WalkAxis.HORIZONTAL.value
        now = time.monotonic()
        self._walk_unacked_since = None
        receipt = self._send_friendship_walk_command("START", axis)
        if receipt is None:
            self._walk_command_state = "UNAVAILABLE"
            self.friendship_walk.pause(WalkStatus.PAUSED_COMMAND)
            self._render_friendship_walk()
            return
        self._remote_walk_stop_requested = False
        if (self._walk_session is None or self._walk_session_ended
                or self._walk_session_identity != self.friendship_walk_group.selected_identity):
            self._walk_session = FriendshipWalkSessionStats(
                started_at=now,
                starting_friendship=pokemon.friendship if pokemon is not None else 0,
                current_friendship=pokemon.friendship if pokemon is not None else 0,
            )
        self._walk_session_ended = False
        self._walk_session_identity = self.friendship_walk_group.selected_identity
        self._walk_target_reached = False
        self._walk_tracked_missing = False
        self._friendship_walk_start_frame = frame
        self._friendship_walk_pending_sequence = receipt.sequence
        self._friendship_walk_pending_axis = axis
        self._friendship_walk_ack_deadline = now + 3.0
        self._last_walk_ping_at = now
        self._walk_command_state = "WAITING FOR START ACK"
        self.friendship_walk.status = WalkStatus.STARTING
        self._render_friendship_walk()

    def _stop_friendship_walk(self) -> None:
        self._friendship_walk_pending_sequence = None
        self._friendship_walk_pending_axis = None
        self.friendship_walk.stop()
        self._walk_session_ended = True
        receipt = self._send_friendship_walk_command("STOP")
        self._walk_command_state = "WAITING FOR STOP ACK" if receipt else "UNAVAILABLE"
        if self._walk_session is not None:
            self._walk_session.observe(time.monotonic(), active=False)
        self._render_friendship_walk()
        pokemon = self.friendship_walk_group.selected_pokemon
        self.start_friendship_walk_button.setEnabled(
            pokemon is not None and pokemon.checksum_valid
            and pokemon.friendship < self.friendship_walk_group.goal
            and not self._walk_tracked_missing
        )

    def _send_friendship_walk_command(
        self, action: str, value: str | None = None
    ) -> FriendshipWalkCommandReceipt | None:
        sender = getattr(self.ram_data_source, "send_friendship_walk_command", None)
        if not callable(sender):
            return None
        receipt = sender(action, value)
        if not isinstance(receipt, FriendshipWalkCommandReceipt):
            return None
        now = time.monotonic()
        if self._walk_unacked_since is None:
            self._walk_unacked_since = now
        self._walk_last_command_sequence = receipt.sequence
        self._walk_command_transport = receipt.transport
        self._walk_command_state = "WAITING FOR ACK"
        return receipt

    def _observe_friendship_walk_ack(self, payload: dict) -> None:
        sequence = payload.get("friendship_walk_ack_sequence")
        if isinstance(sequence, float) and sequence.is_integer():
            sequence = int(sequence)
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            return
        if sequence != self._walk_last_ack_sequence:
            self._walk_last_ack_received_at = time.monotonic()
        self._walk_last_ack_sequence = sequence
        frame = payload.get("friendship_walk_ack_frame")
        self._walk_last_ack_frame = frame if isinstance(frame, int) else None
        if sequence == self._walk_last_command_sequence:
            self._walk_unacked_since = None
            self._walk_command_state = "READY"

    def _refresh_friendship_walk(self, snapshot, party_payload, active_enemies) -> None:
        frame = _frame_number(party_payload.payload if party_payload is not None else {})
        payload = party_payload.payload if party_payload is not None else {}
        position = self.provider.decode_player_position(payload)
        self._observe_friendship_walk_ack(payload)
        ram_fresh = bool(
            party_payload is not None
            and snapshot.details.get("party_payload_fresh", False)
        )
        raw_party_state = snapshot.details.get("party_state")
        fresh_pokemon = None
        if (self._walk_session is not None and self._walk_session_identity is not None
                and ram_fresh and raw_party_state is not None
                and getattr(raw_party_state, "party_count_valid", False)):
            tracked_record = next(
                (p for p in raw_party_state.pokemon
                 if p.stable_id == self._walk_session_identity),
                None,
            )
            if tracked_record is None:
                self._walk_tracked_missing = True
                if self.friendship_walk.active:
                    self.friendship_walk.pause(WalkStatus.PAUSED_RAM)
                    self._send_friendship_walk_command("STOP")
                elif self._friendship_walk_pending_sequence is not None:
                    self._stop_friendship_walk()
                self._walk_session.observe(time.monotonic(), active=False)
                self._render_friendship_walk()
                self.start_friendship_walk_button.setEnabled(False)
                return
            if not tracked_record.checksum_valid:
                if self.friendship_walk.active or self._friendship_walk_pending_sequence is not None:
                    self._friendship_walk_pending_sequence = None
                    self._friendship_walk_pending_axis = None
                    self.friendship_walk.pause(WalkStatus.PAUSED_RAM)
                    self._send_friendship_walk_command("STOP")
                self._walk_session.observe(time.monotonic(), active=False)
                self._render_friendship_walk()
                self.start_friendship_walk_button.setEnabled(False)
                return
            fresh_pokemon = tracked_record
            self._walk_tracked_missing = False
            self._walk_session.current_friendship = fresh_pokemon.friendship
            if fresh_pokemon.friendship >= self.friendship_walk_group.goal:
                self._walk_session.observe(time.monotonic(), active=False,
                                           friendship=fresh_pokemon.friendship)
                self._complete_friendship_goal()
                self.start_friendship_walk_button.setEnabled(False)
                return

        if self._friendship_walk_pending_sequence is not None:
            if not ram_fresh:
                self._friendship_walk_pending_sequence = None
                self._friendship_walk_pending_axis = None
                self.friendship_walk.pause(WalkStatus.PAUSED_RAM)
                self._walk_command_state = "RAM STALE"
                self._send_friendship_walk_command("STOP")
            elif active_enemies:
                self._friendship_walk_pending_sequence = None
                self._friendship_walk_pending_axis = None
                self.friendship_walk.pause(WalkStatus.PAUSED_BATTLE)
                self._send_friendship_walk_command("STOP")
            elif self._walk_last_ack_sequence == self._friendship_walk_pending_sequence:
                if payload.get("friendship_walk_enabled") is True:
                    current_position = (
                        position
                        if position is not None and position.validated_offsets
                        else None
                    )
                    current_frame = frame
                    if current_position is None or current_frame is None:
                        self._friendship_walk_pending_sequence = None
                        self._friendship_walk_pending_axis = None
                        self.friendship_walk.pause(WalkStatus.PAUSED_COORDINATES)
                        self._send_friendship_walk_command("STOP")
                    else:
                        self.friendship_walk.start(
                            self._friendship_walk_pending_axis or WalkAxis.HORIZONTAL.value,
                            current_frame,
                            current_position.x,
                            current_position.y,
                        )
                        self._friendship_walk_start_frame = current_frame
                        self._last_walk_ping_at = time.monotonic()
                        self._friendship_walk_pending_sequence = None
                        self._friendship_walk_pending_axis = None
                else:
                    reason = payload.get("friendship_walk_pause_reason")
                    self._friendship_walk_pending_sequence = None
                    self._friendship_walk_pending_axis = None
                    if reason:
                        self.friendship_walk.acknowledge_remote_pause(reason)
                    else:
                        self.friendship_walk.pause(WalkStatus.PAUSED_COMMAND)
                    self._send_friendship_walk_command("STOP")
            elif time.monotonic() >= self._friendship_walk_ack_deadline:
                self._friendship_walk_pending_sequence = None
                self._friendship_walk_pending_axis = None
                self.friendship_walk.pause(WalkStatus.PAUSED_COMMAND)
                self._walk_command_state = "START ACK TIMEOUT"
                self._send_friendship_walk_command("STOP")

        if self.friendship_walk.active:
            if frame is None:
                self.friendship_walk.pause(WalkStatus.PAUSED_COORDINATES)
                self._send_friendship_walk_command("STOP")
                if self._walk_session is not None:
                    self._walk_session.observe(time.monotonic(), active=False)
                self._render_friendship_walk()
                self.start_friendship_walk_button.setEnabled(
                    ram_fresh
                    and position is not None
                    and position.validated_offsets
                    and not active_enemies
                )
                return
            remote_enabled = payload.get("friendship_walk_enabled")
            remote_reason = payload.get("friendship_walk_pause_reason")
            if (
                remote_enabled is False
                and remote_reason
                and frame is not None
                and self._friendship_walk_start_frame is not None
                and frame > self._friendship_walk_start_frame
            ):
                self.friendship_walk.acknowledge_remote_pause(remote_reason)
            else:
                update = self.friendship_walk.update(
                    frame if frame is not None else -1,
                    position.x if position is not None and position.validated_offsets else None,
                    position.y if position is not None and position.validated_offsets else None,
                    connected=ram_fresh,
                    battle_active=bool(active_enemies),
                )
                if update.send_stop:
                    self._send_friendship_walk_command("STOP")
                elif (
                    update.direction_changed
                    and update.direction is not None
                    and not self._send_friendship_walk_command("DIRECTION", update.direction.value)
                ):
                    self.friendship_walk.pause(WalkStatus.PAUSED_COMMAND)
                    self._send_friendship_walk_command("STOP")
                if self.friendship_walk.active:
                    now = time.monotonic()
                    if (
                        self._walk_unacked_since is not None
                        and now - self._walk_unacked_since >= 4.0
                    ):
                        self.friendship_walk.pause(WalkStatus.PAUSED_COMMAND_LOST)
                        self._walk_command_state = "ACK STALE"
                        self._send_friendship_walk_command("STOP")
                    if (
                        self._walk_unacked_since is None
                        and now - self._last_walk_ping_at >= 1.0
                    ):
                        if self._send_friendship_walk_command("PING") is not None:
                            self._last_walk_ping_at = now
                        else:
                            self.friendship_walk.pause(WalkStatus.PAUSED_COMMAND)
                            self._walk_command_state = "UNAVAILABLE"
                            self._send_friendship_walk_command("STOP")
        elif payload.get("friendship_walk_enabled") is True:
            if not self._remote_walk_stop_requested:
                self._send_friendship_walk_command("STOP")
                self._remote_walk_stop_requested = True
        else:
            self._remote_walk_stop_requested = False
        if self._walk_session is not None:
            self._walk_session.observe(
                time.monotonic(),
                active=(self.friendship_walk.active and ram_fresh
                        and self.friendship_walk.status is not WalkStatus.BLOCKED_REVERSING),
                friendship=fresh_pokemon.friendship if fresh_pokemon is not None else None,
                controller_moves=self.friendship_walk.successful_moves,
                reversal_frame=(
                    self.friendship_walk.reversal_start_frame
                    if self.friendship_walk.status is WalkStatus.BLOCKED_REVERSING
                    else None
                ),
            )
        self._render_friendship_walk()
        self.start_friendship_walk_button.setEnabled(
            not self.friendship_walk.active
            and self._friendship_walk_pending_sequence is None
            and ram_fresh
            and position is not None
            and position.validated_offsets
            and not active_enemies
            and not self._walk_target_reached
            and not self._walk_tracked_missing
            and self.friendship_walk_group.selected_pokemon is not None
            and self.friendship_walk_group.selected_pokemon.friendship
            < self.friendship_walk_group.goal
        )

    def _render_friendship_walk(self) -> None:
        status = (
            WalkStatus.STARTING.value
            if self._friendship_walk_pending_sequence is not None
            else self.friendship_walk.status.value
        )
        pokemon = self.friendship_walk_group.selected_pokemon
        current = (pokemon.friendship if pokemon is not None and pokemon.checksum_valid
                   else None)
        target = self.friendship_walk_group.goal
        if self._walk_tracked_missing:
            status = "Tracked Pokémon left party"
        elif self._walk_target_reached or (
            current is not None and current >= target
            and not self.friendship_walk.active
            and self._friendship_walk_pending_sequence is None
            and self.friendship_walk.status is WalkStatus.IDLE
        ):
            status = "Goal reached"
        rules = self.provider.friendship_rules
        eta = None
        if rules is not None and current is not None:
            if self._walk_session is not None:
                self._walk_session.current_friendship = current
                eta = self._walk_session.eta(
                    FriendshipGoal(target), rules,
                    paused=(not self.friendship_walk.active
                            and self._friendship_walk_pending_sequence is None
                            and self.friendship_walk.status is not WalkStatus.IDLE),
                )
            elif current >= target:
                eta = FriendshipETA(ETAStatus.REACHED, 0, 0, "high")
        if status == WalkStatus.IDLE.value and self._walk_session_ended:
            eta = FriendshipETA(ETAStatus.READY)
        self.friendship_walk_group.render_training(
            status=status, current=current, target=target,
            eta=eta, session=self._walk_session,
        )
        walk_state = (
            "reached"
            if status == "Goal reached"
            else "paused"
            if status == "Tracked Pokémon left party"
            else
            "active"
            if self.friendship_walk.active
            else "pending"
            if self._friendship_walk_pending_sequence is not None
            else "paused"
            if self.friendship_walk.status is not WalkStatus.IDLE
            else "idle"
        )
        self.friendship_walk_status_label.setProperty("walkState", walk_state)
        refresh_style(self.friendship_walk_status_label)

    def set_compact_mode(self, enabled: bool) -> None:
        self._set_compact_mode(bool(enabled), persist=True)

    def set_tracker_view(self, view: str, persist: bool = True) -> None:
        if view not in {"training", "stats"}:
            raise ValueError("Tracker view must be 'training' or 'stats'.")
        self.set_route(view, persist=persist)

    def _set_compact_mode(self, enabled: bool, persist: bool = True, initial: bool = False) -> None:
        if enabled == self.compact_mode and not initial:
            return
        was_visible = self.isVisible()
        self._changing_mode = True
        route = self.route
        if enabled and route == "diagnostics":
            self._compact_return_route = route
            route = self.tracker_view
        elif not enabled and self._compact_return_route:
            route = self._compact_return_route
            self._compact_return_route = None
        if enabled and not initial:
            self.settings.normal_window_geometry = self._current_window_geometry()
        self.compact_mode = enabled
        self.setMinimumSize(QSize(320, 300) if enabled else QSize(620, 480))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.shell.set_compact(enabled)
        training = self.compact_training_view if enabled else self.training_view
        training.set_runtime_widgets(
            opponent=self.current_opponent_panel, recommendations=self.recommendation_panel,
            history=self.ev_change_log, friendship=self.friendship_walk_group)
        self.set_route(route, persist=False)
        if not initial:
            if enabled:
                self._resize_compact_window(self._compact_member_count)
            elif self.settings.normal_window_geometry is not None:
                self.setGeometry(*self.settings.normal_window_geometry)
        self.ev_change_log.render(self._ev_history_records, enabled)
        if not self.provider.capabilities.friendship_walk:
            self.friendship_walk_group.hide()
        if was_visible:
            self.show()
        self._changing_mode = False
        if persist:
            self.settings.compact_mode = enabled
            self.settings.save_default()

    def _resize_compact_window(self, member_count: int) -> None:
        # Purpose-built HUD geometry is independent of the number of full cards.
        self.resize(720, 620)

    def _current_window_geometry(self) -> tuple[int, int, int, int]:
        geometry = self.geometry()
        return geometry.x(), geometry.y(), geometry.width(), geometry.height()

    def _save_window_state(self) -> None:
        if self._suspend_geometry_save:
            return
        geometry = self._current_window_geometry()
        self.settings.window_geometry = geometry
        if not self.compact_mode:
            self.settings.normal_window_geometry = geometry
        self.settings.compact_mode = self.compact_mode
        self.settings.save_default()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._suspend_geometry_save and not self._changing_mode:
            self._geometry_save_timer.start()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if not self._suspend_geometry_save and not self._changing_mode:
            self._geometry_save_timer.start()

    def _refresh_ram_backend_debug(self) -> None:
        snapshot = self.ram_data_source.snapshot()
        if "active_enemy_battlers" not in snapshot.details:
            self.backend_status.set_connection(
                snapshot.backend_name, snapshot.connected, None, None)
            self._set_bottom_status(
                connected=snapshot.connected,
                info="Waiting for a compatible live party snapshot."
                if self.provider.capabilities.live_party else "Live party unavailable for this game.",
            )
            return
        heartbeat = snapshot.details.get("heartbeat")
        party_state = snapshot.details.get("party_state")
        display_party_state = snapshot.details.get("display_party_state", party_state)
        party_payload = snapshot.details.get("party_payload")
        pc_storage_payload = snapshot.details.get("pc_storage_payload")
        pc_storage_discovery_payload = snapshot.details.get(
            "pc_storage_discovery_payload", pc_storage_payload
        )
        pc_storage_discovery_progress = snapshot.details.get(
            "pc_storage_discovery_progress", {"status": "idle"}
        )
        pc_save_offset_test_payload = snapshot.details.get(
            "pc_save_offset_test_payload"
        )
        pc_save_offset_test_status = snapshot.details.get(
            "pc_save_offset_test_status", {"status": "idle"}
        )
        pc_sram_search_payload = snapshot.details.get("pc_sram_search_payload")
        pc_sram_search_status = snapshot.details.get(
            "pc_sram_search_status", {"status": "idle"}
        )
        pc_storage_state = snapshot.details.get("pc_storage_state")
        pc_storage_fresh = snapshot.details.get("pc_storage_payload_fresh", False)
        pc_storage_stable_pokemon = snapshot.details.get("pc_storage_stable_pokemon", ())
        pc_storage_changes = snapshot.details.get("pc_storage_changes", ())
        pc_storage_monitor_debug = snapshot.details.get("pc_storage_monitor_debug")
        self._poll_coordinate_discovery(party_payload)
        battle_battlers = snapshot.details.get("battle_battlers", ())
        active_enemies = (
            snapshot.details["active_enemy_battlers"]
            if self.provider.capabilities.live_opponents else ())
        acquisition_run = (
            self.nuzlocke_view.store.active_run
            if self.provider.capabilities.nuzlocke else None)
        pc_storage_ready = (
            self.provider.capabilities.pc_storage
            and
            snapshot.connected
            and pc_storage_state is not None
            and pc_storage_state.available
            and pc_storage_fresh
        )
        pc_payload_data = (
            pc_storage_payload.payload
            if pc_storage_payload is not None
            and isinstance(pc_storage_payload.payload, dict)
            else {}
        )
        party_payload_data = (
            party_payload.payload
            if party_payload is not None and isinstance(party_payload.payload, dict)
            else {}
        )
        current_lua_run = party_payload_data.get("run_id") or pc_payload_data.get("lua_run_id")
        if self.provider.capabilities.pc_storage:
            self._maybe_auto_discover_pc_layout(
                snapshot.connected, current_lua_run, pc_payload_data,
                pc_storage_discovery_progress,
            )
        pc_storage_acquisition_ready = pc_storage_ready and (
            pc_payload_data.get("pc_storage_resolver_kind") != "session_pc_cache"
            or (
                pc_payload_data.get("pc_storage_acquisition_enabled") is True
                and pc_payload_data.get("lua_run_id", pc_payload_data.get("run_id"))
                == current_lua_run
                and self._pc_layout_requested_at is None
                and not (
                    pc_storage_discovery_progress.get("mode") == "session_layout"
                    and pc_storage_discovery_progress.get("status") == "failed"
                )
            )
        )
        party_snapshot_valid = snapshot.connected and _valid_acquisition_snapshot(party_state)
        party_acquisition_candidates = tuple(
            self.provider.party_acquisition_candidate(pokemon)
            for pokemon in getattr(party_state, "pokemon", ())
        ) if self.provider.capabilities.nuzlocke else ()
        boxed_pokemon = pc_storage_stable_pokemon if pc_storage_ready else ()
        if self.provider.capabilities.pc_storage:
            self._refresh_pc_save_offset_test(
                pc_save_offset_test_payload, pc_save_offset_test_status)
            self._refresh_pc_sram_search(
                pc_sram_search_payload, pc_sram_search_status)
        boxed_acquisition_candidates = (
            tuple(self.provider.boxed_acquisition_candidate(pokemon) for pokemon in boxed_pokemon)
            if pc_storage_acquisition_ready
            else ()
        )
        pc_pre_observer_trace = None
        if pc_storage_changes:
            for mon in pc_storage_changes:
                candidate = next(
                    (item for item in boxed_acquisition_candidates
                     if item.stable_id == mon.stable_id),
                    None,
                )
                inferred_location = None
                if candidate is not None:
                    _, _, location_id = self.provider.classify_acquisition(
                        candidate, acquisition_run
                    )
                    if acquisition_run is not None and location_id in acquisition_run.encounters:
                        inferred_location = acquisition_run.encounters[location_id].location
                LOGGER.info(
                    "Stable BOX record frame=%s timestamp=%s id=%s box=%s slot=%s "
                    "species=%s nickname=%s pid=0x%08X checksum=0x%04X level=%s "
                    "AcquisitionCandidate(source_location='BOX')=%s inferred_location=%s",
                    getattr(pc_storage_state, "scan_frame", None),
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    mon.stable_id,
                    mon.box_index, mon.slot_index, mon.species_name, mon.decoded.nickname,
                    mon.decoded.pid, mon.decoded.checksum, mon.decoded.met_level,
                    candidate is not None, inferred_location or mon.met_location_name,
                )
                if not pc_storage_acquisition_ready:
                    reason = (
                        f"PC scan gate: connected={snapshot.connected}, "
                        f"available={getattr(pc_storage_state, 'available', False)}, "
                        f"fresh={pc_storage_fresh}, "
                        "acquisition_enabled="
                        f"{pc_payload_data.get('pc_storage_acquisition_enabled')}"
                    )
                    LOGGER.warning(
                        "BOX candidate suppressed before observer: id=%s reason=%s",
                        mon.stable_id, reason,
                    )
                    pc_pre_observer_trace = {
                        "source": "BOX",
                        "stable_id": mon.stable_id,
                        "pokemon": mon.decoded.nickname or mon.species_name,
                        "species": mon.species_name,
                        "box": mon.box_index,
                        "slot": mon.slot_index,
                        "location": mon.met_location_name,
                        "status": "suppressed",
                        "reason": reason,
                        "baseline_member": False,
                        "already_observed": None,
                        "timestamp": time.monotonic(),
                    }
        if pc_storage_acquisition_ready:
            lua_run_id = (
                pc_storage_payload.payload.get("run_id")
                if pc_storage_payload is not None
                else None
            )
            baseline_key = (
                lua_run_id,
                acquisition_run.run_id if acquisition_run else None,
            )
            if not self._pc_storage_tracking_active or baseline_key != self._pc_storage_baseline_key:
                self._pc_acquisition_observer.baseline_candidates(
                    boxed_acquisition_candidates,
                    run=acquisition_run,
                    store=self.nuzlocke_view.store,
                )
                self._pc_storage_baseline_ids = {
                    candidate.stable_id for candidate in boxed_acquisition_candidates
                }
                self._pc_storage_post_baseline_ids.clear()
                self._pc_storage_emitted_box_ids.clear()
                self._pc_storage_tracking_active = True
                self._pc_storage_baseline_key = baseline_key
                LOGGER.info(
                    "PC baseline established: Lua run=%s Nuzlocke run=%s occupied=%s ids=%s",
                    lua_run_id, baseline_key[1], len(self._pc_storage_baseline_ids),
                    sorted(self._pc_storage_baseline_ids),
                )
                self._pc_last_event = (
                    f"Baseline established with {len(self._pc_storage_baseline_ids)} Pokemon."
                )
            self._pc_storage_post_baseline_ids.update(
                candidate.stable_id for candidate in boxed_acquisition_candidates
                if candidate.stable_id not in self._pc_storage_baseline_ids
            )
        new_acquisitions = ()
        try:
            party_acquisitions = self._acquisition_observer.observe(
                party_acquisition_candidates,
                connected=snapshot.connected,
                valid_snapshot=party_snapshot_valid,
                run=acquisition_run,
                store=self.nuzlocke_view.store,
                classify=self.provider.classify_acquisition,
                classify_first_party=lambda candidate, run: self.provider.classify_acquisition(
                    candidate, run, first_party_member=True
                ),
            )
            boxed_acquisitions = self._pc_acquisition_observer.observe(
                boxed_acquisition_candidates,
                connected=snapshot.connected,
                valid_snapshot=pc_storage_acquisition_ready,
                run=acquisition_run,
                store=self.nuzlocke_view.store,
                classify=self.provider.classify_acquisition,
            )
            new_acquisitions = (*party_acquisitions, *boxed_acquisitions)
            self._pc_storage_emitted_box_ids.update(
                event.stable_id for event in boxed_acquisitions
            )
            for event in boxed_acquisitions:
                source_candidate = next(
                    (item for item in boxed_acquisition_candidates
                     if item.stable_id == event.stable_id), None
                )
                self._pc_last_event = (
                    f"{event.nickname or event.species_name} appeared in "
                    f"Box {source_candidate.box_index} Slot {source_candidate.slot_index} - "
                    "BOX acquisition emitted."
                    if source_candidate is not None
                    else f"{event.nickname or event.species_name} - BOX acquisition emitted."
                )
                LOGGER.info(
                    "BOX acquisition event emitted: id=%s species=%s nickname=%s level=%s "
                    "location=%s frame=%s timestamp=%s status=pending",
                    event.stable_id, event.species_name, event.nickname, event.met_level,
                    event.suggested_location_name or event.met_location_name,
                    getattr(pc_storage_state, "scan_frame", None), event.detected_at,
                )
        except OSError:
            LOGGER.exception("Could not persist Nuzlocke acquisition observation")
        if new_acquisitions:
            self.nuzlocke_view.refresh_acquisition_suggestions()
        acquisition_trace = max(
            (
                trace for trace in (
                    self._acquisition_observer.last_decision,
                    self._pc_acquisition_observer.last_decision,
                    pc_pre_observer_trace,
                ) if trace is not None
            ),
            key=lambda trace: trace["timestamp"],
            default=None,
        )
        if (
            acquisition_trace is not None
            and acquisition_trace["timestamp"] > self._last_acquisition_trace_timestamp
        ):
            self._last_acquisition_trace_timestamp = acquisition_trace["timestamp"]
            self.nuzlocke_view.report_acquisition_trace(acquisition_trace)
        self._pc_monitoring_ready = bool(
            pc_storage_acquisition_ready
            and pc_payload_data.get("pc_storage_resolver_kind") == "session_pc_cache"
            and acquisition_run is not None
            and self._pc_storage_tracking_active
            and self._pc_storage_baseline_key == (
                pc_payload_data.get("lua_run_id") or pc_payload_data.get("run_id"),
                acquisition_run.run_id,
            )
        )
        detection_sources = []
        if party_snapshot_valid:
            detection_sources.append("Party")
        if self._pc_monitoring_ready:
            detection_sources.append("Box")
        self.nuzlocke_view.detection_sources_label.setText(
            "Automatic encounter detection: "
            + (" + ".join(detection_sources) if detection_sources else "Paused")
        )
        discovery_data = (
            pc_storage_discovery_payload.payload
            if pc_storage_discovery_payload is not None
            and isinstance(pc_storage_discovery_payload.payload, dict)
            else {}
        )
        discovery_status = discovery_data.get("pc_storage_resolver_status")
        cache_progress_status = pc_storage_discovery_progress.get("status")
        if self._pc_monitoring_ready:
            self._pc_resolver_lifecycle = "baseline-ready"
        elif cache_progress_status == "cache-pending":
            self._pc_resolver_lifecycle = "cache-pending"
        elif self._pc_layout_requested_at is not None:
            self._pc_resolver_lifecycle = "discovering"
        elif cache_progress_status == "failed" and discovery_status != "cache-confirmation-failed":
            self._pc_resolver_lifecycle = "discovery-failed"
        elif pc_payload_data.get("pc_storage_resolver_status") == "stale" or (
            self._pc_storage_tracking_active and not pc_storage_ready
        ):
            self._pc_resolver_lifecycle = "stale"
        elif discovery_status in {"stale", "cache-confirmation-failed"}:
            self._pc_resolver_lifecycle = discovery_status
        elif pc_payload_data.get("pc_storage_resolver_status") == "resolved for this session":
            self._pc_resolver_lifecycle = "resolved-awaiting-baseline"
        elif cache_progress_status == "cache-pending" or discovery_status == "cache-pending":
            self._pc_resolver_lifecycle = "cache-pending"
        else:
            self._pc_resolver_lifecycle = "unresolved"
        if self.provider.capabilities.pc_storage:
            self._refresh_pc_storage_debug(
                pc_storage_state, pc_storage_payload, pc_storage_fresh,
                boxed_pokemon, pc_storage_changes, pc_storage_discovery_payload,
                pc_storage_discovery_progress, pc_storage_monitor_debug,
                pc_pre_observer_trace or self._pc_acquisition_observer.last_decision,
            )
            self._refresh_pc_storage_compact_status(
                snapshot.connected, pc_storage_state, pc_payload_data,
                pc_storage_discovery_payload, pc_storage_discovery_progress,
                pc_storage_monitor_debug,
                pc_pre_observer_trace or self._pc_acquisition_observer.last_decision,
            )
        payload_data = party_payload.payload if party_payload is not None else {}
        stale_after = getattr(
            getattr(self.ram_data_source, "server", None), "stale_after_seconds", 5.0
        )
        valid_death_snapshot = _valid_death_snapshot(party_state, party_payload, stale_after)
        hp_samples = tuple(
            PartyHpSample(
                stable_id=pokemon.stable_id,
                species=pokemon.species,
                nickname=(self.provider.party_acquisition_candidate(pokemon).nickname
                          if self.provider.capabilities.nuzlocke else pokemon.nickname),
                level=pokemon.level,
                current_hp=pokemon.current_hp,
                met_location_id=pokemon.met_location_id,
                met_location_name=pokemon.met_location_name,
                met_level=pokemon.met_level,
                origin_game=pokemon.origin_game,
            )
            for pokemon in getattr(party_state, "pokemon", ())
        )
        stream_identity = tuple(
            payload_data.get(key) for key in ("run_id", "core", "domain", "pointer_value")
        )
        frame = _frame_number(payload_data)
        death_candidates = self._party_hp_observer.observe(
            hp_samples,
            connected=snapshot.connected,
            valid_snapshot=valid_death_snapshot,
            run_id=acquisition_run.run_id if acquisition_run else None,
            stream_identity=stream_identity,
            frame=frame,
        )
        if death_candidates:
            self.nuzlocke_view.receive_ram_death_candidates(
                acquisition_run.run_id if acquisition_run else None,
                death_candidates,
            )
        wipe = self._party_wipe_observer.observe(
            hp_samples, connected=snapshot.connected,
            valid_snapshot=valid_death_snapshot,
            run_id=acquisition_run.run_id if acquisition_run else None,
            stream_identity=stream_identity, frame=frame,
        )
        if wipe:
            self._handle_party_wipe(wipe)
        if self._no_run_prompt_observer.observe(
            connected=snapshot.connected, valid_snapshot=valid_death_snapshot,
            party_size=len(hp_samples), active_run=acquisition_run is not None,
            stream_identity=stream_identity, frame=frame,
            enabled=(self.settings.prompt_for_nuzlocke_run
                     and self.provider.capabilities.nuzlocke),
        ):
            self._prompt_create_run_without_active()
        age = max(0.0, time.monotonic() - heartbeat.received_at) if heartbeat else None
        self.backend_status.set_connection(
            snapshot.backend_name, snapshot.connected, heartbeat, age
        )
        domains = heartbeat.payload.get("domains", []) if heartbeat else []
        self.available_domains_label.setText(
            "Available domains: " + (", ".join(map(str, domains)) if domains else "--")
        )
        self._refresh_ram_party_debug(
            party_state,
            party_payload,
            battle_battlers,
            active_enemies,
            acquisition_run,
            ram_fresh=snapshot.details.get("party_payload_fresh", False),
        )
        self._refresh_tracker_party(snapshot.connected, display_party_state)
        nuzlocke_members = ()
        if (
            snapshot.connected
            and display_party_state is not None
            and display_party_state.party_count_valid
        ):
            nuzlocke_members = tuple(
                PartyLevel(
                    nickname=pokemon.nickname,
                    species=pokemon.species,
                    level=pokemon.level,
                )
                for pokemon in display_party_state.pokemon
                if pokemon.checksum_valid and pokemon.level is not None
            )
        self.nuzlocke_view.set_party_levels(nuzlocke_members)
        opponent_summary = (
            ", ".join(f"{battler.species} Lv{battler.level}" for battler in active_enemies)
            or "(none)"
        )
        LOGGER.debug(
            "MainWindow opponent update: %d - %s",
            len(active_enemies),
            opponent_summary,
        )
        if self.provider.capabilities.live_opponents:
            self.current_opponent_panel.set_opponents(active_enemies)
        if self.provider.capabilities.friendship_walk:
            self._refresh_friendship_walk(snapshot, party_payload, active_enemies)
        self._refresh_training_recommendations(active_enemies if snapshot.connected else ())
        self.diagnostics_view.refresh(snapshot, {
            "game_name": self.profile.display_name,
            "command_state": self._walk_command_state,
            "pc_status": self.pc_storage_status_label.text(),
            "pc_layout": self.pc_storage_layout_label.text(),
            "boxed_count": self.pc_storage_occupied_label.text(),
            "acquisition_status": self.pc_storage_catch_status_label.text(),
            "pc_event": self.pc_storage_last_event_label.text(),
            "recovery_message": self.pc_storage_recovery_label.text(),
            "rediscover_enabled": self.pc_discover_current_layout_button.isEnabled(),
        })
        self.nuzlocke_view.set_pc_monitor_status(
            self.pc_storage_status_label.text() + "\n" + self.pc_storage_layout_label.text()
            + "\n" + self.pc_storage_occupied_label.text())
        self.shell.system_label.setText(
            "EMULATOR LINK\n\n" + ("Connected" if snapshot.connected else "Disconnected")
            + "\nCommands · " + self._walk_command_state
            + "\n" + self.pc_storage_status_label.text())
        self.compact_nuzlocke_view.rediscover_button.setEnabled(
            self.pc_discover_current_layout_button.isEnabled())
        self.shell.set_notification(
            "Fainted Pokémon: review the death notification."
            if not self.nuzlocke_view.ram_death_group.isHidden() else
            "New Pokémon: an encounter needs review."
            if self.nuzlocke_view.store.active_run
            and self.nuzlocke_view.acquisition_selector.count() else "")
        if snapshot.connected:
            self._record_ev_changes(party_state)

    def _refresh_tracker_party(self, connected: bool, party_state) -> None:
        self._training_party_connected = connected
        members = ()
        if (
            connected
            and party_state is not None
            and party_state.party_count_valid
            and not (party_state.error and not party_state.pokemon)
        ):
            members = tuple(party_state.pokemon)
        self._compact_member_count = len(members)

        active_slots = {pokemon.slot for pokemon in members}
        for slot, card in self.tracker_party_cards.items():
            if slot not in active_slots:
                card["widget"].hide()
                card["pid"] = None
                card["pokemon"] = None
                if card["sprite_species_id"] is not None:
                    self._clear_tracker_card_sprite(card)

        message = None
        error = None
        warning = getattr(party_state, "live_read_warning", None)
        info = None
        if not connected:
            info = message = "Waiting for BizHawk / EmuHawk RAM connection..."
        elif party_state is None:
            info = message = "Waiting for party RAM data..."
        elif not party_state.party_count_valid:
            error = message = party_state.error or "Waiting for valid party RAM data..."
        elif party_state.error and not party_state.pokemon:
            error = message = party_state.error
        elif warning and not members:
            message = warning
        elif not members:
            info = message = "No Pokémon in party."
        self._set_bottom_status(
            connected=connected,
            error=error,
            warning=warning,
            info=info,
        )
        if message is not None:
            self._refresh_party_workspaces(connected)
            return

        for pokemon in members:
            card = self.tracker_party_cards[pokemon.slot]
            card["pid"] = pokemon.decoded.diagnostics.pid
            card["pokemon"] = pokemon
            self._refresh_tracker_card_sprite(card, pokemon.species_id)
            if self.provider.nickname_is_default(pokemon.nickname, pokemon.species):
                card["name"].hide()
            else:
                card["name"].setText(pokemon.nickname)
                card["name"].show()
            card["species_level"].setText(
                f"{pokemon.species} • Lv. {pokemon.level if pokemon.level is not None else '--'}"
            )
            self._refresh_tracker_held_item(card, pokemon, self.provider)
            card["level_hp"].setText(
                f"HP {pokemon.current_hp if pokemon.current_hp is not None else '--'} / "
                f"{pokemon.max_hp if pokemon.max_hp is not None else '--'}"
            )
            self._refresh_tracker_stats_metadata(card, pokemon)

            self._refresh_tracker_moves(card, pokemon)
            stats = pokemon.current_stats
            current_values = {
                "hp": stats.max_hp if stats is not None else None,
                "attack": stats.attack if stats is not None else None,
                "defense": stats.defense if stats is not None else None,
                "special_attack": stats.special_attack if stats is not None else None,
                "special_defense": stats.special_defense if stats is not None else None,
                "speed": stats.speed if stats is not None else None,
            }
            iv_values = {
                "hp": pokemon.hp_iv,
                "attack": pokemon.attack_iv,
                "defense": pokemon.defense_iv,
                "special_attack": pokemon.special_attack_iv,
                "special_defense": pokemon.special_defense_iv,
                "speed": pokemon.speed_iv,
            }
            for stat, value_label in card["stat_values"].items():
                value = current_values[stat]
                value_label.setText(str(value) if value is not None else "--")
                card["iv_values"][stat].setText(
                    str(iv_values[stat]) if pokemon.checksum_valid else "--"
                )
                nature_role = (
                    "up"
                    if stat != "hp" and stat == pokemon.nature_increased_stat
                    else "down"
                    if stat != "hp" and stat == pokemon.nature_decreased_stat
                    else "neutral"
                )
                for widget in (card["stat_names"][stat], value_label):
                    widget.setProperty("natureRole", nature_role)
                    refresh_style(widget)
            if pokemon.sample_stale and pokemon.battle_stats_stale:
                card["checksum"].setText("Showing last valid record; level/HP may be stale")
            elif pokemon.sample_stale:
                card["checksum"].setText("Warning: showing last valid RAM sample")
            elif pokemon.battle_stats_stale and pokemon.level is None:
                card["checksum"].setText("Warning: invalid level/HP/stats withheld")
            elif pokemon.battle_stats_stale:
                card["checksum"].setText("Warning: keeping last sane level/HP/stats")
            else:
                card["checksum"].setText(
                    "✓ RAM data valid"
                    if pokemon.checksum_valid
                    else "Warning: RAM checksum invalid"
                )
            ram_state = (
                "warning"
                if pokemon.sample_stale or pokemon.battle_stats_stale
                else "valid"
                if pokemon.checksum_valid
                else "invalid"
            )
            card["checksum"].setProperty("ramState", ram_state)
            refresh_style(card["checksum"])
            for stat, label in card["evs"].items():
                value = pokemon.evs[stat]
                label.setText(f"{value} / 252" if pokemon.checksum_valid else "-- / 252")
                card["ev_bars"][stat].setValue(
                    max(0, min(252, value)) if pokemon.checksum_valid else 0
                )
            total = pokemon.ev_total if pokemon.checksum_valid else None
            card["total"].setText(f"Total EVs: {total if total is not None else '--'} / 510")
            card["total_bar"].setValue(max(0, min(510, total)) if total is not None else 0)
            card["widget"].show()
        self._refresh_party_workspaces(connected)

    def _set_bottom_status(
        self,
        *,
        connected: bool,
        error: str | None = None,
        warning: str | None = None,
        info: str | None = None,
    ) -> None:
        if error:
            text, status_role, compact_text = error, "error", None
        elif warning:
            text, status_role = warning, "warning"
            compact_text = "⚠ RAM checksum warning" if self.compact_mode else None
        elif info:
            text, status_role, compact_text = info, "info", None
        else:
            text = f"BizHawk RAM • {'CONNECTED' if connected else 'DISCONNECTED'}"
            status_role = "connected" if connected else "info"
            compact_text = None
        self.tracker_status_message.setProperty("statusRole", status_role)
        refresh_style(self.tracker_status_message)
        self.tracker_status_message.set_full_text(text, compact_text)

    def _refresh_tracker_stats_metadata(self, card: dict[str, object], pokemon) -> None:
        compact = self.compact_mode
        ability_name = pokemon.ability_name or f"Unknown #{pokemon.ability_id}"
        if pokemon.checksum_valid:
            card["nature"].setText(
                f"{pokemon.nature_name} • {ability_name}"
                if compact
                else f"Nature: {pokemon.nature_name}"
            )
            card["ability"].setText(f"Ability: {ability_name}")
            friendship = pokemon.friendship
        else:
            card["nature"].setText("Nature: -- • Ability: --" if compact else "Nature: --")
            card["ability"].setText("Ability: --")
            friendship = None

        card["ability"].setVisible(not compact)
        if card["friendship_bar_compact"] != compact:
            card["friendship_bar"].setVisible(not compact)
            card["friendship_bar_compact"] = compact
        if card["friendship_value"] != friendship:
            card["friendship_value"] = friendship
            if friendship is not None:
                card["friendship_bar"].setValue(friendship)
        display_state = (friendship, compact)
        if card["friendship_display_state"] != display_state:
            card["friendship_display_state"] = display_state
            if friendship is None:
                text = "Friendship: -- / 255"
            elif compact:
                text = f"Friendship {friendship}"
            else:
                text = f"Friendship: {friendship} / 255 • {friendship_label(friendship)}"
            card["friendship"].setText(text)

    def _refresh_tracker_moves(self, card: dict[str, object], pokemon) -> None:
        state = (pokemon.checksum_valid, pokemon.moves)
        if card["moves_display_state"] == state:
            return
        card["moves_display_state"] = state
        if not pokemon.checksum_valid:
            text = "Unavailable (checksum invalid)"
        elif pokemon.moves:
            text = "\n".join(pokemon.moves)
        else:
            text = "No moves learned"
        card["moves_list"].setText(text)

    def _refresh_tracker_target_display(self, card: dict[str, object], pokemon) -> None:
        # Legacy formatter retained for compatibility; not used by Training.
        if self.ev_target_store is None:
            return
        pid = pokemon.decoded.diagnostics.pid
        target = self.ev_target_store.get(pid)
        card["ev_target"] = target
        card["clear_target_button"].setEnabled(target is not None)
        summary = card["target_summary"]
        if target is None:
            summary.setText("No EV target set")
            summary.setProperty("targetSeverity", "normal")
            refresh_style(summary)
            for stat in card["evs"]:
                card["ev_annotations"][stat].clear()
                card["ev_annotations"][stat].hide()
                card["ev_bars"][stat].setRange(0, 252)
                card["ev_bars"][stat].show()
            return

        if not pokemon.checksum_valid:
            summary.setText("Target progress unavailable: checksum invalid")
            summary.setProperty("targetSeverity", "warning")
            refresh_style(summary)
            return

        progress = target.progress(pokemon.evs)
        summary.setText(
            "Target complete"
            if progress.complete
            else f"{progress.remaining} EVs remaining"
            if getattr(self, "compact_mode", False)
            else f"Target progress: {progress.achieved} / {progress.target_total}"
            f" • {progress.remaining} EVs remaining"
        )
        summary.setProperty(
            "targetSeverity", "warning" if progress.overshoots else "normal"
        )
        refresh_style(summary)
        for stat, label in card["evs"].items():
            annotation = card["ev_annotations"][stat]
            bar = card["ev_bars"][stat]
            stat_target = target.as_mapping()[stat]
            current = pokemon.evs[stat]
            if stat_target == 0:
                label.setText(str(current) if current == 0 else f"{current} / 0")
                if current > 0:
                    annotation.setText(f"+{current} over target")
                    annotation.setProperty("targetSeverity", "warning")
                    refresh_style(annotation)
                    annotation.show()
                else:
                    annotation.hide()
                bar.hide()
            else:
                label.setText(f"{current} / {stat_target}")
                annotation.setText(
                    f"+{current - stat_target} over target"
                    if current > stat_target
                    else f"{stat_target - current} remaining"
                )
                annotation.setProperty(
                    "targetSeverity", "warning" if current > stat_target else "normal"
                )
                refresh_style(annotation)
                annotation.show()
                bar.setRange(0, stat_target)
                bar.setValue(max(0, min(stat_target, current)))
                bar.show()

    def _edit_ev_target(self, slot: int) -> None:
        if self.ev_target_store is None:
            return
        card = self.tracker_party_cards.get(slot)
        if card is None or card["pid"] is None or card["pokemon"] is None:
            return
        pokemon, pid = card["pokemon"], card["pid"]
        title = (
            pokemon.species
            if self.provider.nickname_is_default(pokemon.nickname, pokemon.species)
            else f"{pokemon.nickname} ({pokemon.species})"
        )
        dialog = EVTargetDialog(title, self.ev_target_store.get(pid), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.ev_target_store.set(pid, dialog.target())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could not save EV target", str(error))
            return
        self._refresh_tracker_target_display(card, pokemon)
        self._refresh_party_workspaces()

    def _clear_ev_target(self, slot: int) -> None:
        if self.ev_target_store is None:
            return
        card = self.tracker_party_cards.get(slot)
        if card is None or card["pid"] is None or card["pokemon"] is None:
            return
        try:
            self.ev_target_store.clear(card["pid"])
        except OSError as error:
            QMessageBox.warning(self, "Could not clear EV target", str(error))
            return
        self._refresh_tracker_target_display(card, card["pokemon"])
        self._refresh_party_workspaces()

    @staticmethod
    def _refresh_tracker_held_item(
        card: dict[str, object], pokemon, provider: GameProvider | None = None
    ) -> bool:
        item_id = pokemon.held_item_id
        if card["held_item_id"] == item_id:
            return False
        card["held_item_id"] = item_id
        icon, name, hint_label = card["item_icon"], card["item_name"], card["item_hint"]
        if pokemon.held_item_name is None:
            icon.clear()
            name.setText("No held item")
            name.setProperty("uiRole", "muted")
            refresh_style(name)
            hint_label.clear()
            hint_label.hide()
            return True
        icon.setPixmap(get_item_sprite(pokemon.held_item_name, 22))
        name.setText(pokemon.held_item_name)
        name.setProperty("uiRole", "heldItem")
        refresh_style(name)
        hint = (provider or default_game_provider()).item_hint(item_id)
        hint_label.setText(hint or "")
        hint_label.setVisible(bool(hint))
        return True

    @staticmethod
    def _refresh_tracker_card_sprite(card: dict[str, object], species_id: int) -> bool:
        size = card["sprite_size"]
        asset = resolve_sprite_asset(species_id)
        if (
            card["sprite_species_id"] == species_id
            and card["sprite_loaded_size"] == size
            and card["sprite_asset"] == asset
        ):
            return False
        MainWindow._clear_tracker_card_sprite(card)
        sprite = card["sprite"]
        sprite.setFixedSize(size, size)
        if asset.kind == "animated" and asset.path is not None:
            movie = QMovie(str(asset.path))
            movie.setParent(sprite)
            if movie.isValid():
                movie.setScaledSize(get_animated_sprite_size(asset.path, size))
                sprite.setMovie(movie)
                card["sprite_movie"] = movie
                movie.start()
            else:
                movie.deleteLater()
                sprite.setPixmap(get_static_sprite(species_id, size))
        elif asset.kind == "static":
            sprite.setPixmap(get_static_sprite(species_id, size))
        card["sprite_species_id"] = species_id
        card["sprite_loaded_size"] = size
        card["sprite_asset"] = asset
        return True

    def _refresh_card_sprite_for_size(self, card: dict[str, object]) -> None:
        pokemon = card["pokemon"]
        if pokemon is not None:
            self._refresh_tracker_card_sprite(card, pokemon.species_id)

    @staticmethod
    def _clear_tracker_card_sprite(card: dict[str, object]) -> None:
        movie = card["sprite_movie"]
        if movie is not None:
            movie.stop()
            movie.deleteLater()
            card["sprite_movie"] = None
        card["sprite"].clear()
        card["sprite_species_id"] = None
        card["sprite_loaded_size"] = None
        card["sprite_asset"] = None

    def _record_ev_changes(self, party_state) -> None:
        changes = self._ev_change_tracker.observe(party_state)
        if not changes:
            return
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        labels = {
            "hp": "HP",
            "attack": "Attack",
            "defense": "Defense",
            "special_attack": "Special Attack",
            "special_defense": "Special Defense",
            "speed": "Speed",
        }
        for change in changes:
            name = (
                change.species
                if self.provider.nickname_is_default(change.nickname, change.species)
                else f"{change.nickname} ({change.species})"
            )
            stat_name = labels.get(change.stat, change.stat)
            full = (
                f"{timestamp}  {name:<24} {stat_name:<15} "
                f"{change.before:>3} -> {change.after:<3}  {change.delta:+d}"
            )
            compact = f"{change.delta:+d} {stat_name} — {name}"
            self._ev_history_records.append((full, compact))
            self._highlight_ev_change(change.slot, change.stat)
        del self._ev_history_records[: -self._ev_history_limit]
        self._render_ev_history()

    def _highlight_ev_change(self, slot: int, stat: str) -> None:
        card = self.tracker_party_cards.get(slot)
        if card is None:
            return
        row = card["ev_rows"].get(stat)
        timer = card["highlight_timers"].get(stat)
        if row is not None and timer is not None:
            row.setProperty("recentChange", True)
            row.style().unpolish(row)
            row.style().polish(row)
            timer.start(1500)

    def _render_ev_history(self) -> None:
        self.ev_change_log.render(self._ev_history_records, self.compact_mode)

    def _clear_ev_log(self) -> None:
        self._ev_history_records.clear()
        self._render_ev_history()

    def _start_coordinate_capture(self, label: str, *, keep_busy: bool = False) -> None:
        if self._coordinate_capture_busy and not keep_busy:
            return
        request = getattr(self.ram_data_source, "request_coordinate_capture", None)
        if not callable(request):
            self.coordinate_discovery_status.setText("No fresh BizHawk RAM snapshot available.")
            return
        try:
            start_offset = int(self.coordinate_scan_start.text().strip(), 0)
            length = int(self.coordinate_scan_length.text().strip(), 0)
        except ValueError:
            self.coordinate_discovery_status.setText(
                "Enter valid hexadecimal or decimal RAM offsets and length."
            )
            return
        if (
            start_offset < 0
            or start_offset % 2
            or length < 2
            or length % 2
            or length > self.provider.coordinate_scan_max_bytes
        ):
            self.coordinate_discovery_status.setText(
                "Use an even RAM offset and even length from 2 bytes through 4 MiB."
            )
            return

        if label == "baseline":
            self.coordinate_discovery.reset()
            self._render_coordinate_candidates()
            clear_preview = getattr(self.ram_data_source, "set_coordinate_preview", None)
            if callable(clear_preview):
                clear_preview(None, None)
            self.coordinate_candidate_details.setText("No coordinate pair selected.")
            self.coordinate_live_preview.setText("Live X: --    Live Y: --")

        capture_id = uuid4().hex
        request_result = request(capture_id, label, start_offset, length)
        if not request_result.accepted:
            self.coordinate_discovery_status.setText(
                request_result.reason or "No fresh BizHawk RAM snapshot available."
            )
            self._set_coordinate_capture_busy(False)
            return
        self._coordinate_capture_id = capture_id
        self._coordinate_capture_label = label
        self._coordinate_capture_source = request_result.source
        self._set_coordinate_capture_busy(True)
        self.coordinate_discovery_status.setText(
            f"Requesting {label} snapshot via {request_result.source or 'BizHawk'}. "
            "Keep the character still until the capture completes."
        )

    def _set_coordinate_capture_busy(self, busy: bool) -> None:
        self._coordinate_capture_busy = busy
        self.coordinate_scan_start.setEnabled(not busy)
        self.coordinate_scan_length.setEnabled(not busy)
        self.coordinate_candidate_combo.setEnabled(not busy)
        for button in self.coordinate_capture_buttons.values():
            button.setEnabled(not busy)

    def _analyze_coordinate_snapshots(self) -> None:
        snapshots = dict(self.coordinate_discovery.snapshots)

        def analyze() -> None:
            candidates = self.provider.find_coordinate_candidates(snapshots)
            self._coordinate_analysis_signals.completed.emit(candidates)

        threading.Thread(
            target=analyze,
            name="coordinate-candidate-analysis",
            daemon=True,
        ).start()

    def _finish_coordinate_analysis(self, candidates) -> None:
        self.coordinate_discovery.candidates = tuple(candidates)
        self.coordinate_discovery.selected_index = None
        self._set_coordinate_capture_busy(False)
        self._render_coordinate_candidates()
        if candidates:
            self.coordinate_discovery_status.setText(
                f"Ranked {len(candidates)} candidate pair(s). Select one for live preview; "
                "the pattern score is not gameplay validation."
            )
        else:
            self.coordinate_discovery_status.setText(
                "No fixed-address pair matched. Confirm each capture followed exactly one tile "
                "and retry; pointer-backed tracing may be needed if the sequence is clean."
            )

    def _poll_coordinate_discovery(self, party_payload) -> None:
        drain = getattr(self.ram_data_source, "drain_coordinate_events", None)
        if callable(drain):
            for event in drain():
                message = self.coordinate_discovery.accept_event(event)
                if message:
                    self.coordinate_discovery_status.setText(message)
                event_type = event.get("type")
                event_id = event.get("capture_id")
                if (
                    event_type == "coordinate_scan_start"
                    and event_id == self._coordinate_capture_id
                ):
                    label = self._coordinate_capture_label or "coordinate"
                    source = self._coordinate_capture_source or "BizHawk"
                    self.coordinate_discovery_status.setText(
                        f"Capturing {label} RAM snapshot via {source}..."
                    )
                elif event_type in {"coordinate_scan_end", "coordinate_scan_error"}:
                    if event_id != self._coordinate_capture_id:
                        continue
                    completed_label = self._coordinate_capture_label
                    self._coordinate_capture_id = None
                    self._coordinate_capture_label = None
                    self._coordinate_capture_source = None
                    if (
                        event_type == "coordinate_scan_error"
                        or completed_label not in self.coordinate_discovery.snapshots
                    ):
                        self._set_coordinate_capture_busy(False)
                        self._render_coordinate_candidates()
                    elif completed_label == "baseline":
                        self.coordinate_discovery_status.setText(
                            "Baseline captured. Staying still for the automatic idle stability check..."
                        )
                        QTimer.singleShot(
                            500,
                            lambda: self._start_coordinate_capture("idle", keep_busy=True),
                        )
                    else:
                        self._set_coordinate_capture_busy(False)
                        if completed_label == "idle":
                            self.coordinate_discovery_status.setText(
                                "Idle check captured. Move exactly one tile RIGHT, then capture Right."
                            )
                        elif completed_label in {"right", "left", "down"}:
                            next_direction = {"right": "LEFT", "left": "DOWN", "down": "UP"}[
                                completed_label
                            ]
                            self.coordinate_discovery_status.setText(
                                f"{completed_label.title()} captured. Move one tile {next_direction}, then capture {next_direction.title()}."
                            )
                        elif completed_label == "up" and self.coordinate_discovery.ready_to_analyze:
                            self.coordinate_discovery_status.setText(
                                "Up captured. Ranking aligned RAM values in the background..."
                            )
                            self._set_coordinate_capture_busy(True)
                            self._analyze_coordinate_snapshots()
                        self._render_coordinate_candidates()
        self._refresh_coordinate_live_preview(party_payload)

    def _render_coordinate_candidates(self) -> None:
        combo = self.coordinate_candidate_combo
        combo.blockSignals(True)
        combo.clear()
        if not self.coordinate_discovery.candidates:
            if self.coordinate_discovery.ready_to_analyze:
                combo.addItem("No fixed-address pairs matched", None)
                self.coordinate_candidate_details.setText(
                    "No aligned u16/s16 values matched the stable, axis-specific return pattern."
                )
            else:
                combo.addItem("No ranked pairs yet", None)
                self.coordinate_candidate_details.setText(
                    "Capture baseline, idle, right, left, down, and up to rank candidates."
                )
        else:
            combo.addItem("Select a candidate pair...", None)
            for index, candidate in enumerate(self.coordinate_discovery.candidates, start=1):
                combo.addItem(
                    f"#{index}  X 0x{candidate.x_offset:08X}  Y 0x{candidate.y_offset:08X}  "
                    f"{candidate.data_type}  pattern score {candidate.confidence}",
                    index - 1,
                )
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _select_coordinate_candidate(self, _index: int) -> None:
        candidate_index = self.coordinate_candidate_combo.currentData()
        candidate = self.coordinate_discovery.select_candidate(candidate_index)
        set_preview = getattr(self.ram_data_source, "set_coordinate_preview", None)
        if candidate is None:
            if callable(set_preview):
                set_preview(None, None)
            self.coordinate_candidate_details.setText("No coordinate pair selected.")
            self.coordinate_live_preview.setText("Live X: --    Live Y: --")
            return
        if callable(set_preview):
            set_preview(candidate.x_offset, candidate.y_offset, candidate.data_type)
        base = getattr(self.profile.memory_profile, "main_ram_base", 0x02000000)
        self.coordinate_candidate_details.setText(
            f"X address 0x{base + candidate.x_offset:08X} (domain offset 0x{candidate.x_offset:08X}); "
            f"Y address 0x{base + candidate.y_offset:08X} (domain offset 0x{candidate.y_offset:08X}); "
            f"Type {candidate.data_type}; idle stability {candidate.idle_stability:.0%}; "
            f"Right X {candidate.right_delta:+d}, Left X {candidate.left_delta:+d}, "
            f"Down Y {candidate.down_delta:+d}, Up Y {candidate.up_delta:+d}; "
            f"pattern score {candidate.confidence} (still needs live gameplay validation)."
        )
        self.coordinate_live_preview.setText("Waiting for live preview sample...")

    def _refresh_coordinate_live_preview(self, party_payload) -> None:
        candidate = self.coordinate_discovery.select_candidate(
            self.coordinate_candidate_combo.currentData()
        )
        if candidate is None or party_payload is None:
            return
        payload = party_payload.payload
        x = payload.get("coordinate_preview_x")
        y = payload.get("coordinate_preview_y")
        if x is None or y is None:
            return
        previous_x = payload.get("coordinate_preview_previous_x")
        previous_y = payload.get("coordinate_preview_previous_y")
        delta_x = x - previous_x if isinstance(previous_x, int) else "--"
        delta_y = y - previous_y if isinstance(previous_y, int) else "--"
        self.coordinate_live_preview.setText(
            f"Live X: {x}    Live Y: {y}    Previous: "
            f"{previous_x if previous_x is not None else '--'} / "
            f"{previous_y if previous_y is not None else '--'}    "
            f"Delta: {delta_x if delta_x == '--' else f'{delta_x:+d}'} / "
            f"{delta_y if delta_y == '--' else f'{delta_y:+d}'}"
        )

    def _refresh_ram_party_debug(
        self,
        party_state,
        party_payload,
        battle_battlers=(),
        active_enemies=(),
        acquisition_run=None,
        ram_fresh: bool = False,
    ) -> None:
        if party_state is None:
            self.ram_party_summary_label.setText("Party count: --")
            lines = ["Waiting for party memory payload."]
            lines.extend(self._friendship_walk_transport_debug_lines(None, ram_fresh))
            self._set_ram_party_debug_text("\n".join(lines))
            return
        count = str(party_state.party_count) if party_state.party_count is not None else "--"
        self.ram_party_summary_label.setText(
            f"Party count: {count} ({'valid' if party_state.party_count_valid else 'invalid'})"
        )
        lines = []
        if party_payload is not None:
            payload = party_payload.payload
            position = self.provider.decode_player_position(payload)
            walk_controller = getattr(self, "friendship_walk", None)
            walk_moves = getattr(walk_controller, "successful_moves", 0)
            lines.extend(
                (
                    f"Source: {party_payload.source}",
                    *self._friendship_walk_transport_debug_lines(party_payload, ram_fresh),
                    f"Frame: {payload.get('frame', '--')}",
                    f"Domain: {payload.get('domain', '--')}",
                    f"Player X: {position.x if position else '--'}",
                    f"Player Y: {position.y if position else '--'}",
                    f"Delta X: {position.delta_x if position and position.delta_x is not None else '--'}",
                    f"Delta Y: {position.delta_y if position and position.delta_y is not None else '--'}",
                    f"Coordinate offsets: {payload.get('player_x_offset', '--')} / {payload.get('player_y_offset', '--')} (signed 16-bit LE; validated)",
                    (
                        f"Friendship Walk: {payload.get('friendship_walk_status', 'Idle')}; "
                        f"successful moves this session: {walk_moves}"
                    ),
                    f"Pointer address: {payload.get('pointer_address', '--')}",
                    f"Pointer offset: {payload.get('pointer_offset', '--')}",
                    f"Pointer value: {payload.get('pointer_value', '--')}",
                    f"Party relative offset: {payload.get('party_relative_offset', '--')}",
                    f"Party address: {payload.get('party_address', '--')}",
                    f"Party offset: {payload.get('party_offset', '--')}",
                    f"Party records address: {payload.get('party_records_address', '--')}",
                    f"Party records offset: {payload.get('party_records_offset', '--')}",
                    "Offset candidates:",
                    *(f"  {candidate}" for candidate in payload.get("party_offset_candidates", [])),
                    "",
                )
            )
            lines.append("Battle Battlers (candidate offsets/fields; validate in gameplay):")
            lines.append(f"Base pointer: {payload.get('pointer_value', '--')}")
            for battler in battle_battlers:
                species_id = battler.species_id if battler.species_id is not None else "--"
                level = battler.level if battler.level is not None else "--"
                hp = (
                    f"{battler.current_hp} / {battler.max_hp or '?'}"
                    if battler.current_hp is not None
                    else "-- / ?"
                )
                stats = ", ".join(
                    f"{name}={value if value is not None else '--'}"
                    for name, value in battler.stats.items()
                )
                lines.extend(
                    (
                        f"Battler {battler.battler_index} - {battler.role}",
                        f"Relative offset: 0x{battler.relative_offset:05X}",
                        f"Record address: {_format_optional_hex(battler.address)}",
                        f"Species ID: {species_id}; species: {battler.species}",
                        f"Level: {level}; current HP (diagnostic): {hp}",
                        f"Max HP candidate at +0x4E (diagnostic): {battler.max_hp}",
                        f"HP region raw (+0x48..+0x53): {battler.hp_region_raw_hex or '--'}",
                        f"Candidate stats: {stats}",
                        f"Candidate PID: {_format_optional_hex(battler.pid)}",
                        (
                            f"Candidate ability ID: {battler.ability_id if battler.ability_id is not None else '--'}; "
                            f"candidate held item ID: {battler.held_item_id if battler.held_item_id is not None else '--'}"
                        ),
                        f"Candidate nickname bytes: {battler.nickname_raw_hex or '--'}",
                        f"Validation state: {battler.validation_state.value}"
                        + (f" ({battler.validation_reason})" if battler.validation_reason else ""),
                        f"Raw record: {battler.raw_hex or '--'}",
                        "",
                    )
                )
            lines.append("Currently Battling:")
            if active_enemies:
                for battler in active_enemies:
                    lines.append(
                        f"Enemy {1 if battler.battler_index == 1 else 2}: "
                        f"{battler.species}, Lv. {battler.level}, HP "
                        f"{battler.current_hp} / {battler.max_hp or '?'}; "
                        f"Base EV Yield: {self.provider.format_ev_yield(battler.species_id)}"
                    )
            else:
                lines.append("No validated active enemy battlers.")
            lines.append("")
        if party_state.error:
            lines.append(
                f"{'Note' if party_state.party_count_valid else 'Error'}: {party_state.error}"
            )
        if party_state.candidate_count:
            lines.append(f"Scanned non-empty candidates: {party_state.candidate_count}")
        for pokemon in party_state.pokemon:
            diag = pokemon.decoded.diagnostics
            nickname = pokemon.decoded.nickname_diagnostics
            acquisition = pokemon.decoded.acquisition_metadata_diagnostics
            candidate = self.provider.party_acquisition_candidate(pokemon)
            source, confidence, suggested_id = (
                self.provider.classify_acquisition(candidate, acquisition_run)
                if self.provider.capabilities.nuzlocke else ("UNAVAILABLE", "LOW", None))
            suggested_name = None
            if suggested_id and acquisition_run:
                suggested_name = acquisition_run.encounters.get(suggested_id)
                suggested_name = suggested_name.location if suggested_name else None
            elif suggested_id:
                suggested_name = next(
                    (
                        location.name
                        for location in self.provider.nuzlocke_profile.locations
                        if location.location_id == suggested_id
                    ),
                    None,
                )
            if (self.provider.capabilities.nuzlocke
                    and suggested_id is None and source not in {"TRADE", "EGG"}):
                suggested_id = self.provider.nuzlocke_location_id(
                    pokemon.met_location_id, self.provider.nuzlocke_profile
                )
                suggested_name = next(
                    (
                        location.name
                        for location in self.provider.nuzlocke_profile.locations
                        if location.location_id == suggested_id
                    ),
                    None,
                )
            already_observed = (
                self.nuzlocke_view.store.has_observed_pokemon(
                    acquisition_run.run_id, pokemon.stable_id
                )
                if acquisition_run
                else False
            )
            terminator = nickname.terminator_unit_index
            terminator_label = (
                f"unit {terminator} (field byte +0x{terminator * 2:02X})"
                if terminator is not None
                else "not present; decoded to field end"
            )
            lines.extend(
                (
                    f"Slot {pokemon.slot} - {pokemon.species}",
                    f"Address: {_format_optional_hex(diag.address)}",
                    f"PID: 0x{diag.pid:08X}",
                    f"Stable Pokémon ID: {pokemon.stable_id}",
                    (
                        f"Checksum: {'VALID' if pokemon.checksum_valid else 'INVALID'} "
                        f"(stored 0x{diag.checksum:04X}, calculated 0x{diag.calculated_checksum:04X})"
                    ),
                    f"Permutation: {diag.block_order} (index {diag.shuffle_index})",
                    f"Species ID: {pokemon.species_id}",
                    f"Nature ID: {pokemon.nature_id}",
                    f"Nature: {pokemon.nature_name}",
                    f"Friendship: {pokemon.friendship}",
                    f"Nature increased stat: {pokemon.nature_increased_stat or '--'}",
                    f"Nature decreased stat: {pokemon.nature_decreased_stat or '--'}",
                    f"Ability slot: {pokemon.ability_slot or '--'}",
                    f"Ability ID: {pokemon.ability_id}",
                    f"Ability name: {pokemon.ability_name or '--'}",
                    "IVs:",
                    f"  HP: {pokemon.hp_iv}",
                    f"  Attack: {pokemon.attack_iv}",
                    f"  Defense: {pokemon.defense_iv}",
                    f"  Sp. Atk: {pokemon.special_attack_iv}",
                    f"  Sp. Def: {pokemon.special_defense_iv}",
                    f"  Speed: {pokemon.speed_iv}",
                    (
                        "IV packed word: "
                        f"0x{pokemon.decoded.packed_ivs:08X} "
                        f"(record +0x{IVS_RECORD_OFFSET:02X}, "
                        f"decrypted box data +0x{IVS_BOX_DATA_OFFSET:02X})"
                    ),
                    (
                        "IV packed flags: "
                        f"egg={str(pokemon.decoded.ivs.is_egg).lower()}, "
                        f"has_nickname={str(pokemon.decoded.ivs.has_nickname).lower()}"
                    ),
                    f"Held item ID: {pokemon.held_item_id}",
                    f"Held item name: {pokemon.held_item_name or 'None'}",
                    f"Held item sprite slug: {item_sprite_slug(pokemon.held_item_name) or '--'}",
                    f"Held item sprite path: {item_sprite_path(pokemon.held_item_name) or '--'}",
                    f"Held item sprite exists: {str(item_sprite_exists(pokemon.held_item_name)).lower()}",
                    f"Held item decrypted box-data offset: 0x{HELD_ITEM_BOX_DATA_OFFSET:02X}",
                    f"Held item party-record layout offset: 0x{HELD_ITEM_RECORD_OFFSET:02X}",
                    f"Nickname absolute address: {_format_optional_hex(nickname.absolute_address)}",
                    f"Nickname record-relative offset: 0x{nickname.record_relative_offset:02X}",
                    f"Nickname decrypted box-data offset: 0x{nickname.box_data_relative_offset:02X}",
                    f"Nickname raw bytes: {nickname.raw_bytes_hex}",
                    f"Nickname code units: {[f'0x{unit:04X}' for unit in nickname.code_units]}",
                    f"Nickname terminator: {terminator_label}",
                    f"Nickname decoded string: {nickname.decoded_string!r}",
                    f"Nickname final: {pokemon.nickname!r}",
                    (
                        f"Met location: ID {pokemon.met_location_id} "
                        f"({pokemon.met_location_name or 'Unknown'})"
                    ),
                    (
                        f"Met location extended offsets: record +0x{acquisition.met_location_record_offset:02X}, "
                        f"decrypted box +0x{acquisition.met_location_box_data_offset:02X}, "
                        f"absolute {_format_optional_hex(diag.address + acquisition.met_location_record_offset if diag.address is not None else None)}; "
                        f"decrypted field bytes {pokemon.met_location_id.to_bytes(2, 'little').hex(' ').upper()}"
                    ),
                    (
                        f"Met location DP-style ID: {pokemon.decoded.met_location_dp_id} "
                        f"(record +0x{acquisition.met_location_dp_record_offset:02X}, "
                        f"decrypted box +0x{acquisition.met_location_dp_box_data_offset:02X})"
                    ),
                    (
                        f"Met level: {pokemon.met_level if pokemon.met_level is not None else '--'} "
                        f"(record +0x{acquisition.met_level_record_offset:02X}, "
                        f"decrypted box +0x{acquisition.met_level_box_data_offset:02X}, "
                        f"raw 0x{pokemon.decoded.met_level or 0:02X})"
                    ),
                    (
                        f"Egg location ID: {pokemon.egg_location_id} "
                        f"(record +0x{acquisition.egg_location_record_offset:02X}, "
                        f"decrypted box +0x{acquisition.egg_location_box_data_offset:02X}, "
                        f"raw {pokemon.egg_location_id.to_bytes(2, 'little').hex(' ').upper()})"
                    ),
                    (
                        f"Origin game ID: {pokemon.origin_game} "
                        f"(record +0x{acquisition.origin_game_record_offset:02X}, "
                        f"decrypted box +0x{acquisition.origin_game_box_data_offset:02X}, "
                        f"raw 0x{pokemon.origin_game:02X})"
                    ),
                    (
                        f"Met date (YY/MM/DD): {pokemon.met_date or '--'} "
                        f"(record +0x{acquisition.met_date_record_offset:02X}, "
                        f"decrypted box +0x{acquisition.met_date_box_data_offset:02X})"
                    ),
                    f"Is egg: {str(pokemon.is_egg).lower()}",
                    f"Acquisition classification: {source} ({confidence.lower()} confidence)",
                    f"Already observed in active run: {str(already_observed).lower()}",
                    (
                        f"Suggested Nuzlocke location: {suggested_name or '--'}"
                        + (f" ({suggested_id})" if suggested_id else "")
                    ),
                    f"Level: {pokemon.level if pokemon.level is not None else '--'}",
                    (
                        f"Current HP: {pokemon.current_hp if pokemon.current_hp is not None else '--'} / "
                        f"{pokemon.max_hp if pokemon.max_hp is not None else '--'}"
                    ),
                    "Current stats from party tail:",
                    f"  Max HP: {pokemon.decoded.current_stats.max_hp}",
                    f"  Attack: {pokemon.decoded.current_stats.attack}",
                    f"  Defense: {pokemon.decoded.current_stats.defense}",
                    f"  Sp. Atk: {pokemon.decoded.current_stats.special_attack}",
                    f"  Sp. Def: {pokemon.decoded.current_stats.special_defense}",
                    f"  Speed: {pokemon.decoded.current_stats.speed}",
                    f"Battle stats raw (0x88-0x9B): {diag.battle_stats_raw_hex}",
                    f"Battle stats decrypted (0x88-0x9B): {diag.battle_stats_decrypted_hex}",
                    f"Battle stats valid: {str(diag.battle_stats_valid).lower()}",
                    f"Battle stats validation: {diag.battle_stats_error or 'OK'}",
                )
            )
            if pokemon.checksum_valid:
                lines.extend(
                    (
                        *(f"{stat}: {value}" for stat, value in pokemon.evs.items()),
                        f"Total EVs: {pokemon.ev_total}",
                        "",
                    )
                )
            else:
                lines.extend(("EVs withheld because checksum is invalid", ""))
        if not party_state.pokemon and not party_state.error:
            lines.append("No occupied party slots reported.")
        text = "\n".join(lines)
        self._set_ram_party_debug_text(text)

    def _set_pc_storage_discovery_expanded(self, expanded: bool) -> None:
        if expanded:
            current_sizes = self.pc_storage_panes_splitter.sizes()
            if sum(current_sizes) > 0:
                self._pc_storage_discovery_restore_sizes = current_sizes
            self.pc_storage_panes_splitter.setSizes([1, 3, 1, 2])
            self.pc_storage_discovery_expand_button.setText("Restore")
            return

        restore_sizes = getattr(self, "_pc_storage_discovery_restore_sizes", [1, 2, 1, 1])
        self.pc_storage_panes_splitter.setSizes(restore_sizes)
        self.pc_storage_discovery_expand_button.setText("Expand")

    def _request_pc_storage_scan(self) -> None:
        request = getattr(self.ram_data_source, "request_pc_storage_scan", None)
        if not callable(request) or not request():
            self.pc_storage_scan_status_label.setText(
                "Request failed; confirm the updated Lua script is running."
            )
            return
        self._pc_scan_requested_at = time.monotonic()
        self.pc_storage_scan_status_label.setText("Scan requested; waiting for Lua response.")

    def _request_pc_sram_pokemon_search(self) -> None:
        request = getattr(self.ram_data_source, "request_pc_sram_pokemon_search", None)
        if not callable(request):
            self.pc_sram_search_status_label.setText(
                "failed: live SRAM search transport is unavailable"
            )
            return
        nickname = self.pc_layout_nickname_edit.text().strip()
        species_id = self.pc_layout_species_combo.currentData()
        if self._pc_sram_search_target is not None:
            nickname, species_id = self._pc_sram_search_target
        try:
            dispatched = request(nickname, species_id)
        except Exception as exc:
            LOGGER.exception("Live SRAM Pokemon search dispatch failed.")
            self.pc_sram_search_status_label.setText(
                f"failed: {type(exc).__name__}: {exc}"
            )
            return
        if not dispatched:
            server = getattr(self.ram_data_source, "server", None)
            status_getter = getattr(server, "pc_sram_search_status", None)
            status = status_getter() if callable(status_getter) else {}
            error = status.get("error") if isinstance(status, dict) else None
            self.pc_sram_search_status_label.setText(
                f"failed: {error or 'BizHawk did not accept the SRAM search'}"
            )
            return
        self._pc_sram_search_requested_at = time.monotonic()
        if self._pc_sram_search_target is None:
            self._pc_sram_search_target = (nickname, species_id)
        self.pc_sram_search_button.setEnabled(False)
        self.pc_sram_search_status_label.setText(
            f"queued; target {nickname} / #{species_id}; reading SRAM incrementally"
        )
        LOGGER.info(
            "Live SRAM Pokemon search dispatched from UI: nickname=%s species=%s",
            nickname,
            species_id,
        )

    def _reset_pc_sram_search_target(self) -> None:
        self._pc_sram_search_target = None
        self.pc_sram_search_status_label.setText("A/B target reset; next capture uses the selected species")

    def _request_pc_save_offset_test(self) -> None:
        request = getattr(self.ram_data_source, "request_pc_save_offset_test", None)
        if not callable(request):
            self.pc_save_offset_test_status_label.setText(
                "failed: save-offset test transport is unavailable"
            )
            return
        save_ram_directory = self.pc_save_offset_directory_edit.text().strip()
        if not save_ram_directory:
            self.pc_save_offset_test_status_label.setText(
                "failed: select BizHawk's configured SaveRAM directory"
            )
            return
        try:
            dispatched = request(
                save_ram_directory,
                expected_nickname=self.pc_layout_nickname_edit.text().strip() or None,
                expected_species_id=self.pc_layout_species_combo.currentData(),
            )
        except Exception as exc:
            LOGGER.exception("Save-file PC offset test dispatch failed.")
            self.pc_save_offset_test_status_label.setText(
                f"failed: {type(exc).__name__}: {exc}"
            )
            return
        if not dispatched:
            server = getattr(self.ram_data_source, "server", None)
            status_getter = getattr(server, "pc_save_offset_test_status", None)
            dispatch_status = status_getter() if callable(status_getter) else {}
            error = dispatch_status.get("error") if isinstance(dispatch_status, dict) else None
            self.pc_save_offset_test_status_label.setText(
                f"failed: {error or 'BizHawk did not accept the test command'}"
            )
            return
        self._pc_save_offset_test_requested_at = time.monotonic()
        self.pc_save_offset_test_button.setEnabled(False)
        self.pc_save_offset_test_status_label.setText(
            "queued; waiting for SaveRAM flush and file read"
        )
        LOGGER.info("Save-file PC offset test command dispatched from the UI.")

    @staticmethod
    def _default_bizhawk_save_ram_directory() -> str:
        configured = os.environ.get("BIZHAWK_SAVE_RAM_DIR")
        candidates = [
            Path(configured).expanduser() if configured else None,
            Path.home() / "Downloads" / "BizHawk-2.11.1-win-x64" / "NDS" / "SaveRAM",
        ]
        for candidate in candidates:
            if candidate is not None and candidate.is_dir():
                return str(candidate)
        return ""

    def _browse_pc_save_offset_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select BizHawk's NDS SaveRAM directory",
            self.pc_save_offset_directory_edit.text().strip(),
        )
        if directory:
            self.pc_save_offset_directory_edit.setText(directory)

    def _refresh_pc_save_offset_test(self, payload, status) -> None:
        status = status if isinstance(status, dict) else {"status": "idle"}
        state = str(status.get("status", "idle"))
        if state == "scanning":
            received = int(status.get("bytes_received") or 0)
            total = int(status.get("total_bytes") or 0)
            percent = min(100, received * 100 // max(1, total)) if total else 0
            chunks = status.get("chunks_received")
            chunk_count = status.get("chunk_count")
            chunk_text = (
                f"; chunks {chunks}/{chunk_count}"
                if chunks is not None and chunk_count is not None
                else ""
            )
            self.pc_save_offset_test_status_label.setText(
                f"scanning {percent}% ({received:,}/{total:,} bytes){chunk_text}"
            )
            self.pc_save_offset_test_button.setEnabled(False)
        elif state == "queued":
            self.pc_save_offset_test_status_label.setText(
                "queued; waiting for SaveRAM flush and file read"
            )
            self.pc_save_offset_test_button.setEnabled(False)
        elif state == "failed":
            self.pc_save_offset_test_status_label.setText(
                f"failed: {status.get('error') or 'capture failed'}"
            )
            self.pc_save_offset_test_button.setEnabled(True)
        elif state == "completed":
            self.pc_save_offset_test_status_label.setText("completed")
            self.pc_save_offset_test_button.setEnabled(True)
        else:
            self.pc_save_offset_test_status_label.setText("idle")
            self.pc_save_offset_test_button.setEnabled(True)

        if (
            self._pc_save_offset_test_requested_at is not None
            and state in {"queued", "scanning"}
            and time.monotonic() - self._pc_save_offset_test_requested_at > 45.0
        ):
            self.pc_save_offset_test_status_label.setText(
                "failed: no complete save-offset response after 45s"
            )
            self.pc_save_offset_test_button.setEnabled(True)
            self._pc_save_offset_test_requested_at = None

        result = getattr(payload, "payload", None)
        if not isinstance(result, dict):
            return
        result_key = (
            result.get("test_id"),
            result.get("capture_number"),
            result.get("error"),
        )
        if result_key == self._last_pc_save_offset_test_key:
            return
        self._last_pc_save_offset_test_key = result_key
        self._pc_save_offset_test_requested_at = None
        self._last_pc_save_offset_test_text = self._pc_save_offset_test_text(result)
        self.pc_save_offset_test_details.setPlainText(
            self._last_pc_save_offset_test_text
        )

    def _refresh_pc_sram_search(self, payload, status) -> None:
        status = status if isinstance(status, dict) else {"status": "idle"}
        state = str(status.get("status", "idle"))
        if state in {"scanning", "analyzing"}:
            received = int(status.get("bytes_received") or 0)
            total = int(status.get("total_bytes") or 0)
            percent = int(status.get("progress_percent") or 0)
            chunks = status.get("chunks_received")
            chunk_count = status.get("chunk_count")
            chunk_text = (
                f"; chunks {chunks}/{chunk_count}"
                if chunks is not None and chunk_count is not None
                else ""
            )
            phase = "analyzing records" if state == "analyzing" else "scanning"
            self.pc_sram_search_status_label.setText(
                f"{phase} {percent}% ({received:,}/{total:,} bytes){chunk_text}"
            )
            self.pc_sram_search_button.setEnabled(False)
        elif state == "queued":
            self.pc_sram_search_status_label.setText(
                "queued; waiting for Lua to start the SRAM scan"
            )
            self.pc_sram_search_button.setEnabled(False)
        elif state == "failed":
            self.pc_sram_search_status_label.setText(
                f"failed: {status.get('error') or 'SRAM search failed'}"
            )
            self.pc_sram_search_button.setEnabled(True)
        elif state == "completed":
            count = status.get("target_match_count", 0)
            capture = status.get("capture_number", "--")
            self.pc_sram_search_status_label.setText(
                f"completed; capture {capture}; target matches {count}"
            )
            self.pc_sram_search_button.setEnabled(True)
        else:
            self.pc_sram_search_status_label.setText("idle")
            self.pc_sram_search_button.setEnabled(True)

        if (
            self._pc_sram_search_requested_at is not None
            and state in {"queued", "scanning", "analyzing"}
            and time.monotonic() - self._pc_sram_search_requested_at > 55.0
        ):
            self.pc_sram_search_status_label.setText(
                "failed: no complete live SRAM response after 55s"
            )
            self.pc_sram_search_button.setEnabled(True)
            self._pc_sram_search_requested_at = None

        result = getattr(payload, "payload", None)
        if not isinstance(result, dict):
            return
        result_key = (
            result.get("scan_id"),
            result.get("capture_number"),
            result.get("error"),
            result.get("sha256"),
        )
        if result_key == self._last_pc_sram_search_key:
            return
        self._last_pc_sram_search_key = result_key
        self._pc_sram_search_requested_at = None
        self._last_pc_sram_search_text = self._pc_sram_search_text(result)
        self.pc_sram_search_details.setPlainText(self._last_pc_sram_search_text)

    @staticmethod
    def _pc_sram_search_text(result: dict) -> str:
        domain_size = result.get("domain_size")
        if not isinstance(domain_size, int):
            domain_size = 0
        lines = [
            "Live SRAM Box Pokemon Search",
            f"Capture: {result.get('capture_number', '--')} / scan {result.get('scan_id', '--')}",
            f"Lua: {result.get('lua_build_id', '--')} / run {result.get('lua_run_id', '--')}",
            f"Source: {result.get('source', '--')}",
            f"SRAM domain: {result.get('domain', '--')} ({domain_size:,} bytes)",
            (
                "Fixed-offset candidates fitting this domain (not validated): "
                f"{result.get('eligible_save_copy_count', '--')}"
            ),
        ]
        if result.get("error"):
            lines.extend((f"FAILED: {result['error']}", ""))
            return "\n".join(lines)
        identity = result.get("expected_identity") or {}
        domains = result.get("available_domains")
        if isinstance(domains, list):
            lines.append("BizHawk memory domains:")
            for item in domains:
                if isinstance(item, dict):
                    size = item.get("size")
                    size_text = f"0x{size:X}" if isinstance(size, int) else "unknown size"
                    lines.append(
                        f"  {item.get('name', '--')}: {size_text}; "
                        f"readable={item.get('readable', False)}; "
                        f"save-like={item.get('save_like', False)}; "
                        f"offset candidates fit={item.get('eligible_save_copy_count', 0)} (not validated)"
                    )
        lines.extend(
            (
                f"Target: {identity.get('nickname', '--')} / species #{identity.get('species_id', '--')}",
                f"Snapshot SHA-256: {result.get('sha256', '--')}",
                "Checksum-valid target offsets: "
                + (", ".join(result.get("target_match_offsets", [])) or "none"),
                "Matching-copy spacing: "
                + (
                    ", ".join(
                        f"{item.get('from')} -> {item.get('to')} (+0x{item.get('delta', 0):X})"
                        for item in result.get("target_match_copy_deltas", [])
                    )
                    or "single copy or no matches"
                ),
                (
                    "Candidates: "
                    f"{result.get('candidate_records_examined', 0):,} 4-byte-aligned starts; "
                    f"{result.get('checksum_valid_record_count', 0)} checksum-valid records"
                ),
                f"Target matches: {result.get('target_match_count', 0)}",
                f"Layout strongly validated: {str(result.get('layout_strongly_validated', False)).lower()}",
                f"Conclusion: {result.get('conclusion', '--')}",
                "",
            )
        )
        matches = result.get("target_matches")
        if not isinstance(matches, list) or not matches:
            lines.append("No checksum-valid target match was found.")
        else:
            for index, match in enumerate(matches, 1):
                lines.extend(
                    (
                        (
                            f"Match {index}: {match.get('nickname')} / {match.get('species_name')} "
                            f"(#{match.get('species_id')}) at SRAM {match.get('offset_hex')}"
                        ),
                        f"  PID={match.get('pid')} checksum={match.get('checksum')} valid={match.get('checksum_valid')}",
                        "  Nearby raw bytes:",
                    )
                )
                surrounding = match.get("surrounding_bytes") or {}
                raw_hex = str(surrounding.get("hex") or "")
                for position in range(0, len(raw_hex), 32):
                    lines.append(f"    {raw_hex[position : position + 32]}")
                lines.append("  0x88 stride neighbors (-4 through +8 records):")
                for neighbor in match.get("stride_neighbors", []):
                    lines.append(
                        "    "
                        f"{neighbor.get('slot_delta', 0):+d} ({neighbor.get('delta', 0):+d}) "
                        f"{neighbor.get('offset_hex')}: {neighbor.get('classification')}"
                        + (
                            f" {neighbor.get('nickname')} / {neighbor.get('species_name')} "
                            f"(#{neighbor.get('species_id')})"
                            if neighbor.get("nickname")
                            else ""
                        )
                    )
                lines.append("  Nearby checksum-valid records:")
                nearby = match.get("nearby_records") or []
                if not nearby:
                    lines.append("    none within 0x10000 bytes")
                for record in nearby:
                    lines.append(
                        f"    {record.get('offset_hex')} delta={record.get('delta'):+d}: "
                        f"{record.get('nickname')} / {record.get('species_name')} "
                        f"(#{record.get('species_id')}) "
                        f"PID={record.get('pid')} checksum={record.get('checksum')}"
                    )
                stride_walk = match.get("stride_walk") or {}
                lines.append("  0x88 stride walk (up to 540 records each direction):")
                for direction in ("backward", "forward"):
                    direction_data = stride_walk.get(direction, {})
                    lines.append(
                        f"    {direction}: occupied={direction_data.get('occupied', 0)} "
                        f"empty={direction_data.get('empty', 0)} "
                        f"malformed={direction_data.get('malformed', 0)} "
                        f"out-of-range={direction_data.get('out_of_range', 0)} "
                        f"first malformed step={direction_data.get('first_malformed_slot_delta', '--')}"
                    )
                    for record in direction_data.get("occupied_records", []):
                        lines.append(
                            f"      {record.get('slot_delta'):+d} {record.get('offset_hex')}: "
                            f"{record.get('nickname')} / {record.get('species_name')} "
                            f"(#{record.get('species_id')}) checksum={record.get('checksum')}"
                        )
                layout = match.get("layout_as_box_1_slot_1") or {}
                lines.extend(
                    (
                        "  540-slot layout assuming this is Box 1 Slot 1:",
                        (
                            f"    first record={layout.get('first_record_offset_hex', '--')} "
                            f"valid={layout.get('valid_occupied_count', 0)} "
                            f"empty={layout.get('empty_count', 0)} invalid={layout.get('invalid_count', 0)} "
                            f"fits={layout.get('region_fits_domain', False)} "
                            f"plausible={layout.get('plausible', False)}"
                        ),
                    )
                )
                for record in layout.get("occupied_records", []):
                    lines.append(
                        f"    Box {record.get('box')} Slot {record.get('slot')}: "
                        f"{record.get('nickname')} / {record.get('species_name')} "
                        f"(#{record.get('species_id')}) "
                        f"{record.get('offset_hex')} checksum valid"
                    )
                lines.append("")

        comparison = result.get("previous_capture") or {}
        if comparison.get("available"):
            lines.extend(
                (
                    "Capture-to-capture comparison (no in-game save command invoked):",
                    (
                        f"  changed bytes={comparison.get('changed_byte_count', 0)} "
                        f"ranges={comparison.get('changed_range_count', 0)}"
                    ),
                    f"  old target offsets: {', '.join(comparison.get('old_target_offsets', [])) or 'none'}",
                    f"  new target offsets: {', '.join(comparison.get('new_target_offsets', [])) or 'none'}",
                )
            )
            if comparison.get("unmatched_old_target_offsets"):
                lines.append(
                    "  No current match paired with previous offsets: "
                    + ", ".join(comparison["unmatched_old_target_offsets"])
                )
            if comparison.get("unmatched_new_target_offsets"):
                lines.append(
                    "  New matches without a previous offset: "
                    + ", ".join(comparison["unmatched_new_target_offsets"])
                )
            for pair in comparison.get("movement_pairs", []):
                marker = "  <== +0x88 slot forward" if pair.get("one_box_slot_forward") else ""
                lines.append(
                    f"  moved {pair.get('old_offset')} -> {pair.get('new_offset')}: "
                    f"delta {pair.get('delta_hex')}; "
                    f"checksum valid {pair.get('old_checksum_valid', '--')} -> "
                    f"{pair.get('new_checksum_valid', '--')}; "
                    f"PID {pair.get('old_pid', '--')} -> {pair.get('new_pid', '--')}"
                    f"{marker}"
                )
                if pair.get("inferred_first_record_offset") is not None:
                    lines.append(
                        "    Assuming Capture A was Box 1 Slot 1: "
                        f"array start=0x{pair['inferred_first_record_offset']:05X}; "
                        f"current target=Box {pair.get('inferred_box')} "
                        f"Slot {pair.get('inferred_slot')}"
                    )
            lines.append("  Changed SRAM ranges:")
            for changed in comparison.get("changed_ranges", []):
                lines.append(
                    f"    {changed.get('start_offset')}..{changed.get('end_offset')} "
                    f"({changed.get('byte_count')} bytes)"
                )
            if comparison.get("changed_ranges_truncated"):
                lines.append("    Additional changed ranges omitted from display.")
        else:
            lines.extend(
                (
                    "Capture baseline: saved for this Lua run and SRAM domain.",
                    "Move the Pokemon without using the in-game Save command, then run this search again.",
                )
            )
            if comparison.get("reason"):
                lines.append(f"  {comparison['reason']}")
        return "\n".join(lines)

    @staticmethod
    def _pc_save_offset_test_text(result: dict) -> str:
        lines = [
            "Save-File PC Offset Test",
            f"Capture: {result.get('capture_number', '--')} / test {result.get('test_id', '--')}",
            (
                "Lua: "
                f"{result.get('lua_build_id', '--')} / "
                f"{result.get('lua_game_id', '--')} / "
                f"{result.get('lua_game_version', '--')}"
            ),
            f"Script: {result.get('lua_script_source', '--')}",
            f"Lua run: {result.get('lua_run_id', '--')}",
            f"ROM name/hash: {result.get('lua_rom_name', '--')} / {result.get('lua_rom_hash', '--')}",
            f"System ID: {result.get('lua_system_id', '--')}",
            f"Loaded ROM path: {result.get('lua_rom_path') or 'not exposed by this BizHawk Lua API'}",
            f"ROM path API: {result.get('lua_rom_path_api', 'not exposed')}",
            f"Configured SaveRAM path: {result.get('lua_configured_save_ram_path') or 'not exposed by client.getconfig()'}",
            (
                "SaveRAM API: client.saveram() "
                f"available={str(result.get('save_ram_api_available', False)).lower()}; "
                f"flush call ok={str(result.get('save_ram_flush_call_ok', False)).lower()}"
            ),
            f"SaveRAM directory: {result.get('save_ram_directory', '--')}",
            f"Active SaveRAM candidate: {result.get('save_ram_file_path', '--')}",
            f"Detection reason: {result.get('save_ram_detection_reason', '--')}",
            f"Save filename stem: {result.get('save_ram_filename_stem', '--')}",
            f"Flush semantics: {result.get('save_ram_flush_semantics', '--')}",
            "Diagnostic only; PC acquisition and the production reader are unchanged.",
            "",
            "BizHawk memory domains:",
        ]
        for domain in result.get("domains", ()):
            if not isinstance(domain, dict):
                continue
            size = domain.get("size")
            size_text = f"0x{size:X}" if isinstance(size, int) else "unknown size"
            lines.append(
                f"  {domain.get('name', '--')}: {size_text}; "
                f"readable={str(domain.get('readable', False)).lower()}; "
                f"save-like={str(domain.get('save_like', False)).lower()}; "
                f"eligible copies={domain.get('eligible_copy_count', 0)}"
            )
        changed_files = result.get("save_ram_file_changes", ())
        lines.extend(("", "Files changed by the flush:"))
        if not changed_files:
            lines.append("  None detected.")
        for item in changed_files:
            if isinstance(item, dict):
                after = item.get("after") or {}
                lines.append(
                    f"  {item.get('filename', '--')}: {item.get('reason', '--')}; "
                    f"size={after.get('size', '--')}; "
                    f"modified={after.get('modified_timestamp', '--')}"
                )
        before_files = result.get("save_ram_directory_before", ())
        after_files = result.get("save_ram_directory_after", ())
        lines.append(
            f"Directory metadata captured: before={len(before_files)} files; "
            f"after={len(after_files)} files."
        )
        for label, entries in (("Before flush", before_files), ("After flush", after_files)):
            lines.append(f"  {label} file metadata:")
            for item in entries:
                if isinstance(item, dict):
                    lines.append(
                        f"    {item.get('filename', '--')}: size={item.get('size', '--')}; "
                        f"modified={item.get('modified_timestamp', '--')}"
                    )
        if result.get("error"):
            lines.extend(("", f"FAILED: {result['error']}"))
            return "\n".join(lines)

        search = result.get("save_ram_identity_search", {})
        if isinstance(search, dict):
            identity = search.get("identity") or {}
            lines.extend((
                "",
                "Full SaveRAM identity search:",
                f"  Target: {identity.get('nickname', '--')} / species #{identity.get('species_id', '--')}",
                (
                    f"  Searched {search.get('searched_bytes', 0):,} bytes at "
                    f"{search.get('alignment_bytes', 4)}-byte alignment; "
                    f"checksum-valid matches={search.get('match_count', 0)}"
                ),
            ))
            for match in search.get("matches", ()):
                lines.append(
                    f"  {match.get('offset_hex', '--')}: {match.get('nickname', '--')} / "
                    f"{match.get('species', '--')} (#{match.get('species_id', '--')}); "
                    f"checksum={match.get('checksum_valid', False)}"
                )
                positions = match.get("layout_positions", ())
                if positions:
                    lines.append(
                        "    Layout position(s): "
                        + "; ".join(
                            f"{item.get('interpretation')} at copy "
                            f"{item.get('copy_offset')} -> Box {item.get('box')} "
                            f"Slot {item.get('slot')}"
                            for item in positions
                        )
                    )
            if not search.get("matches"):
                lines.append("  No checksum-valid target Pokemon was found in the file.")
            file_comparison = search.get("file_comparison", {})
            if isinstance(file_comparison, dict) and file_comparison.get("has_previous_capture"):
                lines.append(
                    f"  Capture comparison: changed={file_comparison.get('changed')}; "
                    f"changed bytes={file_comparison.get('changed_byte_count')}; "
                    f"changed ranges={file_comparison.get('changed_range_count', 0)}"
                )
                lines.append(
                    "  Mitsu offsets: "
                    f"before={', '.join(file_comparison.get('old_matching_offsets', [])) or 'not found'}; "
                    f"after={', '.join(file_comparison.get('new_matching_offsets', [])) or 'not found'}"
                )
                for movement in file_comparison.get("movement_candidates", ()):
                    lines.append(
                        f"  Movement: {movement.get('old_offset_hex')} -> "
                        f"{movement.get('new_offset_hex')} ({movement.get('delta_hex')} bytes); "
                        f"checksum valid before/after={movement.get('checksum_valid_before')}/"
                        f"{movement.get('checksum_valid_after')}"
                    )
                for changed in file_comparison.get("changed_ranges", ()):
                    lines.append(
                        f"    Changed bytes {changed.get('start')}.."
                        f"{changed.get('end_exclusive')} ({changed.get('length')} bytes)"
                    )

        expected = result.get("expected", {})
        lines.extend((
            "",
            "Layout: 18 boxes x 30 slots x 136 bytes; 540 total records.",
            "Both interpretations are tested: fixed offset as first record (header at -4), and fixed offset as PCBoxes header (records at +4).",
            "",
            "Save-copy candidates:",
        ))
        candidates = result.get("candidates", ())
        if not candidates:
            lines.append("  No eligible save-domain copy was captured.")
        for candidate in candidates:
            lines.extend((
                "",
                (
                    f"{candidate.get('layout_interpretation', '--')}: "
                    f"{candidate.get('domain', '--')} fixed offset "
                    f"{candidate.get('save_copy_offset_tested', '--')}; "
                    f"first record {candidate.get('first_record_offset', '--')}; "
                    f"header {candidate.get('header_offset', '--')}:"
                ),
                (
                    f"  Domain size: {candidate.get('domain_size', '--')}; "
                    f"region fits: {str(candidate.get('record_region_fits_domain', False)).lower()}"
                ),
                f"  PC structure validates: {str(candidate.get('structure_valid', False)).lower()}",
                (
                    f"  Current box: {candidate.get('header_current_box', '--')} "
                    f"(valid={str(candidate.get('header_valid', False)).lower()})"
                ),
                f"  Box 1 Slot 1: {MainWindow._format_save_pc_slot(candidate.get('box_1_slot_1'))}",
                f"  Box 1 Slot 2: {MainWindow._format_save_pc_slot(candidate.get('box_1_slot_2'))}",
                (
                    f"  Expected Box 1 Slot 1 identity match: "
                    f"{str(candidate['expected_identity_match']).lower()}"
                    if "expected_identity_match" in candidate
                    else ""
                ),
                (
                    f"  Occupied: {candidate.get('occupied_count', 0)}; "
                    f"checksum-valid: {candidate.get('checksum_valid_count', 0)}; "
                    f"empty: {candidate.get('empty_count', 0)}; "
                    f"malformed: {candidate.get('invalid_count', 0)}"
                ),
            ))
            for pokemon in candidate.get("occupied_records", ()):
                lines.append(
                    f"    Box {pokemon['box']} Slot {pokemon['slot']}: "
                    f"{pokemon['nickname'] or '(no nickname)'} / "
                    f"{pokemon['species']} (#{pokemon['species_id']}) "
                    f"checksum={'VALID' if pokemon['checksum_valid'] else 'INVALID'}"
                )
            invalid_records = candidate.get("invalid_records", ())
            if invalid_records:
                lines.append(
                    "    Malformed slot examples: "
                    + ", ".join(
                        f"Box {item['box']} Slot {item['slot']}"
                        for item in invalid_records[:8]
                    )
                )

        comparison = result.get("comparison")
        if isinstance(comparison, dict):
            lines.extend(("", "Comparison with previous manual capture:"))
            if not comparison.get("has_previous_capture"):
                lines.append("  This is the baseline capture; click again after moving a Pokemon without saving.")
            for item in comparison.get("targets", ()):
                lines.append(
                    f"  {item.get('domain')} at {item.get('record_offset')}: "
                    f"{item.get('status')}; changed bytes={item.get('changed_byte_count', '--')}; "
                    f"changed slots={item.get('changed_slot_count', 0)}"
                )
                for slot in item.get("changed_slots", ())[:12]:
                    lines.append(f"    Box {slot['box']} Slot {slot['slot']} changed")
            if comparison.get("unchanged_note"):
                lines.append(f"  {comparison['unchanged_note']}")
        lines.append(
            f"Bytes captured from the two PC regions: {result.get('bytes_received', 0):,}; "
            f"fixed offsets tested: {', '.join(expected.get('record_offsets', ())) or '--'}"
        )
        return "\n".join(lines)

    @staticmethod
    def _format_save_pc_slot(slot) -> str:
        if not isinstance(slot, dict) or slot.get("empty"):
            return "empty"
        nickname = slot.get("nickname") or "(no nickname)"
        species = slot.get("species", f"Unknown #{slot.get('species_id', '?')}")
        return (
            f"{nickname} / {species} (#{slot.get('species_id', '?')}), "
            f"checksum={'VALID' if slot.get('checksum_valid') else 'INVALID'}"
        )

    def _request_pc_storage_discovery(self) -> None:
        request = getattr(self.ram_data_source, "request_pc_storage_discovery", None)
        if not callable(request) or not request():
            self.pc_storage_discovery_status_label.setText("failed")
            return
        self._pc_discovery_requested_at = time.monotonic()
        self.pc_storage_discovery_status_label.setText("scanning...")
        self.pc_storage_discovery_cancel_button.setEnabled(True)

    def _reset_pc_baseline(self) -> None:
        self._pc_storage_tracking_active = False
        self._pc_storage_baseline_key = None
        self._pc_storage_baseline_ids.clear()
        self._pc_storage_post_baseline_ids.clear()
        self._pc_storage_emitted_box_ids.clear()
        self._pc_monitoring_ready = False
        self._pc_acquisition_observer = PartyAcquisitionObserver(reset_on_disconnect=False)

    def _maybe_auto_discover_pc_layout(
        self, connected: bool, lua_run_id, pc_payload: dict, progress: dict,
    ) -> None:
        if not connected or not isinstance(lua_run_id, str) or not lua_run_id:
            return
        pc_supported = (
            pc_payload.get("pc_storage_resolver_kind") == "session_pc_cache"
            and isinstance(pc_payload.get("pc_storage_reader_version"), int)
            and pc_payload["pc_storage_reader_version"] >= 4
        )
        if not pc_supported and self._pc_lua_run_id is None:
            return
        previous_run = self._pc_lua_run_id
        if lua_run_id != previous_run:
            self._pc_lua_run_id = lua_run_id
            self._reset_pc_baseline()
            self._pc_layout_requested_at = None
            self._pc_auto_rediscovery_key = None
            if previous_run is not None:
                reset = getattr(self.ram_data_source, "reset_pc_discovery_for_lua_run", None)
                if callable(reset):
                    reset(lua_run_id)
                self._pc_last_event = "Lua session changed - rediscovering PC layout."
        if not pc_supported or pc_payload.get(
            "lua_run_id", pc_payload.get("run_id")
        ) != lua_run_id:
            return
        if progress.get("status") == "failed" and progress.get("lua_run_id") in {
            None, lua_run_id,
        }:
            self._pc_layout_requested_at = None
        cache_valid = (
            pc_payload.get("pc_storage_resolver_status") == "resolved for this session"
            and pc_payload.get("session_pc_run_id") == lua_run_id
            and pc_payload.get("pc_storage_acquisition_enabled") is True
            and not (
                progress.get("mode") == "session_layout"
                and progress.get("status") == "failed"
            )
        )
        if cache_valid:
            self._pc_auto_rediscovery_key = None
            if progress.get("status") == "completed":
                self._pc_layout_requested_at = None
            return
        stale = (
            pc_payload.get("pc_storage_rediscovery_required") is True
            or pc_payload.get("pc_storage_resolver_status") == "stale"
        )
        if stale:
            stale_key = (lua_run_id, pc_payload.get("session_pc_first_record_address"))
            if stale_key == self._pc_auto_rediscovery_key:
                return
            self._pc_auto_rediscovery_key = stale_key
            self._pc_last_event = "PC layout became stale - rediscovering."
            self._reset_pc_baseline()
            self._discover_current_pc_layout()
            return
        if lua_run_id in self._pc_auto_discovery_requested_runs:
            return
        if progress.get("status") == "failed" and previous_run in {None, lua_run_id}:
            return
        if progress.get("status") in {
            "queued", "scanning", "stalled", "retrying", "analyzing", "cache-pending",
        }:
            return
        self._pc_auto_discovery_requested_runs.add(lua_run_id)
        self._discover_current_pc_layout()

    def _rediscover_pc_layout(self) -> None:
        if self._discover_current_pc_layout():
            self._reset_pc_baseline()
            if self._pc_lua_run_id:
                self._pc_auto_discovery_requested_runs.add(self._pc_lua_run_id)
            self._pc_last_event = "PC layout rediscovery requested."

    def _refresh_pc_storage_compact_status(
        self, connected, state, pc_payload, discovery_payload, progress,
        monitor_debug, acquisition_trace,
    ) -> None:
        discovery = getattr(discovery_payload, "payload", {})
        if not isinstance(discovery, dict):
            discovery = {}
        if not isinstance(progress, dict):
            progress = {}
        discovery_summary = discovery.get("pc_storage_discovery_summary") or {}
        failure = progress.get("status") == "failed" or self._pc_resolver_lifecycle in {
            "cache-confirmation-failed", "discovery-failed",
        }
        anchor_failure = (
            failure and discovery.get("pc_storage_session_layout_result") is True
            and discovery.get("pc_storage_resolver_status") == "unresolved"
        )
        self.pc_storage_anchor_recovery.setVisible(anchor_failure)
        if anchor_failure:
            no_matches = discovery_summary.get("matching_box1_slot1_records_found") == 0
            self.pc_storage_recovery_label.setText(
                "PC layout could not be identified. Box 1 Slot 1 may be empty. "
                "What Pokemon is currently in Box 1 Slot 1?"
                if no_matches and discovery_summary.get("candidate_records_found") == 0
                else "PC layout could not be identified. What Pokemon is currently in Box 1 Slot 1?"
            )
        elif failure:
            self.pc_storage_recovery_label.setText(
                "PC layout could not be resolved. Monitoring new boxed catches is paused."
            )
        else:
            self.pc_storage_recovery_label.setText("")
        if not connected or not pc_payload:
            status = "Connecting"
        elif self._pc_monitoring_ready:
            status = "Monitoring"
        elif self._pc_resolver_lifecycle == "cache-pending":
            status = "Validating PC layout"
        elif self._pc_resolver_lifecycle == "resolved-awaiting-baseline":
            status = "Establishing baseline"
        elif self._pc_resolver_lifecycle == "discovering":
            status = "Discovering PC layout"
        elif failure:
            status = "Monitoring paused"
        elif self._pc_resolver_lifecycle == "stale":
            status = "Rediscovery required"
        else:
            status = "Monitoring paused"
        self.pc_storage_status_label.setText(f"Status: {status}")
        self.pc_discover_current_layout_button.setEnabled(
            status not in {"Discovering PC layout", "Validating PC layout"}
        )
        layout_resolved = (
            pc_payload.get("pc_storage_resolver_status") == "resolved for this session"
            and pc_payload.get("session_pc_run_id") == self._pc_lua_run_id
        )
        self.pc_storage_layout_label.setText(
            "Box layout: Resolved" if layout_resolved else "Box layout: Unresolved"
        )
        self.pc_storage_occupied_label.setText(
            f"Boxed Pokemon: {len(state.valid_pokemon)}"
            if state is not None and state.available else "Boxed Pokemon: --"
        )
        self.pc_storage_catch_status_label.setText(
            f"Monitoring new catches: {'Yes' if self._pc_monitoring_ready else 'No'}"
        )
        self.pc_storage_progress_label.setText(
            f"Progress: {progress.get('progress_percent', 0)}%"
            if status == "Discovering PC layout" else ""
        )
        self.pc_storage_last_event_label.setText(f"Last PC event: {self._pc_last_event}")
        cache_install = progress.get("cache_install") or {}
        expected_name = self.pc_layout_species_combo.currentText()
        expected_nickname = self.pc_layout_nickname_edit.text().strip() or "(any nickname)"
        trace = acquisition_trace if isinstance(acquisition_trace, dict) else {}
        monitor = monitor_debug if isinstance(monitor_debug, dict) else {}
        self.pc_storage_advanced_summary_label.setText(
            "Resolver\n"
            f"  Lua run: {self._pc_lua_run_id or '--'}\n"
            f"  Lua build: {pc_payload.get('lua_build_id', '--')}\n"
            f"  First BoxPokemon: {pc_payload.get('session_pc_first_record_address') or discovery.get('session_pc_first_record_address') or '--'}\n"
            f"  State: {self._pc_resolver_lifecycle}; cache validation: {cache_install.get('lua_validation', '--')}\n"
            f"  Baseline: {len(self._pc_storage_baseline_ids) if self._pc_storage_tracking_active else '--'}; "
            f"current occupied: {len(state.valid_pokemon) if state and state.available else '--'}\n"
            "Acquisition\n"
            f"  Last PC event: {self._pc_last_event}\n"
            f"  Pending stability: {len(monitor.get('pending_stability', ()))}; "
            f"BOX candidates emitted: {len(self._pc_storage_emitted_box_ids)}\n"
            f"  Observer: {trace.get('status', '--')}; suppression: {trace.get('reason') or '--'}\n"
            "Discovery Anchor\n"
            f"  Box 1 Slot 1: {expected_nickname} / {expected_name}"
        )
        self.pc_storage_retry_cache_button.setVisible(
            self._pc_resolver_lifecycle == "cache-confirmation-failed"
        )

    def _discover_current_pc_layout(self) -> bool:
        species_id = self.pc_layout_species_combo.currentData()
        nickname = self.pc_layout_nickname_edit.text().strip()
        request = getattr(
            self.ram_data_source,
            "request_pc_storage_session_layout_discovery",
            None,
        )
        if not isinstance(species_id, int) or not callable(request):
            self.pc_storage_resolver_status_label.setText(
                "PC resolver: failed to dispatch discovery"
            )
            return False
        try:
            dispatched = request(species_id, nickname or None)
        except Exception as exc:
            LOGGER.exception("Session PC layout discovery dispatch failed.")
            self.pc_storage_resolver_status_label.setText(
                f"PC resolver: failed ({type(exc).__name__}: {exc})"
            )
            return False
        if not dispatched:
            self.pc_storage_resolver_status_label.setText(
                "PC resolver: failed to dispatch discovery"
            )
            return False
        if (
            self.settings.pc_box1_species_id != species_id
            or self.settings.pc_box1_nickname != nickname
        ):
            self.settings.pc_box1_species_id = species_id
            self.settings.pc_box1_nickname = nickname
            try:
                self.settings.save_default()
            except OSError:
                LOGGER.exception("Could not save the PC discovery anchor preference")
        self._pc_layout_requested_at = time.monotonic()
        self._pc_discovery_requested_at = self._pc_layout_requested_at
        self.pc_storage_resolver_status_label.setText("PC resolver: discovering")
        self.pc_storage_resolver_address_label.setText("First record: --")
        self.pc_storage_discovery_status_label.setText("scanning...")
        self.pc_storage_discovery_cancel_button.setEnabled(True)
        LOGGER.info(
            "Session-local PC layout discovery dispatched: expected=%s species=%s",
            nickname or "(any nickname)",
            self.pc_layout_species_combo.currentText(),
        )
        return True

    def _retry_pc_storage_cache_install(self) -> None:
        retry = getattr(self.ram_data_source, "retry_pc_storage_session_cache_install", None)
        if not callable(retry) or not retry():
            self.pc_storage_cache_status_label.setText(
                "Cache install: retry failed; check Lua run ID and command transport"
            )
            return
        self._pc_resolver_lifecycle = "cache-pending"
        self.pc_storage_resolver_status_label.setText("PC resolver: cache-pending")
        self.pc_storage_cache_status_label.setText(
            "Cache install: retry queued; waiting for Lua confirmation"
        )
        self.pc_storage_retry_cache_button.setEnabled(False)

    def _cancel_pc_storage_discovery(self) -> None:
        request = getattr(
            self.ram_data_source, "request_pc_storage_discovery_cancel", None
        )
        if not callable(request) or not request():
            self.pc_storage_discovery_status_label.setText("failed: cancel dispatch failed")
            LOGGER.error("PC discovery cancellation could not be dispatched.")
            return
        self.pc_storage_discovery_status_label.setText("cancelling...")
        if self._pc_inspection_requested_at is not None:
            self.pc_storage_inspection_status_label.setText("cancelling...")
        self.pc_storage_discovery_cancel_button.setEnabled(False)

    def _request_pc_storage_anchor_discovery(self) -> None:
        raw_address = self.pc_storage_anchor_address_edit.text().strip()
        address = _parse_optional_int(raw_address)
        request = getattr(self.ram_data_source, "request_pc_storage_discovery", None)
        LOGGER.info(
            "PC anchor UI click: raw_address=%r parsed_address=%s",
            raw_address,
            f"0x{address:08X}" if address is not None else None,
        )
        if address is None:
            error = f"failed: invalid address {raw_address!r}; enter a hex RAM address"
            self.pc_storage_anchor_status_label.setText(error)
            self.pc_storage_discovery_status_label.setText("failed")
            LOGGER.error("PC anchor dispatch stopped: %s", error)
            return
        if not callable(request):
            error = "failed: PC discovery transport is unavailable"
            self.pc_storage_anchor_status_label.setText(error)
            self.pc_storage_discovery_status_label.setText("failed")
            LOGGER.error("PC anchor dispatch stopped: %s", error)
            return
        try:
            dispatched = request(anchor_address=address)
        except Exception as exc:
            error = f"failed: transport raised {type(exc).__name__}: {exc}"
            self.pc_storage_anchor_status_label.setText(error)
            self.pc_storage_discovery_status_label.setText("failed")
            LOGGER.exception("PC anchor transport raised an exception.")
            return
        if not dispatched:
            error = "failed: BizHawk transport did not accept the anchor command"
            self.pc_storage_anchor_status_label.setText(error)
            self.pc_storage_discovery_status_label.setText("failed")
            LOGGER.error("PC anchor dispatch failed: %s", error)
            return
        self._pc_anchor_requested_at = time.monotonic()
        self._pc_anchor_requested_address = address
        self._pc_discovery_requested_at = time.monotonic()
        self.pc_storage_anchor_status_label.setText(
            f"sent 0x{address:08X}; waiting for Lua response"
        )
        self.pc_storage_discovery_status_label.setText("scanning...")
        self.pc_storage_discovery_cancel_button.setEnabled(True)
        LOGGER.info("PC anchor command dispatched from UI: address=0x%08X", address)

    def _inspect_pc_pokemon_anchor(self) -> None:
        raw_address = self.pc_storage_anchor_address_edit.text().strip()
        address = _parse_optional_int(raw_address)
        request = getattr(self.ram_data_source, "request_pc_pokemon_inspection", None)
        LOGGER.info(
            "Pokemon anchor inspection UI click: raw_address=%r parsed_address=%s",
            raw_address,
            f"0x{address:08X}" if address is not None else None,
        )
        if address is None:
            self.pc_storage_inspection_status_label.setText(
                f"failed: invalid address {raw_address!r}"
            )
            return
        if not callable(request):
            self.pc_storage_inspection_status_label.setText(
                "failed: inspection transport is unavailable"
            )
            return
        try:
            dispatched = request(address)
        except Exception as exc:
            LOGGER.exception("Pokemon anchor inspection transport raised an exception.")
            self.pc_storage_inspection_status_label.setText(
                f"failed: {type(exc).__name__}: {exc}"
            )
            return
        if not dispatched:
            self.pc_storage_inspection_status_label.setText(
                "failed: BizHawk did not accept the inspection command"
            )
            return
        self._pc_inspection_requested_at = time.monotonic()
        self._pc_inspection_requested_address = address
        self._pc_discovery_requested_at = self._pc_inspection_requested_at
        self.pc_storage_inspection_status_label.setText(
            f"scanning Main RAM around 0x{address:08X}..."
        )
        self.pc_storage_discovery_cancel_button.setEnabled(True)
        LOGGER.info(
            "Pokemon anchor inspection command dispatched: address=0x%08X", address
        )

    def _search_pc_structure_pointers(self) -> None:
        raw_address = self.pc_storage_anchor_address_edit.text().strip()
        first_record_address = _parse_optional_int(raw_address)
        if first_record_address is None:
            self.pc_storage_pointer_search_status_label.setText(
                f"failed: invalid address {raw_address!r}"
            )
            return
        header_address = first_record_address - 0x28
        request = getattr(
            self.ram_data_source, "request_pc_structure_pointer_search", None
        )
        if not callable(request):
            self.pc_storage_pointer_search_status_label.setText(
                "failed: structure pointer search transport is unavailable"
            )
            return
        try:
            dispatched = request(header_address, first_record_address)
        except Exception as exc:
            LOGGER.exception("PC structure pointer search transport raised an exception.")
            self.pc_storage_pointer_search_status_label.setText(
                f"failed: {type(exc).__name__}: {exc}"
            )
            return
        if not dispatched:
            self.pc_storage_pointer_search_status_label.setText(
                "failed: BizHawk did not accept the pointer search command"
            )
            return
        self._pc_pointer_search_requested_at = time.monotonic()
        self._pc_discovery_requested_at = self._pc_pointer_search_requested_at
        self.pc_storage_pointer_search_status_label.setText(
            f"searching for references to 0x{header_address:08X} and "
            f"0x{first_record_address:08X}..."
        )
        self.pc_storage_discovery_status_label.setText("scanning Main RAM...")
        self.pc_storage_discovery_cancel_button.setEnabled(True)
        LOGGER.info(
            "PC structure pointer search dispatched: header=0x%08X first_record=0x%08X",
            header_address,
            first_record_address,
        )

    def _reset_pc_pokemon_inspection_baseline(self) -> None:
        reset = getattr(
            self.ram_data_source, "reset_pc_pokemon_inspection_baseline", None
        )
        if not callable(reset):
            self.pc_storage_inspection_status_label.setText(
                "failed: inspection baseline control is unavailable"
            )
            return
        reset()
        self.pc_storage_inspection_status_label.setText(
            "baseline cleared; next inspection captures it"
        )

    def _search_pc_pokemon_identity(self) -> None:
        request = getattr(self.ram_data_source, "request_pc_pokemon_search", None)
        if not callable(request):
            self.pc_storage_inspection_status_label.setText(
                "failed: Pokemon identity search transport is unavailable"
            )
            return
        try:
            dispatched = request()
        except Exception as exc:
            LOGGER.exception("Pokemon identity search transport raised an exception.")
            self.pc_storage_inspection_status_label.setText(
                f"failed: {type(exc).__name__}: {exc}"
            )
            return
        if not dispatched:
            self.pc_storage_inspection_status_label.setText(
                "failed: inspect a checksum-valid anchor first, then search RAM"
            )
            return
        self._pc_search_requested_at = time.monotonic()
        self._pc_search_requested_address = _parse_optional_int(
            self.pc_storage_anchor_address_edit.text().strip()
        ) or self._pc_last_inspected_address
        self.pc_storage_inspection_status_label.setText(
            "searching Main RAM for the last inspected Pokemon..."
        )
        self.pc_storage_discovery_cancel_button.setEnabled(True)
        LOGGER.info("Pokemon identity RAM search dispatched from UI.")

    def _refresh_pc_storage_debug(
        self,
        state,
        payload,
        payload_fresh: bool,
        stable_pokemon=(),
        changes=(),
        discovery_payload=None,
        discovery_progress=None,
        monitor_debug=None,
        acquisition_trace=None,
    ) -> None:
        received_at = getattr(payload, "received_at", None)
        pc_payload_data = getattr(payload, "payload", {})
        discovery_payload_data = getattr(discovery_payload, "payload", {})
        resolved_address = (
            pc_payload_data.get("session_pc_first_record_address")
            if isinstance(pc_payload_data, dict)
            else None
        )
        if not resolved_address and isinstance(discovery_payload_data, dict):
            resolved_address = discovery_payload_data.get("session_pc_first_record_address")
        current_run = pc_payload_data.get("lua_run_id") if isinstance(pc_payload_data, dict) else None
        discovered_run = (
            discovery_payload_data.get("session_lua_run_id")
            if isinstance(discovery_payload_data, dict) else None
        )
        resolver_status = self._pc_resolver_lifecycle
        if current_run and discovered_run and current_run != discovered_run:
            resolver_status = "stale"
        if resolver_status in {"cache-pending", "baseline-ready"}:
            self._pc_layout_requested_at = None
        self.pc_storage_resolver_status_label.setText(
            f"PC resolver: {resolver_status}"
        )
        self.pc_storage_resolver_address_label.setText(
            f"First record: {resolved_address or '--'}"
        )
        self.pc_storage_baseline_count_label.setText(
            "Baseline occupied Pokemon: "
            f"{len(self._pc_storage_baseline_ids) if self._pc_storage_tracking_active else '--'}"
        )
        self.pc_storage_monitoring_label.setText(
            "Monitoring for new boxed Pokemon: "
            f"{'yes' if self._pc_monitoring_ready else 'no'}"
        )
        self.pc_storage_baseline_message_label.setText(
            "PC baseline established. New Pokemon appearing after this point will be treated "
            "as acquisition candidates."
            if self._pc_monitoring_ready else
            "Waiting for Lua cache confirmation and a validated PC scan before arming BOX acquisitions."
            if resolver_status in {"cache-pending", "resolved-awaiting-baseline"} else ""
        )
        cache_install = (
            discovery_progress.get("cache_install")
            if isinstance(discovery_progress, dict) else None
        )
        if isinstance(cache_install, dict):
            confirmation_at = cache_install.get("last_confirmation_at")
            confirmation_age = (
                f"{max(0.0, time.monotonic() - confirmation_at):.1f}s"
                if isinstance(confirmation_at, (int, float)) else "--"
            )
            self.pc_storage_cache_status_label.setText(
                f"Cache install: command sent={cache_install.get('command_sent', False)}; "
                f"queued={cache_install.get('command_queued', False)}; "
                f"Lua received={cache_install.get('lua_received', False)}; "
                f"Lua validation={cache_install.get('lua_validation', 'pending')}; "
                f"confirmation received={cache_install.get('confirmation_received', False)}; "
                f"address={cache_install.get('address') or '--'}; "
                f"expected run={cache_install.get('lua_run_id') or '--'}; "
                f"last Lua run={cache_install.get('last_lua_run_id') or current_run or '--'}; "
                f"confirmation age={confirmation_age}"
                + (f"; error={cache_install['error']}" if cache_install.get("error") else "")
            )
        self.pc_storage_retry_cache_button.setEnabled(
            resolver_status == "cache-confirmation-failed"
            and isinstance(cache_install, dict)
            and cache_install.get("lua_run_id") == current_run
        )
        if (
            self._pc_anchor_requested_at is not None
            and time.monotonic() - self._pc_anchor_requested_at >= 30.0
        ):
            error = (
                "failed: no Lua anchor result after 30s; check that BizHawk is advancing "
                "frames and polling the command file"
            )
            self.pc_storage_anchor_status_label.setText(error)
            LOGGER.error(
                "PC anchor timed out waiting for Lua: address=%s",
                f"0x{self._pc_anchor_requested_address:08X}"
                if self._pc_anchor_requested_address is not None
                else None,
            )
            self._pc_anchor_requested_at = None
            self._pc_anchor_requested_address = None
        if (
            self._pc_pointer_search_requested_at is not None
            and time.monotonic() - self._pc_pointer_search_requested_at >= 90.0
        ):
            self.pc_storage_pointer_search_status_label.setText(
                "failed: no Lua pointer-search result after 90s"
            )
            self._pc_pointer_search_requested_at = None
        if (
            self._pc_layout_requested_at is not None
            and resolver_status == "discovering"
            and time.monotonic() - self._pc_layout_requested_at >= 300.0
        ):
            self.pc_storage_resolver_status_label.setText(
                "PC resolver: stale (no Lua cache confirmation after 5 minutes)"
            )
            self.pc_storage_discovery_status_label.setText("failed")
            self._pc_layout_requested_at = None
        if (
            self._pc_scan_requested_at is not None
            and isinstance(received_at, (int, float))
            and received_at >= self._pc_scan_requested_at
        ):
            frame = getattr(state, "scan_frame", None)
            self.pc_storage_scan_status_label.setText(
                f"Fresh scan received at frame {frame if frame is not None else '--'}."
            )
            self._pc_scan_requested_at = None

        if state is None:
            summary = "PC STORAGE READ UNAVAILABLE\nNo PC storage scan has been received."
        elif not state.available:
            summary = (
                "PC STORAGE READ INVALID\n"
                f"0 / {state.records_scanned} valid Pokémon records\n"
                f"{state.error or 'PC storage source validation failed.'}"
            )
        else:
            valid_count = len(state.valid_pokemon)
            if valid_count == 0 and state.records:
                summary = (
                    "PC STORAGE READ INVALID\n"
                    f"0 / {state.records_scanned} valid Pokémon records; "
                    f"{len(state.records)} non-empty records failed validation."
                )
            else:
                summary = (
                    f"PC STORAGE SCAN VALID\n{valid_count} / "
                    f"{state.records_scanned} valid Pokémon records"
                )
                if valid_count == 0:
                    summary += " (no occupied records reported)."
        if summary != self.pc_storage_summary_label.text():
            self.pc_storage_summary_label.setText(summary)

        self._refresh_pc_storage_discovery_results(
            discovery_payload if discovery_payload is not None else payload
        )
        self._refresh_pc_storage_discovery_progress(discovery_progress)
        details = self._pc_storage_debug_text(
            state, payload, payload_fresh, stable_pokemon, changes,
            monitor_debug, acquisition_trace, discovery_payload,
        )
        self._set_pc_storage_debug_text(details)
        self._last_pc_storage_scan_frame = getattr(state, "scan_frame", None)

    def _pc_storage_debug_text(
        self, state, payload, payload_fresh: bool, stable_pokemon=(), changes=(),
        monitor_debug=None, acquisition_trace=None, discovery_payload=None,
    ) -> str:
        source = getattr(payload, "source", None) or "--"
        received_at = getattr(payload, "received_at", None)
        age = (
            f"{max(0.0, time.monotonic() - received_at):.1f}s"
            if isinstance(received_at, (int, float))
            else "--"
        )
        received_bytes = getattr(payload, "bytes_received", 0) or 0
        pointer = getattr(state, "save_data_pointer", None)
        pc_boxes_address = getattr(state, "pc_boxes_address", None)
        records_address = getattr(state, "records_address", None)
        payload_data = getattr(payload, "payload", {})
        reader_version = (
            payload_data.get("pc_storage_reader_version", "unmarked / legacy Lua script")
            if isinstance(payload_data, dict)
            else "unmarked / legacy Lua script"
        )
        lines = [
            "PC Storage Debug",
            f"Storage source: {source} / Main RAM",
            (
                "Lua build ID: "
                f"{payload_data.get('lua_build_id', '--') if isinstance(payload_data, dict) else '--'}"
            ),
            (
                "Lua configured game: "
                f"{payload_data.get('lua_game_id', '--') if isinstance(payload_data, dict) else '--'} / "
                f"{payload_data.get('lua_game_version', '--') if isinstance(payload_data, dict) else '--'}"
            ),
            (
                "Lua script source: "
                f"{payload_data.get('lua_script_source', '--') if isinstance(payload_data, dict) else '--'}"
            ),
            (
                "Lua run ID: "
                f"{payload_data.get('run_id', '--') if isinstance(payload_data, dict) else '--'}"
            ),
            f"Lua PC reader version: {reader_version}",
            f"PC resolver lifecycle: {self._pc_resolver_lifecycle}",
            (
                "PC resolver status: "
                f"{payload_data.get('pc_storage_resolver_status', 'unresolved') if isinstance(payload_data, dict) else 'unresolved'}"
            ),
            (
                "Resolved first record: "
                f"{payload_data.get('session_pc_first_record_address', '--') if isinstance(payload_data, dict) else '--'}"
            ),
            (
                "Resolver scope: Lua run "
                f"{payload_data.get('session_pc_run_id', '--') if isinstance(payload_data, dict) else '--'}"
            ),
            f"Party-reader pointer slot (not proven SaveData): {_format_optional_hex(self.provider.pc_storage.save_data_pointer_address)}",
            f"Party-reader pointer value (not used for PC resolution): {_format_optional_hex(pointer)}",
            "SavePageInfo: not used by the session-local PC resolver",
            (
                "Resolved PC record array start: "
                f"{_format_optional_hex(pc_boxes_address)}"
            ),
            f"Final first BoxPokemon address: {_format_optional_hex(records_address)}",
            f"Raw bytes 0x28 before first record: {getattr(state, 'pc_boxes_header_hex', None) or '--'}",
            f"Final address RAM validation: {str(getattr(state, 'final_address_valid', False)).lower()}",
            f"First BoxPokemon record: {_format_optional_hex(records_address)}",
            f"Record size: 0x{self.provider.pc_storage.box_pokemon_size:X} ({self.provider.pc_storage.box_pokemon_size} bytes)",
            (
                f"Expected boxes: {self.provider.pc_storage.box_count}; slots per box: {self.provider.pc_storage.slots_per_box}; "
                f"total slots: {self.provider.pc_storage.box_count * self.provider.pc_storage.slots_per_box}"
            ),
            (
                f"Scan length: {self.provider.pc_storage.records_size:,} bytes; bytes read from RAM: "
                f"{getattr(state, 'bytes_read', 0):,}"
            ),
            (
                f"Last scan frame: {getattr(state, 'scan_frame', None) if state else '--'}; "
                f"age: {age}; fresh: {str(payload_fresh).lower()}"
            ),
            f"Bytes received: {received_bytes:,}",
            f"Valid records: {len(state.valid_pokemon) if state else 0}",
            f"Stable identities: {len(stable_pokemon)}",
            f"Baseline identity count: {len(self._pc_storage_baseline_ids) if self._pc_storage_tracking_active else '--'}",
            f"Current identity count: {len(state.valid_pokemon) if state and state.available else '--'}",
            f"Additions since baseline: {len(self._pc_storage_post_baseline_ids)}",
            f"Pending stability candidates: {len(monitor_debug.get('pending_stability', ())) if isinstance(monitor_debug, dict) else 0}",
            f"Emitted BOX candidates: {len(self._pc_storage_emitted_box_ids)}",
            f"Monitoring armed: {str(self._pc_monitoring_ready).lower()}",
            f"Empty records: {state.empty_records if state else '--'}",
            f"Checksum failures: {state.checksum_failures if state else 0}",
            f"Malformed records: {getattr(state, 'malformed_records', 0)}",
            f"Scan result: {getattr(state, 'error', None) or ('OK' if state and state.available else 'Waiting for scan')}",
        ]
        discovery_data = getattr(discovery_payload, "payload", {})
        cache_validation = (
            discovery_data.get("cache_validation")
            if isinstance(discovery_data, dict) else None
        )
        if isinstance(cache_validation, dict):
            lines.extend(("", "PC Cache Validation"))
            for label, key, fields in (
                ("Run ID match", "run_id_match", ("expected", "actual", "pass")),
                ("Main RAM range", "main_ram_range", (
                    "first_record", "final_record_end", "main_ram_start",
                    "main_ram_end", "domain_offset", "pass",
                )),
                ("Party range", "party_range", (
                    "available", "party_base", "party_count", "party_record_size",
                    "party_byte_count", "party_end",
                    "pc_region_overlaps_party", "pass",
                )),
                ("Anchor RAM read", "anchor_record_ram_read", (
                    "absolute_address", "domain_offset", "bytes_read",
                    "first_64_bytes_hex", "pass",
                )),
                ("Anchor checksum", "anchor_checksum", (
                    "stored", "calculated", "shuffle_index", "pass",
                )),
                ("Anchor species", "anchor_species", (
                    "decoded_species_id", "decoded_species_name",
                    "expected_species_id", "expected_species_name", "pass",
                )),
                ("Anchor nickname", "anchor_nickname", (
                    "decoded_nickname", "expected_nickname", "decoded_by",
                    "checked_by_lua", "pass",
                )),
                ("Anchor sanity", "anchor_sanity", ("value", "pass")),
                ("540-slot layout", "layout", (
                    "attempted", "valid_occupied", "empty", "malformed", "pass",
                )),
                ("Python/Lua bytes", "anchor_byte_comparison", (
                    "bytes_match", "python_first_64_bytes_hex", "lua_first_64_bytes_hex",
                )),
                ("Python decode of Lua bytes", "python_anchor_decode", (
                    "species_id", "species_name", "nickname", "checksum_valid",
                    "stored_checksum", "calculated_checksum", "shuffle_index",
                )),
            ):
                check = cache_validation.get(key)
                if isinstance(check, dict):
                    lines.append(f"{label}: " + "; ".join(
                        f"{field}={check.get(field, '--')}" for field in fields
                    ))
            if cache_validation.get("python_decode_error"):
                lines.append(f"Python decode error: {cache_validation['python_decode_error']}")
        if isinstance(monitor_debug, dict):
            lines.extend((
                "", "PC Occupancy Trace",
                f"Status: {monitor_debug.get('status', '--')}; frame: {monitor_debug.get('frame', '--')}",
                f"Reason: {monitor_debug.get('reason', '--')}",
            ))
            for label, key in (
                ("Previous occupied", "previous"),
                ("Current occupied", "current"),
                ("New identities", "added"),
                ("Removed identities", "removed"),
                ("Stable new identities", "stable_new"),
                ("Awaiting second scan", "pending_stability"),
            ):
                identities = monitor_debug.get(key) or []
                lines.append(f"{label}: {len(identities)}")
                for item in identities:
                    lines.append(
                        f"  {item.get('stable_id')} · Box {item.get('box')} Slot {item.get('slot')} · "
                        f"{item.get('nickname') or item.get('species')} / {item.get('species')} · "
                        f"PID {item.get('pid')} · checksum {item.get('checksum')}"
                    )
        if isinstance(acquisition_trace, dict):
            lines.extend((
                "", "BOX Acquisition Trace",
                (f"Candidate: {acquisition_trace.get('stable_id', '--')} · "
                 f"{acquisition_trace.get('pokemon', '--')} / {acquisition_trace.get('species', '--')}"),
                (f"Source: {acquisition_trace.get('source', '--')}; "
                 f"Box {acquisition_trace.get('box', '--')} Slot {acquisition_trace.get('slot', '--')}"),
                f"Location: {acquisition_trace.get('location', '--')}",
                (f"Baseline member: {acquisition_trace.get('baseline_member', '--')}; "
                 f"already observed: {acquisition_trace.get('already_observed', '--')}"),
                (f"Status: {acquisition_trace.get('status', '--')}; reason: "
                 f"{acquisition_trace.get('reason', '--')}"),
            ))
        diagnostic_candidates = getattr(state, "diagnostic_candidates", ())
        if diagnostic_candidates:
            lines.extend(("", "PC Storage Diagnostic PageInfo Candidates:"))
            for candidate in diagnostic_candidates:
                lines.append(
                    "  "
                    + ", ".join(
                        f"{key}={value}"
                        for key, value in candidate.items()
                    )
                )
        if changes:
            lines.extend(("", "PC Storage Changes (RAM diff; before Nuzlocke processing):"))
            for mon in changes:
                decoded = mon.decoded
                location = mon.met_location_name or f"Unknown #{decoded.met_location_id}"
                lines.extend(
                    (
                        f"NEW: Box {mon.box_index} Slot {mon.slot_index}",
                        f"Species: {mon.species_name}; nickname: {decoded.nickname or mon.species_name}",
                        (
                            f"PID: 0x{decoded.pid:08X}; checksum: "
                            f"{'VALID' if decoded.checksum_valid else 'INVALID'}"
                        ),
                        (
                            f"Met location: {location} (ID {decoded.met_location_id}); "
                            f"met level: {decoded.met_level if decoded.met_level is not None else '--'}"
                        ),
                    )
                )
        if state is not None and state.records:
            lines.extend(("", "Non-empty box records:"))
            for mon in state.records:
                decoded = mon.decoded
                record_address = records_address + (
                    (mon.box_index - 1) * self.provider.pc_storage.slots_per_box + mon.slot_index - 1
                ) * self.provider.pc_storage.box_pokemon_size
                location = mon.met_location_name or f"Unknown #{decoded.met_location_id}"
                lines.extend(
                    (
                        f"Box {mon.box_index} Slot {mon.slot_index}",
                        f"  Address: {_format_optional_hex(record_address)}",
                        f"  Species: {mon.species_name} (#{decoded.species_id})",
                        f"  Nickname: {decoded.nickname or mon.species_name}",
                        f"  PID: 0x{decoded.pid:08X}",
                        (
                            f"  Checksum: {'VALID' if decoded.checksum_valid else 'INVALID'} "
                            f"(stored 0x{decoded.checksum:04X}, calculated 0x{decoded.calculated_checksum:04X})"
                        ),
                        f"  Met location: {location} (ID {decoded.met_location_id})",
                        f"  Met level: {decoded.met_level if decoded.met_level is not None else '--'}",
                        f"  Raw 136-byte record: {getattr(mon, 'raw_hex', '') or '--'}",
                    )
                )
        elif state is not None and state.available:
            lines.extend(("", "No non-empty PC BoxPokemon records were reported by the Lua scan."))
        lines.append("")
        return "\n".join(lines)

    def _refresh_pc_storage_discovery_results(self, payload) -> None:
        payload_data = getattr(payload, "payload", {})
        if not isinstance(payload_data, dict):
            return
        is_discovery_result = payload_data.get("pc_storage_discovery_requested") is True
        is_anchor_result = payload_data.get("pc_storage_anchor_result") is True
        is_inspection_result = (
            payload_data.get("pc_storage_pokemon_inspection_result") is True
        )
        is_search_result = payload_data.get("pc_storage_pokemon_search_result") is True
        is_pointer_search_result = (
            payload_data.get("pc_storage_structure_pointer_search_result") is True
        )
        is_session_layout_result = (
            payload_data.get("pc_storage_session_layout_result") is True
        )
        if not (
            is_discovery_result
            or is_anchor_result
            or is_inspection_result
            or is_search_result
            or is_pointer_search_result
            or is_session_layout_result
        ):
            return

        result_key = (
            payload_data.get("run_id"),
            payload_data.get("frame"),
            payload_data.get("pc_storage_discovery_summary", {}).get("mode")
            if isinstance(payload_data.get("pc_storage_discovery_summary"), dict)
            else None,
            is_inspection_result,
            is_search_result,
            is_pointer_search_result,
            is_session_layout_result,
        )
        is_new_result = result_key != self._last_pc_storage_discovery_key
        payload_received_at = getattr(payload, "received_at", None)
        operation_requested_at = (
            self._pc_pointer_search_requested_at
            if is_pointer_search_result
            else self._pc_layout_requested_at
            if is_session_layout_result
            else self._pc_search_requested_at
            if is_search_result
            else self._pc_discovery_requested_at
        )
        discovery_request_is_current = (
            operation_requested_at is None
            or not isinstance(payload_received_at, (int, float))
            or payload_received_at >= operation_requested_at
        )
        anchor_address = _parse_optional_int(payload_data.get("pc_storage_anchor_address"))
        inspection_address = _parse_optional_int(
            payload_data.get("pc_storage_inspection_anchor_address")
        )
        anchor_request_matches = (
            is_anchor_result
            and self._pc_anchor_requested_at is not None
            and isinstance(payload_received_at, (int, float))
            and payload_received_at >= self._pc_anchor_requested_at
            and anchor_address == self._pc_anchor_requested_address
        )
        inspection_request_matches = (
            is_inspection_result
            and self._pc_inspection_requested_at is not None
            and isinstance(payload_received_at, (int, float))
            and payload_received_at >= self._pc_inspection_requested_at
            and inspection_address == self._pc_inspection_requested_address
        )
        search_anchor = _parse_optional_int(
            payload_data.get("pc_storage_inspection_anchor_address")
        )
        search_request_matches = (
            is_search_result
            and self._pc_search_requested_at is not None
            and isinstance(payload_received_at, (int, float))
            and payload_received_at >= self._pc_search_requested_at
            and search_anchor == self._pc_search_requested_address
        )
        if is_new_result:
            LOGGER.info(
                "UI received PC discovery payload: frame=%s requested=%s "
                "anchor_result=%s inspection_result=%s search_result=%s anchor=%s",
                payload_data.get("frame"),
                is_discovery_result,
                is_anchor_result,
                is_inspection_result,
                is_search_result,
                payload_data.get("pc_storage_anchor_address"),
            )
            self._last_pc_storage_discovery_key = result_key
        elif is_inspection_result:
            return

        lines = ["PC Storage Discovery"]
        if is_session_layout_result:
            expected = payload_data.get("expected_box1_slot1", {})
            lines.extend(
                (
                    (
                        f"Expected Box 1 Slot 1: {expected.get('nickname') or '(any nickname)'} / "
                        f"{self.provider.species_names().get(expected.get('species_id'), 'unknown species')}"
                    ),
                    f"Lua run: {payload_data.get('session_lua_run_id', '--')}",
                    f"Resolver status: {payload_data.get('pc_storage_resolver_status', 'unresolved')}",
                    f"Resolved first record: {payload_data.get('session_pc_first_record_address', '--')}",
                )
            )
        elif is_search_result:
            lines.append(
                "Pokemon identity search result: "
                f"anchor={payload_data.get('pc_storage_inspection_anchor_address', '--')}, "
                f"frame={payload_data.get('frame', '--')}"
            )
        else:
            lines.append(
                "Payload result: "
                f"anchor={is_anchor_result}, "
                f"address={payload_data.get('pc_storage_anchor_address', '--')}, "
                f"frame={payload_data.get('frame', '--')}"
            )
        discovery_summary = payload_data.get("pc_storage_discovery_summary")
        discovery_cancelled = (
            isinstance(discovery_summary, dict)
            and discovery_summary.get("cancelled") is True
        )
        discovery_candidates = payload_data.get("pc_storage_discovery_candidates")
        if isinstance(discovery_summary, dict):
            lines.append(
                "Summary: "
                + ", ".join(f"{key}={value}" for key, value in discovery_summary.items())
            )
        else:
            lines.append("Summary: discovery payload did not include a summary.")

        party_range_available = (
            isinstance(discovery_summary, dict)
            and discovery_summary.get("party_range_available") is True
        )
        party_range = (
            discovery_summary.get("party_range")
            if isinstance(discovery_summary, dict)
            else None
        )
        if party_range_available and isinstance(party_range, dict):
            lines.append(
                "Active party RAM range: "
                f"[{party_range.get('start_address', '--')}, "
                f"{party_range.get('end_address_exclusive', '--')}) "
                f"({party_range.get('party_count', '--')} x "
                f"{party_range.get('record_size', '--')} bytes)"
            )
        else:
            lines.append("Active party RAM range: unavailable")
        if is_session_layout_result:
            candidates = payload_data.get("pc_storage_discovery_candidates", [])
            lines.append("540-slot candidate evaluations:")
            if isinstance(candidates, list) and candidates:
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    lines.append(
                        f"  {candidate.get('first_box_pokemon_address', '--')}: "
                        f"valid={candidate.get('valid_occupied_records', 0)}, "
                        f"empty={candidate.get('empty_records', 0)}, "
                        f"header-zero empty={candidate.get('zero_header_nonzero_payload_empty_records', 0)}, "
                        f"empty patterns={candidate.get('distinct_empty_patterns', '--')}, "
                        f"invalid={candidate.get('invalid_records', 0)}, "
                        f"party overlap={candidate.get('party_region_overlap', '--')}, "
                        f"plausible={candidate.get('plausible', False)}"
                    )
                    for sample in candidate.get("invalid_record_samples", [])[:4]:
                        lines.append(
                            f"    malformed Box {sample.get('box')} Slot {sample.get('slot')} "
                            f"at {sample.get('address')}: {sample.get('first_32_bytes_hex')}"
                        )
                    for pattern in candidate.get("empty_pattern_samples", [])[:3]:
                        lines.append(
                            f"    empty pattern x{pattern.get('count')}: "
                            f"{pattern.get('first_32_bytes_hex')}"
                        )
            else:
                lines.append("  No matching non-party candidate formed a plausible 540-slot array.")
            occupied = payload_data.get("pc_storage_decoded_occupied", [])
            lines.append("Decoded occupied slots:")
            if isinstance(occupied, list) and occupied:
                for mon in occupied:
                    if not isinstance(mon, dict):
                        continue
                    species_id = _parse_optional_int(mon.get("species_id"))
                    species_name = self.provider.species_names().get(
                        species_id,
                        f"Unknown #{species_id if species_id is not None else '--'}",
                    )
                    lines.append(
                        f"  Box {mon.get('box')} Slot {mon.get('slot')} "
                        f"{mon.get('address')}: {mon.get('nickname') or '--'} / "
                        f"{species_name}, checksum={mon.get('checksum_valid', False)}"
                    )
            else:
                lines.append("  No decoded occupied slots in the selected candidate.")
        elif is_pointer_search_result:
            lines.extend(self._pc_structure_pointer_search_lines(payload_data))
        elif is_inspection_result:
            lines.extend(self._pc_pokemon_inspection_lines(payload_data))
        elif is_search_result:
            lines.extend(self._pc_pokemon_search_lines(payload_data))
        else:
            identity_matches = payload_data.get(
                "pc_storage_discovery_identity_matches"
            )
            lines.extend(("", "Expected identity matches outside party RAM:"))
            if not party_range_available:
                lines.append(
                    "Unable to classify identity matches because the active party range is unavailable."
                )
            elif isinstance(identity_matches, list) and identity_matches:
                species_names = self.provider.species_names()
                for match in identity_matches:
                    if not isinstance(match, dict):
                        continue
                    species_id = _parse_optional_int(match.get("species_id"))
                    species_name = species_names.get(
                        species_id, f"Unknown #{species_id if species_id is not None else '--'}"
                    )
                    lines.append(
                        f"  {match.get('address', '--')}: {match.get('nickname') or '--'} "
                        f"({species_name}, #{species_id if species_id is not None else '--'}); "
                        f"checksum valid={match.get('checksum_valid', False)}, "
                        f"party overlap={match.get('party_overlap', '--')}"
                    )
            else:
                lines.append("No matching expected-identity records were found.")

        excluded_party_records = payload_data.get("pc_storage_excluded_party_records")
        lines.extend(("", "Excluded party records:"))
        if isinstance(excluded_party_records, list) and excluded_party_records:
            species_names = self.provider.species_names()
            for record in excluded_party_records:
                if not isinstance(record, dict):
                    continue
                species_id = _parse_optional_int(record.get("species_id"))
                species_name = species_names.get(
                    species_id, f"Unknown #{species_id if species_id is not None else '--'}"
                )
                lines.append(
                    f"  {record.get('address', '--')}, party slot {record.get('party_slot', '--')}: "
                    f"{record.get('nickname') or species_name}/{species_name} "
                    f"(#{species_id if species_id is not None else '--'}), "
                    f"checksum valid={record.get('checksum_valid', False)}"
                )
        else:
            lines.append("  None recognized in this scan.")

        if (
            not is_inspection_result
            and not is_search_result
            and not is_pointer_search_result
            and not is_session_layout_result
        ):
            if isinstance(discovery_candidates, list) and discovery_candidates:
                lines.append("")
                lines.append("Candidate PCBoxes Regions:")
                for candidate in discovery_candidates:
                    lines.extend(self._pc_storage_discovery_candidate_lines(candidate))
            else:
                lines.append("")
                lines.append("No candidate PCBoxes regions were reported.")

            anchor_diagnostic = payload_data.get("pc_storage_anchor_diagnostic")
            lines.extend(("", "Known Box 1 Slot 1 Anchor:"))
            if isinstance(anchor_diagnostic, dict):
                lines.extend(self._pc_storage_anchor_diagnostic_lines(anchor_diagnostic))
            else:
                lines.append("No anchor address was supplied for this scan.")

        probe_rows = payload_data.get("runtime_base_probe_rows")
        if isinstance(probe_rows, list) and probe_rows:
            lines.extend(("", "Runtime Pointer Probe (-0x400..+0x0FFF):"))
            for row in probe_rows:
                if not isinstance(row, dict):
                    continue
                u32_values = row.get("u32_le")
                u32_text = (
                    ", ".join(str(value) for value in u32_values)
                    if isinstance(u32_values, list)
                    else "--"
                )
                lines.append(
                    "  "
                    f"{row.get('relative_offset', '--')} "
                    f"{row.get('address', '--')} "
                    f"valid={str(row.get('valid_ram', False)).lower()} "
                    f"bytes={row.get('hex') or '--'} "
                    f"u32=[{u32_text}]"
                )

        self._last_pc_storage_discovery_text = "\n".join(lines)
        self._set_pc_storage_discovery_text(self._last_pc_storage_discovery_text)
        if is_session_layout_result and discovery_request_is_current:
            resolver_status = str(
                payload_data.get("pc_storage_resolver_status") or "unresolved"
            )
            address = payload_data.get("session_pc_first_record_address")
            if resolver_status == "cache-pending":
                if self._pc_resolver_lifecycle in {"unresolved", "discovering", "cache-pending"}:
                    self._pc_resolver_lifecycle = "cache-pending"
                    self.pc_storage_resolver_status_label.setText("PC resolver: cache-pending")
                    self.pc_storage_resolver_address_label.setText(
                        f"First record: {address or '--'}"
                    )
                self.pc_storage_discovery_status_label.setText(
                    "completed" if self._pc_resolver_lifecycle == "baseline-ready"
                    else "waiting for Lua cache confirmation"
                )
            elif resolver_status == "resolved for this session":
                if self._pc_resolver_lifecycle != "baseline-ready":
                    self._pc_resolver_lifecycle = "resolved-awaiting-baseline"
                    self.pc_storage_resolver_status_label.setText(
                        "PC resolver: resolved-awaiting-baseline"
                    )
                    self.pc_storage_resolver_address_label.setText(
                        f"First record: {address or '--'}"
                    )
                self.pc_storage_discovery_status_label.setText("completed")
                self._pc_layout_requested_at = None
            else:
                error = payload_data.get("pc_storage_resolver_error") or payload_data.get(
                    "pc_storage_anchor_error"
                )
                if self._pc_resolver_lifecycle not in {"baseline-ready", "cache-pending"}:
                    self._pc_resolver_lifecycle = resolver_status
                    self.pc_storage_resolver_status_label.setText(
                        f"PC resolver: {resolver_status}"
                    )
                    self.pc_storage_resolver_address_label.setText(
                        f"First record: {address or '--'}"
                    )
                self.pc_storage_discovery_status_label.setText(
                    "retrying cache install" if self._pc_resolver_lifecycle == "cache-pending"
                    else "failed" if error else "completed; no valid layout"
                )
                self._pc_layout_requested_at = None
        elif discovery_request_is_current:
            if discovery_cancelled:
                result_status = "cancelled"
            elif isinstance(discovery_summary, dict) and discovery_summary.get("error"):
                result_status = "failed"
            else:
                result_status = "completed"
            self.pc_storage_discovery_status_label.setText(result_status)
        anchor_error = payload_data.get("pc_storage_anchor_error")
        if is_anchor_result and discovery_cancelled:
            self.pc_storage_anchor_status_label.setText("cancelled")
            self._pc_anchor_requested_at = None
            self._pc_anchor_requested_address = None
        elif is_anchor_result and (
            anchor_request_matches
            or (is_new_result and self._pc_anchor_requested_at is None)
        ):
            if anchor_error:
                self.pc_storage_anchor_status_label.setText(f"failed: {anchor_error}")
            elif not isinstance(anchor_diagnostic, dict):
                self.pc_storage_anchor_status_label.setText(
                    "failed: Lua payload omitted anchor diagnostic"
                )
            else:
                anchor = payload_data.get("pc_storage_anchor_address", "--")
                self.pc_storage_anchor_status_label.setText(
                    f"completed: {anchor or '--'} at frame {payload_data.get('frame', '--')}"
                )
            if anchor_request_matches:
                self._pc_anchor_requested_at = None
                self._pc_anchor_requested_address = None
        if discovery_request_is_current:
            self._pc_discovery_requested_at = None

        inspection_error = payload_data.get("pc_storage_anchor_error")
        if is_inspection_result and inspection_request_matches:
            self._pc_last_inspected_address = inspection_address
            if discovery_cancelled:
                self.pc_storage_inspection_status_label.setText("cancelled")
            elif inspection_error:
                self.pc_storage_inspection_status_label.setText(
                    f"failed: {inspection_error}"
                )
            else:
                comparison = payload_data.get("pc_storage_movement_comparison")
                if isinstance(comparison, dict) and comparison.get("status") == "baseline captured":
                    status = "completed; movement baseline captured"
                elif isinstance(comparison, dict) and comparison.get("movement_detected"):
                    status = "completed; Pokemon movement detected"
                elif isinstance(comparison, dict) and comparison.get("status") == "compared":
                    status = "completed; compared with previous snapshot"
                else:
                    status = "completed"
                anchor_record = payload_data.get("pc_storage_inspection_anchor_record")
                if not isinstance(anchor_record, dict):
                    status += "; no valid Pokemon at supplied address"
                self.pc_storage_inspection_status_label.setText(status)
            self._pc_inspection_requested_at = None
            self._pc_inspection_requested_address = None
        if is_search_result and search_request_matches:
            if discovery_cancelled:
                self.pc_storage_inspection_status_label.setText("cancelled")
            elif inspection_error:
                self.pc_storage_inspection_status_label.setText(
                    f"failed: {inspection_error}"
                )
            else:
                summary = payload_data.get("pc_storage_discovery_summary", {})
                movement = payload_data.get("pc_storage_movement_comparison", {})
                count = summary.get("non_party_match_count", 0) if isinstance(summary, dict) else 0
                if isinstance(movement, dict) and movement.get("movement_detected"):
                    self.pc_storage_inspection_status_label.setText(
                        f"completed; matching Pokemon moved; {count} non-party match(es)"
                    )
                else:
                    self.pc_storage_inspection_status_label.setText(
                        f"completed; {count} non-party match(es)"
                    )
            self._pc_search_requested_at = None
            self._pc_search_requested_address = None
        if is_pointer_search_result and discovery_request_is_current:
            pointer_error = payload_data.get("pc_storage_anchor_error")
            if discovery_cancelled:
                self.pc_storage_pointer_search_status_label.setText("cancelled")
            elif pointer_error:
                self.pc_storage_pointer_search_status_label.setText(
                    f"failed: {pointer_error}"
                )
            else:
                summary = payload_data.get("pc_storage_discovery_summary", {})
                count = summary.get("reference_count", 0) if isinstance(summary, dict) else 0
                self.pc_storage_pointer_search_status_label.setText(
                    f"completed: {count} direct reference(s)"
                )
            self._pc_pointer_search_requested_at = None

    def _pc_pokemon_search_lines(self, payload: dict) -> list[str]:
        lines = ["", "Search RAM for This Pokemon:"]
        identity = payload.get("pc_storage_search_identity")
        if isinstance(identity, dict):
            lines.append(
                f"  Identity: {identity.get('nickname') or '--'}; "
                f"PID={identity.get('pid', '--')}; checksum={identity.get('checksum', '--')}; "
                f"species #{identity.get('species_id', '--')}"
            )
        summary = payload.get("pc_storage_discovery_summary", {})
        if isinstance(summary, dict):
            lines.append(
                f"  Searched {summary.get('bytes_scanned', 0)} / "
                f"{summary.get('total_bytes', 0)} bytes; "
                f"matches={summary.get('match_count', 0)}; "
                f"outside party={summary.get('non_party_match_count', 0)}"
            )
        matches = payload.get("pc_storage_search_matches")
        names = self.provider.species_names()
        if isinstance(matches, list) and matches:
            for match in matches:
                if not isinstance(match, dict):
                    continue
                species_id = _parse_optional_int(match.get("species_id"))
                species = names.get(
                    species_id, f"Unknown #{species_id if species_id is not None else '--'}"
                )
                lines.append(
                    f"  {match.get('address', '--')}: {match.get('nickname') or species} "
                    f"({species}, #{species_id if species_id is not None else '--'}); "
                    f"checksum={match.get('checksum_valid', False)}; "
                    f"party overlap={match.get('party_overlap', '--')} "
                    f"(slot {match.get('party_slot', '--')})"
                )
        else:
            lines.append("  No exact checksum-valid identity copies were found.")
        movement = payload.get("pc_storage_movement_comparison")
        if isinstance(movement, dict):
            lines.append(
                "  Movement: "
                f"{movement.get('baseline_addresses', [])} -> "
                f"{movement.get('current_addresses', [])}; "
                f"detected={movement.get('movement_detected', False)}"
            )
            for candidate in movement.get("movement_candidates", [])[:16]:
                lines.append(
                    f"    {candidate.get('from_address', '--')} -> "
                    f"{candidate.get('to_address', '--')} "
                    f"delta={candidate.get('delta_bytes', '--')} bytes "
                    f"({candidate.get('delta_hex', '--')}); "
                    f"old/new checksum valid="
                    f"{candidate.get('old_checksum_valid', False)}/"
                    f"{candidate.get('new_checksum_valid', False)}; "
                    f"party overlap={candidate.get('old_party_overlap', '--')}/"
                    f"{candidate.get('new_party_overlap', '--')}"
                )
        return lines

    def _pc_pokemon_inspection_lines(self, payload: dict) -> list[str]:
        lines = ["", "Pokemon Anchor Inspection:"]
        anchor = payload.get("pc_storage_inspection_anchor_address", "--")
        record = payload.get("pc_storage_inspection_anchor_record")
        if isinstance(record, dict):
            species_id = _parse_optional_int(record.get("species_id"))
            species_name = self.provider.species_names().get(
                species_id, f"Unknown #{species_id if species_id is not None else '--'}"
            )
            lines.append(
                f"  Anchor {anchor}: checksum-valid {record.get('nickname') or species_name} "
                f"({species_name}, #{species_id if species_id is not None else '--'}); "
                f"party overlap={record.get('party_overlap', '--')}; "
                f"PID={record.get('pid', '--')}; stable ID={record.get('stable_id', '--')}"
            )
        else:
            lines.append(
                f"  No checksum-valid Pokemon record starts exactly at {anchor}; "
                "the Main RAM identity search still ran."
            )

        nearby = payload.get("pc_storage_inspection_nearby_records")
        if isinstance(nearby, list):
            lines.extend(("  Nearby checksum-valid records (4-byte start alignment):",))
            names = self.provider.species_names()
            for item in nearby:
                if not isinstance(item, dict):
                    continue
                species_id = _parse_optional_int(item.get("species_id"))
                species_name = names.get(
                    species_id, f"Unknown #{species_id if species_id is not None else '--'}"
                )
                lines.append(
                    f"    {item.get('address', '--')} delta={item.get('delta_hex', '--')}: "
                    f"{item.get('nickname') or species_name}/{species_name} "
                    f"(#{species_id if species_id is not None else '--'}), "
                    f"checksum={item.get('checksum_valid', False)}, "
                    f"party overlap={item.get('party_overlap', '--')}, "
                    f"delta%136={item.get('delta_multiple_of_136', False)}, "
                    f"delta%236={item.get('delta_multiple_of_236', False)}"
                )

        spacing = payload.get("pc_storage_inspection_spacing_summary")
        if isinstance(spacing, dict):
            lines.append(
                "  Repeated spacing: "
                f"adjacent pairs={spacing.get('adjacent_record_pairs', 0)}, "
                f"multiples of 136={spacing.get('pairs_multiple_of_136', 0)}, "
                f"multiples of 236={spacing.get('pairs_multiple_of_236', 0)}"
            )
            for item in spacing.get("repeated_neighbor_spacings", [])[:16]:
                lines.append(
                    f"    {item.get('delta_bytes', '--')} bytes apart "
                    f"({item.get('occurrences', 0)} adjacent pairs)"
                )

        copies = payload.get("pc_storage_discovery_identity_matches")
        lines.append("  Copies matching the inspected Pokemon identity:")
        if isinstance(copies, list) and copies:
            names = self.provider.species_names()
            for item in copies:
                if not isinstance(item, dict):
                    continue
                species_id = _parse_optional_int(item.get("species_id"))
                species_name = names.get(
                    species_id, f"Unknown #{species_id if species_id is not None else '--'}"
                )
                lines.append(
                    f"    {item.get('address', '--')} delta={item.get('delta_hex', '--')}: "
                    f"{item.get('nickname') or '--'} / {species_name} "
                    f"(#{species_id if species_id is not None else '--'}); "
                    f"checksum={item.get('checksum_valid', False)}, "
                    f"party overlap={item.get('party_overlap', '--')}, "
                    f"same anchor identity={item.get('same_as_anchor_identity', '--')}"
                )
                for neighbor in item.get("surrounding_valid_pokemon", [])[:24]:
                    neighbor_id = _parse_optional_int(neighbor.get("species_id"))
                    neighbor_name = names.get(
                        neighbor_id,
                        f"Unknown #{neighbor_id if neighbor_id is not None else '--'}",
                    )
                    lines.append(
                        f"      nearby {neighbor.get('address', '--')} "
                        f"delta={neighbor.get('delta_hex', '--')}: "
                        f"{neighbor.get('nickname') or neighbor_name}/{neighbor_name}; "
                        f"party overlap={neighbor.get('party_overlap', '--')}"
                    )
                for reference in item.get("pointer_references", []):
                    lines.append(
                        f"      pointer {reference.get('reference_address', '--')} -> "
                        f"{reference.get('target_address', '--')}"
                    )
        else:
            lines.append("    No checksum-valid copies of the inspected identity were found.")

        lines.append("  Direct pointers to supplied address and address - 4:")
        anchor_references = payload.get("pc_storage_inspection_pointer_references")
        if isinstance(anchor_references, list) and anchor_references:
            for reference in anchor_references:
                lines.append(
                    f"    {reference.get('reference_address', '--')} -> "
                    f"{reference.get('target_address', '--')} "
                    f"({', '.join(reference.get('target', []))})"
                )
        else:
            lines.append("    No direct 32-bit little-endian references found.")

        lines.extend(("  Immediate bytes around the anchor:",))
        adjacent_start = payload.get("pc_storage_inspection_adjacent_start", "--")
        adjacent_hex = payload.get("pc_storage_inspection_adjacent_hex")
        if isinstance(adjacent_hex, str):
            try:
                adjacent = bytes.fromhex(adjacent_hex)
                start_address = _parse_optional_int(adjacent_start) or 0
                for offset in range(0, len(adjacent), 16):
                    lines.append(
                        f"    0x{start_address + offset:08X}: "
                        + adjacent[offset : offset + 16].hex(" ").upper()
                    )
            except ValueError:
                lines.append("    Adjacent byte dump was malformed.")
        for item in payload.get("pc_storage_inspection_adjacent_u32", []):
            lines.append(
                f"    u32 {item.get('relative_offset', '--')} "
                f"{item.get('address', '--')} = {item.get('u32_le', '--')}"
            )

        comparison = payload.get("pc_storage_movement_comparison")
        if isinstance(comparison, dict):
            lines.extend(("  Movement experiment:", f"    Status: {comparison.get('status', '--')}"))
            lines.append(f"    Stable ID: {comparison.get('stable_id') or '--'}")
            if comparison.get("status") == "baseline captured":
                lines.append(
                    "    Baseline addresses: "
                    + ", ".join(comparison.get("baseline_addresses", []))
                )
            else:
                lines.append(
                    "    Before: " + ", ".join(comparison.get("baseline_addresses", []))
                )
                lines.append(
                    "    After: " + ", ".join(comparison.get("current_addresses", []))
                )
                for movement in comparison.get("movement_candidates", []):
                    lines.append(
                        f"    Move {movement.get('from_address', '--')} -> "
                        f"{movement.get('to_address', '--')}: "
                        f"{movement.get('delta_bytes', '--')} bytes "
                        f"(136-stride={movement.get('delta_multiple_of_136', False)}, "
                        f"236-stride={movement.get('delta_multiple_of_236', False)})"
                    )
                changes = comparison.get("changed_ram", {})
                if isinstance(changes, dict):
                    lines.append(
                        f"    Changed RAM: {changes.get('changed_bytes', 0)} bytes in "
                        f"{changes.get('changed_region_count', 0)} regions; "
                        f"focus regions={changes.get('focus_region_count', 0)}"
                    )
                    for region in changes.get("focus_changed_regions", [])[:24]:
                        lines.append(
                            f"      {region.get('address_start', '--')}.."
                            f"{region.get('address_end_exclusive', '--')}: "
                            f"{region.get('changed_bytes', 0)} changed bytes"
                        )

        region_hex = payload.get("pc_storage_inspection_region_hex")
        region_start = _parse_optional_int(
            payload.get("pc_storage_inspection_region_start")
        )
        if isinstance(region_hex, str) and region_start is not None:
            lines.append(
                "  Raw anchor region "
                f"{payload.get('pc_storage_inspection_region_start', '--')}.."
                f"{payload.get('pc_storage_inspection_region_end_exclusive', '--')} "
                f"({len(region_hex) // 2} bytes; 0x4000 before, 0x20000 after):"
            )
            try:
                region = bytes.fromhex(region_hex)
                for offset in range(0, len(region), 16):
                    lines.append(
                        f"    0x{region_start + offset:08X}: "
                        + region[offset : offset + 16].hex(" ").upper()
                    )
            except ValueError:
                lines.append("    Anchor region dump was malformed.")
        return lines

    def _refresh_pc_storage_discovery_progress(self, progress) -> None:
        if not isinstance(progress, dict):
            progress = {"status": "idle"}
        status = progress.get("status", "idle")
        active = status in {
            "queued",
            "scanning",
            "stalled",
            "retrying",
            "analyzing",
            "cancel-requested",
        }
        self.pc_storage_discovery_cancel_button.setEnabled(active)
        if status in {"queued", "cancel-requested"}:
            text = "cancelling..." if status == "cancel-requested" else "scanning 0%"
        elif status == "scanning":
            percent = progress.get("progress_percent", 0)
            address = progress.get("current_address", "--")
            if progress.get("mode") in {
                "pokemon_search",
                "structure_pointer_search",
            }:
                matches = progress.get("matches_found", 0)
                elapsed = progress.get("elapsed_seconds", 0.0)
                label = (
                    "searching structure pointers"
                    if progress.get("mode") == "structure_pointer_search"
                    else "searching Pokemon identity"
                )
                text = (
                    f"{label} {percent}% at {address}; "
                    f"matches={matches}; elapsed={elapsed:.1f}s"
                )
                self.pc_storage_discovery_status_label.setText(text)
                if progress.get("mode") == "structure_pointer_search":
                    self.pc_storage_pointer_search_status_label.setText(text)
                return
            candidates = progress.get("candidates_found", 0)
            elapsed = progress.get("elapsed_seconds", 0.0)
            chunks_received = progress.get(
                "received_chunk_count", progress.get("chunks_received", 0)
            )
            expected_chunks = progress.get("expected_chunk_count")
            expected_text = (
                expected_chunks
                if expected_chunks is not None
                else f"~{progress.get('estimated_chunk_count')}"
                if progress.get("estimated_chunk_count") is not None
                else "?"
            )
            missing_count = progress.get("missing_chunk_count", 0)
            text = (
                f"scanning {percent}% at {address}; chunks received: {chunks_received}/"
                f"{expected_text}; proven missing: {missing_count}; "
                f"candidates={candidates}; elapsed={elapsed:.1f}s"
            )
            if progress.get("producer_status") == "stalled":
                text += "; waiting for producer..."
        elif status == "stalled":
            percent = progress.get("progress_percent", 0)
            chunks_received = progress.get(
                "received_chunk_count", progress.get("chunks_received", 0)
            )
            missing_count = progress.get("missing_chunk_count", 0)
            text = (
                f"scanning {percent}%; chunks received: {chunks_received}; "
                f"proven missing: {missing_count}; waiting for producer..."
            )
        elif status == "retrying":
            percent = progress.get("progress_percent", 0)
            missing = progress.get("missing_chunk_index", "?")
            chunks_received = progress.get(
                "received_chunk_count", progress.get("chunks_received", 0)
            )
            expected_chunks = progress.get("expected_chunk_count")
            expected_text = (
                expected_chunks
                if expected_chunks is not None
                else f"~{progress.get('estimated_chunk_count')}"
                if progress.get("estimated_chunk_count") is not None
                else "?"
            )
            missing_count = progress.get("missing_chunk_count", 1)
            text = (
                f"scanning {percent}%; chunks received: {chunks_received}/{expected_text}; "
                f"proven missing: {missing_count}; retrying chunk {missing}..."
            )
        elif status == "analyzing":
            text = f"scanning 100%; candidates={progress.get('candidates_found', 0)}; analyzing..."
        elif status == "completed":
            elapsed = progress.get("elapsed_seconds", 0.0)
            text = f"completed in {elapsed:.1f}s"
        elif status == "cancelled":
            text = "cancelled"
        elif status == "failed":
            text = f"failed: {progress.get('error') or 'discovery failed'}"
        else:
            text = "idle"
        self.pc_storage_discovery_status_label.setText(text)

    def _pc_storage_anchor_diagnostic_lines(self, diagnostic: dict) -> list[str]:
        lines = [
            f"  Record address: {diagnostic.get('anchor_address', '--')}",
            (
                f"  Record RAM valid: {diagnostic.get('record_address_ram_valid', False)}; "
                f"checksum valid: {diagnostic.get('record_checksum_valid', False)}; "
                f"species ID: {diagnostic.get('species_id', '--')}"
            ),
            (
                f"  Candidate PCBoxes header (-0x28): "
                f"{diagnostic.get('candidate_pc_boxes_base', '--')} "
                f"(RAM valid={diagnostic.get('candidate_pc_boxes_base_ram_valid', False)})"
            ),
            f"  Full 540-slot region fits RAM: {diagnostic.get('full_540_slot_region_fits_ram', False)}",
            f"  Runtime base to anchor delta: {diagnostic.get('runtime_base_to_anchor_delta', '--')}",
            (
                f"  Party buffer: records={diagnostic.get('party_records_address', '--')}, "
                f"count={diagnostic.get('party_count', '--')}, "
                f"anchor matches party slot={diagnostic.get('party_record_slot', '--')} "
                f"({diagnostic.get('matches_party_record', False)})"
            ),
            (
                f"  Bytes around anchor, starting {diagnostic.get('nearby_dump_start', '--')}: "
                f"{diagnostic.get('bytes_before_and_at_record_address') or '--'}"
            ),
        ]
        lines.append(
            f"  Proposed header bytes at {diagnostic.get('header_address', '--')} "
            f"({diagnostic.get('header_byte_count', 0)} bytes; interpretation not assumed):"
        )
        header_hex = diagnostic.get("header_bytes_hex")
        if isinstance(header_hex, str) and header_hex:
            lines.append(f"    {header_hex}")
        header_values = diagnostic.get("header_u32_le_values")
        if isinstance(header_values, list):
            lines.append("  Header u32 LE words:")
            for item in header_values:
                if isinstance(item, dict):
                    lines.append(
                        f"    +{item.get('relative_offset', '--')} "
                        f"{item.get('address', '--')} = {item.get('u32_le', '--')}"
                    )
        relative_values = diagnostic.get("relative_u32_values")
        if isinstance(relative_values, list) and relative_values:
            lines.append("  Nearby u32 LE values:")
            for item in relative_values:
                if isinstance(item, dict):
                    lines.append(
                        f"    {item.get('relative_offset', '--')} "
                        f"{item.get('address', '--')} = {item.get('u32_le', '--')}"
                    )

        candidate = diagnostic.get("pcboxes_candidate")
        if isinstance(candidate, dict):
            lines.append(
                "  540-slot layout from anchor: "
                f"valid={candidate.get('valid_occupied_records', '--')}, "
                f"empty={candidate.get('empty_records', '--')}, "
                f"invalid={candidate.get('invalid_records', '--')}; "
                f"record size={candidate.get('record_size', '--')} bytes"
            )
            samples = candidate.get("sample_records")
            if isinstance(samples, list) and samples:
                lines.append("  Decoded occupied Pokemon from the anchored layout:")
                for sample in samples[:6]:
                    lines.append("    " + self._pc_storage_discovery_sample_text(sample))
        occupied = diagnostic.get("occupied_slots")
        if isinstance(occupied, list):
            lines.append(f"  All occupied slots ({len(occupied)}):")
            names = self.provider.species_names()
            for item in occupied:
                if not isinstance(item, dict):
                    continue
                species_id = _parse_optional_int(item.get("species_id"))
                species = names.get(
                    species_id,
                    f"Unknown #{species_id if species_id is not None else '--'}",
                )
                lines.append(
                    f"    Box {item.get('box', '--')} Slot {item.get('slot', '--')} "
                    f"{item.get('address', '--')}: {item.get('nickname') or species} "
                    f"({species}, #{species_id if species_id is not None else '--'})"
                )
        key_checks = diagnostic.get("key_position_checks")
        if isinstance(key_checks, list):
            lines.append("  Requested position checks:")
            names = self.provider.species_names()
            for item in key_checks:
                if not isinstance(item, dict):
                    continue
                species_id = _parse_optional_int(item.get("species_id"))
                species = names.get(species_id, "empty/invalid") if species_id else "empty/invalid"
                identity = item.get("nickname") or species
                lines.append(
                    f"    Box {item.get('box', '--')} Slot {item.get('slot', '--')} "
                    f"{item.get('address', '--')}: status={item.get('status', '--')}, "
                    f"{identity}"
                )
        invalid_slots = diagnostic.get("invalid_slots")
        if isinstance(invalid_slots, list):
            lines.append(f"  Malformed slot addresses ({len(invalid_slots)}):")
            for item in invalid_slots:
                if isinstance(item, dict):
                    lines.append(
                        f"    Box {item.get('box', '--')} Slot {item.get('slot', '--')} "
                        f"{item.get('address', '--')}"
                    )
        else:
            lines.append("  A complete anchored layout could not be evaluated.")

        references = diagnostic.get("pointer_references")
        if isinstance(references, list):
            lines.append(f"  Direct RAM references to candidate addresses: {len(references)}")
            for reference in references[:32]:
                if isinstance(reference, dict):
                    lines.append(
                        f"    {reference.get('reference_address', '--')} -> "
                        f"{reference.get('target_address', '--')} "
                        f"({reference.get('target', '--')}; "
                        f"runtime-base offset={reference.get('runtime_base_relative_offset', '--')})"
                    )
        return lines

    def _pc_structure_pointer_search_lines(self, payload: dict) -> list[str]:
        summary = payload.get("pc_storage_discovery_summary", {})
        lines = [
            "",
            "PC Structure Pointer References:",
            f"  Candidate header: {payload.get('pc_storage_pointer_search_header_address', '--')}",
            f"  First BoxPokemon: {payload.get('pc_storage_pointer_search_first_record_address', '--')}",
        ]
        if isinstance(summary, dict):
            lines.append(
                f"  Searched {summary.get('bytes_scanned', 0)} / "
                f"{summary.get('total_bytes', 0)} bytes; direct references="
                f"{summary.get('reference_count', 0)}"
            )
            runtime_base = summary.get("runtime_base_address")
            lines.append(
                f"  Party-reader pointer slot {summary.get('runtime_pointer_slot', '--')} "
                f"-> runtime base {runtime_base or 'unavailable'}"
            )
            for label, key in (
                ("header", "header_offset_from_runtime_base"),
                ("first record", "first_record_offset_from_runtime_base"),
            ):
                offset = summary.get(key)
                if isinstance(offset, int):
                    lines.append(f"  Candidate {label} delta from runtime base: {offset:+#x}")
            if summary.get("references_truncated"):
                lines.append("  Reference list was capped; reported addresses are incomplete.")
            if summary.get("error"):
                lines.append(f"  Search error: {summary['error']}")
        references = payload.get("pc_storage_pointer_references")
        if isinstance(references, list) and references:
            for reference in references:
                if isinstance(reference, dict):
                    lines.append(
                        f"  {reference.get('reference_address', '--')} -> "
                        f"{reference.get('target_address', '--')} "
                        f"({reference.get('target', '--')})"
                    )
        else:
            lines.append("  No direct 32-bit references were found.")
        return lines

    def _pc_storage_discovery_candidate_lines(self, candidate) -> list[str]:
        if not isinstance(candidate, dict):
            return []
        lines = [
            (
                "  "
                f"Rank {candidate.get('rank', '--')}: "
                f"PCBoxes={candidate.get('candidate_pc_boxes_base', '--')}, "
                f"first BoxPokemon={candidate.get('first_box_pokemon_address', '--')}, "
                f"valid={candidate.get('valid_occupied_records', '--')}, "
                f"empty={candidate.get('empty_records', '--')}, "
                f"invalid={candidate.get('invalid_records', '--')}, "
                f"party-overlap={candidate.get('party_record_overlaps', '--')}, "
                f"score={candidate.get('confidence_score', '--')}"
            )
        ]
        reasons = candidate.get("reasons")
        if isinstance(reasons, list) and reasons:
            lines.append("    Reasons: " + "; ".join(str(reason) for reason in reasons))
        samples = candidate.get("sample_records")
        if isinstance(samples, list) and samples:
            lines.append("    Recognized records:")
            for sample in samples[:6]:
                lines.append("      " + self._pc_storage_discovery_sample_text(sample))
        return lines

    def _pc_storage_discovery_sample_text(self, sample) -> str:
        if not isinstance(sample, dict):
            return "--"
        species_id = _parse_optional_int(sample.get("species_id"))
        species_name = self.provider.species_names().get(species_id, f"Unknown #{species_id}")
        nickname = None
        raw_hex = sample.get("raw_hex")
        address = _parse_optional_int(sample.get("address"))
        if isinstance(raw_hex, str):
            try:
                raw = bytes.fromhex(raw_hex)
                if len(raw) == self.provider.pc_storage.box_pokemon_size:
                    decoded = self.provider.pc_storage.decode_box_pokemon(raw, address)
                    species_id = decoded.species_id
                    species_name = self.provider.species_names().get(
                        species_id, f"Unknown #{species_id}"
                    )
                    nickname = decoded.nickname
            except ValueError:
                nickname = None
        return (
            f"Box {sample.get('box', '--')} Slot {sample.get('slot', '--')} "
            f"at {sample.get('address', '--')}: "
            f"{species_name} (#{species_id if species_id is not None else '--'}), "
            f"nickname={nickname or species_name}"
        )

    def _set_pc_storage_debug_text(self, text: str) -> None:
        editor = self.pc_storage_details
        self._set_plain_text_preserving_scroll(editor, text)

    def _set_pc_storage_discovery_text(self, text: str) -> None:
        editor = self.pc_storage_discovery_details
        self._set_plain_text_preserving_scroll(editor, text)

    def _set_plain_text_preserving_scroll(self, editor: QPlainTextEdit, text: str) -> None:
        if text == editor.toPlainText():
            return
        vertical = editor.verticalScrollBar()
        horizontal = editor.horizontalScrollBar()
        vertical_position = vertical.value()
        was_at_bottom = vertical.maximum() > 0 and vertical_position >= vertical.maximum()
        horizontal_position = horizontal.value()
        editor.setPlainText(text)
        vertical = editor.verticalScrollBar()
        horizontal = editor.horizontalScrollBar()
        vertical.setValue(vertical.maximum() if was_at_bottom else vertical_position)
        horizontal.setValue(horizontal_position)

    def _friendship_walk_transport_debug_lines(self, party_payload, ram_fresh: bool) -> list[str]:
        now = time.monotonic()
        walk = getattr(self, "friendship_walk", None)
        last_ack_received_at = getattr(self, "_walk_last_ack_received_at", None)
        unacked_since = getattr(self, "_walk_unacked_since", None)
        ram_source = getattr(party_payload, "source", "--")
        received_at = getattr(party_payload, "received_at", None)
        ram_age = (
            f"{max(0.0, now - received_at):.1f}s"
            if isinstance(received_at, (int, float))
            else "--"
        )
        ack_age = (
            f"{max(0.0, now - last_ack_received_at):.1f}s"
            if last_ack_received_at is not None
            else "--"
        )
        command_state = getattr(self, "_walk_command_state", "UNKNOWN")
        if unacked_since is not None and now - unacked_since >= 4.0:
            command_state = "ACK STALE"
        payload = getattr(party_payload, "payload", {}) if party_payload else {}
        position = self.provider.decode_player_position(payload) if payload else None
        frame = _frame_number(payload)
        axis = getattr(walk, "axis", WalkAxis.HORIZONTAL)
        axis_name = "Horizontal (X)" if axis is WalkAxis.HORIZONTAL else "Vertical (Y)"
        axis_value = (
            position.x if position and axis is WalkAxis.HORIZONTAL
            else position.y if position
            else getattr(walk, "relevant_axis_value", None)
        )
        pause_reason = payload.get("friendship_walk_pause_reason") or "--"
        walk_status = getattr(walk, "status", WalkStatus.IDLE)
        requested_direction = (
            getattr(walk, "direction", None).value
            if getattr(walk, "active", False) and getattr(walk, "direction", None)
            else payload.get("friendship_walk_requested_direction")
            or payload.get("friendship_walk_direction")
            or "--"
        )
        previous_direction = getattr(walk, "previous_direction", None)
        previous_direction = previous_direction.value if previous_direction else "--"
        grace_total = getattr(walk, "grace_frames", 0)
        grace_remaining = getattr(walk, "reversal_grace_remaining_frames", 0)
        moves_since_reversal = getattr(walk, "successful_moves_since_reversal", 0)
        minimum_moves = getattr(walk, "min_moves_after_reversal", 0)
        if pause_reason == "--" and walk_status in {
            WalkStatus.PAUSED_RAM,
            WalkStatus.PAUSED_BATTLE,
            WalkStatus.PAUSED_COORDINATES,
            WalkStatus.PAUSED_MANUAL,
            WalkStatus.PAUSED_COMMAND,
            WalkStatus.PAUSED_COMMAND_LOST,
        }:
            pause_reason = walk_status.value
        return [
            f"RAM connection: {'CONNECTED' if ram_fresh else 'STALE / DISCONNECTED'}",
            f"RAM source: {ram_source}",
            f"Last RAM age: {ram_age}",
            f"Command channel: {command_state}",
            f"Command transport: {getattr(self, '_walk_command_transport', None) or '--'}",
            f"Last command sequence: {getattr(self, '_walk_last_command_sequence', None) if getattr(self, '_walk_last_command_sequence', None) is not None else '--'}",
            f"Last Lua ack sequence: {getattr(self, '_walk_last_ack_sequence', None) if getattr(self, '_walk_last_ack_sequence', None) is not None else '--'}",
            f"Last Lua ack frame: {getattr(self, '_walk_last_ack_frame', None) if getattr(self, '_walk_last_ack_frame', None) is not None else '--'}",
            f"Last Lua ack action: {payload.get('friendship_walk_ack_action') or '--'}",
            f"Last ack age: {ack_age}",
            f"Mode: {axis_name}",
            f"Current direction: {requested_direction.upper()}",
            f"Previous direction: {previous_direction.upper()}",
            f"Injected direction: {(payload.get('friendship_walk_injected_direction') or '--').upper()}",
            f"B injected: {payload.get('friendship_walk_b_injected', '--')}",
            f"Friendship Walk enabled: {payload.get('friendship_walk_enabled', '--')}",
            f"Lua walk status: {payload.get('friendship_walk_status', '--')}",
            f"Lua reversal until frame: {payload.get('friendship_walk_reversal_until_frame', '--')}",
            f"Relevant axis value: {axis_value if axis_value is not None else '--'}",
            f"Last axis change frame: {getattr(walk, 'last_position_change_frame', None) if getattr(walk, 'last_position_change_frame', None) is not None else '--'}",
            f"Current emulator frame: {frame if frame is not None else '--'}",
            f"Frames since axis change: {getattr(walk, 'frames_since_axis_change', None) if getattr(walk, 'frames_since_axis_change', None) is not None else '--'}",
            f"Blocked timeout: {getattr(walk, 'blocked_timeout_frames', '--')} frames",
            f"Reversal grace: {grace_remaining} / {grace_total} frames",
            f"Successful moves since reversal: {moves_since_reversal} / {minimum_moves}",
            f"Wall detection armed: {getattr(walk, 'wall_detection_armed', '--')}",
            f"State: {getattr(walk, 'debug_state', 'UNKNOWN')}",
            "Manual override detection: DISABLED",
            f"Pause reason: {pause_reason}",
        ]

    def _set_ram_party_debug_text(self, text: str) -> None:
        editor = self.ram_party_details
        if text == editor.toPlainText():
            return

        vertical = editor.verticalScrollBar()
        horizontal = editor.horizontalScrollBar()
        vertical_position = vertical.value()
        was_at_bottom = vertical.maximum() > 0 and vertical_position >= vertical.maximum()
        horizontal_position = horizontal.value()

        editor.setPlainText(text)

        vertical = editor.verticalScrollBar()
        horizontal = editor.horizontalScrollBar()
        vertical.setValue(vertical.maximum() if was_at_bottom else vertical_position)
        horizontal.setValue(horizontal_position)

    def closeEvent(self, event) -> None:
        self.refresh_timer.stop()
        if hasattr(self, "_geometry_save_timer"):
            self._geometry_save_timer.stop()
        self.friendship_walk.stop()
        self._send_friendship_walk_command("STOP")
        self.ram_data_source.stop()
        self._save_window_state()
        super().closeEvent(event)
