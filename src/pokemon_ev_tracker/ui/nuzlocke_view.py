"""Game-independent UI for managing local Nuzlocke runs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.core.nuzlocke.acquisition import pending_acquisition_events
from pokemon_ev_tracker.core.nuzlocke.death_detection import (
    DeathCandidate,
    match_death_candidate,
)
from pokemon_ev_tracker.core.nuzlocke.models import (
    ENCOUNTER_STATUSES,
    EncounterRecord,
    EncounterStatus,
    NuzlockeGameProfile,
    PartyLevel,
    PokemonAcquisitionEvent,
    next_level_cap,
    over_cap_party_members,
    resolved_level_caps,
    summarize_run,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.nuzlocke import (
    PLATINUM_NUZLOCKE_PROFILE,
    PLATINUM_RETIRED_DEFAULT_LOCATIONS,
)
from pokemon_ev_tracker.ui.nuzlocke_dialogs import (
    DeathDialog,
    EncounterDialog,
    LevelCapsDialog,
    NewRunDialog,
)
from pokemon_ev_tracker.ui.theme import COLORS

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
    def __init__(
        self,
        store: NuzlockeStore | None = None,
        profiles: tuple[NuzlockeGameProfile, ...] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("nuzlockeView")
        self.store = store or NuzlockeStore()
        self.profiles = {profile.game_id: profile for profile in profiles}
        self.party_levels: tuple[PartyLevel, ...] = ()
        self._rendering = False
        self._ram_death_queue: list[tuple[str, DeathCandidate, str | None, bool]] = []
        self._ram_death_notice = ""
        self._ram_death_notice_run_id: str | None = None
        self._shown_ram_death_key: tuple[str, str] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(7)
        toolbar = QHBoxLayout()
        self.run_selector = QComboBox()
        self.run_selector.setMinimumWidth(120)
        self.run_selector.currentIndexChanged.connect(self._switch_run)
        toolbar.addWidget(QLabel("Run"))
        toolbar.addWidget(self.run_selector, 1)
        self.create_button = QPushButton("Create Run")
        self.create_button.setProperty("buttonRole", "primary")
        self.rename_button = QPushButton("Rename")
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("buttonRole", "danger")
        for button in (self.create_button, self.rename_button, self.delete_button):
            toolbar.addWidget(button)
        self.create_button.clicked.connect(self._create_run)
        self.rename_button.clicked.connect(self._rename_run)
        self.delete_button.clicked.connect(self._delete_run)
        root.addLayout(toolbar)

        self.empty_label = QLabel("Create a run to start tracking encounters and level caps.")
        self.empty_label.setObjectName("nuzlockeEmpty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_label)

        self.run_content = QWidget()
        content = QVBoxLayout(self.run_content)
        self._run_content_layout = content
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(7)
        self.cap_group = QGroupBox("Next Level Cap")
        self.cap_group.setObjectName("nextLevelCap")
        cap_layout = QVBoxLayout(self.cap_group)
        cap_heading = QHBoxLayout()
        self.next_cap_label = QLabel("No remaining fights")
        self.next_cap_label.setProperty("nuzlockeRole", "capTitle")
        self.next_cap_label.setWordWrap(True)
        cap_heading.addWidget(self.next_cap_label, 1)
        self.cap_progress_label = QLabel("0 / 0 major fights completed")
        self.cap_progress_label.setProperty("uiRole", "muted")
        cap_heading.addWidget(self.cap_progress_label)
        cap_layout.addLayout(cap_heading)
        cap_actions = QHBoxLayout()
        cap_actions.addStretch(1)
        self.edit_caps_button = QPushButton("Edit Level Caps")
        cap_actions.addWidget(self.edit_caps_button)
        self.complete_cap_button = QPushButton("Mark Fight Completed")
        cap_actions.addWidget(self.complete_cap_button)
        cap_layout.addLayout(cap_actions)
        self.healing_item_hint_label = QLabel()
        self.healing_item_hint_label.setProperty("nuzlockeRole", "muted")
        self.healing_item_hint_label.setToolTip(
            "The opposing trainer's configured healing-item inventory. The battle AI may not "
            "use every item. Held items and out-of-battle healing are not included."
        )
        cap_layout.addWidget(self.healing_item_hint_label)
        self.party_warning_label = QLabel()
        self.party_warning_label.setWordWrap(True)
        self.party_warning_label.setProperty("nuzlockeRole", "warning")
        cap_layout.addWidget(self.party_warning_label)
        content.addWidget(self.cap_group)
        self.edit_caps_button.clicked.connect(self._edit_caps)
        self.complete_cap_button.clicked.connect(self._complete_next_cap)

        self.ram_death_group = QGroupBox("Fainted Pokémon")
        self.ram_death_group.setObjectName("ramDeathPanel")
        ram_death_layout = QVBoxLayout(self.ram_death_group)
        self.ram_death_detail_label = QLabel()
        self.ram_death_detail_label.setWordWrap(True)
        ram_death_layout.addWidget(self.ram_death_detail_label)
        ram_death_actions = QHBoxLayout()
        self.ram_death_location_selector = QComboBox()
        self.ram_death_location_selector.setMinimumWidth(100)
        self.ram_death_location_selector.currentIndexChanged.connect(
            self._update_ram_death_action_state
        )
        ram_death_actions.addWidget(self.ram_death_location_selector, 1)
        self.confirm_ram_death_button = QPushButton("Mark Dead")
        self.confirm_ram_death_button.setProperty("buttonRole", "danger")
        self.ignore_ram_death_button = QPushButton("Ignore")
        self.dismiss_ram_death_notice_button = QPushButton("Dismiss")
        ram_death_actions.addWidget(self.confirm_ram_death_button)
        ram_death_actions.addWidget(self.ignore_ram_death_button)
        ram_death_actions.addWidget(self.dismiss_ram_death_notice_button)
        ram_death_layout.addLayout(ram_death_actions)
        self.confirm_ram_death_button.clicked.connect(self._confirm_ram_death)
        self.ignore_ram_death_button.clicked.connect(self._ignore_ram_death)
        self.dismiss_ram_death_notice_button.clicked.connect(self._dismiss_ram_death_notice)
        self.ram_death_group.hide()
        content.addWidget(self.ram_death_group)

        self.acquisition_group = QGroupBox("New Pokémon")
        self.acquisition_group.setObjectName("acquisitionPanel")
        acquisition_layout = QVBoxLayout(self.acquisition_group)
        self.detection_sources_label = QLabel("Automatic encounter detection: Paused")
        self.detection_sources_label.setProperty("uiRole", "muted")
        acquisition_layout.addWidget(self.detection_sources_label)
        self.acquisition_status_label = QLabel("Last acquisition candidate: --")
        self.acquisition_status_label.setWordWrap(True)
        self.acquisition_status_label.setProperty("uiRole", "muted")
        acquisition_layout.addWidget(self.acquisition_status_label)
        self.auto_record_acquisitions = QCheckBox("Automatically record unambiguous encounters")
        self.auto_record_acquisitions.setToolTip(
            "Only unused, clearly mapped wild locations are recorded automatically."
        )
        self.auto_record_acquisitions.toggled.connect(self._set_auto_record_acquisitions)
        acquisition_layout.addWidget(self.auto_record_acquisitions)
        self.auto_confirm_deaths = QCheckBox("Automatically confirm linked deaths")
        self.auto_confirm_deaths.setToolTip(
            "When enabled, fainted party members linked to this run are recorded as dead automatically."
        )
        self.auto_confirm_deaths.toggled.connect(self._set_auto_confirm_deaths)
        acquisition_layout.addWidget(self.auto_confirm_deaths)
        self.acquisition_detail_label = QLabel("No new Pokémon detected.")
        self.acquisition_detail_label.setWordWrap(True)
        suggestion_detail = QHBoxLayout()
        suggestion_detail.addWidget(self.acquisition_detail_label, 1)
        acquisition_layout.addLayout(suggestion_detail)
        selection_row = QHBoxLayout()
        self.acquisition_selector = QComboBox()
        self.acquisition_selector.setMinimumWidth(120)
        self.acquisition_selector.currentIndexChanged.connect(self._select_acquisition)
        selection_row.addWidget(self.acquisition_selector, 2)
        self.acquisition_location_selector = QComboBox()
        self.acquisition_location_selector.setMinimumWidth(110)
        self.acquisition_location_selector.currentIndexChanged.connect(
            self._render_selected_acquisition
        )
        selection_row.addWidget(self.acquisition_location_selector, 1)
        acquisition_layout.addLayout(selection_row)
        suggestion_actions = QHBoxLayout()
        suggestion_actions.addStretch(1)
        self.accept_acquisition_button = QPushButton("Add")
        self.replace_acquisition_button = QPushButton("Replace Existing")
        self.extra_acquisition_button = QPushButton("Add as Extra")
        self.ignore_acquisition_button = QPushButton("Ignore")
        for button in (
            self.accept_acquisition_button,
            self.replace_acquisition_button,
            self.extra_acquisition_button,
            self.ignore_acquisition_button,
        ):
            suggestion_actions.addWidget(button)
        acquisition_layout.addLayout(suggestion_actions)
        content.addWidget(self.acquisition_group)
        self.accept_acquisition_button.clicked.connect(self._accept_selected_acquisition)
        self.replace_acquisition_button.clicked.connect(self._replace_selected_acquisition)
        self.extra_acquisition_button.clicked.connect(self._add_selected_acquisition_extra)
        self.ignore_acquisition_button.clicked.connect(self._ignore_selected_acquisition)

        self.summary_label = QLabel()
        self.summary_label.setProperty("nuzlockeRole", "summary")
        self.summary_label.setWordWrap(True)
        content.addWidget(self.summary_label)

        run_details = QGroupBox("Run Notes")
        details_layout = QHBoxLayout(run_details)
        self.run_notes_input = QTextEdit()
        self.run_notes_input.setMaximumHeight(62)
        self.run_notes_input.setPlaceholderText("Optional notes for this run")
        details_layout.addWidget(self.run_notes_input, 1)
        details_actions = QVBoxLayout()
        self.run_completed_input = QCheckBox("Run completed")
        self.save_run_details_button = QPushButton("Save Details")
        details_actions.addWidget(self.run_completed_input)
        details_actions.addWidget(self.save_run_details_button)
        details_actions.addStretch(1)
        details_layout.addLayout(details_actions)
        content.addWidget(run_details)
        self.save_run_details_button.clicked.connect(self._save_run_details)
        self.run_completed_input.toggled.connect(self._save_run_details)

        self.sections = QTabWidget()
        self.sections.setObjectName("nuzlockeSections")
        content.addWidget(self.sections, 1)
        self._build_encounters_tab()
        self._build_caps_tab()
        self._build_deaths_tab()
        root.addWidget(self.run_content, 1)
        self._refresh_all()

    def set_party_levels(self, members: tuple[PartyLevel, ...]) -> None:
        self.party_levels = members
        self._refresh_cap_panel()

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
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(5, 5, 5, 5)
        controls = QHBoxLayout()
        self.encounter_filter = QComboBox()
        self.encounter_filter.addItem("All statuses", "")
        for status in ENCOUNTER_STATUSES:
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
        self.sections.addTab(page, "Encounters")

    def _build_caps_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.caps_table = QTableWidget(0, 4)
        self.caps_table.setHorizontalHeaderLabels(("Fight", "Category", "Level Cap", "Completed"))
        self.caps_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.caps_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.caps_table.verticalHeader().hide()
        self.caps_table.setAlternatingRowColors(True)
        self._configure_table(self.caps_table)
        self.caps_table.setColumnWidth(0, 260)
        self.caps_table.setColumnWidth(1, 160)
        self.caps_table.setColumnWidth(2, 90)
        self.caps_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.caps_table, 1)
        self.sections.addTab(page, "Level Caps")

    def _build_deaths_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
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
        self.sections.addTab(page, "Death Log")

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
        run = self.store.active_run
        self.run_content.setVisible(run is not None)
        self.empty_label.setVisible(run is None)
        if run is None:
            self.ram_death_group.hide()
            return
        profile = self._active_profile()
        if profile:
            retired_locations = (
                PLATINUM_RETIRED_DEFAULT_LOCATIONS
                if run.game == PLATINUM_NUZLOCKE_PROFILE.game_id
                else ()
            )
            self.store.ensure_locations(
                run.run_id,
                profile.locations,
                obsolete_locations=retired_locations,
            )
        self.run_notes_input.blockSignals(True)
        self.run_notes_input.setPlainText(run.notes)
        self.run_notes_input.blockSignals(False)
        self.run_completed_input.blockSignals(True)
        self.run_completed_input.setChecked(run.completed)
        self.run_completed_input.blockSignals(False)
        self.auto_record_acquisitions.blockSignals(True)
        self.auto_record_acquisitions.setChecked(run.auto_record_unambiguous_encounters)
        self.auto_record_acquisitions.blockSignals(False)
        self.auto_confirm_deaths.blockSignals(True)
        self.auto_confirm_deaths.setChecked(run.automatically_confirm_deaths)
        self.auto_confirm_deaths.blockSignals(False)
        self.rename_button.setEnabled(True)
        self.delete_button.setEnabled(True)
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
            return
        self._render_encounters()
        self._render_caps()
        self._render_deaths()
        self._render_ram_death_candidate()
        self._refresh_cap_panel()
        self.refresh_acquisition_suggestions()

    def _render_run_selector(self) -> None:
        self.run_selector.blockSignals(True)
        self.run_selector.clear()
        active_id = self.store.active_run_id
        for run in self.store.runs:
            self.run_selector.addItem(run.name, run.run_id)
        index = self.run_selector.findData(active_id)
        self.run_selector.setCurrentIndex(index)
        self.run_selector.blockSignals(False)
        has_run = self.store.active_run is not None
        self.rename_button.setEnabled(has_run)
        self.delete_button.setEnabled(has_run)

    def refresh_acquisition_suggestions(self) -> None:
        run = self.store.active_run
        if not run:
            self.acquisition_group.setVisible(False)
            return
        self.acquisition_group.setVisible(True)
        if self._try_auto_record_pending(run):
            self._refresh_all()
            return
        self._render_acquisition_suggestions()

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
            except OSError as error:
                self._show_error("Could not switch run", error)
            self._refresh_all()

    def _create_run(self) -> None:
        if not self.profiles:
            return
        dialog = NewRunDialog(tuple(self.profiles.values()), self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        name, profile = dialog.values()
        try:
            self.store.create_run(name, profile)
        except (OSError, ValueError) as error:
            self._show_error("Could not create run", error)
            return
        self._refresh_all()

    def _rename_run(self) -> None:
        run = self.store.active_run
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
        run = self.store.active_run
        if not run:
            return
        answer = QMessageBox.question(
            self,
            "Delete Run",
            f"Delete '{run.name}' and all of its encounter, cap, and death data?",
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
            for status in ENCOUNTER_STATUSES:
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
        run, profile = self.store.active_run, self._active_profile()
        if not run or not profile:
            self.caps_table.setRowCount(0)
            return
        caps = resolved_level_caps(run, profile)
        self.caps_table.setRowCount(len(caps))
        for row, cap in enumerate(caps):
            self.caps_table.setItem(row, 0, QTableWidgetItem(cap.name))
            self.caps_table.setItem(row, 1, QTableWidgetItem(cap.category))
            self.caps_table.setItem(row, 2, QTableWidgetItem(f"Lv. {cap.level_cap}"))
            completed = QCheckBox()
            completed.setChecked(cap.cap_id in run.completed_cap_ids)
            completed.setToolTip("Fight completed")
            completed.toggled.connect(
                lambda value, cap_id=cap.cap_id: self._set_cap_completed(cap_id, value)
            )
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            holder_layout.addWidget(completed)
            self.caps_table.setCellWidget(row, 3, holder)

    def _edit_caps(self) -> None:
        run, profile = self.store.active_run, self._active_profile()
        if not run or not profile:
            return
        dialog = LevelCapsDialog(resolved_level_caps(run, profile), run.level_cap_overrides, self)
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

    def _complete_next_cap(self) -> None:
        run, profile = self.store.active_run, self._active_profile()
        if not run or not profile:
            return
        cap = next_level_cap(run, resolved_level_caps(run, profile))
        if cap:
            self._set_cap_completed(cap.cap_id, True)

    def _refresh_cap_panel(self) -> None:
        run, profile = self.store.active_run, self._active_profile()
        if not run or not profile:
            return
        caps = resolved_level_caps(run, profile)
        cap = next_level_cap(run, caps)
        summary = summarize_run(run, caps)
        self.cap_progress_label.setText(
            f"{summary.completed_fights} / {summary.total_fights} major fights completed"
        )
        if cap:
            self.next_cap_label.setText(f"{cap.name}  ·  Lv. {cap.level_cap}")
            if cap.healing_item_count is None:
                self.healing_item_hint_label.setText("Opponent healing items: unknown")
            else:
                breakdown = ", ".join(
                    f"{name} x{count}" for name, count in (cap.healing_items or ())
                )
                hint = (
                    f"Opponent healing items: {cap.healing_item_count}"
                    if cap.healing_item_count
                    else "Opponent healing items: none"
                )
                self.healing_item_hint_label.setText(
                    f"{hint} ({breakdown})" if breakdown else hint
                )
            self.complete_cap_button.setEnabled(True)
        else:
            self.next_cap_label.setText("All listed fights completed")
            self.healing_item_hint_label.clear()
            self.complete_cap_button.setEnabled(False)
        over = over_cap_party_members(self.party_levels, cap.level_cap if cap else None)
        if over:
            labels = [
                f"{member.nickname or member.species} ({member.species}) Lv. {member.level}"
                if member.nickname and member.nickname.casefold() != member.species.casefold()
                else f"{member.species} Lv. {member.level}"
                for member in over
            ]
            self.party_warning_label.setText(
                f"Over current cap ({cap.level_cap}): " + ", ".join(labels)
            )
        else:
            self.party_warning_label.setText("No party members are above the current cap.")
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
                completed=self.run_completed_input.isChecked(),
            )
        except OSError as error:
            self._show_error("Could not save run details", error)

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
