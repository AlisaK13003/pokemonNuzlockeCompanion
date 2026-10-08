"""Settings and disconnected screens, wired to application-owned actions."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.disconnected_view import DisconnectedView
from pokemon_ev_tracker.ui.inspection_views import label
from pokemon_ev_tracker.ui.training_panels import line_icon

__all__ = ["DisconnectedView", "NotificationToggle", "PreferencesView"]


class NotificationToggle(QCheckBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#14221e" if self.isChecked() else "#131a24"))
        painter.setPen(QColor("#32654f" if self.isChecked() else "#354155"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.fillRect(24 if self.isChecked() else 4, 4, 15, 14,
                         QColor("#57e5a2" if self.isChecked() else "#5b6779"))
        if self.hasFocus():
            painter.setPen(QColor("#8fb8ff"))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


class PreferencesView(QWidget):
    backup_requested = Signal()
    changed = Signal(str, object)
    reset_requested = Signal()
    compact_requested = Signal()
    rediscover_requested = Signal()
    preview_requested = Signal(str)

    def __init__(self, settings, nuzlocke, provider, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.nuzlocke = nuzlocke
        self.inputs = {}
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.sidebar = QFrame()
        self.sidebar.setProperty("uiRole", "panel")
        self.sidebar.setFixedWidth(210)
        nav = QVBoxLayout(self.sidebar)
        nav.setContentsMargins(16, 16, 16, 16)
        nav.addWidget(label("SETTINGS", "technicalHeading"))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(14)
        self.sections = {}
        for key, title, detail in (
            ("window", "Window & appearance", "Control how the companion fits alongside your game."),
            ("automation", "Run automation", "Choose when reliable detections can act without asking."),
            ("notifications", "Notifications", "Choose which live events interrupt play."),
            ("connection", "Connection & recovery", "BizHawk connection and PC resolver behavior."),
            ("backups", "Save Backups", "Independent recovery history, retained for 7 days and at least 100 snapshots per game."),
            ("developer", "Developer", "Keep technical tools separate from normal gameplay."),
        ):
            panel = QFrame()
            panel.setProperty("uiRole", "panel")
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(20, 20, 20, 20)
            layout.setSpacing(18)
            if key == "notifications":
                heading = QWidget()
                heading.setObjectName("notificationSettingsHeader")
                heading_row = QHBoxLayout(heading)
                heading_row.setContentsMargins(0, 4, 0, 20)
                heading_row.setSpacing(16)
                icon = QLabel()
                icon.setFixedSize(42, 42)
                icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                icon.setPixmap(line_icon("health", "#9cb6df", 20).pixmap(QSize(20, 20)))
                icon.setStyleSheet("background: #172132; border: 1px solid #34445d;")
                heading_row.addWidget(icon)
                copy = QVBoxLayout()
                copy.setSpacing(5)
                copy.addWidget(label(title, "ledgerTitle"))
                copy.addWidget(label("Decide which live events are allowed to interrupt play.", "runHint"))
                heading_row.addLayout(copy, 1)
                layout.addWidget(heading)
            else:
                layout.addWidget(label(title, "pokemonName"))
                layout.addWidget(label(detail, "micro"))
            self.sections[key] = panel
            self.body.addWidget(panel)
            button = QPushButton(title)
            button.setProperty("buttonRole", "navigation")
            button.clicked.connect(lambda checked=False, widget=panel: self.scroll.ensureWidgetVisible(widget))
            nav.addWidget(button)
        nav.addWidget(label(f"ACTIVE SKIN\n{provider.display_name}\nAutomatic", "small"))
        nav.addStretch(1)
        root.addWidget(self.sidebar, 0, Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)
        for key, caption, description in (
            ("always_on_top", "Always on top", "Keep the companion above the emulator window."),
            ("remember_geometry", "Remember window geometry", "Restore size and screen position between sessions."),
            ("launch_compact", "Launch in Compact Mode", "Open the battle view when live data is available."),
        ):
            self._check("window", key, caption, description)
        compact = QPushButton("Open Compact Mode")
        compact.clicked.connect(self.compact_requested)
        self.compact_button = compact
        self.sections["window"].layout().addWidget(compact)
        self._combo("window", "tracker_view", "Tracker default", (("Training", "training"), ("Party Stats", "stats"), ("Box", "box")))
        self.sections["window"].layout().addWidget(label("GAME SKIN · Automatic, selected by the active game provider."))
        self._combo("automation", "encounter_action", "New encounter behavior", (("Ask me", "ASK ME"), ("Automatic", "AUTOMATIC"), ("Ignore", "IGNORE")))
        for key, caption in (("auto_record", "Auto-record unambiguous encounters"), ("auto_deaths", "Auto-confirm matched deaths")):
            check = QCheckBox(caption)
            check.toggled.connect(lambda checked, key=key: self.changed.emit(key, checked))
            self.inputs[key] = check
            self.sections["automation"].layout().addWidget(check)
        self._combo("automation", "nuzlocke_wipe_action", "Party wipe behavior", (("Ask me", "ASK ME"), ("Automatic", "END RUN AS WIPED"), ("Ignore", "IGNORE")))
        self.notification_grid = QGridLayout()
        self.notification_grid.setHorizontalSpacing(26)
        self.notification_grid.setVerticalSpacing(0)
        self.notification_rows = []
        self.sections["notifications"].layout().addLayout(self.notification_grid)
        for index, (key, caption, detail) in enumerate((
            ("notify_connection", "Connection changes", "Lost and restored connections."),
            ("notify_encounters", "New encounters", "Party and PC acquisitions requiring review."),
            ("notify_faints", "Faints and wipes", "Potential deaths and validated party wipes."),
            ("notify_friendship", "Friendship goals", "Notify when the selected target is reached."),
        )):
            row = QWidget()
            row.setObjectName("notificationSetting")
            contents = QHBoxLayout(row)
            contents.setContentsMargins(0, 16, 0, 22)
            copy = QVBoxLayout()
            copy.setSpacing(5)
            copy.addWidget(label(caption, "notificationSettingTitle"))
            copy.addWidget(label(detail, "runHint"))
            contents.addLayout(copy, 1)
            check = NotificationToggle()
            check.setAccessibleName(caption)
            check.toggled.connect(lambda checked, key=key: self.changed.emit(key, checked))
            contents.addWidget(check)
            self.inputs[key] = check
            self.notification_rows.append(row)
            self.notification_grid.addWidget(row, index // 2, index % 2)
        preview = QWidget()
        preview.setObjectName("notificationPreviews")
        self.preview_layout = preview_row = QBoxLayout(QBoxLayout.Direction.LeftToRight, preview)
        preview_row.setContentsMargins(0, 16, 0, 0)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        copy.addWidget(label("Preview notifications", "readoutName"))
        copy.addWidget(label("See how each event appears without waiting for emulator activity.", "runHint"))
        preview_row.addLayout(copy, 1)
        self.preview_buttons = {}
        actions = QHBoxLayout()
        actions.setSpacing(6)
        for kind, title in (("connection", "Connection"), ("encounter", "Encounter"),
                            ("shiny", "Shiny"), ("friendship", "Friendship"), ("faint", "Faint")):
            button = QPushButton(title)
            button.setProperty("buttonRole", "notificationPreview")
            button.setFixedHeight(34)
            button.clicked.connect(lambda checked=False, kind=kind: self.preview_requested.emit(kind))
            self.preview_buttons[kind] = button
            actions.addWidget(button)
        preview_row.addLayout(actions)
        self.sections["notifications"].layout().addWidget(preview)
        self.connection = label("Waiting for BizHawk")
        self.sections["connection"].layout().addWidget(self.connection)
        self.sections["connection"].layout().addWidget(label("Automatic reconnection is managed by the local transport."))
        self._check("connection", "auto_pc_rediscovery", "Rediscover PC layout automatically", "Recover safely when the current session layout becomes invalid.")
        rediscover = QPushButton("Rediscover PC Layout")
        rediscover.clicked.connect(self.rediscover_requested)
        self.rediscover_button = rediscover
        rediscover.setVisible(provider.capabilities.pc_storage)
        self.sections["connection"].layout().addWidget(rediscover)
        backups = self.sections["backups"].layout()
        backups.addWidget(label("Automatic backups: Enabled · Interval: Every 5 minutes"))
        for key, caption in (("bizhawk_save_ram_directory", "BizHawk SaveRAM directory"),
                             ("bizhawk_state_directory", "BizHawk State directory"),
                             ("bizhawk_save_name", "Save name (optional, without .SaveRAM)")):
            backups.addWidget(label(caption))
            row = QHBoxLayout()
            edit = QLineEdit()
            edit.setAccessibleName(caption)
            edit.editingFinished.connect(
                lambda key=key, edit=edit: self.changed.emit(key, edit.text().strip()))
            self.inputs[key] = edit
            row.addWidget(edit, 1)
            if key != "bizhawk_save_name":
                browse = QPushButton("Browse…")
                browse.clicked.connect(lambda checked=False, key=key, edit=edit:
                                       self._browse_backup_directory(key, edit))
                row.addWidget(browse)
            backups.addLayout(row)
        backups.addWidget(label("State directory defaults to the State folder beside SaveRAM. "
                                "Select a save name when multiple games are present.", "micro"))
        self.backup_status = label("Backup directory: —\nLast successful backup: Never\nBackups retained: 0")
        self.backup_status.setWordWrap(True)
        self.backup_status.setTextFormat(Qt.TextFormat.PlainText)
        backups.addWidget(self.backup_status)
        self.backup_button = QPushButton("Back Up Now")
        self.backup_button.clicked.connect(self.backup_requested)
        backups.addWidget(self.backup_button)
        self._check("developer", "advanced_diagnostics", "Enable advanced diagnostics", "Show transport, resolver, RAM and coordinate tools.")
        reset = QPushButton("Reset companion preferences")
        reset.setProperty("buttonRole", "danger")
        reset.clicked.connect(self.reset_requested)
        self.sections["developer"].layout().addWidget(label("Restore defaults without deleting Nuzlocke runs."))
        self.sections["developer"].layout().addWidget(reset)
        self.body.addStretch(1)
        self.refresh()

    def _check(self, section, key, caption, detail):
        check = QCheckBox(caption)
        check.setToolTip(detail)
        check.toggled.connect(lambda checked: self.changed.emit(key, checked))
        self.inputs[key] = check
        self.sections[section].layout().addWidget(check)
        self.sections[section].layout().addWidget(label(detail, "micro"))

    def _combo(self, section, key, caption, values):
        row = QHBoxLayout()
        row.addWidget(label(caption), 1)
        combo = QComboBox()
        for title, value in values:
            combo.addItem(title, value)
        combo.currentIndexChanged.connect(lambda index: self.changed.emit(key, combo.currentData()))
        self.inputs[key] = combo
        row.addWidget(combo)
        self.sections[section].layout().addLayout(row)

    def refresh(self):
        run = self.nuzlocke.store.active_run
        for key, widget in self.inputs.items():
            value = (getattr(run, "auto_record_unambiguous_encounters", False) if key == "auto_record" else
                     getattr(run, "automatically_confirm_deaths", False) if key == "auto_deaths" else
                     getattr(self.settings, key))
            widget.blockSignals(True)
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(widget.findData(value))
            elif isinstance(widget, QLineEdit):
                widget.setText(value)
            else:
                widget.setChecked(value)
            widget.blockSignals(False)
            if key in {"auto_record", "auto_deaths"}:
                widget.setEnabled(run is not None)

    def _browse_backup_directory(self, key, edit):
        directory = QFileDialog.getExistingDirectory(self, edit.accessibleName(), edit.text())
        if directory:
            self.changed.emit(key, directory)
            self.refresh()

    def update_backup_status(self, status):
        self.backup_status.setText(
            f"Backup directory: {status['directory']}\n"
            f"Last successful backup: {status['last_backup']}\n"
            f"Backups retained: {status['count']}\n{status['message']}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sidebar.setVisible(self.width() >= 850)
        columns = 1 if self.width() < 700 else 2
        for index, row in enumerate(self.notification_rows):
            self.notification_grid.addWidget(row, index // columns, index % columns)
        self.preview_layout.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 1100
                                         else QBoxLayout.Direction.LeftToRight)

