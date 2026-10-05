"""User-facing health presentation around the existing diagnostic tooling.

This module consumes snapshots and already-resolved runtime messages.  It never
issues RAM reads, changes baselines, or decides whether an observer may run.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from PySide6.QtCore import Qt
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

from pokemon_ev_tracker.ui.sprite_loader import get_static_sprite
from pokemon_ev_tracker.ui.theme import refresh_style


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


def _payload(wrapper) -> Mapping:
    value = getattr(wrapper, "payload", {})
    return value if isinstance(value, Mapping) else {}


def _without_prefix(value, prefix: str, fallback: str = "Unavailable") -> str:
    text = str(value).strip() if value is not None else ""
    if text.lower().startswith(prefix.lower()):
        text = text[len(prefix):].strip()
    return text or fallback


def _status(label: QLabel, text: str, role: str) -> None:
    label.setText(text)
    if label.property("statusRole") != role:
        label.setProperty("statusRole", role)
        refresh_style(label)


class _HealthField(QFrame):
    def __init__(self, caption: str) -> None:
        super().__init__()
        self.setProperty("uiRole", "panel")
        self.setProperty("inspection", True)
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        self.caption = _label(caption, "micro")
        self.value = _label("Unavailable", "metricValue")
        self.hint = _label("Waiting for a runtime snapshot.", "small")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)
        layout.addStretch(1)

    def update_value(self, value: str, hint: str, role: str = "info") -> None:
        _status(self.value, value, role)
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
        layout.addWidget(tabs_panel)

        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(0)
        layout.addWidget(self.stack, 1)
        self.status_page = QWidget()
        self.status_page.setMinimumWidth(0)
        status_layout = QVBoxLayout(self.status_page)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(10)

        self.hero = _panel()
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(16, 13, 16, 13)
        hero_layout.setSpacing(5)
        self.hero_title = _label("Waiting for BizHawk", "pageTitle")
        self.hero_description = _label("Connect the emulator to inspect live system health.")
        self.last_update = _label("LAST RAM UPDATE  ·  Never", "small")
        self.domain = _label("RAM DOMAIN  ·  Unavailable", "micro")
        hero_layout.addWidget(self.hero_title)
        hero_layout.addWidget(self.hero_description)
        hero_layout.addWidget(self.last_update)
        hero_layout.addWidget(self.domain)
        status_layout.addWidget(self.hero)

        self.health_grid = QGridLayout()
        self.health_grid.setContentsMargins(0, 0, 0, 0)
        self.health_grid.setSpacing(10)
        self.fields = {
            key: _HealthField(caption)
            for key, caption in (
                ("connection", "BIZHAWK CONNECTION"),
                ("game", "CURRENT GAME"),
                ("party", "PARTY STREAM"),
                ("command", "COMMAND CHANNEL"),
                ("pc", "PC MONITOR"),
                ("layout", "BOX LAYOUT"),
                ("acquisition", "NEW-CATCH MONITORING"),
                ("boxed", "BOXED POKÉMON"),
            )
        }
        status_layout.addLayout(self.health_grid)

        self.lower_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.lower_layout.setSpacing(10)
        self.party_panel = _panel()
        party_layout = QVBoxLayout(self.party_panel)
        party_layout.setContentsMargins(12, 12, 12, 12)
        heading = QHBoxLayout()
        heading.addWidget(_label("PARTY SNAPSHOT", "sectionHeading"))
        heading.addStretch(1)
        self.party_state_label = _label("WAITING", "micro")
        heading.addWidget(self.party_state_label)
        party_layout.addLayout(heading)
        self.party_empty = _label("No party snapshot received.", "emptyState")
        party_layout.addWidget(self.party_empty)
        self.party_rows = []
        for index in range(6):
            row = QFrame()
            row.setProperty("uiRole", "raisedPanel")
            row.setMinimumWidth(0)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(7, 5, 7, 5)
            row_layout.setSpacing(7)
            sprite = QLabel()
            sprite.setFixedSize(32, 32)
            sprite.setAlignment(Qt.AlignmentFlag.AlignCenter)
            identity = _label("", "pokemonMeta")
            hp = _label("", "small")
            row_layout.addWidget(sprite)
            row_layout.addWidget(identity, 1)
            row_layout.addWidget(hp)
            row.hide()
            party_layout.addWidget(row)
            self.party_rows.append((row, sprite, identity, hp))
        party_layout.addStretch(1)

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
        self.stack.setCurrentIndex(0 if section == "status" else 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow(event.size().width())

    def _reflow(self, width: int) -> None:
        columns = 4 if width >= 1050 else 2 if width >= 440 else 1
        if columns != self._columns:
            for field in self.fields.values():
                self.health_grid.removeWidget(field)
            for index, field in enumerate(self.fields.values()):
                self.health_grid.addWidget(field, index // columns, index % columns)
            for column in range(4):
                self.health_grid.setColumnStretch(column, int(column < columns))
            self._columns = columns
        self.lower_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if width >= 850
            else QBoxLayout.Direction.TopToBottom
        )

    def refresh(self, snapshot, presentation: Mapping | None = None) -> None:
        presentation = presentation or {}
        details = getattr(snapshot, "details", {}) or {}
        connected = bool(getattr(snapshot, "connected", False))
        heartbeat = _payload(details.get("heartbeat"))
        party_payload = _payload(details.get("party_payload"))
        party = details.get("display_party_state") or details.get("party_state")
        members = tuple(getattr(party, "pokemon", ()) or ())
        fresh = details.get("party_payload_fresh") is True
        valid = bool(party is not None and getattr(party, "party_count_valid", False)
                     and not getattr(party, "error", None)
                     and all(getattr(mon, "checksum_valid", False) for mon in members))
        warning = getattr(party, "live_read_warning", None)
        domain = party_payload.get("active_domain") or heartbeat.get("active_domain")
        game = str(presentation.get("game_name") or "Unavailable")
        age = details.get("ram_age_seconds")
        if age is None:
            received = getattr(details.get("party_payload"), "received_at", None)
            if isinstance(received, (int, float)) and not isinstance(received, bool):
                age = max(0.0, time.monotonic() - received)
        self.last_update.setText(
            f"LAST RAM UPDATE  ·  {max(0.0, age):.1f}s ago"
            if isinstance(age, (int, float)) and not isinstance(age, bool)
            else "LAST RAM UPDATE  ·  Never"
        )
        self.domain.setText(f"RAM DOMAIN  ·  {domain or 'Unavailable'}")
        self.fields["connection"].update_value(
            "Connected" if connected else "Disconnected",
            "Emulator heartbeat received." if connected else "Waiting for the BizHawk Lua connection.",
            "success" if connected else "warning",
        )
        self.fields["game"].update_value(game, f"RAM domain · {domain or 'Unavailable'}")
        party_value = "Valid" if valid and fresh and not warning else (
            "Read warning" if fresh and warning else "Invalid" if fresh and not valid
            else "Stale" if party is not None else "Waiting"
        )
        self.fields["party"].update_value(
            party_value,
            str(warning or getattr(party, "error", None) or (
                f"{len(members)} party members decoded." if valid else "No valid party snapshot received."
            )),
            "success" if valid and fresh and not warning else "warning",
        )
        command = str(presentation.get("command_state") or "UNKNOWN")
        command_ready = command.upper() in {"READY", "ACKNOWLEDGED", "AVAILABLE"}
        command_problem = any(word in command.upper() for word in ("STALE", "TIMEOUT", "UNAVAILABLE"))
        self.fields["command"].update_value(
            "Unverified" if command == "UNKNOWN" else command.replace("_", " ").title(),
            "Status from movement command acknowledgements.",
            "success" if command_ready else "warning" if command_problem else "info",
        )
        pc_status = _without_prefix(presentation.get("pc_status"), "Status:")
        pc_layout = _without_prefix(presentation.get("pc_layout"), "Box layout:")
        boxed = _without_prefix(presentation.get("boxed_count"), "Boxed Pokemon:", "—")
        acquisition = _without_prefix(presentation.get("acquisition_status"), "Monitoring new catches:")
        pc_event = _without_prefix(presentation.get("pc_event"), "Last PC event:", "")
        self.fields["pc"].update_value(pc_status, "Current PC resolver and monitor state.")
        self.fields["layout"].update_value(pc_layout, "Validated layout for the current emulator session.")
        self.fields["acquisition"].update_value(acquisition, "PC acquisition monitoring reported by the runtime.")
        self.fields["boxed"].update_value(boxed, pc_event or "Waiting for a validated PC snapshot.")

        recovering = any(term in pc_status.lower() for term in (
            "discovering", "validating", "baseline", "rediscovery", "recover"
        ))
        if not connected:
            title, description, state, role = (
                "Waiting for BizHawk", "Connect the emulator to inspect live system health.",
                "DISCONNECTED", "warning",
            )
        elif not fresh or not valid or warning:
            title, description, state, role = (
                "Connection needs attention",
                "The emulator heartbeat is available; party data is stale or has a read warning.",
                "ATTENTION", "warning",
            )
        elif recovering:
            title, description, state, role = (
                "PC resolver is recovering",
                "Party data is current. PC monitoring is waiting for layout validation and its baseline.",
                "RECOVERING", "warning",
            )
        else:
            # Do not claim command/PC health from a fresh party stream alone.
            title, description, state, role = (
                "Live party connection is current",
                "Review command and PC monitoring status below.", "LIVE RAM", "success",
            )
        self.hero_title.setText(title)
        self.hero_description.setText(description)
        _status(self.global_status, state, role)
        self._refresh_party(members, fresh, valid)
        self.rediscover_button.setEnabled(bool(presentation.get("rediscover_enabled", connected)))
        recovery_message = str(presentation.get("recovery_message") or "")
        self.recovery_message.setText(recovery_message)
        self.recovery_message.setVisible(bool(recovery_message))
        progress = presentation.get("discovery_progress")
        self.progress.setVisible(isinstance(progress, (int, float)) and not isinstance(progress, bool))
        if isinstance(progress, (int, float)) and not isinstance(progress, bool):
            self.progress.setValue(max(0, min(100, int(progress))))
        supplied_events = presentation.get("events") or ()
        if isinstance(supplied_events, str):
            supplied_events = (supplied_events,)
        for event in supplied_events:
            self._record_event(str(event))
        if pc_event and pc_event != self._last_pc_event:
            self._record_event(pc_event)
            self._last_pc_event = pc_event
        if recovery_message and recovery_message != self._last_recovery:
            self._record_event(recovery_message)
            self._last_recovery = recovery_message
        self._render_events()

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
            if sprite.property("speciesId") != species_id:
                sprite.setPixmap(get_static_sprite(species_id, 30))
                sprite.setProperty("speciesId", species_id)
            species = getattr(mon, "species", "Unknown species")
            nickname = getattr(mon, "nickname", "")
            identity.setText(f"{nickname}\n{species}" if nickname and nickname.casefold() != species.casefold() else species)
            stable_id = str(getattr(mon, "stable_id", "") or "Identity unavailable")
            identity.setToolTip(stable_id)
            checksum_valid = getattr(mon, "checksum_valid", False)
            current, maximum = getattr(mon, "current_hp", None), getattr(mon, "max_hp", None)
            hp.setText(f"HP {current} / {maximum}" if checksum_valid and current is not None and maximum is not None else "HP —")
            hp.setToolTip("Last received snapshot" if not fresh else "")

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
