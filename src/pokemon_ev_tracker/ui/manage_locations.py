"""Staged encounter editing using existing run records and atomic persistence."""

from collections import Counter
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.core.nuzlocke.models import EncounterRecord
from pokemon_ev_tracker.ui.app_shell import PageHeading
from pokemon_ev_tracker.ui.icons import reset_icon
from pokemon_ev_tracker.ui.inspection_views import label
from pokemon_ev_tracker.ui.theme import refresh_style

STATUSES = (("CAUGHT", "Caught"), ("NOT_ENCOUNTERED", "Not encountered"),
            ("FAILED", "Failed"), ("DEAD", "Dead"))


class LocationEditor(QFrame):
    changed = Signal()

    def __init__(self, record, area):
        super().__init__()
        self.setObjectName("locationEditor")
        self.original = record
        self.status = record.status
        self.was_reset = False
        self.area = area
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("locationEditorHeader")
        heading = QHBoxLayout(header)
        heading.setContentsMargins(13, 14, 13, 14)
        copy = QVBoxLayout()
        copy.setSpacing(7)
        copy.addWidget(label(area.upper(), "locationMicro"))
        copy.addWidget(label(record.location, "locationTitle"))
        heading.addLayout(copy, 1)
        reset = QPushButton("Reset")
        reset.setProperty("buttonRole", "locationReset")
        reset.setIcon(reset_icon())
        reset.clicked.connect(self.reset)
        heading.addWidget(reset)
        root.addWidget(header)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(13, 10, 13, 15)
        layout.setSpacing(8)
        layout.addWidget(label("STATUS", "locationMicro"))
        choices = QHBoxLayout()
        choices.setSpacing(4)
        self.buttons = {}
        for status, caption in STATUSES:
            button = QPushButton(caption)
            button.setProperty("buttonRole", "locationStatus")
            button.setCheckable(True)
            button.setFixedHeight(27)
            button.clicked.connect(lambda checked=False, status=status: self.set_status(status))
            self.buttons[status] = button
            choices.addWidget(button, 1)
        layout.addLayout(choices)
        fields = QHBoxLayout()
        fields.setSpacing(8)
        self.nickname = self.field(fields, "NICKNAME", record.nickname)
        self.species = self.field(fields, "SPECIES", record.species)
        layout.addLayout(fields)
        self.notes = self.field(layout, "NOTES", record.notes)
        root.addWidget(body)
        self.set_status(record.status, notify=False)

    def field(self, layout, caption, value):
        group = QVBoxLayout()
        group.setSpacing(5)
        group.addWidget(label(caption, "locationMicro"))
        field = QLineEdit(value)
        field.setProperty("uiRole", "locationInput")
        field.setFixedHeight(31)
        field.setPlaceholderText("None" if caption != "NOTES" else "")
        field.setAccessibleName(f"{self.original.location}: {caption.title()}")
        field.textChanged.connect(self.changed)
        group.addWidget(field)
        layout.addLayout(group)
        return field

    def set_status(self, status, notify=True):
        self.status = status
        self.setProperty("status", status)
        for key, button in self.buttons.items():
            button.setChecked(key == status)
        refresh_style(self)
        if notify:
            self.changed.emit()

    def reset(self):
        self.was_reset = True
        for field in (self.nickname, self.species, self.notes):
            field.blockSignals(True)
            field.clear()
            field.blockSignals(False)
        self.set_status("NOT_ENCOUNTERED")

    def record(self):
        base = EncounterRecord(self.original.location_id, self.original.location) if self.was_reset else self.original
        return replace(base, status=self.status, nickname=self.nickname.text().strip(),
                       species=self.species.text().strip(), notes=self.notes.text().strip())

    def matches(self, query):
        text = (f"{self.original.location} {self.area} {self.nickname.text()} "
                f"{self.species.text()} {self.notes.text()}")
        return query in text.casefold()


class ManageLocations(QWidget):
    cancelled = Signal()
    saved = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.run_id = None
        self.cards = []
        self._columns = None
        self._visible_cards = ()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.back = QPushButton("‹  Nuzlocke dashboard")
        self.back.setProperty("buttonRole", "locationBack")
        self.back.clicked.connect(self.cancelled)
        root.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        header = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.header_layout = header
        copy = QVBoxLayout()
        copy.setSpacing(5)
        copy.addWidget(label("RUN MANAGEMENT", "pageEyebrow"))
        title = PageHeading("Manage locations")
        title.setProperty("uiRole", "locationPageTitle")
        title.setProperty("fitTitle", True)
        title.setMinimumWidth(0)
        from PySide6.QtWidgets import QSizePolicy
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        copy.addWidget(title)
        copy.addWidget(label("Correct encounter records without losing the game-specific location list.", "pageSubtitle"))
        header.addLayout(copy, 1)
        for caption, callback in (("Cancel", self.cancelled.emit), ("Save changes", self.save)):
            button = QPushButton(caption)
            button.setProperty("buttonRole", "ledgerAction")
            button.setProperty("primary", caption == "Save changes")
            button.setFixedHeight(34)
            button.clicked.connect(callback)
            header.addWidget(button)
        root.addLayout(header)
        self.error = label("", "locationError")
        self.error.setWordWrap(True)
        self.error.hide()
        root.addWidget(self.error)
        summary = QFrame()
        summary.setObjectName("locationSummary")
        summary_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, summary)
        self.summary_layout = summary_layout
        summary_layout.setContentsMargins(22, 22, 22, 22)
        info = QVBoxLayout()
        info.addWidget(label("ENCOUNTER LEDGER", "runKicker"))
        self.summary_title = label("Locations", "ledgerTitle")
        self.summary_detail = label("", "runHint")
        info.addWidget(self.summary_title)
        info.addWidget(self.summary_detail)
        summary_layout.addLayout(info, 1)
        counts = QHBoxLayout()
        self.counts = {}
        for status, caption in STATUSES:
            cell = QFrame()
            cell.setObjectName("locationCount")
            column = QVBoxLayout(cell)
            value = label("0", "locationCountValue")
            column.addWidget(value)
            column.addWidget(label(caption, "locationMicro"))
            counts.addWidget(cell)
            self.counts[status] = value
        summary_layout.addLayout(counts)
        root.addWidget(summary)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setProperty("uiRole", "locationInput")
        self.search.setFixedHeight(35)
        self.search.setMaximumWidth(365)
        self.search.setPlaceholderText("Find a location or Pokémon...")
        self.search.setAccessibleName("Find a location or Pokémon")
        self.search.textChanged.connect(self.refresh_matches)
        search_row.addWidget(self.search, 1)
        search_row.addStretch(1)
        self.match_count = label("", "ledgerFooterText")
        search_row.addWidget(self.match_count)
        root.addLayout(search_row)
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        root.addLayout(self.grid)
        footer = QHBoxLayout()
        footer.addWidget(label("Changes only affect this run's encounter records.", "runHint"), 1)
        discard = QPushButton("Discard")
        discard.setProperty("buttonRole", "ledgerAction")
        discard.clicked.connect(self.cancelled)
        footer.addWidget(discard)
        save = QPushButton("Save changes")
        save.setProperty("buttonRole", "ledgerAction")
        save.setProperty("primary", True)
        save.clicked.connect(self.save)
        footer.addWidget(save)
        root.addLayout(footer)

    def open_run(self, run, profile):
        self.run_id = run.run_id
        for card in self.cards:
            self.grid.removeWidget(card)
            card.deleteLater()
        self.cards = []
        self._visible_cards = ()
        by_id = {location.location_id: location for location in profile.locations} if profile else {}
        records = sorted(run.encounters.values(), key=lambda r: getattr(by_id.get(r.location_id), "order", 99999))
        for record in records:
            card = LocationEditor(record, getattr(by_id.get(record.location_id), "area_type", "Location"))
            card.changed.connect(self.refresh_matches)
            self.cards.append(card)
        self.summary_title.setText(f"{profile.region_name} locations" if profile else "Locations")
        self.summary_detail.setText(f"{len(records)} locations supplied by the {profile.display_name if profile else run.game} run profile.")
        self.error.hide()
        self.search.clear()
        self.refresh_matches()

    def refresh_matches(self):
        query = self.search.text().strip().casefold()
        columns = 2 if self.width() >= 1000 else 1
        counts = Counter(card.status for card in self.cards)
        for status, value in self.counts.items():
            value.setText(str(counts[status]))
        visible = tuple(card for card in self.cards if card.matches(query))
        self.match_count.setText(f"{len(visible)} matching locations")
        if visible == self._visible_cards and columns == self._columns:
            return
        for card in self.cards:
            self.grid.removeWidget(card)
        # QGridLayout retains empty row/column constraints after reflowing.
        for row in range(self.grid.rowCount()):
            self.grid.setRowMinimumHeight(row, 0)
            self.grid.setRowStretch(row, 0)
        visible_set = set(visible)
        for card in self.cards:
            card.setVisible(card in visible_set)
        for index, card in enumerate(visible):
            self.grid.addWidget(card, index // columns, index % columns)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if columns == 2 else 0)
        self._columns = columns
        self._visible_cards = visible

    def save(self):
        run = self.store.active_run
        if not run or run.run_id != self.run_id:
            self.error.setText("The active run changed. Reopen Manage locations for the current run.")
            self.error.show()
            return
        changed = [card for card in self.cards if card.record() != card.original]
        if any(run.encounters.get(card.original.location_id) != card.original for card in changed):
            self.error.setText("An edited location was updated by live tracking. Cancel and reopen this page to review its latest record.")
            self.error.show()
            return
        try:
            if changed:
                self.store.update_encounters(run.run_id, tuple(card.record() for card in changed))
        except (OSError, ValueError, KeyError) as error:
            self.error.setText(f"Could not save location changes: {error}")
            self.error.show()
            return
        self.saved.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 700
        direction = QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
        self.header_layout.setDirection(direction)
        self.summary_layout.setDirection(direction)
        if (2 if self.width() >= 1000 else 1) != self._columns:
            self.refresh_matches()
