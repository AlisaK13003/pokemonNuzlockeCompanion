"""Trainer Lab navigation and compact workspace chrome; no runtime logic."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.icons import settings_icon, tracker_icon
from pokemon_ev_tracker.ui.theme import refresh_style
from pokemon_ev_tracker.ui.training_panels import line_icon


class PageHeading(QLabel):
    """Reference pixel heading with its small offset text shadow."""

    def paintEvent(self, event):
        painter = QPainter(self)
        font = self.font()
        if self.property("fitTitle"):
            while QFontMetricsF(font).horizontalAdvance(self.text()) > self.width() and font.pixelSize() > 20:
                font.setPixelSize(font.pixelSize() - 1)
            painter.setClipRect(self.contentsRect())
        painter.setFont(font)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.setPen(QColor("#05070b"))
        painter.drawText(self.contentsRect().translated(3, 3), flags, self.text())
        painter.setPen(self.palette().windowText().color())
        painter.drawText(self.contentsRect(), flags, self.text())


class AppShell(QWidget):
    route_requested = Signal(str)
    compact_requested = Signal(bool)
    notification_requested = Signal()

    def __init__(self, game_name, backend_status, parent=None, *, generation="", emulator=""):
        super().__init__(parent)
        self.setObjectName("appRoot")
        self.root_layout = QVBoxLayout(self)
        # Let resizeEvent select the smaller chrome before Qt applies the old
        # text-bearing navigation's minimum width.
        self.root_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)
        self.header = QFrame()
        self.header.setObjectName("applicationToolbar")
        header = QHBoxLayout(self.header)
        self.header_layout = header
        header.setContentsMargins(16, 12, 16, 12)
        self.brand_icon = QSvgWidget(str(Path(__file__).resolve().parents[1] / "assets/ui/terminal_mark.svg"))
        self.brand_icon.setFixedSize(34, 34)
        header.addWidget(self.brand_icon)
        self.brand = QLabel("COMPANION")
        self.brand.setObjectName("applicationTitle")
        header.addWidget(self.brand)
        self.game = QLabel(f"{game_name} · {emulator.split(' / ')[0]}")
        self._game_caption = self.game.text()
        self.game.setProperty("uiRole", "muted")
        brand_layout = QVBoxLayout()
        header.removeWidget(self.brand)
        brand_layout.addWidget(self.brand)
        brand_layout.addWidget(self.game)
        brand_layout.setSpacing(1)
        header.addLayout(brand_layout)
        header.addStretch(1)
        self.compact_button = QPushButton("COMPACT MODE")
        self.compact_button.setCheckable(True)
        self.compact_button.setProperty("buttonRole", "compactToggle")
        self.compact_button.setToolTip("Compact always-on-top workspace · Ctrl+Shift+C")
        self.compact_button.toggled.connect(self.compact_requested)
        header.addWidget(self.compact_button)
        self.settings_button = QPushButton()
        self.settings_button.setIcon(settings_icon())
        self.settings_button.setToolTip("Settings")
        self.settings_button.setObjectName("settingsCog")
        self.settings_button.setFixedWidth(36)
        self.settings_button.setIconSize(QSize(22, 22))
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.clicked.connect(lambda: self.route_requested.emit("settings"))
        header.addWidget(self.settings_button)
        header.addWidget(backend_status)
        self.backend_status = backend_status
        self.header_actions = QWidget()
        self.header_actions.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        actions = QHBoxLayout(self.header_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(12)
        for control in (self.compact_button, self.settings_button, backend_status):
            header.removeWidget(control)
            actions.addWidget(control)
        header.addWidget(self.header_actions)
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
        # Move the existing route buttons into horizontal navigation, preserving signals.
        self.primary_nav = QFrame()
        self.primary_nav.setObjectName("primaryNavigation")
        primary = QHBoxLayout(self.primary_nav)
        primary.setContentsMargins(0, 0, 0, 0)
        self.tracker_button = QPushButton("Tracker")
        self.tracker_button.setCheckable(True)
        self.tracker_button.setProperty("buttonRole", "topNavigation")
        self.tracker_button.clicked.connect(lambda: self.route_requested.emit(self._tracker_route))
        primary.addWidget(self.tracker_button)
        for key in ("nuzlocke", "diagnostics"):
            button = self.nav_buttons[key]
            button.setProperty("buttonRole", "topNavigation")
            primary.addWidget(button)
        header.insertWidget(3, self.primary_nav)
        header.insertStretch(4, 1)
        for button, kind in ((self.tracker_button, "health"), (self.nav_buttons["nuzlocke"], "shield"), (self.nav_buttons["diagnostics"], "pulse")):
            button.setIcon(line_icon(kind))
            button.setIconSize(QSize(16, 16))
        self.compact_button.setIcon(tracker_icon("box"))
        self.page_heading = QFrame()
        heading = QVBoxLayout(self.page_heading)
        heading.setContentsMargins(30, 24, 30, 20)
        heading.setSpacing(0)
        self.eyebrow = QLabel("LIVE TRACKER")
        self.eyebrow.setProperty("uiRole", "pageEyebrow")
        self.eyebrow.setFixedHeight(14)
        eyebrow_row = QHBoxLayout()
        eyebrow_row.setSpacing(11)
        eyebrow_row.addWidget(self.eyebrow)
        self.heading_rule = QFrame()
        self.heading_rule.setObjectName("headingAccentRule")
        self.heading_rule.setFixedSize(40, 1)
        eyebrow_row.addWidget(self.heading_rule)
        eyebrow_row.addStretch(1)
        heading.addLayout(eyebrow_row)
        self.heading_actions = eyebrow_row
        heading.addSpacing(9)
        self.page_title = PageHeading("Field session")
        self.page_title.setProperty("uiRole", "pageHeaderTitle")
        self.page_title.setFixedHeight(40)
        heading.addWidget(self.page_title)
        heading.addSpacing(5)
        self.page_description = QLabel("Party data is updating from emulator memory.")
        self.page_description.setProperty("uiRole", "pageSubtitle")
        self.page_description.setFixedHeight(18)
        heading.addWidget(self.page_description)
        self.root_layout.addWidget(self.page_heading)
        self.tracker_tabs = QFrame()
        tabs = QHBoxLayout(self.tracker_tabs)
        tabs.setContentsMargins(30, 0, 30, 16)
        self.tab_group = QFrame()
        self.tab_group.setObjectName("trackerTabGroup")
        self.tab_group.setFixedSize(319, 42)
        tab_row = QHBoxLayout(self.tab_group)
        tab_row.setContentsMargins(5, 5, 5, 5)
        tab_row.setSpacing(0)
        shadow = QGraphicsDropShadowEffect(self.tab_group)
        shadow.setBlurRadius(0)
        shadow.setOffset(3, 3)
        shadow.setColor(QColor("#05070b"))
        self.tab_group.setGraphicsEffect(shadow)
        tabs.addWidget(self.tab_group)
        group = QButtonGroup(self)
        for key in ("training", "stats"):
            button = self.nav_buttons[key]
            button.setProperty("buttonRole", "trackerTab")
            group.addButton(button)
            tab_row.addWidget(button)
        self.nav_buttons["box"] = QPushButton("Box")
        self.nav_buttons["box"].setCheckable(True)
        self.nav_buttons["box"].setProperty("buttonRole", "trackerTab")
        self.nav_buttons["box"].clicked.connect(lambda: self.route_requested.emit("box"))
        group.addButton(self.nav_buttons["box"])
        tab_row.addWidget(self.nav_buttons["box"])
        for key, width in (("training", 102), ("stats", 133), ("box", 74)):
            button = self.nav_buttons[key]
            button.setMinimumHeight(0)
            button.setFixedSize(width, 32)
            button.setIcon(tracker_icon(key))
            button.setIconSize(QSize(14, 14))
        tabs.addStretch(1)
        self.root_layout.addWidget(self.tracker_tabs)
        self.root_layout.addLayout(body, 1)
        self.pages = {}
        self._compact = False
        self._connected = False
        self._route = "training"
        self._tracker_route = "training"
        self.session_title = "Field session"
        self.set_compact(False)

    def add_page(self, key, widget):
        self.pages[key] = widget
        self.workspace.addWidget(widget)

    def show_route(self, route):
        self._route = route
        tracker = route in {"training", "stats", "box"}
        if tracker:
            self._tracker_route = route
        key = "compact_training" if self._compact else route
        if not self._connected and route not in {"settings", "diagnostics"}:
            key = "disconnected"
        if key in self.pages:
            self.workspace.setCurrentWidget(self.pages[key])
        self.tracker_tabs.setVisible(tracker and not self._compact and self._connected)
        self.page_heading.setVisible(not self._compact and route != "nuzlocke" and key != "disconnected")
        titles = {"settings": ("COMPANION PREFERENCES", "Settings", "Customize how the companion behaves while you play."),
                  "diagnostics": ("SYSTEM HEALTH", "Diagnostics", "Connection and live memory monitoring.")}
        eyebrow, title, detail = titles.get(route, ("LIVE TRACKER", self.session_title, "Party data is updating from emulator memory."))
        self.eyebrow.setText(eyebrow)
        self.page_title.setText(title)
        self.page_description.setText(detail)
        self.tracker_button.setChecked(tracker)
        groups = {button.group() for buttons in (self.nav_buttons, self.compact_buttons)
                  for button in buttons.values() if button.group() is not None}
        for group in groups:
            group.setExclusive(False)
        for buttons in (self.nav_buttons, self.compact_buttons):
            for name, button in buttons.items():
                button.setChecked(name == route)
        for group in groups:
            group.setExclusive(True)
        self._layout_chrome()

    def set_compact(self, enabled):
        self._compact = enabled
        self.rail.hide()
        self.compact_switcher.hide()
        self.primary_nav.setVisible(not enabled)
        self.settings_button.setVisible(not enabled)
        self.brand.setVisible(not enabled)
        self.brand_icon.setVisible(not enabled)
        self.game.setVisible(not enabled and self.width() >= 900)
        self.compact_button.blockSignals(True)
        self.compact_button.setChecked(enabled)
        self.compact_button.setText("Expand" if enabled else "Compact")
        self.compact_button.blockSignals(False)
        self._layout_chrome()

    def _layout_chrome(self):
        self.compact_switcher.hide()
        width = self.width()
        navigation_icons = not self._compact and width <= 1100
        icons_only = not self._compact and width <= 520
        for button, title in ((self.tracker_button, "Tracker"),
                              (self.nav_buttons["nuzlocke"], "Nuzlocke"),
                              (self.nav_buttons["diagnostics"], "Diagnostics")):
            button.setText("" if navigation_icons else title)
            button.setAccessibleName(title)
            button.setToolTip(title)
            button.setIconSize(QSize(19, 19) if navigation_icons else QSize(16, 16))
            button.setFixedHeight(50)
        self.primary_nav.setVisible(not self._compact)
        self.brand.setVisible(not self._compact)
        self.brand_icon.setVisible(not self._compact)
        self.game.setVisible(not self._compact)
        self.brand_icon.setFixedSize(29 if icons_only else 34, 29 if icons_only else 34)
        self.compact_button.setVisible(self._compact or width > 760)
        self.compact_button.setAccessibleName("Expand" if self._compact else "Compact")
        self.compact_button.setFixedHeight(30 if self._compact else 42)
        self.compact_button.setFixedWidth(42 if self._compact and width < 750 else 122)
        self.settings_button.setFixedSize(31 if icons_only else 42, 31 if icons_only else 42)
        badge = self.backend_status.compact_label
        badge.setProperty("iconOnly", icons_only)
        badge.setFixedHeight(30 if self._compact else 31 if icons_only else 42)
        badge.setFixedWidth(31 if icons_only else 110 if self._connected or width <= 760 else 232)
        self.backend_status.setFixedWidth(badge.width())
        badge.setProperty("hideDetail", not self._compact and width <= 760)
        badge.update()
        action_widths = [badge.width()]
        if not self._compact:
            action_widths.append(self.settings_button.width())
        if self._compact or width > 760:
            action_widths.append(self.compact_button.width())
        self.header_actions.setFixedWidth(sum(action_widths) + 12 * (len(action_widths) - 1))
        if not self._compact:
            self.compact_button.setText("Compact")
        else:
            self.compact_button.setText("" if width < 750 else "Expand")
        for button in (self.tracker_button, self.nav_buttons["nuzlocke"], self.nav_buttons["diagnostics"]):
            tight = self.width() < 850
            if button.property("tight") != tight:
                button.setProperty("tight", tight)
                refresh_style(button)
        margin = 10 if icons_only else 14 if width <= 760 else 16
        self.header_layout.setContentsMargins(6, 5, 6, 5) if self._compact else self.header_layout.setContentsMargins(margin, 12, margin, 12)
        if self._compact:
            self.body_layout.setContentsMargins(10, 10, 10, 10)
        else:
            top = 6 if self._connected and self._route in {"training", "stats", "box"} else 30
            inset = max(30, (self.width() - 1440) // 2) if self._route in {"nuzlocke", "diagnostics"} else 30
            if self._route == "diagnostics":
                top = 6
                self.page_heading.layout().setContentsMargins(inset, 34, inset, 20)
            else:
                self.page_heading.layout().setContentsMargins(30, 24, 30, 20)
            self.body_layout.setContentsMargins(inset, top, inset, 30)

    def set_notification(self, text):
        self.notification_text.setText(text)
        self.notification.setVisible(bool(text))

    def set_connection(self, connected):
        self._connected = bool(connected)
        self.game.setText(self._game_caption if self._connected else "Waiting for BizHawk")
        self.compact_button.setEnabled(self._connected)
        self.show_route(self._route)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.rail.setFixedWidth(126 if self.width() < 900 else 168)
        self._layout_chrome()
