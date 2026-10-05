"""Advanced diagnostic widget construction, separate from runtime/observer logic.

The owner retains the existing widget handles and callbacks. No RAM protocol,
resolver, baseline or acquisition behavior is implemented here.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.collapsible_section import CollapsibleSection


class _NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


def build_advanced_diagnostics(self):
    debug_page = QWidget()
    debug_page.setObjectName("ramDebugPage")
    debug_layout = QVBoxLayout(debug_page)
    debug_layout.setContentsMargins(8, 8, 8, 8)
    self.ram_backend_labels = {}
    status_grid = QVBoxLayout()
    self.available_domains_label = QLabel("Available domains: --")
    self.available_domains_label.setWordWrap(True)
    self.ram_backend_labels["available_domains"] = self.available_domains_label
    self.ram_backend_labels.update(self.tracker_status_labels)
    for key, title in (
        ("backend", "Backend"),
        ("connection", "Connection"),
    ):
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        row.addWidget(self.tracker_status_labels[key], 1)
        status_grid.addLayout(row)
    status_grid.addWidget(self.available_domains_label)
    debug_layout.addLayout(status_grid)
    self.capability_status_label = QLabel()
    self.capability_status_label.setWordWrap(True)
    debug_layout.addWidget(self.capability_status_label)
    self.backend_details_section = CollapsibleSection(
        "Backend details", self.backend_status.details_widget, expanded=False
    )
    debug_layout.addWidget(self.backend_details_section)
    build_coordinate_discovery(self, debug_layout, debug_page)
    self.pc_storage_summary_label = QLabel("Waiting for PC storage scan.")
    self.pc_storage_summary_label.setWordWrap(True)
    self.pc_storage_scan_button = QPushButton("Scan PC Now")
    self.pc_storage_scan_button.setToolTip(
        "Request an immediate PC-box RAM scan from the BizHawk Lua script."
    )
    self.pc_storage_scan_button.clicked.connect(self._request_pc_storage_scan)
    self.pc_storage_discovery_button = QPushButton("Discover PC Storage Address")
    self.pc_storage_discovery_button.setToolTip(
        "Run a one-shot Main RAM search for checksum-valid Gen IV boxed Pokemon."
    )
    self.pc_storage_discovery_button.clicked.connect(self._request_pc_storage_discovery)
    self.pc_storage_discovery_cancel_button = QPushButton("Cancel Discovery")
    self.pc_storage_discovery_cancel_button.setToolTip(
        "Stop the active incremental PC storage discovery scan."
    )
    self.pc_storage_discovery_cancel_button.setEnabled(False)
    self.pc_storage_discovery_cancel_button.clicked.connect(
        self._cancel_pc_storage_discovery
    )
    self.pc_storage_scan_status_label = QLabel("Automatic scan: every 60 frames")
    self.pc_storage_discovery_status_label = QLabel("idle")
    self.pc_storage_discovery_status_label.setWordWrap(True)
    self.pc_storage_discovery_status_label.setSizePolicy(
        QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
    )
    pc_storage_toolbar = QHBoxLayout()
    pc_storage_toolbar.addWidget(self.pc_storage_scan_button)
    pc_storage_toolbar.addWidget(self.pc_storage_discovery_button)
    pc_storage_toolbar.addWidget(self.pc_storage_discovery_cancel_button)
    pc_storage_toolbar.addWidget(QLabel("Discovery:"))
    pc_storage_toolbar.addWidget(self.pc_storage_discovery_status_label, 1)
    pc_storage_toolbar.addWidget(self.pc_storage_scan_status_label, 1)
    pc_save_test_toolbar = QHBoxLayout()
    pc_save_file_layout = QHBoxLayout()
    pc_save_file_layout.addWidget(QLabel("External SaveRAM fallback directory:"))
    self.pc_save_offset_directory_edit = QLineEdit(
        self._default_bizhawk_save_ram_directory()
    )
    self.pc_save_offset_directory_edit.setPlaceholderText(
        "Select BizHawk's NDS\\SaveRAM directory"
    )
    self.pc_save_offset_directory_edit.setToolTip(
        "Optional fallback only. The live SRAM-domain search does not read or depend on files in this directory."
    )
    pc_save_file_layout.addWidget(self.pc_save_offset_directory_edit, 1)
    self.pc_save_offset_directory_browse_button = QPushButton("Browse")
    self.pc_save_offset_directory_browse_button.setToolTip(
        "Select BizHawk's configured NDS SaveRAM directory."
    )
    self.pc_save_offset_directory_browse_button.clicked.connect(
        self._browse_pc_save_offset_directory
    )
    pc_save_file_layout.addWidget(self.pc_save_offset_directory_browse_button)
    self.pc_save_offset_test_button = QPushButton("Test Save-File PC Offsets (fallback)")
    self.pc_save_offset_test_button.setToolTip(
        "Optional fallback experiment using external SaveRAM files when the live SRAM domain is unavailable. "
        "It is separate from Search SRAM for Box Pokemon."
    )
    self.pc_save_offset_test_button.clicked.connect(
        self._request_pc_save_offset_test
    )
    pc_save_test_toolbar.addWidget(self.pc_save_offset_test_button)
    pc_save_test_toolbar.addWidget(QLabel("Save test:"))
    self.pc_save_offset_test_status_label = QLabel("idle")
    self.pc_save_offset_test_status_label.setWordWrap(True)
    self.pc_save_offset_test_status_label.setSizePolicy(
        QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
    )
    pc_save_test_toolbar.addWidget(self.pc_save_offset_test_status_label, 1)
    pc_sram_search_toolbar = QHBoxLayout()
    self.pc_sram_search_button = QPushButton("Search SRAM for Box Pokemon")
    self.pc_sram_search_button.setToolTip(
        "Incrementally read the live BizHawk SRAM domain, then search for the selected boxed Pokemon. "
        "Click again after moving it without saving in-game to compare captures."
    )
    self.pc_sram_search_button.clicked.connect(self._request_pc_sram_pokemon_search)
    pc_sram_search_toolbar.addWidget(self.pc_sram_search_button)
    self.pc_sram_reset_target_button = QPushButton("Reset A/B Target")
    self.pc_sram_reset_target_button.clicked.connect(self._reset_pc_sram_search_target)
    pc_sram_search_toolbar.addWidget(self.pc_sram_reset_target_button)
    pc_sram_search_toolbar.addWidget(QLabel("SRAM search:"))
    self.pc_sram_search_status_label = QLabel("idle")
    self.pc_sram_search_status_label.setWordWrap(True)
    self.pc_sram_search_status_label.setSizePolicy(
        QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
    )
    pc_sram_search_toolbar.addWidget(self.pc_sram_search_status_label, 1)
    pc_storage_session_toolbar = QHBoxLayout()
    pc_storage_session_toolbar.addWidget(QLabel("Box 1 Slot 1:"))
    self.pc_layout_nickname_edit = QLineEdit(self.settings.pc_box1_nickname)
    self.pc_layout_nickname_edit.setMaxLength(10)
    self.pc_layout_nickname_edit.setPlaceholderText("nickname (optional)")
    pc_storage_session_toolbar.addWidget(self.pc_layout_nickname_edit)
    self.pc_layout_species_combo = _NoWheelComboBox()
    for species in self.provider.species_catalog():
        self.pc_layout_species_combo.addItem(
            species.name, species.national_dex_number
        )
    self.pc_layout_species_combo.setCurrentIndex(
        max(0, self.pc_layout_species_combo.findData(self.settings.pc_box1_species_id))
    )
    pc_storage_session_toolbar.addWidget(self.pc_layout_species_combo)
    self.pc_discover_current_layout_button = QPushButton("Rediscover PC Layout")
    self.pc_discover_current_layout_button.clicked.connect(
        self._rediscover_pc_layout
    )
    self.pc_layout_retry_button = QPushButton("Retry Discovery")
    self.pc_layout_retry_button.clicked.connect(self._rediscover_pc_layout)
    pc_storage_session_toolbar.addWidget(self.pc_layout_retry_button)
    self.pc_storage_resolver_status_label = QLabel("PC resolver: unresolved")
    self.pc_storage_resolver_status_label.setWordWrap(True)
    self.pc_storage_resolver_address_label = QLabel("First record: --")
    pc_storage_baseline_row = QHBoxLayout()
    self.pc_storage_baseline_count_label = QLabel("Baseline occupied Pokemon: --")
    pc_storage_baseline_row.addWidget(self.pc_storage_baseline_count_label)
    self.pc_storage_monitoring_label = QLabel("Monitoring for new boxed Pokemon: no")
    pc_storage_baseline_row.addWidget(self.pc_storage_monitoring_label)
    pc_storage_baseline_row.addStretch(1)
    self.pc_storage_baseline_message_label = QLabel("")
    self.pc_storage_baseline_message_label.setWordWrap(True)
    self.pc_storage_cache_status_label = QLabel("Cache install: not sent")
    self.pc_storage_cache_status_label.setWordWrap(True)
    self.pc_storage_retry_cache_button = QPushButton("Retry Cache Install")
    self.pc_storage_retry_cache_button.setEnabled(False)
    self.pc_storage_retry_cache_button.hide()
    self.pc_storage_retry_cache_button.clicked.connect(self._retry_pc_storage_cache_install)
    pc_storage_anchor_toolbar = QHBoxLayout()
    pc_storage_anchor_toolbar.addWidget(QLabel("Pokemon RAM address:"))
    self.pc_storage_anchor_address_edit = QLineEdit()
    self.pc_storage_anchor_address_edit.setPlaceholderText("0x02200000")
    self.pc_storage_anchor_address_edit.setMaxLength(10)
    self.pc_storage_anchor_address_edit.setToolTip(
        "Session-specific address of a known Box 1 Slot 1 record, not the proposed "
        "0x28-byte structure base. Used for diagnosis only."
    )
    pc_storage_anchor_toolbar.addWidget(self.pc_storage_anchor_address_edit)
    self.pc_storage_anchor_button = QPushButton("Scan Anchor")
    self.pc_storage_anchor_button.setToolTip(
        "Evaluate 18 x 30 records from this address and inspect the preceding PCBoxes bytes."
    )
    self.pc_storage_anchor_button.clicked.connect(
        self._request_pc_storage_anchor_discovery
    )
    pc_storage_anchor_toolbar.addWidget(self.pc_storage_anchor_button)
    self.pc_storage_anchor_status_label = QLabel("idle")
    self.pc_storage_anchor_status_label.setWordWrap(True)
    pc_storage_anchor_toolbar.addWidget(self.pc_storage_anchor_status_label)
    self.pc_storage_pointer_search_button = QPushButton(
        "Search Structure Pointers"
    )
    self.pc_storage_pointer_search_button.setToolTip(
        "Incrementally search Main RAM for direct references to the proposed "
        "0x28-byte header and first BoxPokemon record."
    )
    self.pc_storage_pointer_search_button.clicked.connect(
        self._search_pc_structure_pointers
    )
    pc_storage_anchor_toolbar.addWidget(self.pc_storage_pointer_search_button)
    self.pc_storage_pointer_search_status_label = QLabel("idle")
    self.pc_storage_pointer_search_status_label.setWordWrap(True)
    pc_storage_anchor_toolbar.addWidget(
        self.pc_storage_pointer_search_status_label
    )
    self.pc_storage_inspect_button = QPushButton("Inspect Pokemon Anchor")
    self.pc_storage_inspect_button.setToolTip(
        "Incrementally inspect Main RAM around this record, scan aligned Pokemon records, "
        "and search for direct pointers and matching copies."
    )
    self.pc_storage_inspect_button.clicked.connect(
        self._inspect_pc_pokemon_anchor
    )
    self.pc_storage_search_button = QPushButton("Search RAM for This Pokemon")
    self.pc_storage_search_button.setToolTip(
        "Use the last inspected Pokemon's PID and checksum to find matching records; "
        "only matching records are returned."
    )
    self.pc_storage_search_button.clicked.connect(self._search_pc_pokemon_identity)
    self.pc_storage_inspection_status_label = QLabel("idle")
    self.pc_storage_inspection_status_label.setWordWrap(True)
    self.pc_storage_inspection_reset_button = QPushButton(
        "Reset Movement Baseline"
    )
    self.pc_storage_inspection_reset_button.setToolTip(
        "Clear the saved Pokemon identity and address baseline; inspect the anchor again to recapture it."
    )
    self.pc_storage_inspection_reset_button.clicked.connect(
        self._reset_pc_pokemon_inspection_baseline
    )
    pc_storage_anchor_toolbar.addStretch(1)
    pc_storage_inspection_toolbar = QHBoxLayout()
    pc_storage_inspection_toolbar.addWidget(self.pc_storage_inspect_button)
    pc_storage_inspection_toolbar.addWidget(self.pc_storage_search_button)
    pc_storage_inspection_toolbar.addWidget(
        self.pc_storage_inspection_status_label, 1
    )
    pc_storage_inspection_toolbar.addWidget(
        self.pc_storage_inspection_reset_button
    )
    self.pc_storage_details = QPlainTextEdit()
    self.pc_storage_details.setReadOnly(True)
    self.pc_storage_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    self.pc_storage_details.setPlaceholderText("Waiting for PC storage RAM payload.")
    self.pc_storage_details.setMinimumHeight(110)
    self.pc_storage_discovery_details = QPlainTextEdit()
    self.pc_storage_discovery_details.setReadOnly(True)
    self.pc_storage_discovery_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    self.pc_storage_discovery_details.setMinimumHeight(180)
    self.pc_storage_discovery_details.setPlaceholderText(
        "Click Discover PC Storage Address to run a one-shot Main RAM search."
    )
    self.pc_storage_discovery_details.setPlainText(self._last_pc_storage_discovery_text)
    self.pc_save_offset_test_details = QPlainTextEdit()
    self.pc_save_offset_test_details.setReadOnly(True)
    self.pc_save_offset_test_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    self.pc_save_offset_test_details.setMinimumHeight(120)
    self.pc_save_offset_test_details.setPlaceholderText(
        "Capture once, move a boxed Pokemon without saving, then capture again."
    )
    self.pc_save_offset_test_details.setPlainText(
        self._last_pc_save_offset_test_text
    )
    self.pc_sram_search_details = QPlainTextEdit()
    self.pc_sram_search_details.setReadOnly(True)
    self.pc_sram_search_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    self.pc_sram_search_details.setMinimumHeight(180)
    self.pc_sram_search_details.setPlaceholderText(
        "Live SRAM search results, matching records, neighboring slots, and movement between captures."
    )
    self.pc_sram_search_details.setPlainText(self._last_pc_sram_search_text)
    pc_storage_discovery_pane = QWidget()
    pc_storage_discovery_pane.setSizePolicy(
        QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
    )
    pc_storage_discovery_layout = QVBoxLayout(pc_storage_discovery_pane)
    pc_storage_discovery_layout.setContentsMargins(0, 0, 0, 0)
    pc_storage_discovery_heading = QHBoxLayout()
    pc_storage_discovery_heading.addWidget(QLabel("PC Storage Discovery"), 1)
    self.pc_storage_discovery_expand_button = QPushButton("Expand")
    self.pc_storage_discovery_expand_button.setObjectName(
        "pcStorageDiscoveryExpandButton"
    )
    self.pc_storage_discovery_expand_button.setCheckable(True)
    self.pc_storage_discovery_expand_button.setToolTip(
        "Give the discovery results more vertical space; drag the divider to resize manually."
    )
    self.pc_storage_discovery_expand_button.toggled.connect(
        self._set_pc_storage_discovery_expanded
    )
    pc_storage_discovery_heading.addWidget(
        self.pc_storage_discovery_expand_button
    )
    pc_storage_discovery_layout.addLayout(pc_storage_discovery_heading)
    pc_storage_discovery_layout.addWidget(self.pc_storage_discovery_details, 1)
    self.pc_storage_panes_splitter = QSplitter(Qt.Orientation.Vertical)
    self.pc_storage_panes_splitter.setObjectName("pcStoragePanesSplitter")
    self.pc_storage_panes_splitter.setChildrenCollapsible(False)
    self.pc_storage_panes_splitter.setHandleWidth(8)
    self.pc_storage_panes_splitter.addWidget(self.pc_storage_details)
    self.pc_storage_panes_splitter.addWidget(pc_storage_discovery_pane)
    self.pc_storage_panes_splitter.addWidget(self.pc_save_offset_test_details)
    self.pc_storage_panes_splitter.addWidget(self.pc_sram_search_details)
    self.pc_storage_panes_splitter.setStretchFactor(0, 1)
    self.pc_storage_panes_splitter.setStretchFactor(1, 2)
    self.pc_storage_panes_splitter.setStretchFactor(2, 1)
    self.pc_storage_panes_splitter.setStretchFactor(3, 2)
    self.pc_storage_panes_splitter.setSizes([1, 2, 1, 2])
    pc_storage_content = QWidget(debug_page)
    pc_storage_layout = QVBoxLayout(pc_storage_content)
    pc_storage_layout.setContentsMargins(0, 0, 0, 0)
    self.pc_storage_status_label = QLabel("Status: Connecting")
    self.pc_storage_layout_label = QLabel("Box layout: Unresolved")
    self.pc_storage_occupied_label = QLabel("Boxed Pokemon: --")
    self.pc_storage_catch_status_label = QLabel("Monitoring new catches: No")
    self.pc_storage_progress_label = QLabel("")
    self.pc_storage_last_event_label = QLabel("Last PC event: No PC events yet.")
    self.pc_storage_recovery_label = QLabel("")
    for label in (
        self.pc_storage_status_label, self.pc_storage_layout_label,
        self.pc_storage_occupied_label, self.pc_storage_catch_status_label,
        self.pc_storage_progress_label, self.pc_storage_last_event_label,
        self.pc_storage_recovery_label,
    ):
        label.setWordWrap(True)
        pc_storage_layout.addWidget(label)
    normal_actions = QHBoxLayout()
    normal_actions.addWidget(self.pc_discover_current_layout_button)
    normal_actions.addStretch(1)
    pc_storage_layout.addLayout(normal_actions)
    self.pc_storage_anchor_recovery = QWidget(pc_storage_content)
    recovery_layout = QVBoxLayout(self.pc_storage_anchor_recovery)
    recovery_layout.setContentsMargins(0, 0, 0, 0)
    recovery_layout.addLayout(pc_storage_session_toolbar)
    pc_storage_layout.addWidget(self.pc_storage_anchor_recovery)
    self.pc_storage_anchor_recovery.hide()

    advanced_content = QWidget(pc_storage_content)
    advanced_layout = QVBoxLayout(advanced_content)
    advanced_layout.setContentsMargins(0, 0, 0, 0)
    self.pc_storage_advanced_summary_label = QLabel("Resolver: --\nAcquisition: --\nDiscovery anchor: --")
    self.pc_storage_advanced_summary_label.setWordWrap(True)
    advanced_layout.addWidget(self.pc_storage_advanced_summary_label)
    advanced_layout.addWidget(self.pc_storage_resolver_status_label)
    advanced_layout.addWidget(self.pc_storage_resolver_address_label)
    advanced_layout.addLayout(pc_storage_baseline_row)
    advanced_layout.addWidget(self.pc_storage_baseline_message_label)
    pc_storage_cache_row = QHBoxLayout()
    pc_storage_cache_row.addWidget(self.pc_storage_cache_status_label, 1)
    pc_storage_cache_row.addWidget(self.pc_storage_retry_cache_button)
    advanced_layout.addLayout(pc_storage_cache_row)
    legacy_content = QWidget(advanced_content)
    legacy_layout = QVBoxLayout(legacy_content)
    legacy_layout.setContentsMargins(0, 0, 0, 0)
    legacy_layout.addWidget(self.pc_storage_summary_label)
    legacy_layout.addLayout(pc_storage_toolbar)
    legacy_layout.addLayout(pc_sram_search_toolbar)
    legacy_layout.addLayout(pc_save_file_layout)
    legacy_layout.addLayout(pc_save_test_toolbar)
    legacy_layout.addLayout(pc_storage_anchor_toolbar)
    legacy_layout.addLayout(pc_storage_inspection_toolbar)
    self.pc_storage_legacy_section = CollapsibleSection(
        "Legacy diagnostics", legacy_content, expanded=False
    )
    advanced_layout.addWidget(self.pc_storage_legacy_section)
    self.pc_storage_raw_section = CollapsibleSection(
        "Show raw debug output", self.pc_storage_panes_splitter, expanded=False
    )
    advanced_layout.addWidget(self.pc_storage_raw_section)
    self.pc_storage_advanced_section = CollapsibleSection(
        "Advanced Diagnostics", advanced_content, expanded=False
    )
    pc_storage_layout.addWidget(self.pc_storage_advanced_section)
    self.pc_storage_section = CollapsibleSection(
        "PC Storage", pc_storage_content, expanded=True
    )
    debug_layout.addWidget(self.pc_storage_section)
    self.ram_party_summary_label = QLabel("Party count: --")
    self.ram_party_details = QPlainTextEdit()
    self.ram_party_details.setReadOnly(True)
    self.ram_party_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    self.ram_party_details.setPlaceholderText("Waiting for party memory payload.")
    party_debug_content = QWidget(debug_page)
    party_debug_layout = QVBoxLayout(party_debug_content)
    party_debug_layout.setContentsMargins(0, 0, 0, 0)
    party_debug_layout.addWidget(self.ram_party_summary_label)
    party_debug_layout.addWidget(self.ram_party_details, 1)
    self.ram_party_section = CollapsibleSection(
        "Party snapshot", party_debug_content, expanded=True
    )
    debug_layout.addWidget(self.ram_party_section, 1)
    return debug_page


def build_coordinate_discovery(self, parent_layout, parent) -> None:
    group = QGroupBox("Advanced Coordinate Discovery", parent)
    group.setCheckable(True)
    group.setChecked(False)
    self.coordinate_discovery_group = group
    layout = QVBoxLayout(group)
    contents = QWidget(group)
    contents.setVisible(False)
    self.coordinate_discovery_contents = contents
    contents_layout = QVBoxLayout(contents)
    contents_layout.setContentsMargins(0, 0, 0, 0)
    group.toggled.connect(contents.setVisible)
    range_row = QHBoxLayout()
    range_row.addWidget(QLabel("Main RAM offset"))
    self.coordinate_scan_start = QLineEdit("0x00000000")
    self.coordinate_scan_start.setMaximumWidth(120)
    self.coordinate_scan_start.setToolTip("Byte offset within BizHawk's Main RAM domain")
    range_row.addWidget(self.coordinate_scan_start)
    range_row.addWidget(QLabel("Length"))
    self.coordinate_scan_length = QLineEdit("0x00400000")
    self.coordinate_scan_length.setMaximumWidth(120)
    self.coordinate_scan_length.setToolTip("Even byte length, up to 4 MiB")
    range_row.addWidget(self.coordinate_scan_length)
    range_row.addStretch(1)
    contents_layout.addLayout(range_row)

    capture_row = QHBoxLayout()
    self.coordinate_capture_buttons = {}
    for label, title in (
        ("baseline", "Baseline + Idle"),
        ("right", "Right"),
        ("left", "Left"),
        ("down", "Down"),
        ("up", "Up"),
    ):
        button = QPushButton(title)
        button.setToolTip(
            "Capture Baseline + Idle Check" if label == "baseline" else f"Capture {title}"
        )
        button.clicked.connect(
            lambda _checked=False, capture_label=label: self._start_coordinate_capture(
                capture_label
            )
        )
        self.coordinate_capture_buttons[label] = button
        capture_row.addWidget(button)
    contents_layout.addLayout(capture_row)

    result_row = QHBoxLayout()
    result_row.addWidget(QLabel("Top coordinate pairs"))
    self.coordinate_candidate_combo = QComboBox()
    self.coordinate_candidate_combo.addItem("Capture all six samples first", None)
    self.coordinate_candidate_combo.currentIndexChanged.connect(
        self._select_coordinate_candidate
    )
    result_row.addWidget(self.coordinate_candidate_combo, 1)
    contents_layout.addLayout(result_row)

    self.coordinate_discovery_status = QLabel(
        "Validated player coordinates are read at Main RAM offsets "
        "0x001C5AFE / 0x001C5B02. This advanced scanner remains available for diagnostics."
    )
    self.coordinate_discovery_status.setWordWrap(True)
    contents_layout.addWidget(self.coordinate_discovery_status)
    self.coordinate_candidate_details = QLabel("No coordinate pair selected.")
    self.coordinate_candidate_details.setWordWrap(True)
    contents_layout.addWidget(self.coordinate_candidate_details)
    self.coordinate_live_preview = QLabel("Live X: --    Live Y: --")
    contents_layout.addWidget(self.coordinate_live_preview)
    layout.addWidget(contents)
    parent_layout.addWidget(group)
