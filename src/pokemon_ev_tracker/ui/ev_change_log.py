"""Observed EV gains with native sprite rows and stable relative timestamps."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.party_selector import PokemonSprite, pokemon_name
from pokemon_ev_tracker.ui.training_panels import caption


class HistoryList(QListWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(100, self.viewport().width())
        for index in range(self.count()):
            item = self.item(index)
            if self.itemWidget(item) is not None:
                item.setSizeHint(QSize(width, 50))
        self.doItemsLayout()


class EvChangeLogWidget(QFrame):
    def __init__(self, clear_callback, parent=None):
        super().__init__(parent)
        self._records, self._times = [], []
        self._compact = False
        self._species = {}
        self._entry_species = {}
        self._ages = []
        self.setObjectName("evChangeLog")
        self.setProperty("uiRole", "panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 6)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        header = QWidget()
        header.setFixedHeight(40)
        heading = QHBoxLayout(header)
        heading.setContentsMargins(12, 0, 12, 0)
        self.heading = caption("RECENT EV GAINS")
        heading.addWidget(self.heading, 1)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setProperty("buttonRole", "trainingText")
        self.clear_button.clicked.connect(clear_callback)
        heading.addWidget(self.clear_button)
        layout.addWidget(header)
        self.list = HistoryList()
        self.list.setObjectName("evChangeHistory")
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMinimumHeight(84)
        self.list.setMaximumHeight(168)
        layout.addWidget(self.list)
        self.age_timer = QTimer(self)
        self.age_timer.setInterval(1000)
        self.age_timer.timeout.connect(self._update_ages)
        self.age_timer.start()
        self.render([], False)

    def set_party(self, cards):
        for card in cards.values():
            p = card.get("pokemon")
            if p:
                name = pokemon_name(p)
                for alias in (name, p.species, f"{name} ({p.species})"):
                    self._species[alias] = p.species_id

    def render(self, records, compact):
        self._compact = compact
        previous = defaultdict(deque)
        for record, timestamp in zip(self._records, self._times):
            previous[record].append(timestamp)
        self._times = [previous[record].popleft() if previous[record] else time.monotonic() for record in records]
        self._records = list(records)
        self._entry_species = {r: s for r, s in self._entry_species.items() if r in records}
        self._ages = []
        self.list.clear()
        entries = list(zip(self._records, self._times))
        if compact:
            entries = entries[-4:]
        self.list.setFixedHeight(min(150, max(50, len(entries) * 50)))
        if not entries:
            self.list.addItem("No EV changes recorded.")
        for record, timestamp in reversed(entries):
            full_text, compact_text = record
            parts = compact_text.split(" — ", 1)
            gain, name = (parts[0], parts[1]) if len(parts) == 2 else (compact_text, "Observed gain")
            age = int(time.monotonic() - timestamp)
            relative = f"{age}s" if age < 60 else f"{age // 60}m" if age < 3600 else f"{age // 3600}h"
            species = self._entry_species.get(record) or self._species.get(name)
            if species:
                self._entry_species[record] = species
            item = QListWidgetItem(f"{name}   {gain}   {relative}")
            item.setForeground(QColor(0, 0, 0, 0))
            item.setSizeHint(QSize(max(100, self.list.viewport().width()), 50))
            item.setToolTip(full_text)
            self.list.addItem(item)
            row = QWidget()
            content = QHBoxLayout(row)
            content.setContentsMargins(12, 3, 12, 3)
            content.setSpacing(10)
            sprite = PokemonSprite(28, padding=0, reference_art=True, reference_bounds=(22, 28))
            sprite.setObjectName("historySprite")
            sprite.set_species(species)
            content.addWidget(sprite)
            title = caption(name.split(" (", 1)[0], "readoutName")
            content.addWidget(title, 1)
            content.addWidget(caption(gain, "historyGain"))
            age_label = caption(relative, "historyAge")
            content.addWidget(age_label)
            self._ages.append((age_label, timestamp, item, name, gain))
            self.list.setItemWidget(item, row)
        self.list.doItemsLayout()
        self.list.scrollToTop()

    def _update_ages(self):
        for label, timestamp, item, name, gain in self._ages:
            age = int(time.monotonic() - timestamp)
            relative = f"{age}s" if age < 60 else f"{age // 60}m" if age < 3600 else f"{age // 3600}h"
            label.setText(relative)
            item.setText(f"{name}   {gain}   {relative}")
