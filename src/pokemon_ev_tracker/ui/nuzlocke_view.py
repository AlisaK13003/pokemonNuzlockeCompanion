"""Game-independent UI for managing local Nuzlocke runs."""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.core.nuzlocke.acquisition import pending_acquisition_events
from pokemon_ev_tracker.core.nuzlocke.death_detection import (
    DeathCandidate,
    match_death_candidate,
)
from pokemon_ev_tracker.core.nuzlocke.fights import build_fight_timeline
from pokemon_ev_tracker.core.nuzlocke.models import (
    EncounterRecord,
    EncounterStatus,
    NuzlockeGameProfile,
    PartyLevel,
    PokemonAcquisitionEvent,
    RunStatus,
    resolved_level_caps,
    summarize_run,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.core.nuzlocke.wipe_detection import PartyWipeCandidate
from pokemon_ev_tracker.ui.nuzlocke_dialogs import (
    DeathDialog,
    EncounterDialog,
    FightDetailsDialog,
    LevelCapsDialog,
    NewRunDialog,
    RunHistoryDialog,
)
from pokemon_ev_tracker.ui.nuzlocke_panels import RunPanel, run_label
from pokemon_ev_tracker.ui.nuzlocke_workspace import build_run_workspace
from pokemon_ev_tracker.ui.run_design import VISIBLE_STATUSES, install_run_design
from pokemon_ev_tracker.ui.sprite_loader import get_static_sprite
from pokemon_ev_tracker.ui.theme import COLORS, refresh_style

_STATUS_LABELS = {
    "NOT_ENCOUNTERED": "Not encountered",
    "CAUGHT": "Caught",
    "FAILED": "Failed",
    "DEAD": "Dead",
    "SKIPPED": "Skipped",
    "DUPES": "Dupes clause",
}
_STATUS_COLORS = {
    "CAUGHT": COLORS["status_caught"],
    "FAILED": COLORS["status_failed"],
    "DEAD": COLORS["status_dead"],
    "SKIPPED": COLORS["status_skipped"],
    "DUPES": COLORS["status_dupes"],
}


class NuzlockeView(QWidget):
    presentation_changed = Signal()
    rediscover_pc_requested = Signal()

    def __init__(
        self,
        store: NuzlockeStore | None = None,
        profiles: tuple[NuzlockeGameProfile, ...] = (),
        parent=None,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("nuzlockeView")
        self.store = store or NuzlockeStore()
        self.profiles = {profile.game_id: profile for profile in profiles}
        self.settings = settings
        self.party_levels: tuple[PartyLevel, ...] = ()
        self._rendering = False
        self._ram_death_queue: list[tuple[str, DeathCandidate, str | None, bool]] = []
        self._ram_death_notice = ""
        self._ram_death_notice_run_id: str | None = None
        self._shown_ram_death_key: tuple[str, str] | None = None

        build_run_workspace(self)
        install_run_design(self)
        if settings is not None:
            self.wipe_action_input.blockSignals(True)
            self.no_run_prompt_input.blockSignals(True)
            self.wipe_action_input.setCurrentIndex(
                self.wipe_action_input.findData(settings.nuzlocke_wipe_action))
            self.no_run_prompt_input.setChecked(settings.prompt_for_nuzlocke_run)
            self.wipe_action_input.blockSignals(False)
            self.no_run_prompt_input.blockSignals(False)
        self._refresh_all()

    def _save_lifecycle_preferences(self, *_args) -> None:
        if self.settings is None:
            return
        self.settings.nuzlocke_wipe_action = self.wipe_action_input.currentData()
        self.settings.prompt_for_nuzlocke_run = self.no_run_prompt_input.isChecked()
        self.settings.save_default()

    def set_party_levels(self, members: tuple[PartyLevel, ...]) -> None:
        self.party_levels = members
        self._refresh_cap_panel()
        self.fight_line.refresh(self.fight_timeline())
        timeline = self.fight_timeline()
        fight = timeline.next_fight if timeline else None
        self.hero_fight_title.setText(f"Next: {fight.name}" if fight else "All fights complete" if timeline else "No active run")
        self.hero_cap_value.setText(str(fight.effective_level_cap) if fight else "—")
        self.hero_cap_warning.setText(
            f"{fight.over_count} party members above cap" if fight and fight.over_count else "")
        self.readiness.refresh(self._party_cards, self.fight_timeline())
        self.presentation_changed.emit()

    def set_party_cards(self, cards) -> None:
        self._party_cards = cards
        self.readiness.refresh(cards, self.fight_timeline())

    def _edit_ledger_location(self, location_id) -> None:
        self.encounter_filter.setCurrentIndex(0)
        self._render_encounters()
        for row in range(self.encounters_table.rowCount()):
            item = self.encounters_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == location_id:
                self.encounters_table.setCurrentCell(row, 0)
                self._edit_selected_encounter()
                break

    def _open_manage_locations(self) -> None:
        run = self.store.active_run
        if run:
            self.manage_locations.open_run(run, self._active_profile())
            self.show_section("Manage locations")

    def _locations_saved(self) -> None:
        self._refresh_all()
        self.show_section("Dashboard")

    def _finish_run(self) -> None:
        from pokemon_ev_tracker.ui.nuzlocke_dialogs import FinishRunDialog
        run = self.store.active_run
        if not run:
            return
        dialog = FinishRunDialog(run.name, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            self.store.set_run_status(run.run_id, dialog.outcome.currentData(), dialog.notes.toPlainText())
        except (OSError, ValueError) as error:
            self._show_error("Could not finish run", error)
            return
        self._refresh_all()
        self.show_section("Run Library")

    def fight_timeline(self):
        run = self.store.active_run
        return build_fight_timeline(run, self.profiles.get(run.game) if run else None,
                                    self.party_levels)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def resizeEvent(self, event) -> None:
        narrow = event.size().width() < 650
        for row in self._responsive_rows:
            row.setDirection(
                QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
            )
        self.metric_strip.setVisible(not narrow)
        self.summary_label.hide()
        super().resizeEvent(event)

    def show_section(self, name: str) -> None:
        self.run_back_button.setVisible(name != "Dashboard")
        for index in range(self.sections.count()):
            if self.sections.tabText(index).casefold() == name.casefold():
                self.sections.setCurrentIndex(index)
                self._update_section_visibility()
                return

    def _update_section_visibility(self, *_args) -> None:
        has_run = self.store.active_run is not None
        library = self.sections.tabText(self.sections.currentIndex()) == "Run Library"
        managing = self.sections.tabText(self.sections.currentIndex()) == "Manage locations"
        self.run_back_button.setVisible(self.sections.currentIndex() != 0 and not managing and not library)
        self.run_header.setVisible(has_run and not library and not managing)
        self.empty_panel.setVisible(not has_run and not library)
        self.sections.setVisible(has_run or library)

    def set_pc_monitor_status(self, text: str) -> None:
        """Presentation adapter for the existing runtime's PC monitor summary."""
        text = text or "PC monitor status unavailable."
        if self.pc_monitor_label.text() == text:
            return
        self.pc_monitor_label.setText(text)
        self.presentation_changed.emit()

    def _open_preview_encounter(self, index) -> None:
        item = self.encounter_preview.item(index.row(), 0)
        if item is None:
            return
        location_id = item.data(Qt.ItemDataRole.UserRole)
        self.encounter_filter.setCurrentIndex(0)
        self.show_section("Encounters")
        for row in range(self.encounters_table.rowCount()):
            if self.encounters_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == location_id:
                self.encounters_table.selectRow(row)
                self.encounters_table.scrollToItem(self.encounters_table.item(row, 0))
                break

    def _activate_library_run(self, index) -> None:
        item = self.run_library_table.item(index.row(), 0)
        if item is not None:
            run = self.store.get_run(item.data(Qt.ItemDataRole.UserRole))
            if run and run.status == RunStatus.ACTIVE.value:
                self.run_selector.setCurrentIndex(self.run_selector.findData(run.run_id))
            elif run:
                self._show_historical_run(run)

    def _open_selected_run(self) -> None:
        run = self._selected_library_run()
        if not run:
            return
        if run.status == RunStatus.ACTIVE.value:
            self._set_selected_active()
            self.show_section("Dashboard")
        else:
            self._show_historical_run(run)

    def _selected_library_run(self):
        row = self.run_library_table.currentRow()
        item = self.run_library_table.item(row, 0) if row >= 0 else None
        return self.store.get_run(item.data(Qt.ItemDataRole.UserRole)) if item else self.store.active_run

    def _update_library_actions(self) -> None:
        run = self._selected_library_run()
        for button in (self.open_button, self.rename_button, self.delete_button):
            button.setEnabled(run is not None)
        self.set_active_button.setEnabled(
            run is not None and run.status == RunStatus.ACTIVE.value
            and run.run_id != self.store.active_run_id)
        self.abandon_button.setEnabled(run is not None and run.status == RunStatus.ACTIVE.value)

    def _render_run_library(self) -> None:
        requested = self.run_status_filter.currentData()
        shown = [run for run in self.store.runs if not requested or run.status == requested]
        self.run_library_table.setRowCount(len(shown))
        for row, run in enumerate(shown):
            profile = self.profiles.get(run.game)
            caps = resolved_level_caps(run, profile) if profile else ()
            summary = summarize_run(run, caps)
            values = (
                run.name, run.status, profile.display_name if profile else run.game,
                run.started_at[:10], run.ended_at[:10] if run.ended_at else "—",
                str(summary.caught), str(summary.deaths),
                f"{summary.completed_fights} / {summary.total_fights}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 1:
                    item.setForeground(QColor({
                        RunStatus.ACTIVE.value: COLORS["accent"],
                        RunStatus.WON.value: COLORS["success"],
                        RunStatus.WIPED.value: COLORS["danger"],
                        RunStatus.ABANDONED.value: COLORS["text_muted"],
                    }.get(run.status, COLORS["text"])))
                self.run_library_table.setItem(row, column, item)
            self.run_library_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, run.run_id)
        self._update_library_actions()

        self.library_cards.refresh(shown)
        self.run_library_table.setFixedHeight(max(100, len(shown) * 38 + 36))
        self.run_library_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def _show_historical_run(self, run) -> None:
        RunHistoryDialog(run, self.profiles.get(run.game), self).exec()

    def _render_dashboard(self) -> None:
        """Read-only projection of existing run records; no detection runs here."""
        # TODO: per-fight notes require a validated model/storage contract; only run notes exist.
        run, profile = self.store.active_run, self._active_profile()
        self.ledger.refresh(run, profile)
        self.shiny_clause.refresh(run)
        self.fight_line.refresh(self.fight_timeline())
        self.readiness.refresh(self._party_cards, self.fight_timeline())
        self.finish_run_button.setEnabled(run is not None)
        if not run:
            self.encounter_preview.setRowCount(0)
            self.pending_warning_label.clear()
            self.recent_events_label.setText("No detection events recorded.")
            self.presentation_changed.emit()
            return
        caps = resolved_level_caps(run, profile) if profile else ()
        summary = summarize_run(run, caps)
        self.run_title_label.setText(run.name)
        state = run.status
        game = profile.display_name if profile else f"Unsupported profile: {run.game}"
        try:
            started = datetime.fromisoformat(run.started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            days = max(0, (datetime.now(UTC) - started).days)
            age = "Started today" if days == 0 else f"Started {days} day{'s' if days != 1 else ''} ago"
        except (TypeError, ValueError):
            age = "Start date unavailable"
        self.run_metadata_label.setText(f"{game} · {age}")
        self.hero_run_state.setText(f"{state.upper()} RUN")
        for key, label in self.run_metrics.items():
            value = sum(not _encounter_is_unused(record) for record in run.encounters.values()) if key == "encounters" else getattr(summary, key)
            label.setText(str(value))
        records = [record for record in run.encounters.values() if not _encounter_is_unused(record)]
        self.encounter_preview.setRowCount(min(5, len(records)))
        self.preview_empty_label.setVisible(not records)
        self.encounter_preview.setVisible(bool(records))
        for row, record in enumerate(records[:5]):
            values = (
                record.location, _STATUS_LABELS.get(record.status, record.status),
                f"{record.nickname} · {record.species}" if record.nickname else record.species,
                str(record.level) if record.level is not None else "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.encounter_preview.setItem(row, column, item)
            self.encounter_preview.item(row, 0).setData(Qt.ItemDataRole.UserRole, record.location_id)
        pending = len(pending_acquisition_events(run))
        self.pending_warning_label.setText(
            f"{pending} acquisition{'s' if pending != 1 else ''} awaiting a decision."
            if pending else "No encounters awaiting a decision."
        )
        self.review_pending_button.setVisible(bool(pending))
        events = []
        for shiny in run.shiny_encounters:
            events.append((shiny.detected_at, (
                f"Shiny {shiny.nickname or shiny.species_name} · {shiny.location_name} · shiny clause"
            )))
        for event in run.acquisition_events:
            status = "reviewed" if event.stable_id in run.resolved_acquisition_ids else "pending"
            events.append((event.detected_at, (
                f"{event.nickname or event.species_name} · {event.source_location} · {status}"
            )))
        for death in run.deaths:
            if death.detection_source == "RAM_AUTO":
                events.append((death.timestamp, f"{death.nickname or death.species} · faint recorded"))
        events.sort(key=lambda item: item[0], reverse=True)
        self.recent_events_label.setText(
            "\n".join(f"{when}  {message}" for when, message in events[:4])
            or "No detection events recorded."
        )
        self.presentation_changed.emit()

    def _add_shiny_manually(self) -> None:
        from pokemon_ev_tracker.ui.shiny_clause import AddShinyDialog
        run = self.store.active_run
        if run is None:
            return
        dialog = AddShinyDialog(run, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            self.store.record_shiny_encounters(run.run_id, (dialog.record(),))
        except (OSError, ValueError) as error:
            self._show_error("Could not record shiny encounter", error)
            return
        self._refresh_all()

    def move_ram_death_notification(self, parent: QWidget) -> None:
        self._run_content_layout.removeWidget(self.ram_death_group)
        self.ram_death_group.setParent(parent)

    def receive_ram_death_candidates(
        self, run_id: str | None, candidates: tuple[DeathCandidate, ...]
    ) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            return
        for candidate in candidates:
            match = match_death_candidate(candidate, run)
            location_id = match.location_id
            if location_id and (
                run.encounters[location_id].status == EncounterStatus.DEAD.value
                or any(death.stable_id == candidate.stable_id for death in run.deaths)
            ):
                continue
            if run.automatically_confirm_deaths and location_id:
                try:
                    recorded = self.store.record_ram_death(run.run_id, candidate, location_id)
                except (OSError, KeyError, ValueError) as error:
                    self._show_error("Could not record RAM-detected death", error)
                    recorded = False
                if recorded:
                    self._set_ram_death_notice(run.run_id, candidate, "recorded")
                    self._refresh_all()
                continue
            self._ram_death_queue.append(
                (run.run_id, candidate, location_id, match.ambiguous)
            )
        self._render_ram_death_candidate()

    def _build_encounters_tab(self) -> None:
        page = RunPanel("Encounter ledger")
        layout = page.content
        controls = QHBoxLayout()
        self._responsive_rows.append(controls)
        self.encounter_filter = QComboBox()
        self.encounter_filter.addItem("All statuses", "")
        for status in VISIBLE_STATUSES:
            self.encounter_filter.addItem(_STATUS_LABELS[status], status)
        self.encounter_filter.currentIndexChanged.connect(self._render_encounters)
        controls.addWidget(QLabel("Filter"))
        controls.addWidget(self.encounter_filter)
        controls.addStretch(1)
        self.edit_encounter_button = QPushButton("Edit Encounter")
        self.reset_encounter_button = QPushButton("Reset Encounter")
        self.reset_encounter_button.setProperty("buttonRole", "danger")
        controls.addWidget(self.edit_encounter_button)
        controls.addWidget(self.reset_encounter_button)
        layout.addLayout(controls)
        self.encounters_table = QTableWidget(0, 6)
        self.encounters_table.setHorizontalHeaderLabels(
            ("Location", "Status", "Pokémon", "Nickname", "Level", "Notes")
        )
        self.encounters_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.encounters_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.encounters_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.encounters_table.verticalHeader().hide()
        self.encounters_table.setAlternatingRowColors(True)
        self._configure_table(self.encounters_table)
        self.encounters_table.setColumnWidth(0, 190)
        self.encounters_table.setColumnWidth(1, 135)
        self.encounters_table.setColumnWidth(2, 145)
        self.encounters_table.setColumnWidth(3, 120)
        self.encounters_table.setColumnWidth(4, 60)
        self.encounters_table.horizontalHeader().setStretchLastSection(True)
        self.encounters_table.doubleClicked.connect(self._edit_selected_encounter)
        layout.addWidget(self.encounters_table, 1)
        self.edit_encounter_button.clicked.connect(self._edit_selected_encounter)
        self.reset_encounter_button.clicked.connect(self._reset_selected_encounter)
        layout.addWidget(self.auto_record_acquisitions)
        layout.addWidget(self.acquisition_group)
        self.sections.addTab(page, "Encounters")

    def _build_caps_tab(self) -> None:
        page = RunPanel("Major fights")
        layout = page.content
        heading = QHBoxLayout()
        self._responsive_rows.append(heading)
        heading.addWidget(run_label(
            "Ordered by the game profile. Double-click a fight to edit its cap or notes."
        ), 1)
        edit = QPushButton("Edit Level Caps")
        edit.clicked.connect(self._edit_caps)
        heading.addWidget(edit)
        layout.addLayout(heading)
        self.caps_table = QTableWidget(0, 9)
        self.caps_table.setHorizontalHeaderLabels((
            "#", "Fight", "Category", "Default", "Effective", "Healing items",
            "Status", "Note", "Completed",
        ))
        self.caps_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.caps_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.caps_table.verticalHeader().hide()
        self.caps_table.setAlternatingRowColors(True)
        self._configure_table(self.caps_table)
        self.caps_table.setColumnWidth(0, 45)
        self.caps_table.setColumnWidth(1, 220)
        self.caps_table.setColumnWidth(2, 130)
        self.caps_table.horizontalHeader().setStretchLastSection(True)
        self.caps_table.cellDoubleClicked.connect(self._open_fight_row)
        layout.addWidget(self.caps_table, 1)
        self.sections.addTab(page, "Fights")

    def _build_deaths_tab(self) -> None:
        page = RunPanel("Death log")
        layout = page.content
        layout.addWidget(run_label("Chronological · newest first · encounter history preserved"))
        controls = QHBoxLayout()
        self._responsive_rows.append(controls)
        controls.addStretch(1)
        self.add_death_button = QPushButton("Record Death")
        self.remove_death_button = QPushButton("Remove Death")
        self.remove_death_button.setProperty("buttonRole", "danger")
        controls.addWidget(self.add_death_button)
        controls.addWidget(self.remove_death_button)
        layout.addLayout(controls)
        self.deaths_table = QTableWidget(0, 8)
        self.deaths_table.setHorizontalHeaderLabels(
            (
                "Pokémon", "Nickname", "Lv. at death", "Encounter", "Death location / fight",
                "Timestamp", "Source", "Notes",
            )
        )
        self.deaths_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.deaths_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.deaths_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.deaths_table.verticalHeader().hide()
        self.deaths_table.setAlternatingRowColors(True)
        self._configure_table(self.deaths_table)
        self.deaths_table.setColumnWidth(0, 150)
        self.deaths_table.setColumnWidth(1, 120)
        self.deaths_table.setColumnWidth(2, 55)
        self.deaths_table.setColumnWidth(3, 150)
        self.deaths_table.setColumnWidth(4, 150)
        self.deaths_table.setColumnWidth(5, 150)
        self.deaths_table.setColumnWidth(6, 90)
        self.deaths_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.deaths_table, 1)
        self.add_death_button.clicked.connect(self._add_death)
        self.remove_death_button.clicked.connect(self._remove_death)
        self.auto_confirm_deaths = QCheckBox("Auto-confirm linked deaths")
        self.auto_confirm_deaths.setMinimumWidth(0)
        self.auto_confirm_deaths.setToolTip(
            "When enabled, fainted party members linked to this run are recorded as dead automatically."
        )
        self.auto_confirm_deaths.toggled.connect(self._set_auto_confirm_deaths)
        layout.addWidget(self.auto_confirm_deaths)
        layout.addWidget(run_label(
            "Faint candidates use fresh HP transitions and stable Pokémon identity. "
            "Unlinked candidates require review."
        ))
        # TODO: party-wipe detection is a separate, unvalidated backend feature.
        layout.addWidget(run_label("Party wipe detection is not available."))
        self.sections.addTab(page, "Deaths")

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setMinimumWidth(0)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.horizontalHeader().setMinimumSectionSize(52)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

    def _active_profile(self) -> NuzlockeGameProfile | None:
        run = self.store.active_run
        return self.profiles.get(run.game) if run else None

    def _refresh_all(self) -> None:
        self._render_run_selector()
        self._render_run_library()
        run = self.store.active_run
        self._update_section_visibility()
        if run is None:
            self.ram_death_group.hide()
            self.encounters_table.setRowCount(0)
            self.caps_table.setRowCount(0)
            self.deaths_table.setRowCount(0)
            self._render_dashboard()
            self._refresh_cap_panel()
            self.presentation_changed.emit()
            return
        profile = self._active_profile()
        if profile:
            self.store.ensure_locations(
                run.run_id,
                profile.locations,
                obsolete_locations=profile.retired_default_locations,
            )
        self.run_notes_input.blockSignals(True)
        self.run_notes_input.setPlainText(run.notes)
        self.run_notes_input.blockSignals(False)
        self.auto_record_acquisitions.blockSignals(True)
        self.auto_record_acquisitions.setChecked(run.auto_record_unambiguous_encounters)
        self.auto_record_acquisitions.blockSignals(False)
        self.auto_confirm_deaths.blockSignals(True)
        self.auto_confirm_deaths.setChecked(run.automatically_confirm_deaths)
        self.auto_confirm_deaths.blockSignals(False)
        self._update_library_actions()
        supported = profile is not None
        self.cap_group.setVisible(supported)
        self.edit_caps_button.setEnabled(supported)
        self.complete_cap_button.setEnabled(supported)
        if not supported:
            self.summary_label.setText(f"{run.name} · Unsupported game profile: {run.game}")
            self.encounters_table.setRowCount(0)
            self.caps_table.setRowCount(0)
            self.deaths_table.setRowCount(0)
            self._render_ram_death_candidate()
            self._render_dashboard()
            return
        self._render_encounters()
        self._render_caps()
        self._render_deaths()
        self._render_ram_death_candidate()
        self._refresh_cap_panel()
        self.refresh_acquisition_suggestions()
        self._render_dashboard()
        self.presentation_changed.emit()

    def _render_run_selector(self) -> None:
        self.run_selector.blockSignals(True)
        self.run_selector.clear()
        active_id = self.store.active_run_id
        for run in self.store.runs:
            if run.status != RunStatus.ACTIVE.value:
                continue
            self.run_selector.addItem(run.name, run.run_id)
        index = self.run_selector.findData(active_id)
        self.run_selector.setCurrentIndex(index)
        self.run_selector.blockSignals(False)
        self._update_library_actions()

    def refresh_acquisition_suggestions(self) -> None:
        run = self.store.active_run
        if not run:
            self.acquisition_group.setVisible(False)
            return
        self.acquisition_group.setVisible(True)
        if self.settings and self.settings.encounter_action == "IGNORE":
            try:
                for event in pending_acquisition_events(run):
                    self.store.resolve_acquisition(run.run_id, event.stable_id)
            except OSError as error:
                self._show_error("Could not ignore encounter", error)
        if self._try_auto_record_pending(run):
            self._refresh_all()
            return
        self._render_acquisition_suggestions()
        self._render_dashboard()

    def report_acquisition_trace(self, trace: dict[str, object]) -> None:
        self.acquisition_status_label.setText(
            "Last acquisition candidate:\n"
            f"source: {trace.get('source') or '--'}\n"
            f"pokemon: {trace.get('pokemon') or '--'} "
            f"({trace.get('species') or '--'})\n"
            f"location: {trace.get('location') or '--'}\n"
            f"status: {trace.get('status') or '--'}"
            + (f" ({trace['reason']})" if trace.get("reason") else "")
        )

    def _render_acquisition_suggestions(self) -> None:
        run = self.store.active_run
        events = pending_acquisition_events(run)
        self.acquisition_group.setVisible(bool(events))
        self.acquisition_selector.blockSignals(True)
        self.acquisition_selector.clear()
        for event in events:
            label = event.nickname or event.species_name
            self.acquisition_selector.addItem(
                f"{label} ({event.species_name})" if label != event.species_name else label,
                event.stable_id,
            )
        self.acquisition_selector.setVisible(bool(events))
        self.acquisition_selector.blockSignals(False)

        profile = self._active_profile()
        self.acquisition_location_selector.blockSignals(True)
        self.acquisition_location_selector.clear()
        self.acquisition_location_selector.addItem("Choose location...", None)
        if profile:
            for location in sorted(profile.locations, key=lambda item: item.order):
                self.acquisition_location_selector.addItem(location.name, location.location_id)
        self.acquisition_location_selector.blockSignals(False)
        self._select_acquisition()

    def _select_acquisition(self, *_args) -> None:
        event = self._selected_acquisition()
        suggested_index = self.acquisition_location_selector.findData(
            event.suggested_location_id if event else None
        )
        self.acquisition_location_selector.blockSignals(True)
        self.acquisition_location_selector.setCurrentIndex(max(0, suggested_index))
        self.acquisition_location_selector.blockSignals(False)
        self._render_selected_acquisition()

    def _selected_acquisition(self) -> PokemonAcquisitionEvent | None:
        run = self.store.active_run
        stable_id = self.acquisition_selector.currentData()
        if run is None or not stable_id:
            return None
        return next(
            (event for event in pending_acquisition_events(run) if event.stable_id == stable_id),
            None,
        )

    def _render_selected_acquisition(self, *_args) -> None:
        event = self._selected_acquisition()
        run = self.store.active_run
        if not event or not run:
            self.acquisition_sprite.clear()
            self.acquisition_sprite.hide()
            self.acquisition_detail_label.setText("No new Pokémon detected.")
            self.acquisition_location_selector.setEnabled(False)
            for button in (
                self.accept_acquisition_button,
                self.replace_acquisition_button,
                self.extra_acquisition_button,
                self.ignore_acquisition_button,
            ):
                button.setEnabled(False)
            return

        pixmap = get_static_sprite(event.species_id, 64)
        self.acquisition_sprite.setPixmap(pixmap)
        self.acquisition_sprite.setVisible(not pixmap.isNull())
        self.report_acquisition_trace({
            "source": event.source_location,
            "pokemon": event.nickname or event.species_name,
            "species": event.species_name,
            "location": event.suggested_location_name or event.met_location_name,
            "status": "pending",
        })
        met = event.met_location_name or f"Unknown location #{event.met_location_id}"
        shown_level = event.met_level or event.level
        level_text = f"Lv. {shown_level}" if shown_level else "Level unknown"
        suggested = event.suggested_location_name or "Choose a location"
        storage_hint = " · Stored in PC" if event.source_location == "BOX" else ""
        self.acquisition_detail_label.setText(
            f"{event.nickname + ' · ' if event.nickname and event.nickname != event.species_name else ''}"
            f"{event.species_name} · {level_text}\nMet at {met} · {event.source.title()} ({event.confidence.lower()} confidence){storage_hint}\n"
            f"Suggested: {suggested}"
        )
        self.acquisition_location_selector.setEnabled(True)
        location_id = self.acquisition_location_selector.currentData()
        encounter = run.encounters.get(location_id) if location_id else None
        occupied = encounter is not None and not _encounter_is_unused(encounter)
        if occupied:
            self.acquisition_detail_label.setText(
                self.acquisition_detail_label.text()
                + f"\nEncounter already recorded for {encounter.location}."
            )
        self.accept_acquisition_button.setEnabled(bool(location_id and not occupied))
        self.replace_acquisition_button.setEnabled(bool(location_id and occupied))
        self.extra_acquisition_button.setEnabled(bool(location_id))
        self.ignore_acquisition_button.setEnabled(True)

    def _try_auto_record_pending(self, run) -> bool:
        if not run.auto_record_unambiguous_encounters:
            return False
        for event in pending_acquisition_events(run):
            location_id = event.suggested_location_id
            encounter = run.encounters.get(location_id) if location_id else None
            if (
                event.source != "WILD"
                or event.confidence != "HIGH"
                or event.is_egg
                or event.egg_location_id
                or event.origin_game != 12
                or location_id == "platinum-starter"
                or encounter is None
                or not _encounter_is_unused(encounter)
            ):
                continue
            try:
                self.store.accept_acquisition(
                    run.run_id, event.stable_id, location_id, mode="add"
                )
            except (OSError, ValueError, KeyError) as error:
                self._show_error("Could not automatically record encounter", error)
                return False
            return True
        return False

    def _set_auto_record_acquisitions(self, enabled: bool) -> None:
        run = self.store.active_run
        if not run:
            return
        try:
            self.store.set_auto_record_unambiguous(run.run_id, enabled)
        except OSError as error:
            self._show_error("Could not save acquisition setting", error)
            return
        self.refresh_acquisition_suggestions()

    def _set_auto_confirm_deaths(self, enabled: bool) -> None:
        run = self.store.active_run
        if not run:
            return
        try:
            self.store.set_automatically_confirm_deaths(run.run_id, enabled)
        except OSError as error:
            self._show_error("Could not save death-confirmation setting", error)
            return
        if enabled:
            remaining = []
            for run_id, candidate, location_id, ambiguous in self._ram_death_queue:
                if run_id != run.run_id or location_id is None:
                    remaining.append((run_id, candidate, location_id, ambiguous))
                    continue
                try:
                    recorded = self.store.record_ram_death(run_id, candidate, location_id)
                except (OSError, KeyError, ValueError) as error:
                    self._show_error("Could not record RAM-detected death", error)
                    remaining.append((run_id, candidate, location_id, ambiguous))
                    continue
                if recorded:
                    self._set_ram_death_notice(run_id, candidate, "recorded")
            self._ram_death_queue = remaining
            self._refresh_all()
        else:
            self._render_ram_death_candidate()

    def _active_ram_death_candidate(self):
        run = self.store.active_run
        if run is None:
            return None
        return next(
            (
                entry
                for entry in self._ram_death_queue
                if entry[0] == run.run_id
            ),
            None,
        )

    def _render_ram_death_candidate(self, *_args) -> None:
        run = self.store.active_run
        entry = self._active_ram_death_candidate()
        if run is None or entry is None:
            notice = (
                self._ram_death_notice
                if run is not None and self._ram_death_notice_run_id == run.run_id
                else ""
            )
            if not notice:
                self._shown_ram_death_key = None
                self.ram_death_group.hide()
                return
            self._shown_ram_death_key = None
            self.ram_death_detail_label.setText(notice)
            self.ram_death_location_selector.hide()
            self.confirm_ram_death_button.hide()
            self.ignore_ram_death_button.hide()
            self.dismiss_ram_death_notice_button.show()
            self.ram_death_group.show()
            return

        run_id, candidate, linked_location_id, ambiguous = entry
        display_name = candidate.nickname or candidate.species
        level_text = f"Lv. {candidate.level}" if candidate.level else "Level unknown"
        encounter = run.encounters.get(linked_location_id) if linked_location_id else None
        encounter_text = encounter.location if encounter else "Not linked to an encounter"
        detail = (
            f"{display_name}\n{candidate.species} · {level_text}\n"
            f"Encounter: {encounter_text}"
        )
        if linked_location_id is None:
            detail += "\nFainted Pokémon is not linked to this run."
            if ambiguous:
                detail += " Multiple legacy encounter rows match; choose the correct one or record unlinked."
        self.ram_death_detail_label.setText(detail)
        candidate_key = (run_id, candidate.stable_id)
        if self._shown_ram_death_key != candidate_key:
            self.ram_death_location_selector.blockSignals(True)
            self.ram_death_location_selector.clear()
            self.ram_death_location_selector.addItem("No encounter link", None)
            profile = self._active_profile()
            if profile:
                for location in sorted(profile.locations, key=lambda item: item.order):
                    self.ram_death_location_selector.addItem(location.name, location.location_id)
            preferred = next(
                (
                    location.location_id
                    for location in (profile.locations if profile else ())
                    if candidate.met_location_name
                    and location.name.casefold() == candidate.met_location_name.casefold()
                ),
                None,
            )
            if preferred and linked_location_id is None:
                self.ram_death_location_selector.setCurrentIndex(
                    self.ram_death_location_selector.findData(preferred)
                )
            self.ram_death_location_selector.blockSignals(False)
            self._shown_ram_death_key = candidate_key
        self.ram_death_location_selector.setVisible(linked_location_id is None)
        self._update_ram_death_action_state()
        self.confirm_ram_death_button.show()
        self.ignore_ram_death_button.show()
        self.dismiss_ram_death_notice_button.hide()
        self.ram_death_group.show()

    def _update_ram_death_action_state(self, *_args) -> None:
        entry = self._active_ram_death_candidate()
        run = self.store.active_run
        if entry is None or run is None:
            return
        _run_id, candidate, linked_location_id, _ambiguous = entry
        if linked_location_id:
            self.confirm_ram_death_button.setText("Mark Dead")
            self.confirm_ram_death_button.setEnabled(True)
            return
        selected_id = self.ram_death_location_selector.currentData()
        selected = run.encounters.get(selected_id) if selected_id else None
        if selected and selected.species:
            same_member = (
                selected.stable_id == candidate.stable_id
                if selected.stable_id
                else (
                    selected.species.casefold() == candidate.species.casefold()
                    and (selected.nickname or selected.species).casefold()
                    == (candidate.nickname or candidate.species).casefold()
                )
            )
            if not same_member:
                self.confirm_ram_death_button.setEnabled(False)
                self.confirm_ram_death_button.setText("Different Pokémon in this row")
                return
        self.confirm_ram_death_button.setEnabled(True)
        self.confirm_ram_death_button.setText(
            "Link / Mark Dead" if selected_id else "Mark Dead (Unlinked)"
        )

    def _confirm_ram_death(self) -> None:
        entry = self._active_ram_death_candidate()
        run = self.store.active_run
        if entry is None or run is None or entry[0] != run.run_id:
            return
        _run_id, candidate, linked_location_id, _ambiguous = entry
        location_id = linked_location_id or self.ram_death_location_selector.currentData()
        try:
            recorded = self.store.record_ram_death(run.run_id, candidate, location_id)
        except (OSError, KeyError, ValueError) as error:
            self._show_error("Could not record RAM-detected death", error)
            return
        self._ram_death_queue.remove(entry)
        if recorded:
            self._set_ram_death_notice(run.run_id, candidate, "recorded")
        self._refresh_all()

    def _ignore_ram_death(self) -> None:
        entry = self._active_ram_death_candidate()
        if entry is not None:
            self._ram_death_queue.remove(entry)
            self._render_ram_death_candidate()

    def _set_ram_death_notice(self, run_id: str, candidate: DeathCandidate, action: str) -> None:
        display_name = candidate.nickname or candidate.species
        level_text = f"Lv. {candidate.level}" if candidate.level else "level unknown"
        self._ram_death_notice_run_id = run_id
        self._ram_death_notice = (
            f"RAM faint recorded: {display_name} ({candidate.species}, {level_text})."
            if action == "recorded"
            else f"Faint detected: {display_name} ({candidate.species}, {level_text})."
        )

    def _dismiss_ram_death_notice(self) -> None:
        self._ram_death_notice = ""
        self._ram_death_notice_run_id = None
        self._render_ram_death_candidate()

    def _accept_selected_acquisition(self) -> None:
        self._save_selected_acquisition("add")

    def _replace_selected_acquisition(self) -> None:
        event = self._selected_acquisition()
        location_id = self.acquisition_location_selector.currentData()
        if not event or not location_id:
            return
        run = self.store.active_run
        encounter = run.encounters.get(location_id) if run else None
        if encounter is None:
            return
        answer = QMessageBox.question(
            self,
            "Replace Encounter",
            f"Replace the existing {encounter.location} encounter with {event.species_name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._save_selected_acquisition("replace")

    def _add_selected_acquisition_extra(self) -> None:
        self._save_selected_acquisition("extra")

    def _save_selected_acquisition(self, mode: str) -> None:
        event = self._selected_acquisition()
        location_id = self.acquisition_location_selector.currentData()
        run = self.store.active_run
        if not event or not location_id or not run:
            return
        try:
            self.store.accept_acquisition(run.run_id, event.stable_id, location_id, mode=mode)
        except (OSError, ValueError, KeyError) as error:
            self._show_error("Could not record acquisition", error)
            return
        self.report_acquisition_trace({
            "source": event.source_location,
            "pokemon": event.nickname or event.species_name,
            "species": event.species_name,
            "location": event.suggested_location_name or event.met_location_name,
            "status": "accepted",
            "reason": mode,
        })
        self._refresh_all()

    def _ignore_selected_acquisition(self) -> None:
        event = self._selected_acquisition()
        run = self.store.active_run
        if not event or not run:
            return
        try:
            self.store.resolve_acquisition(run.run_id, event.stable_id)
        except OSError as error:
            self._show_error("Could not ignore acquisition", error)
            return
        self.report_acquisition_trace({
            "source": event.source_location,
            "pokemon": event.nickname or event.species_name,
            "species": event.species_name,
            "location": event.suggested_location_name or event.met_location_name,
            "status": "ignored",
        })
        self.refresh_acquisition_suggestions()

    def _switch_run(self, index: int) -> None:
        run_id = self.run_selector.itemData(index)
        if run_id and run_id != self.store.active_run_id:
            try:
                self.store.switch_run(run_id)
            except (OSError, ValueError) as error:
                self._show_error("Could not switch run", error)
            self._refresh_all()

    def _create_run(self) -> None:
        if not self.profiles:
            return
        dialog = NewRunDialog(tuple(self.profiles.values()), self)
        if self.store.active_run:
            dialog.active_run_notice.setText("The current run remains in the library as an ongoing run. This new run will become active.")
        preferred = self._active_profile() or next(iter(self.profiles.values()))
        dialog.game_input.setCurrentIndex(dialog.game_input.findData(preferred.game_id))
        dialog.name_input.setText(self.store.suggest_next_run_name(preferred))
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        name, profile = dialog.values()
        try:
            created = self.store.create_run(name, profile)
            if self.settings and self.settings.encounter_action == "AUTOMATIC":
                self.store.set_auto_record_unambiguous(created.run_id, True)
        except (OSError, ValueError) as error:
            self._show_error("Could not create run", error)
            return
        self._refresh_all()

    def _rename_run(self) -> None:
        run = self._selected_library_run()
        if not run:
            return
        name, accepted = QInputDialog.getText(self, "Rename Run", "Run name", text=run.name)
        if not accepted:
            return
        try:
            self.store.rename_run(run.run_id, name)
        except (OSError, ValueError) as error:
            self._show_error("Could not rename run", error)
            return
        self._refresh_all()

    def _delete_run(self) -> None:
        run = self._selected_library_run()
        if not run:
            return
        answer = QMessageBox.question(
            self,
            "Delete Run",
            f"Permanently delete '{run.name}' and all encounters, deaths, fights, "
            "notes, overrides, acquisition history, and lifecycle data?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete_run(run.run_id)
        except OSError as error:
            self._show_error("Could not delete run", error)
            return
        self._refresh_all()

    def _render_encounters(self, _index: int = -1) -> None:
        run = self.store.active_run
        if not run:
            self.encounters_table.setRowCount(0)
            return
        profile = self._active_profile()
        locations = profile.locations if profile else ()
        filter_status = self.encounter_filter.currentData()
        records = sorted(run.encounters.values(), key=lambda item: item.location_id)
        if locations:
            order = {item.location_id: item.order for item in locations}
            records.sort(key=lambda item: order.get(item.location_id, 99999))
        records = [item for item in records if not filter_status or item.status == filter_status]
        self._rendering = True
        self.encounters_table.setRowCount(len(records))
        for row, encounter in enumerate(records):
            values = (
                encounter.location,
                "",
                encounter.species,
                encounter.nickname,
                str(encounter.level) if encounter.level is not None else "",
                encounter.notes,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, encounter.location_id)
                if column == 1:
                    item.setToolTip(_STATUS_LABELS.get(encounter.status, encounter.status))
                    color = _STATUS_COLORS.get(encounter.status)
                    if color:
                        item.setBackground(QColor(color))
                self.encounters_table.setItem(row, column, item)
            status_box = QComboBox()
            for status in VISIBLE_STATUSES + ((encounter.status,) if encounter.status not in VISIBLE_STATUSES else ()):
                status_box.addItem(_STATUS_LABELS[status], status)
            status_box.setCurrentIndex(status_box.findData(encounter.status))
            status_box.setProperty("encounterStatus", encounter.status)
            status_box.style().unpolish(status_box)
            status_box.style().polish(status_box)
            status_box.currentIndexChanged.connect(
                lambda _i, location_id=encounter.location_id, box=status_box: (
                    self._set_encounter_status(location_id, box.currentData())
                )
            )
            self.encounters_table.setCellWidget(row, 1, status_box)
        self._rendering = False

    def _selected_location_id(self) -> str | None:
        row = self.encounters_table.currentRow()
        item = self.encounters_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _set_encounter_status(self, location_id: str, status: str) -> None:
        if self._rendering:
            return
        run = self.store.active_run
        if not run or location_id not in run.encounters:
            return
        previous = run.encounters[location_id]
        try:
            from dataclasses import replace

            self.store.update_encounter(
                run.run_id, replace(run.encounters[location_id], status=status)
            )
        except (OSError, ValueError) as error:
            self._show_error("Could not update encounter", error)
        else:
            if previous.status == EncounterStatus.DEAD.value and status != EncounterStatus.DEAD.value:
                self._discard_ram_death_candidate(run.run_id, previous.stable_id)
        self._refresh_all()

    def _discard_ram_death_candidate(self, run_id: str, stable_id: str | None) -> None:
        if stable_id is None:
            return
        self._ram_death_queue = [
            entry
            for entry in self._ram_death_queue
            if not (entry[0] == run_id and entry[1].stable_id == stable_id)
        ]

    def _edit_selected_encounter(self, *_args) -> None:
        run = self.store.active_run
        location_id = self._selected_location_id()
        if not run or not location_id:
            return
        encounter = run.encounters[location_id]
        dialog = EncounterDialog(encounter.location, self)
        dialog.status_input.setCurrentIndex(dialog.status_input.findData(encounter.status))
        dialog.species_input.setText(encounter.species)
        dialog.nickname_input.setText(encounter.nickname)
        dialog.level_input.setValue(encounter.level or 0)
        dialog.notes_input.setPlainText(encounter.notes)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        new_status = dialog.status_input.currentData()
        try:
            self.store.update_encounter(
                run.run_id,
                EncounterRecord(
                    location_id=encounter.location_id,
                    location=encounter.location,
                    status=dialog.status_input.currentData(),
                    species=dialog.species_input.text().strip(),
                    nickname=dialog.nickname_input.text().strip(),
                    level=dialog.level_input.value() or None,
                    notes=dialog.notes_input.toPlainText().strip(),
                    stable_id=encounter.stable_id,
                    met_location_id=encounter.met_location_id,
                    met_location_name=encounter.met_location_name,
                ),
            )
        except (OSError, ValueError) as error:
            self._show_error("Could not save encounter", error)
            return
        if encounter.status == EncounterStatus.DEAD.value and new_status != EncounterStatus.DEAD.value:
            self._discard_ram_death_candidate(run.run_id, encounter.stable_id)
        self._refresh_all()

    def _reset_selected_encounter(self) -> None:
        run = self.store.active_run
        location_id = self._selected_location_id()
        if not run or not location_id:
            return
        encounter = run.encounters[location_id]
        answer = QMessageBox.question(
            self,
            "Reset Encounter",
            f"Clear the encounter for {encounter.location}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.update_encounter(
                run.run_id,
                EncounterRecord(encounter.location_id, encounter.location),
            )
        except OSError as error:
            self._show_error("Could not reset encounter", error)
            return
        self._discard_ram_death_candidate(run.run_id, encounter.stable_id)
        self._refresh_all()

    def _render_caps(self) -> None:
        timeline = self.fight_timeline()
        if timeline is None:
            self.caps_table.setRowCount(0)
            return
        self.caps_table.setRowCount(len(timeline.fights))
        for row, fight in enumerate(timeline.fights):
            values = (
                f"{fight.order:02d}", fight.name, fight.category,
                str(fight.default_level_cap),
                f"Custom: {fight.effective_level_cap}" if fight.overridden
                else str(fight.effective_level_cap),
                fight.healing_text, fight.status.title(),
                "Notes" if fight.notes else "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(fight.notes if column == 7 and fight.notes else value)
                if column == 6:
                    item.setForeground(QColor(
                        COLORS["success"] if fight.completed else
                        COLORS["accent"] if fight.status == "UP NEXT" else
                        COLORS["text_muted"]
                    ))
                self.caps_table.setItem(row, column, item)
            completed = QCheckBox()
            completed.setChecked(fight.completed)
            completed.setToolTip("Fight completed")
            completed.toggled.connect(
                lambda value, cap_id=fight.fight_id: self._set_cap_completed(cap_id, value)
            )
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            holder_layout.addWidget(completed)
            self.caps_table.setCellWidget(row, 8, holder)

    def _open_fight_row(self, row: int, _column: int) -> None:
        timeline = self.fight_timeline()
        if timeline and 0 <= row < len(timeline.fights):
            self._edit_fight(timeline.fights[row].fight_id)

    def _edit_caps(self) -> None:
        run, profile = self.store.active_run, self._active_profile()
        if not run or not profile:
            return
        dialog = LevelCapsDialog(tuple(sorted(profile.level_caps, key=lambda cap: cap.order)),
                                 run.level_cap_overrides, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            for cap_id, value in dialog.overrides().items():
                self.store.set_level_cap_override(run.run_id, cap_id, value)
        except (OSError, ValueError) as error:
            self._show_error("Could not save level caps", error)
            return
        self._refresh_all()

    def _set_cap_completed(self, cap_id: str, completed: bool) -> None:
        run = self.store.active_run
        if not run:
            return
        try:
            self.store.set_cap_completed(run.run_id, cap_id, completed)
        except OSError as error:
            self._show_error("Could not update fight status", error)
        self._refresh_all()

    def _set_selected_active(self) -> None:
        run = self._selected_library_run()
        if not run or run.status != RunStatus.ACTIVE.value:
            return
        try:
            self.store.switch_run(run.run_id)
        except (OSError, ValueError) as error:
            self._show_error("Could not activate run", error)
            return
        self._refresh_all()

    def _abandon_run(self) -> None:
        run = self._selected_library_run()
        if not run or run.status != RunStatus.ACTIVE.value:
            return
        answer = QMessageBox.question(
            self, "Abandon Run",
            f'Abandon "{run.name}"? The run history will be preserved.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.set_run_status(run.run_id, RunStatus.ABANDONED)
        except OSError as error:
            self._show_error("Could not abandon run", error)
            return
        self._refresh_all()

    def _mark_run_won(self) -> None:
        run = self.store.active_run
        timeline = self.fight_timeline()
        if not run or not timeline or timeline.next_fight is not None:
            return
        try:
            self.store.set_run_status(run.run_id, RunStatus.WON)
        except OSError as error:
            self._show_error("Could not mark run won", error)
            return
        self._refresh_all()

    def end_run_wiped(self, candidate: PartyWipeCandidate) -> bool:
        run = self.store.active_run
        if run is None or run.run_id != candidate.run_id:
            return False
        try:
            for member in candidate.members:
                death = DeathCandidate(
                    stable_id=member.stable_id, species=member.species,
                    nickname=member.nickname, level=member.level,
                    met_location_id=member.met_location_id,
                    met_location_name=member.met_location_name,
                    met_level=member.met_level, detected_at=candidate.detected_at,
                )
                match = match_death_candidate(death, run)
                self.store.record_ram_death(
                    run.run_id, death, match.location_id if not match.ambiguous else None)
            self.store.set_run_status(run.run_id, RunStatus.WIPED)
        except (OSError, KeyError, ValueError) as error:
            self._show_error("Could not end wiped run", error)
            return False
        self._ram_death_queue = [entry for entry in self._ram_death_queue if entry[0] != run.run_id]
        self._refresh_all()
        return True

    def _edit_next_fight(self) -> None:
        timeline = self.fight_timeline()
        if timeline and timeline.next_fight:
            self._edit_fight(timeline.next_fight.fight_id)

    def _edit_fight(self, fight_id: str) -> None:
        timeline = self.fight_timeline()
        run = self.store.active_run
        fight = next((item for item in timeline.fights if item.fight_id == fight_id), None) if timeline else None
        if run is None or fight is None:
            return
        dialog = FightDetailsDialog(fight, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        override, notes = dialog.values()
        try:
            self.store.set_level_cap_override(run.run_id, fight_id, override)
            self.store.set_fight_notes(run.run_id, fight_id, notes)
        except (OSError, ValueError) as error:
            self._show_error("Could not save fight details", error)
            return
        self._refresh_all()

    def _complete_next_cap(self) -> None:
        timeline = self.fight_timeline()
        if timeline and timeline.next_fight:
            self._set_cap_completed(timeline.next_fight.fight_id, True)

    def _refresh_cap_panel(self) -> None:
        timeline = self.fight_timeline()
        run, profile = self.store.active_run, self._active_profile()
        if timeline is None:
            self.next_cap_label.setText("No active Nuzlocke run")
            self.cap_category_label.setText("No cap currently applied")
            for label in (self.cap_effective_label, self.cap_default_label,
                          self.cap_progress_label, self.healing_item_hint_label,
                          self.party_readiness_label, self.party_detail_label,
                          self.fight_note_label, self.party_warning_label):
                label.clear()
            self.complete_cap_button.setEnabled(False)
            self.edit_caps_button.setEnabled(False)
            self.mark_won_button.hide()
            return
        cap = timeline.next_fight
        summary = summarize_run(run, resolved_level_caps(run, profile))
        self.cap_progress_label.setText(
            f"{timeline.completed_count} / {timeline.total_fights} major fights completed"
        )
        self.complete_cap_button.setEnabled(cap is not None)
        self.edit_caps_button.setEnabled(cap is not None)
        if cap is None:
            self.next_cap_label.setText("All major fights complete")
            self.cap_category_label.clear()
            self.cap_effective_label.setText(f"{timeline.total_fights} / {timeline.total_fights}")
            for label in (self.cap_default_label, self.healing_item_hint_label,
                          self.party_readiness_label, self.party_detail_label,
                          self.fight_note_label, self.party_warning_label):
                label.clear()
            self.mark_won_button.show()
        else:
            self.mark_won_button.hide()
            self.next_cap_label.setText(cap.name)
            self.cap_category_label.setText(cap.category)
            self.cap_effective_label.setText(f"LEVEL CAP  {cap.effective_level_cap}")
            self.cap_default_label.setText(
                f"Custom · Default {cap.default_level_cap}" if cap.overridden
                else "Game default")
            self.cap_progress_label.setText(
                f"Fight {cap.order} / {cap.total_fights} · "
                f"{timeline.completed_count} completed")
            self.healing_item_hint_label.setText(f"Opponent healing: {cap.healing_text}")
            if cap.party:
                self.party_readiness_label.setText(
                    f"Party readiness: {cap.ready_count} ready · {cap.over_count} over cap")
                over = [f"{member.name} Lv. {member.level}  +{member.over_by} over cap"
                        for member in cap.party if member.over_by]
                self.party_detail_label.setText("\n".join(over) if over else "Party within cap")
            else:
                self.party_readiness_label.setText("Party levels unavailable")
                self.party_detail_label.clear()
            for label in (self.party_readiness_label, self.party_detail_label):
                label.setProperty("nuzlockeRole", "warning" if cap.over_count else "summary")
                refresh_style(label)
            self.party_warning_label.setText(self.party_detail_label.text())
            self.fight_note_label.setText(
                f"Note: {cap.notes.strip().splitlines()[0][:100]}" if cap.notes.strip() else "")
        self.summary_label.setText(
            f"Caught {summary.caught}   ·   Failed {summary.failed}   ·   "
            f"Deaths {summary.deaths}   ·   Skipped {summary.skipped}   ·   "
            f"Fights {summary.completed_fights}/{summary.total_fights}"
        )

    def _save_run_details(self, *_args) -> None:
        run = self.store.active_run
        if not run:
            return
        try:
            self.store.update_run_details(
                run.run_id,
                notes=self.run_notes_input.toPlainText(),
                completed=run.completed,
            )
        except OSError as error:
            self._show_error("Could not save run details", error)
            return
        self._render_run_library()
        self._render_dashboard()

    def _add_death(self) -> None:
        run, profile = self.store.active_run, self._active_profile()
        if not run:
            return
        locations = tuple(location.name for location in profile.locations) if profile else ()
        dialog = DeathDialog(locations, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            self.store.add_death(run.run_id, dialog.record())
        except (OSError, ValueError) as error:
            self._show_error("Could not record death", error)
            return
        self._refresh_all()

    def _render_deaths(self) -> None:
        run = self.store.active_run
        deaths = list(reversed(run.deaths)) if run else []
        self.deaths_table.setRowCount(len(deaths))
        for row, death in enumerate(deaths):
            encounter = run.encounters.get(death.encounter_location_id) if run else None
            values = (
                death.species,
                death.nickname,
                str(death.level_at_death or ""),
                encounter.location if encounter else "",
                death.location_or_fight,
                death.timestamp,
                death.detection_source,
                death.notes,
            )
            for column, value in enumerate(values):
                self.deaths_table.setItem(row, column, QTableWidgetItem(value))
            self.deaths_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, death.death_id)
            self.deaths_table.item(row, 0).setData(
                Qt.ItemDataRole.UserRole + 1, death.encounter_location_id
            )
            self.deaths_table.item(row, 0).setToolTip(
                f"Stable identity: {death.stable_id or 'not recorded'}"
            )

    def _remove_death(self) -> None:
        run = self.store.active_run
        row = self.deaths_table.currentRow()
        item = self.deaths_table.item(row, 0)
        if not run or not item:
            return
        if item.data(Qt.ItemDataRole.UserRole + 1):
            QMessageBox.information(
                self,
                "Encounter-linked death",
                "This death is linked to an encounter. Change that encounter's status to remove it.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Remove Death",
            "Remove this death record?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.remove_death(run.run_id, item.data(Qt.ItemDataRole.UserRole))
        except OSError as error:
            self._show_error("Could not remove death", error)
            return
        self._refresh_all()

    def _show_error(self, title: str, error: Exception) -> None:
        QMessageBox.warning(self, title, str(error))


def _encounter_is_unused(encounter: EncounterRecord) -> bool:
    return encounter.status == "NOT_ENCOUNTERED" and not encounter.species
