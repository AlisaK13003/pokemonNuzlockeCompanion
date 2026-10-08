"""User-facing health presentation around the existing diagnostic tooling.

This module consumes snapshots and already-resolved runtime messages.  It never
issues RAM reads, changes baselines, or decides whether an observer may run.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.core.diagnostic_health import DiagnosticHealthObservation
from pokemon_ev_tracker.ui.diagnostic_health_compatibility import legacy_health_observation
from pokemon_ev_tracker.ui.diagnostic_health_presentation import format_diagnostic_health
from pokemon_ev_tracker.ui.party_selector import PokemonSprite
from pokemon_ev_tracker.ui.theme import refresh_style
from pokemon_ev_tracker.ui.training_panels import line_icon


def _label(text: str = "", role: str = "muted") -> QLabel:
    label = QLabel(text)
    label.setProperty("uiRole", role)
    label.setProperty("inspection", True)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _panel() -> QFrame:
    panel = QFrame()
    panel.setProperty("uiRole", "panel")
    panel.setProperty("inspection", True)
    panel.setMinimumWidth(0)
    panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return panel


def _status(label: QLabel, text: str, role: str) -> None:
    label.setText(text)
    if label.property("statusRole") != role:
        label.setProperty("statusRole", role)
        refresh_style(label)


class AdvancedSwitch(QPushButton):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#365582" if self.isChecked() else "#252e3c"))
        painter.drawRoundedRect(QRectF(0, 2, 27, 14), 7, 7)
        painter.setBrush(QColor("#a4c5ff" if self.isChecked() else "#6d7c91"))
        painter.drawEllipse(QRectF(15 if self.isChecked() else 2, 4, 10, 10))
        painter.setPen(QColor("#7890b4"))
        painter.setFont(self.font())
        painter.drawText(self.rect().adjusted(35, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self.text())


class _HealthField(QFrame):
    def __init__(self, caption: str) -> None:
        super().__init__()
        self.setProperty("uiRole", "panel")
        self.setProperty("inspection", True)
        self.setObjectName("healthField")
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        self.setMinimumHeight(124)
        layout.setSpacing(5)
        self.caption = _label(caption, "healthCaption")
        self.indicator = _label("", "healthCheck")
        heading = QHBoxLayout()
        heading.addWidget(self.caption, 1)
        heading.addWidget(self.indicator)
        self.value = _label("Unavailable", "metricValue")
        self.hint = _label("Waiting for a runtime snapshot.", "small")
        layout.addLayout(heading)
        layout.addSpacing(10)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)
        layout.addStretch(1)

    def update_value(self, value: str, hint: str, role: str = "info") -> None:
        _status(self.value, value, role)
        _status(self.indicator, "✓" if role == "success" else "·", role)
        if role == "success":
            self.indicator.setPixmap(line_icon("check", "#57e5a2", 12).pixmap(12, 12))
        self.hint.setText(hint)


class DiagnosticsView(QWidget):
    """Status/Advanced routes, with truthful unknown and stale data states.

    ``presentation`` accepts optional game_name, command_state, pc_status,
    pc_layout, boxed_count, acquisition_status, pc_event, recovery_message,
    rediscover_enabled, events, and discovery_progress. PC fields may be the
    existing MainWindow labels including their prefixes. ``events`` contains
    real runtime messages, never mock examples. No presentation field persists.
    """

    def __init__(
        self,
        advanced_widget: QWidget,
        rediscover_callback: Callable[[], object],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("diagnosticsView")
        self.setProperty("inspection", True)
        self._columns = 0
        self._events: list[str] = []
        self._last_pc_event = ""
        self._last_recovery = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        tabs_panel = _panel()
        self.tabs_panel = tabs_panel
        tabs = QHBoxLayout(tabs_panel)
        tabs.setContentsMargins(6, 6, 6, 6)
        tabs.setSpacing(6)
        self.section_buttons: dict[str, QPushButton] = {}
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        for section, caption in (("status", "STATUS"), ("advanced", "ADVANCED")):
            button = QPushButton(caption)
            button.setCheckable(True)
            button.setProperty("buttonRole", "segmented")
            button.setProperty("inspection", True)
            button.setMinimumHeight(30)
            button.clicked.connect(lambda _checked=False, key=section: self.set_section(key))
            self._button_group.addButton(button)
            self.section_buttons[section] = button
            tabs.addWidget(button)
        tabs.addStretch(1)
        self.global_status = _label("WAITING", "micro")
        tabs.addWidget(self.global_status)
        # Keep the existing section buttons as adapters; the header switch is
        # the visible navigation between status and technical tooling.
        tabs_panel.hide()
        self.advanced_toggle = AdvancedSwitch("Advanced view")
        self.advanced_toggle.setFixedSize(150, 26)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setProperty("buttonRole", "advancedSwitch")
        self.advanced_toggle.clicked.connect(
            lambda checked: self.set_section("advanced" if checked else "status"))

        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(0)
        layout.addWidget(self.stack, 1)
        self.status_page = QWidget()
        self.status_page.setMinimumWidth(0)
        status_layout = QVBoxLayout(self.status_page)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(10)

        self.hero = _panel()
        self.hero.setObjectName("diagnosticsHero")
        self.hero.setMinimumHeight(126)
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(25, 24, 25, 24)
        hero_layout.setSpacing(5)
        self.connection_icon = QLabel()
        self.connection_icon.setObjectName("connectionHealthIcon")
        self.connection_icon.setFixedSize(56, 56)
        self.connection_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.connection_icon.setPixmap(line_icon("health", "#57e5a2", 24).pixmap(24, 24))
        hero_layout.addWidget(self.connection_icon)
        hero_layout.addSpacing(15)
        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(6)
        hero_copy.addWidget(_label("EMULATOR CONNECTION", "healthCaption"))
        self.hero_title = _label("Waiting for BizHawk", "healthHeroTitle")
        self.hero_description = _label("Connect the emulator to inspect live system health.")
        self.last_update = _label("LAST RAM UPDATE  ·  Never", "small")
        self.domain = _label("RAM DOMAIN  ·  Unavailable", "micro")
        self.last_update.setParent(self.hero)
        self.domain.setParent(self.hero)
        hero_copy.addWidget(self.hero_title)
        self.hero_description.setProperty("uiRole", "healthHint")
        hero_copy.addWidget(self.hero_description)
        hero_layout.addLayout(hero_copy, 1)
        hero_layout.addWidget(self.global_status)
        self.last_update.hide()
        self.domain.hide()
        status_layout.addWidget(self.hero)

        self.health_grid = QGridLayout()
        self.health_grid.setContentsMargins(0, 0, 0, 0)
        self.health_grid.setSpacing(10)
        self.fields = {
            key: _HealthField(caption)
            for key, caption in (
                ("party", "Party stream"),
                ("pc", "PC monitoring"),
                ("layout", "Box layout"),
                ("command", "Command channel"),
                ("connection", "BIZHAWK CONNECTION"),
                ("game", "CURRENT GAME"),
                ("acquisition", "NEW-CATCH MONITORING"),
                ("boxed", "BOXED POKÉMON"),
            )
        }
        for key in ("connection", "game", "acquisition", "boxed"):
            self.fields[key].setParent(self.status_page)
            self.fields[key].resize(0, 124)
            self.fields[key].hide()
        status_layout.addLayout(self.health_grid)

        self.lower_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.lower_layout.setSpacing(10)
        self.party_panel = _panel()
        self.party_panel.setObjectName("diagnosticParty")
        party_layout = QVBoxLayout(self.party_panel)
        party_layout.setContentsMargins(0, 0, 0, 0)
        party_layout.setSpacing(0)
        snapshot_header = QFrame()
        snapshot_header.setObjectName("diagnosticSnapshotHeader")
        snapshot_header.setFixedHeight(62)
        heading = QHBoxLayout(snapshot_header)
        heading.setContentsMargins(20, 18, 20, 18)
        party_heading = _label("PARTY SNAPSHOT", "runKicker")
        party_heading.setWordWrap(False)
        heading.addWidget(party_heading)
        heading.addStretch(1)
        self.party_state_label = _label("WAITING", "micro")
        heading.addWidget(self.party_state_label)
        party_layout.addWidget(snapshot_header)
        self.party_empty = _label("No party snapshot received.", "emptyState")
        party_layout.addWidget(self.party_empty)
        self.party_rows = []
        self.party_meta = []
        self.party_grid = QGridLayout()
        self.party_grid.setContentsMargins(12, 12, 12, 12)
        self.party_grid.setSpacing(4)
        party_layout.addLayout(self.party_grid)
        for index in range(6):
            row = QFrame()
            row.setProperty("uiRole", "raisedPanel")
            row.setMinimumWidth(0)
            row_layout = QHBoxLayout(row)
            row.setFixedHeight(60)
            row_layout.setContentsMargins(10, 3, 10, 3)
            row_layout.setSpacing(7)
            sprite = PokemonSprite(46, reference_art=True, reference_bounds=(42, 49))
            identity = _label("", "healthPartyName")
            hp = _label("", "small")
            hp.setParent(row)
            copy = QVBoxLayout()
            copy.setSpacing(3)
            copy.addWidget(identity)
            meta = _label("", "healthHint")
            self.party_meta.append(meta)
            copy.addWidget(meta)
            row_layout.addWidget(sprite)
            row_layout.addLayout(copy, 1)
            hp.hide()
            row.hide()
            self.party_grid.addWidget(row, index // 2, index % 2)
            self.party_rows.append((row, sprite, identity, hp))

        self.recovery_panel = _panel()
        recovery_layout = QVBoxLayout(self.recovery_panel)
        recovery_layout.setContentsMargins(12, 12, 12, 12)
        recovery_layout.setSpacing(8)
        recovery_layout.addWidget(_label("RECOVERY + RECENT EVENTS", "sectionHeading"))
        self.event_list = QListWidget()
        self.event_list.setMinimumWidth(0)
        self.event_list.setMinimumHeight(100)
        self.event_list.setMaximumHeight(220)
        self.event_list.setWordWrap(True)
        self.event_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.event_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        recovery_layout.addWidget(self.event_list)
        self.recovery_message = _label("", "small")
        self.recovery_message.hide()
        recovery_layout.addWidget(self.recovery_message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFormat("PC layout discovery · %p%")
        self.progress.hide()
        recovery_layout.addWidget(self.progress)
        self.rediscover_button = QPushButton("REDISCOVER PC LAYOUT")
        self.rediscover_button.setProperty("inspection", True)
        self.rediscover_button.setEnabled(False)
        self.rediscover_button.clicked.connect(lambda _checked=False: rediscover_callback())
        recovery_layout.addWidget(self.rediscover_button)
        self.advanced_button = QPushButton("OPEN ADVANCED DIAGNOSTICS")
        self.advanced_button.setProperty("inspection", True)
        self.advanced_button.clicked.connect(lambda: self.set_section("advanced"))
        recovery_layout.addWidget(self.advanced_button)
        recovery_layout.addStretch(1)
        self.lower_layout.addWidget(self.party_panel, 6)
        self.lower_layout.addWidget(self.recovery_panel, 5)
        self.lower_layout.addStretch(6)
        status_layout.addLayout(self.lower_layout)
        status_layout.addStretch(1)

        self.status_scroll_area = self._scroll_area(self.status_page)
        self.stack.addWidget(self.status_scroll_area)
        self.advanced_widget = advanced_widget
        self.advanced_scroll_area = (
            advanced_widget if isinstance(advanced_widget, QScrollArea)
            else self._scroll_area(advanced_widget)
        )
        # Existing technical tools include wide tables and address controls.
        # Keep every control reachable when the main window is narrow.
        self.advanced_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.stack.addWidget(self.advanced_scroll_area)
        self.set_section("status")
        self._reflow(1000)
        self._render_events()

    @staticmethod
    def _scroll_area(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setMinimumWidth(0)
        scroll.setWidget(widget)
        return scroll

    def set_section(self, section: str) -> None:
        if section not in self.section_buttons:
            raise ValueError(f"Unknown diagnostics section: {section}")
        self.section_buttons[section].setChecked(True)
        self.advanced_toggle.setChecked(section == "advanced")
        self.stack.setCurrentIndex(0 if section == "status" else 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow(event.size().width())

    def _reflow(self, width: int) -> None:
        columns = 4 if width >= 1050 else 2 if width >= 440 else 1
        if columns != self._columns:
            for field in self.fields.values():
                self.health_grid.removeWidget(field)
            for index, (key, field) in enumerate(self.fields.items()):
                if key not in {"party", "pc", "layout", "command"}:
                    field.hide()
                    continue
                self.health_grid.addWidget(field, index // columns, index % columns)
            for column in range(4):
                self.health_grid.setColumnStretch(column, int(column < columns))
            self._columns = columns
        self.lower_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if width >= 850
            else QBoxLayout.Direction.TopToBottom
        )
        for index, (row, *_) in enumerate(self.party_rows):
            self.party_grid.addWidget(row, index // (2 if width >= 600 else 1), index % (2 if width >= 600 else 1))

    def refresh(self, snapshot, presentation: Mapping | None = None) -> None:
        # Compatibility for previews/older callers; the application uses typed
        # runtime observations and never sends widget strings through this path.
        health, overrides = legacy_health_observation(snapshot, presentation, now=time.monotonic())
        self.refresh_health(health, _legacy_overrides=overrides)

    def refresh_health(self, health: DiagnosticHealthObservation, *, _legacy_overrides=None) -> None:
        text = format_diagnostic_health(health)
        self.last_update.setText(text.last_update)
        self.domain.setText(text.domain)
        for key, field in text.fields:
            value = (_legacy_overrides or {}).get(key, field.value)
            self.fields[key].update_value(value, field.detail, field.role)
        self.hero_title.setText(text.title)
        self.hero_description.setText(text.description)
        self.hero.setToolTip(f"{text.last_update}\n{text.domain}")
        _status(self.global_status, text.global_status, text.role)
        if self.connection_icon.property("statusRole") != text.role:
            self.connection_icon.setProperty("statusRole", text.role)
            self.connection_icon.setPixmap(line_icon("health", "#57e5a2" if text.role == "success" else "#ffcb69", 24).pixmap(24, 24))
            refresh_style(self.connection_icon)
        self._refresh_party(health.party.members, health.party.fresh, health.party.display_valid)
        pc = health.pc
        self.rediscover_button.setEnabled(pc.rediscover_enabled)
        self.recovery_message.setText(pc.recovery_message)
        self.recovery_message.setVisible(bool(pc.recovery_message))
        self.progress.setVisible(health.progress_percent is not None)
        if health.progress_percent is not None:
            self.progress.setValue(max(0, min(100, int(health.progress_percent))))
        for event in health.events:
            self._record_event(event)
        if pc.last_event and pc.last_event != self._last_pc_event:
            self._record_event(pc.last_event)
            self._last_pc_event = pc.last_event
        if pc.recovery_message and pc.recovery_message != self._last_recovery:
            self._record_event(pc.recovery_message)
            self._last_recovery = pc.recovery_message
        self._render_events()
        useful_events = [event for event in self._events if event.lower() not in {"no pc events yet.", "no pc events yet"}]
        self.recovery_panel.setVisible(bool(pc.recovery_message or pc.recovering or useful_events))

    def _refresh_party(self, members: tuple, fresh: bool, valid: bool) -> None:
        _status(self.party_state_label, "LIVE" if fresh and valid else "LAST SNAPSHOT" if members else "WAITING",
                "success" if fresh and valid else "warning")
        self.party_empty.setVisible(not members)
        self.party_empty.setText("Party is empty." if fresh and valid else "No party snapshot received.")
        for index, (row, sprite, identity, hp) in enumerate(self.party_rows):
            mon = members[index] if index < len(members) else None
            row.setVisible(mon is not None)
            if mon is None:
                continue
            species_id = getattr(mon, "species_id", 0)
            sprite.set_species(species_id)
            species = getattr(mon, "species", "Unknown species")
            nickname = getattr(mon, "nickname", "")
            identity.setText(nickname or species)
            stable_id = str(getattr(mon, "stable_id", "") or "Identity unavailable")
            identity.setToolTip(stable_id)
            checksum_valid = getattr(mon, "checksum_valid", False)
            current, maximum = getattr(mon, "current_hp", None), getattr(mon, "max_hp", None)
            hp.setText(f"HP {current} / {maximum}" if checksum_valid and current is not None and maximum is not None else "HP —")
            hp.setToolTip("Last received snapshot" if not fresh else "")
            self.party_meta[index].setText(
                f"Slot {index + 1} · {current}/{maximum} HP"
                if checksum_valid and current is not None and maximum is not None
                else f"Slot {index + 1} · HP unavailable")

    def _record_event(self, text: str) -> None:
        text = text.strip()
        if not text or text in {"--", "—", "None"} or (self._events and self._events[-1] == text):
            return
        if text in self._events:
            return
        self._events.append(text)
        self._events = self._events[-20:]

    def _render_events(self) -> None:
        lines = list(reversed(self._events[-8:])) or ["No runtime events received yet."]
        current = [self.event_list.item(index).text() for index in range(self.event_list.count())]
        if current != lines:
            self.event_list.clear()
            self.event_list.addItems(lines)
