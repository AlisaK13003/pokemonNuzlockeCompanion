"""Trainer Lab navigation and compact workspace chrome; no runtime logic."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class AppShell(QWidget):
    route_requested = Signal(str)
    compact_requested = Signal(bool)
    notification_requested = Signal()

    def __init__(self, game_name, backend_status, parent=None, *, generation="", emulator=""):
        super().__init__(parent)
        self.setObjectName("appRoot")
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)
        self.header = QFrame()
        self.header.setObjectName("applicationToolbar")
        header = QHBoxLayout(self.header)
        self.header_layout = header
        header.setContentsMargins(16, 12, 16, 12)
        self.brand_icon = QSvgWidget(str(Path(__file__).resolve().parents[1] / "assets/ui/terminal_mark.svg"))
        self.brand_icon.setFixedSize(24, 24)
        header.addWidget(self.brand_icon)
        self.brand = QLabel("POKÉMON COMPANION")
        self.brand.setObjectName("applicationTitle")
        header.addWidget(self.brand)
        self.game = QLabel(" · ".join(part for part in (game_name, generation, emulator) if part))
        self.game.setProperty("uiRole", "muted")
        header.addWidget(self.game, 1)
        header.addWidget(backend_status)
        self.compact_button = QPushButton("COMPACT MODE")
        self.compact_button.setCheckable(True)
        self.compact_button.setProperty("buttonRole", "compactToggle")
        self.compact_button.setToolTip("Compact always-on-top workspace · Ctrl+Shift+C")
        self.compact_button.toggled.connect(self.compact_requested)
        header.addWidget(self.compact_button)
        self.root_layout.addWidget(self.header)
        self.compact_switcher = QFrame()
        self.compact_switcher.setObjectName("compactSwitcher")
        compact_row = QHBoxLayout(self.compact_switcher)
        compact_row.setContentsMargins(6, 5, 6, 5)
        compact_row.setSpacing(4)
        self.compact_buttons = {}
        group = QButtonGroup(self)
        for route, title in (("training", "TRAINING"), ("stats", "PARTY STATS"), ("nuzlocke", "NUZLOCKE")):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setProperty("buttonRole", "segmented")
            button.setProperty("inspection", route == "stats")
            group.addButton(button)
            compact_row.addWidget(button, 1)
            button.clicked.connect(lambda checked=False, key=route: self.route_requested.emit(key))
            self.compact_buttons[route] = button
        self.root_layout.addWidget(self.compact_switcher)
        self.notification = QFrame()
        self.notification.setProperty("uiRole", "panel")
        notice = QHBoxLayout(self.notification)
        notice.setContentsMargins(10, 5, 10, 5)
        self.notification_text = QLabel()
        self.notification_text.setProperty("statusRole", "warning")
        self.notification_text.setWordWrap(True)
        notice.addWidget(self.notification_text, 1)
        self.review_notification = QPushButton("Review")
        self.review_notification.clicked.connect(self.notification_requested)
        notice.addWidget(self.review_notification)
        self.notification.hide()
        self.root_layout.addWidget(self.notification)
        body = QHBoxLayout()
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(12)
        self.body_layout = body
        self.rail = QFrame()
        self.rail.setObjectName("navigationRail")
        self.rail.setFixedWidth(168)
        rail = QVBoxLayout(self.rail)
        rail.setContentsMargins(12, 14, 12, 14)
        rail.setSpacing(7)
        self.nav_buttons = {}
        nav_group = QButtonGroup(self)
        for heading, routes in (("TRACKER", (("training", "Training"), ("stats", "Party Stats"))),
                                ("RUN", (("nuzlocke", "Nuzlocke"), ("diagnostics", "Diagnostics")))):
            label = QLabel(heading)
            label.setProperty("uiRole", "micro")
            rail.addWidget(label)
            for route, title in routes:
                button = QPushButton(title)
                button.setCheckable(True)
                button.setProperty("buttonRole", "navigation")
                button.setProperty("inspection", route in {"stats", "diagnostics"})
                button.setMinimumHeight(38)
                button.clicked.connect(lambda checked=False, key=route: self.route_requested.emit(key))
                nav_group.addButton(button)
                self.nav_buttons[route] = button
                rail.addWidget(button)
            rail.addSpacing(14)
        rail.addStretch(1)
        self.system_label = QLabel("EMULATOR LINK\n\nWaiting for connection")
        self.system_label.setProperty("uiRole", "systemSummary")
        self.system_label.setWordWrap(True)
        rail.addWidget(self.system_label)
        body.addWidget(self.rail)
        self.workspace = QStackedWidget()
        self.workspace.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        body.addWidget(self.workspace, 1)
        self.root_layout.addLayout(body, 1)
        self.pages = {}
        self._compact = False
        self.set_compact(False)

    def add_page(self, key, widget):
        self.pages[key] = widget
        self.workspace.addWidget(widget)

    def show_route(self, route):
        key = f"compact_{route}" if self._compact else route
        self.workspace.setCurrentWidget(self.pages[key])
        for buttons in (self.nav_buttons, self.compact_buttons):
            for name, button in buttons.items():
                button.setChecked(name == route)

    def set_compact(self, enabled):
        self._compact = enabled
        self.rail.setVisible(not enabled)
        self.compact_switcher.setVisible(enabled)
        self.brand.setVisible(not enabled)
        self.brand_icon.setVisible(not enabled)
        self.game.setVisible(not enabled and self.width() >= 900)
        self.compact_button.blockSignals(True)
        self.compact_button.setChecked(enabled)
        self.compact_button.setText("EXPAND" if enabled else "COMPACT MODE")
        self.compact_button.blockSignals(False)
        margin = 5 if enabled else 12
        self.body_layout.setContentsMargins(margin, margin, margin, margin)
        self._layout_chrome()

    def _layout_chrome(self):
        if self._compact and self.width() >= 600:
            if self.compact_switcher.parentWidget() is not self.header:
                self.header_layout.insertWidget(0, self.compact_switcher, 1)
        elif self.compact_switcher.parentWidget() is self.header:
            self.root_layout.insertWidget(1, self.compact_switcher)
        self.compact_switcher.setVisible(self._compact)
        self.header_layout.setContentsMargins(6, 5, 6, 5) if self._compact else self.header_layout.setContentsMargins(16, 12, 16, 12)

    def set_notification(self, text):
        self.notification_text.setText(text)
        self.notification.setVisible(bool(text))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.game.setVisible(not self._compact and self.width() >= 900)
        self.rail.setFixedWidth(126 if self.width() < 900 else 168)
        self._layout_chrome()
