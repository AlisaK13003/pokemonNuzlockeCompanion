"""Trainer Lab run workspace composition, separate from store/action handlers."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.nuzlocke_panels import RunColumns, RunCount, RunPanel, run_label


class RunPages(QStackedWidget):
    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()

    def __init__(self, navigation: QTabBar, parent=None) -> None:
        super().__init__(parent)
        self.navigation = navigation
        navigation.currentChanged.connect(self.setCurrentIndex)
        self.currentChanged.connect(navigation.setCurrentIndex)

    def addTab(self, page: QWidget, title: str) -> int:
        index = self.addWidget(page)
        self.navigation.addTab(title)
        return index

    def tabText(self, index: int) -> str:
        return self.navigation.tabText(index)


def _button(text, callback, role=None):
    button = QPushButton(text)
    if role:
        button.setProperty("buttonRole", role)
    button.clicked.connect(callback)
    return button


def build_run_workspace(view) -> None:
    view._responsive_rows = []
    root = QVBoxLayout(view)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(12)
    view.navigation = QTabBar()
    view.navigation.setObjectName("runNavigation")
    view.navigation.setExpanding(False)
    view.navigation.setUsesScrollButtons(True)
    root.addWidget(view.navigation)

    view.run_header = RunPanel("Active run")
    view.run_header.heading.hide()
    header = view.run_header.content
    view.run_title_label = run_label("", "pageTitle")
    view.run_metadata_label = run_label()
    identity = QVBoxLayout()
    identity.addWidget(view.run_title_label)
    identity.addWidget(view.run_metadata_label)
    view.header_row = QHBoxLayout()
    view.header_row.addLayout(identity, 1)
    header.addLayout(view.header_row)
    view.metric_strip = QFrame()
    counts = QHBoxLayout(view.metric_strip)
    counts.setContentsMargins(0, 0, 0, 0)
    view.run_metrics = {}
    for key, title in (("encounters", "Encounters"), ("caught", "Caught"),
                       ("deaths", "Deaths"), ("failed", "Failed")):
        metric = RunPanel(title)
        metric.setProperty("uiRole", "raisedPanel")
        label = RunCount("0")
        label.setProperty("uiRole", "metric")
        metric.content.addWidget(label)
        view.run_metrics[key] = label
        counts.addWidget(metric)
    view.header_row.addWidget(view.metric_strip, 1)
    # Retain a text summary for accessibility and existing consumers.
    view.summary_label = run_label()
    header.addWidget(view.summary_label)
    root.addWidget(view.run_header)

    view.empty_panel = RunPanel("Nuzlocke")
    view.empty_panel.content.addStretch(1)
    view.empty_label = run_label("No active Nuzlocke run", "pageTitle")
    view.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    view.empty_panel.content.addWidget(view.empty_label)
    detail = run_label("Create a run to track encounters, deaths, and major fights.")
    detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
    view.empty_panel.content.addWidget(detail)
    view.empty_panel.content.addWidget(
        _button("Create Run", view._create_run, "primary"),
        alignment=Qt.AlignmentFlag.AlignHCenter,
    )
    unavailable = run_label("A run is created only when you choose Create Run.")
    unavailable.setAlignment(Qt.AlignmentFlag.AlignCenter)
    view.empty_panel.content.addWidget(unavailable)
    view.empty_panel.content.addStretch(1)
    root.addWidget(view.empty_panel, 1)

    view.sections = RunPages(view.navigation)
    view.sections.setObjectName("nuzlockeSections")
    view.run_content = view.sections
    root.addWidget(view.sections, 1)

    dashboard = QWidget()
    dashboard_layout = QVBoxLayout(dashboard)
    dashboard_layout.setContentsMargins(0, 0, 0, 0)
    dashboard_layout.setSpacing(12)
    view._run_content_layout = dashboard_layout
    left, right = QWidget(), QWidget()
    left_layout, right_layout = QVBoxLayout(left), QVBoxLayout(right)
    for layout in (left_layout, right_layout):
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
    view.cap_group = RunPanel("Next fight")
    view.next_cap_label = run_label("No remaining fights", "pageTitle")
    view.next_cap_label.setProperty("nuzlockeRole", "capTitle")
    view.cap_progress_label = run_label("0 / 0 major fights completed")
    view.cap_category_label = run_label()
    view.cap_effective_label = run_label("—", "metric")
    view.cap_default_label = run_label()
    view.healing_item_hint_label = run_label()
    view.fight_note_label = run_label()
    view.party_readiness_label = run_label()
    view.party_detail_label = run_label()
    view.healing_item_hint_label.setToolTip(
        "The opposing trainer's configured healing-item inventory. The battle AI may not "
        "use every item. Held items and out-of-battle healing are not included."
    )
    for widget in (view.next_cap_label, view.cap_category_label,
                   view.cap_effective_label, view.cap_default_label,
                   view.cap_progress_label, view.healing_item_hint_label,
                   view.party_readiness_label, view.party_detail_label,
                   view.fight_note_label):
        view.cap_group.content.addWidget(widget)
    view.edit_caps_button = _button("Edit Fight", view._edit_next_fight)
    view.complete_cap_button = _button("Mark Fight Complete", view._complete_next_cap, "primary")
    view.mark_won_button = _button("Mark Run Won", view._mark_run_won, "primary")
    view.mark_won_button.hide()
    cap_actions = QHBoxLayout()
    view._responsive_rows.append(cap_actions)
    cap_actions.addWidget(view.complete_cap_button)
    cap_actions.addWidget(view.edit_caps_button)
    cap_actions.addWidget(_button("View Fights", lambda: view.show_section("Fights")))
    cap_actions.addStretch(1)
    view.cap_group.content.addLayout(cap_actions)
    view.cap_group.content.addWidget(view.mark_won_button)
    left_layout.addWidget(view.cap_group)

    preview = RunPanel("Encounter preview")
    preview.content.addWidget(
        _button("View All Encounters", lambda: view.show_section("Encounters")),
        alignment=Qt.AlignmentFlag.AlignRight,
    )
    view.encounter_preview = QTableWidget(0, 4)
    view.encounter_preview.setHorizontalHeaderLabels(("Location", "Status", "Pokémon", "Level"))
    view.encounter_preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    view.encounter_preview.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    view.encounter_preview.verticalHeader().hide()
    view._configure_table(view.encounter_preview)
    view.encounter_preview.setMinimumHeight(200)
    view.encounter_preview.setMaximumHeight(266)
    view.encounter_preview.setColumnWidth(0, 150)
    view.encounter_preview.setColumnWidth(1, 108)
    view.encounter_preview.setColumnWidth(2, 145)
    view.encounter_preview.horizontalHeader().setStretchLastSection(True)
    view.encounter_preview.doubleClicked.connect(view._open_preview_encounter)
    preview.content.addWidget(view.encounter_preview)
    view.preview_empty_label = run_label("No encounters recorded. Open the ledger to add one.")
    preview.content.addWidget(view.preview_empty_label)
    left_layout.addWidget(preview)

    warnings = RunPanel("Warnings")
    view.party_warning_label = run_label()
    view.party_warning_label.setProperty("nuzlockeRole", "warning")
    view.pending_warning_label = run_label()
    warnings.content.addWidget(view.party_warning_label)
    warnings.content.addWidget(view.pending_warning_label)
    view.review_pending_button = _button("Review Encounters", lambda: view.show_section("Encounters"))
    warnings.content.addWidget(view.review_pending_button)
    right_layout.addWidget(warnings)
    events = RunPanel("Recent detection events")
    view.recent_events_label = run_label("No detection events recorded.")
    events.content.addWidget(view.recent_events_label)
    right_layout.addWidget(events)
    pc_panel = RunPanel("PC box monitor")
    view.pc_panel = pc_panel
    view.pc_monitor_label = run_label("PC monitor status unavailable.")
    view.detection_sources_label = run_label("Automatic encounter detection: Paused")
    pc_panel.content.addWidget(view.pc_monitor_label)
    pc_panel.content.addWidget(view.detection_sources_label)
    view.rediscover_pc_button = _button("Rediscover PC Layout", view.rediscover_pc_requested.emit)
    pc_panel.content.addWidget(view.rediscover_pc_button)
    right_layout.addWidget(pc_panel)
    notes = RunPanel("Run notes")
    view.run_notes_input = QTextEdit()
    view.run_notes_input.setMaximumHeight(80)
    view.run_notes_input.setPlaceholderText("Optional notes for this run")
    notes.content.addWidget(view.run_notes_input)
    view.save_run_details_button = _button("Save Notes", view._save_run_details)
    notes.content.addWidget(view.save_run_details_button, alignment=Qt.AlignmentFlag.AlignRight)
    right_layout.addWidget(notes)
    dashboard_layout.addWidget(RunColumns(left, right))
    dashboard_layout.addStretch(1)
    view.sections.addTab(dashboard, "Dashboard")

    _build_acquisition_panel(view)
    _build_death_notification(view)
    dashboard_layout.insertWidget(0, view.ram_death_group)
    view._build_encounters_tab()
    view._build_deaths_tab()
    view._build_caps_tab()
    _build_library(view)
    view.navigation.currentChanged.connect(view._update_section_visibility)


def _build_acquisition_panel(view) -> None:
    view.acquisition_group = RunPanel("New Pokémon")
    content = view.acquisition_group.content
    view.acquisition_status_label = run_label("Last acquisition candidate: --")
    view.auto_record_acquisitions = QCheckBox("Auto-record unambiguous encounters")
    view.auto_record_acquisitions.setMinimumWidth(0)
    view.auto_record_acquisitions.setToolTip(
        "Only unused, clearly mapped wild locations are recorded automatically."
    )
    view.auto_record_acquisitions.toggled.connect(view._set_auto_record_acquisitions)
    view.acquisition_detail_label = run_label("No new Pokémon detected.")
    candidate_row = QHBoxLayout()
    view.acquisition_sprite = QLabel()
    view.acquisition_sprite.setProperty("uiRole", "sprite")
    view.acquisition_sprite.setFixedSize(66, 66)
    view.acquisition_sprite.setAlignment(Qt.AlignmentFlag.AlignCenter)
    candidate_row.addWidget(view.acquisition_sprite)
    candidate_row.addWidget(view.acquisition_detail_label, 1)
    content.addLayout(candidate_row)
    view.acquisition_selector = QComboBox()
    view.acquisition_selector.setMinimumWidth(0)
    view.acquisition_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    view.acquisition_selector.currentIndexChanged.connect(view._select_acquisition)
    view.acquisition_location_selector = QComboBox()
    view.acquisition_location_selector.setMinimumWidth(0)
    view.acquisition_location_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    view.acquisition_location_selector.currentIndexChanged.connect(view._render_selected_acquisition)
    selection = QHBoxLayout()
    selection.addWidget(view.acquisition_selector, 1)
    selection.addWidget(view.acquisition_location_selector, 1)
    content.addLayout(selection)
    actions = QHBoxLayout()
    view._responsive_rows.append(actions)
    view.accept_acquisition_button = _button("Add", view._accept_selected_acquisition, "primary")
    view.replace_acquisition_button = _button("Replace Existing", view._replace_selected_acquisition, "danger")
    view.extra_acquisition_button = _button("Add as Extra", view._add_selected_acquisition_extra)
    view.ignore_acquisition_button = _button("Ignore", view._ignore_selected_acquisition)
    for button in (view.accept_acquisition_button, view.replace_acquisition_button,
                   view.extra_acquisition_button, view.ignore_acquisition_button):
        actions.addWidget(button)
    content.addLayout(actions)
    content.addWidget(view.acquisition_status_label)


def _build_death_notification(view) -> None:
    view.ram_death_group = RunPanel("Fainted Pokémon")
    view.ram_death_group.setObjectName("ramDeathPanel")
    view.ram_death_detail_label = run_label()
    view.ram_death_group.content.addWidget(view.ram_death_detail_label)
    view.ram_death_location_selector = QComboBox()
    view.ram_death_location_selector.setMinimumWidth(0)
    view.ram_death_location_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    view.ram_death_location_selector.currentIndexChanged.connect(view._update_ram_death_action_state)
    view.ram_death_group.content.addWidget(view.ram_death_location_selector)
    actions = QHBoxLayout()
    view._responsive_rows.append(actions)
    view.confirm_ram_death_button = _button("Mark Dead", view._confirm_ram_death, "danger")
    view.ignore_ram_death_button = _button("Ignore", view._ignore_ram_death)
    view.dismiss_ram_death_notice_button = _button("Dismiss", view._dismiss_ram_death_notice)
    for button in (view.confirm_ram_death_button, view.ignore_ram_death_button,
                   view.dismiss_ram_death_notice_button):
        actions.addWidget(button)
    view.ram_death_group.content.addLayout(actions)
    view.ram_death_group.hide()


def _build_library(view) -> None:
    page = RunPanel("Run library")
    controls = QHBoxLayout()
    view.run_selector = QComboBox()
    view.run_selector.setMinimumWidth(0)
    view.run_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    view.run_selector.currentIndexChanged.connect(view._switch_run)
    controls.addWidget(view.run_selector, 1)
    view.create_button = _button("Create Run", view._create_run, "primary")
    controls.addWidget(view.create_button)
    page.content.addLayout(controls)
    filters = QHBoxLayout()
    filters.addWidget(QLabel("Status"))
    view.run_status_filter = QComboBox()
    for status in ("All", "Active", "Won", "Wiped", "Abandoned"):
        view.run_status_filter.addItem(status, status.upper() if status != "All" else "")
    view.run_status_filter.currentIndexChanged.connect(view._render_run_library)
    filters.addWidget(view.run_status_filter)
    filters.addStretch(1)
    page.content.addLayout(filters)
    view.run_library_table = QTableWidget(0, 8)
    view.run_library_table.setHorizontalHeaderLabels((
        "Run", "Status", "Game", "Started", "Ended", "Caught", "Deaths", "Fights"))
    view.run_library_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    view.run_library_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    view.run_library_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    view.run_library_table.verticalHeader().hide()
    view._configure_table(view.run_library_table)
    view.run_library_table.setColumnWidth(0, 210)
    view.run_library_table.setColumnWidth(1, 100)
    view.run_library_table.setColumnWidth(2, 180)
    view.run_library_table.horizontalHeader().setStretchLastSection(True)
    view.run_library_table.doubleClicked.connect(view._activate_library_run)
    page.content.addWidget(view.run_library_table, 1)
    page.content.addWidget(run_label(
        "Select a run for actions. Double-click an ongoing run to track it. "
        "Historical runs remain in the library."))
    actions = QHBoxLayout()
    view._responsive_rows.append(actions)
    view.rename_button = _button("Rename Run", view._rename_run)
    view.open_button = _button("Open", view._open_selected_run)
    view.delete_button = _button("Delete Run", view._delete_run, "danger")
    view.set_active_button = _button("Set Active", view._set_selected_active)
    view.abandon_button = _button("Abandon Run", view._abandon_run, "danger")
    actions.addStretch(1)
    actions.addWidget(view.open_button)
    actions.addWidget(view.set_active_button)
    actions.addWidget(view.abandon_button)
    actions.addWidget(view.rename_button)
    actions.addWidget(view.delete_button)
    page.content.addLayout(actions)
    preferences = RunPanel("Nuzlocke preferences")
    wipe_row = QHBoxLayout()
    wipe_row.addWidget(QLabel("On party wipe"))
    view.wipe_action_input = QComboBox()
    for label in ("ASK ME", "END RUN AS WIPED", "IGNORE"):
        view.wipe_action_input.addItem(label.title(), label)
    view.wipe_action_input.currentIndexChanged.connect(view._save_lifecycle_preferences)
    wipe_row.addWidget(view.wipe_action_input, 1)
    preferences.content.addLayout(wipe_row)
    view.no_run_prompt_input = QCheckBox(
        "Prompt me to create a Nuzlocke run when playing without one")
    view.no_run_prompt_input.setChecked(True)
    view.no_run_prompt_input.toggled.connect(view._save_lifecycle_preferences)
    preferences.content.addWidget(view.no_run_prompt_input)
    page.content.addWidget(preferences)
    view.run_library_table.itemSelectionChanged.connect(view._update_library_actions)
    view.sections.addTab(page, "Run Library")
