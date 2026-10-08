"""Responsive run dashboard projections around the existing store and handlers."""

from collections import Counter

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.app_shell import PageHeading
from pokemon_ev_tracker.ui.icons import chevron_icon, sort_order_icon
from pokemon_ev_tracker.ui.inspection_views import label
from pokemon_ev_tracker.ui.nuzlocke_panels import RunColumns
from pokemon_ev_tracker.ui.party_selector import PokemonSprite, pokemon_name
from pokemon_ev_tracker.ui.theme import refresh_style

VISIBLE_STATUSES = ("NOT_ENCOUNTERED", "CAUGHT", "FAILED", "DEAD")


class LedgerActionButton(QPushButton):
    """Footer action with the reference's trailing native chevron."""

    def __init__(self, text):
        super().__init__()
        self.setProperty("buttonRole", "ledgerAction")
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)
        self.copy = label(text, "ledgerActionText")
        self.copy.setWordWrap(False)
        self.arrow = label()
        self.arrow.setFixedSize(12, 12)
        for widget in (self.copy, self.arrow):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.copy)
        layout.addWidget(self.arrow)
        self.setText(text)

    def setText(self, text):
        self.copy.setText(text)
        self.setAccessibleName(text)

    def text(self):
        return self.copy.text()

    def sizeHint(self):
        return self.layout().sizeHint() + QSize(4, 0)

    def minimumSizeHint(self):
        return self.sizeHint()

    def set_chevron(self, down=None):
        icon = chevron_icon(down is not None)
        pixmap = icon.pixmap(12, 12)
        if down is False:
            from PySide6.QtGui import QTransform
            pixmap = pixmap.transformed(QTransform().rotate(180))
        self.arrow.setPixmap(pixmap)


class FightNode(QPushButton):
    def __init__(self, index):
        super().__init__()
        self.index = index
        self.fight = None
        self.setFixedSize(100, 72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = 12 if self.index == 0 else self.width() - 12 if self.index == 2 else self.width() // 2
        align = (Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter, Qt.AlignmentFlag.AlignRight)[self.index]
        painter.setFont(QFont("DM Mono", 9))
        painter.setPen(QColor("#627087"))
        painter.drawText(0, 0, self.width(), 14, align, ("PREVIOUS", "CURRENT", "NEXT")[self.index])
        completed = bool(self.fight and self.fight.completed)
        color = "#57e5a2" if completed else "#8fb8ff" if self.index == 1 and self.fight else "#354155"
        painter.setPen(QPen(QColor(color), 1))
        painter.setBrush(QColor(color if self.index == 1 and self.fight else "#101720"))
        painter.drawEllipse(center - 9, 19, 18, 18)
        painter.setPen(QColor("#101720" if self.index == 1 and self.fight else color))
        if completed:
            painter.drawLine(center - 4, 28, center - 1, 31)
            painter.drawLine(center - 1, 31, center + 4, 24)
        else:
            painter.drawText(center - 9, 19, 18, 18, Qt.AlignmentFlag.AlignCenter,
                             str(self.fight.order) if self.fight else "—")
        name_font = QFont("Pixelify Sans", 11)
        painter.setFont(name_font)
        painter.setPen(QColor("#d5dfef" if self.index == 1 else "#6f7b8e"))
        text = QFontMetrics(name_font).elidedText(self.fight.name if self.fight else "—", Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(0, 43, self.width(), 25, align, text)


class FightLine(QWidget):
    fight_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(76)
        self.nodes = ()
        self.buttons = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for index in range(3):
            button = FightNode(index)
            button.clicked.connect(lambda checked=False, index=index: self._clicked(index))
            self.buttons.append(button)
            row.addWidget(button)
            if index < 2:
                row.addStretch(1)

    def _clicked(self, index):
        if index < len(self.nodes) and self.nodes[index]:
            self.fight_requested.emit(self.nodes[index].fight_id)

    def refresh(self, timeline):
        if not timeline or not timeline.fights:
            self.nodes = ()
        else:
            current = timeline.next_fight
            index = timeline.fights.index(current) if current else len(timeline.fights) - 1
            self.nodes = tuple(timeline.fights[i] if 0 <= i < len(timeline.fights) else None
                               for i in (index - 1, index, index + 1))
        for index, button in enumerate(self.buttons):
            fight = self.nodes[index] if index < len(self.nodes) else None
            caption = ("PREVIOUS", "CURRENT", "NEXT")[index]
            button.setText(f"{caption}\n{'✓' if fight and fight.completed else fight.order if fight else '—'}\n{fight.name if fight else '—'}")
            button.setEnabled(fight is not None)
            button.fight = fight
            button.setAccessibleName(button.text())
            button.update()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        centers = [self.buttons[0].x() + 12, self.buttons[1].geometry().center().x(), self.buttons[2].geometry().right() - 12]
        painter.setPen(QPen(QColor("#29313f"), 2))
        painter.drawLine(centers[0], 28, centers[-1], 28)
        painter.setPen(QPen(QColor("#8faeff"), 2))
        painter.drawLine(centers[0], 28, centers[1], 28)


class PartyReadiness(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("uiRole", "panel")
        self.setObjectName("runReadiness")
        self.setMinimumHeight(153)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 16, 12, 12)
        heading_row = QHBoxLayout()
        self.heading = label("CURRENT PARTY", "runKicker")
        heading_row.addWidget(self.heading)
        self.count = label("0 / 6 Pokémon", "runMicro")
        heading_row.addWidget(self.count, 1, Qt.AlignmentFlag.AlignRight)
        root.addLayout(heading_row)
        root.addWidget(label("Readiness for the next major fight", "runHint"))
        root.addSpacing(10)
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        self.tiles = []
        for _ in range(6):
            tile = QFrame()
            tile.setProperty("uiRole", "metricRow")
            tile.setObjectName("readinessTile")
            tile.setFixedHeight(67)
            row = QHBoxLayout(tile)
            row.setContentsMargins(6, 4, 6, 4)
            row.setSpacing(4)
            sprite = PokemonSprite(43, reference_art=True, reference_bounds=(35, 44))
            row.addWidget(sprite)
            copy = QVBoxLayout()
            name, meta, status = label("Empty slot", "readinessName"), label("", "readinessMeta"), label("", "readinessState")
            copy.setSpacing(2)
            copy.addWidget(name)
            copy.addWidget(meta)
            row.addLayout(copy, 1)
            row.addWidget(status, 0, Qt.AlignmentFlag.AlignTop)
            self.tiles.append((tile, sprite, name, meta, status))
        root.addLayout(self.grid)
        self._reflow()

    def refresh(self, cards, timeline):
        members = [card["pokemon"] for card in cards.values() if card.get("pokemon")]
        fight = timeline.next_fight if timeline else None
        cap = fight.effective_level_cap if fight else None
        self.count.setText(f"{len(members)} / 6 Pokémon")
        for index, (tile, sprite, name, meta, status) in enumerate(self.tiles):
            pokemon = members[index] if index < len(members) else None
            sprite.set_species(getattr(pokemon, "species_id", None))
            name.setText(pokemon_name(pokemon) if pokemon else "Empty slot")
            meta.setText(f"{pokemon.species} · Lv. {pokemon.level or '—'}" if pokemon else "")
            over = bool(pokemon and cap and pokemon.level is not None and pokemon.level > cap)
            status.setText("OVER CAP" if over else "READY" if pokemon and cap and pokemon.level is not None else "—")
            status.setProperty("status", "mixed" if over else "good" if pokemon and cap else "idle")
            from pokemon_ev_tracker.ui.theme import refresh_style
            refresh_style(status)

    def _reflow(self):
        columns = 6 if self.width() >= 1150 else 3 if self.width() >= 650 else 2 if self.width() >= 420 else 1
        for index, (tile, *_) in enumerate(self.tiles):
            self.grid.addWidget(tile, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()


class LedgerRow(QPushButton):
    """Reusable visible row; filtering updates content instead of rebuilding widgets."""

    def __init__(self, edit_requested):
        super().__init__()
        self.setProperty("buttonRole", "ledgerRow")
        self.setMinimumWidth(0)
        self.location_id = None
        self._signature = None
        self.contents = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.contents.setContentsMargins(20, 12, 20, 12)
        self.contents.setSpacing(0)
        self.location = label("", "ledgerLocation")
        self.name = label("", "ledgerName")
        self.species = label("", "ledgerSpecies")
        self.badge = label("", "ledgerBadge")
        self.badge.setWordWrap(False)
        self.notes = label("", "ledgerNotes")
        for cell in (self.location, self.name, self.species, self.badge, self.notes):
            cell.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            cell.setTextFormat(Qt.TextFormat.PlainText)
        self.contents.addWidget(self.location, 25)
        copy = QVBoxLayout()
        copy.setSpacing(1)
        copy.addWidget(self.name)
        copy.addWidget(self.species)
        self.contents.addLayout(copy, 20)
        holder = QWidget()
        holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        badge_layout = QHBoxLayout(holder)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        badge_layout.addWidget(self.badge)
        badge_layout.addStretch(1)
        self.contents.addWidget(holder, 17)
        self.contents.addWidget(self.notes, 27)
        self.clicked.connect(lambda checked=False: edit_requested.emit(self.location_id))

    def refresh(self, record, area, narrow):
        signature = (record, area, narrow)
        if signature == self._signature:
            return
        self._signature = signature
        self.location_id = record.location_id
        self.location.setText(record.location)
        self.name.setText(record.nickname or record.species or "—")
        self.species.setText(record.species if record.nickname else "—")
        self.badge.setText(record.status.replace("_", " "))
        self.badge.setProperty("status", record.status)
        self.notes.setText(record.notes or "—")
        self.setProperty("openEncounter", record.status == "NOT_ENCOUNTERED")
        description = f"{record.location} · {area} · {self.name.text()} · {self.badge.text()} · {record.notes}"
        self.setAccessibleName(description)
        self.setToolTip(description)
        self.contents.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        self.setMinimumHeight(140 if narrow else 52)
        refresh_style(self)
        refresh_style(self.badge)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.property("openEncounter"):
            painter = QPainter(self)
            painter.fillRect(0, (self.height() - 28) // 2, 2, 28, QColor("#57e5a2"))


class EncounterLedger(QFrame):
    edit_requested = Signal(str)
    manage_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("uiRole", "panel")
        self.setObjectName("encounterLedger")
        self._status = ""
        self._run = None
        self._profile = None
        self._signature = None
        self._reverse = False
        self._expanded = False
        self._run_id = None
        self._row_pool = []
        self.visible_records = ()
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)
        ledger_header = QWidget()
        ledger_header.setObjectName("ledgerHeader")
        header = QVBoxLayout(ledger_header)
        header.setContentsMargins(22, 20, 22, 20)
        header.setSpacing(6)
        header.addWidget(label("ENCOUNTER LOCATIONS", "runKicker"))
        self.title = label("Encounter ledger", "ledgerTitle")
        header.addWidget(self.title)
        header.addWidget(label("Every eligible location in this run's game profile.", "runHint"))
        self.root.addWidget(ledger_header)
        filter_host = QFrame()
        filter_host.setObjectName("ledgerFilters")
        filters = QHBoxLayout(filter_host)
        self.filters = {}
        filters.setContentsMargins(18, 10, 18, 10)
        filters.setSpacing(5)
        for status, caption in (("", "All"), ("NOT_ENCOUNTERED", "Open"), ("CAUGHT", "Caught"), ("FAILED", "Failed"), ("DEAD", "Dead")):
            button = QPushButton(caption)
            button.setProperty("buttonRole", "ledgerFilter")
            button.setFixedHeight(36)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, status=status: self._filter(status))
            filters.addWidget(button)
            self.filters[status] = button
        self.root.addWidget(filter_host)
        self.reverse_button = QPushButton()
        self.reverse_button.setProperty("buttonRole", "ledgerAction")
        self.reverse_button.setFixedSize(34, 34)
        self.reverse_button.setIcon(sort_order_icon())
        self.reverse_button.setAccessibleName("Reverse status order")
        self.expand_button = LedgerActionButton("View all")
        self.expand_button.setProperty("primary", True)
        for button in (self.reverse_button, self.expand_button):
            button.setCheckable(True)
        self.reverse_button.toggled.connect(self._set_reverse)
        self.expand_button.toggled.connect(self._set_expanded)
        self.reverse_button.setToolTip("Reverse the status groups; locations within each group keep their game order.")
        self.table_heading = QWidget()
        self.table_heading.setObjectName("ledgerHeading")
        headings = QHBoxLayout(self.table_heading)
        headings.setContentsMargins(20, 10, 20, 10)
        headings.setSpacing(0)
        for caption, stretch in (("LOCATION", 25), ("ENCOUNTER", 20), ("STATUS", 17), ("NOTES", 27)):
            headings.addWidget(label(caption, "runMicro"), stretch)
        self.root.addWidget(self.table_heading)
        self.entries = QWidget()
        self.rows = QVBoxLayout(self.entries)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.root.addWidget(self.entries)
        self.footer_host = QFrame()
        self.footer_host.setObjectName("ledgerFooter")
        self.footer_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.footer_host)
        self.footer_layout.setContentsMargins(20, 8, 20, 8)
        self.footer = label("", "ledgerFooterText")
        self.footer_layout.addWidget(self.footer, 1)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(self.reverse_button)
        actions.addWidget(self.expand_button)
        self.manage_button = LedgerActionButton("Manage locations")
        self.manage_button.set_chevron()
        self.manage_button.clicked.connect(self.manage_requested)
        actions.addWidget(self.manage_button)
        self.footer_layout.addLayout(actions)
        self.root.addWidget(self.footer_host)

    def _filter(self, status):
        if status == self._status:
            self.filters[status].setChecked(True)
            return
        self._status = status
        self.refresh(self._run, self._profile)

    def _set_reverse(self, reverse):
        self._reverse = reverse
        self.refresh(self._run, self._profile)

    def _set_expanded(self, expanded):
        self._expanded = expanded
        self.refresh(self._run, self._profile)

    def refresh(self, run, profile):
        self._run, self._profile = run, profile
        run_id = run.run_id if run else None
        if run_id != self._run_id:
            self._expanded = False
            self._run_id = run_id
        narrow = self.width() < 800
        records = tuple(run.encounters.values()) if run else ()
        locations = tuple(profile.locations) if profile else ()
        signature = (run_id, locations, narrow, self._status, self._reverse, self._expanded, records)
        if signature == self._signature:
            return
        self._signature = signature
        region = getattr(profile, "region_name", "")
        self.title.setText(f"{region} encounter ledger" if region else "Encounter ledger")
        self.table_heading.setVisible(not narrow)
        counts = Counter(record.status for record in records)
        for status, button in self.filters.items():
            button.setChecked(status == self._status)
            caption = {"": "All", "NOT_ENCOUNTERED": "Open", "CAUGHT": "Caught", "FAILED": "Failed", "DEAD": "Dead"}[status]
            button.setText(f"{caption}  {counts[status] if status else len(records)}")
        status_order = ("FAILED", "DEAD", "CAUGHT", "NOT_ENCOUNTERED") if self._reverse else (
            "NOT_ENCOUNTERED", "CAUGHT", "DEAD", "FAILED")
        priority = {status: index for index, status in enumerate(status_order)}
        order = {location.location_id: location.order for location in locations}
        matching = sorted((record for record in records if not self._status or record.status == self._status),
                          key=lambda record: (priority.get(record.status, 4),
                                              order.get(record.location_id, 99999), record.location_id))
        self.visible_records = tuple(matching if self._expanded else matching[:10])
        for button, checked in ((self.reverse_button, self._reverse), (self.expand_button, self._expanded)):
            button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(False)
        self.expand_button.setText("Collapse" if self._expanded else "View all")
        self.expand_button.set_chevron(not self._expanded)
        self.expand_button.setEnabled(len(matching) > 10 or self._expanded)
        self.footer_layout.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        by_location = {location.location_id: location for location in locations}
        self.entries.setUpdatesEnabled(False)
        try:
            while len(self._row_pool) < len(self.visible_records):
                row = LedgerRow(self.edit_requested)
                self._row_pool.append(row)
                self.rows.addWidget(row)
            for index, row in enumerate(self._row_pool):
                if index >= len(self.visible_records):
                    row.hide()
                    continue
                record = self.visible_records[index]
                area = getattr(by_location.get(record.location_id), "area_type", "—")
                row.refresh(record, area, narrow)
                row.show()
        finally:
            self.entries.setUpdatesEnabled(True)
        self.footer.setText(f"Showing {len(self.visible_records)} of {len(matching)} matching locations")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh(self._run, self._profile)


def install_run_design(view):
    view.navigation.hide()
    view.summary_label.hide()
    view.run_header.setObjectName("activeRunHeader")
    view.run_header.content.setContentsMargins(0, 6, 0, 8)
    view.run_eyebrow = label("ACTIVE NUZLOCKE", "pageEyebrow")
    eyebrow_row = QHBoxLayout()
    eyebrow_row.setSpacing(12)
    eyebrow_row.addWidget(view.run_eyebrow)
    rule = QFrame()
    rule.setObjectName("headingAccentRule")
    rule.setFixedSize(40, 1)
    eyebrow_row.addWidget(rule)
    eyebrow_row.addStretch(1)
    identity = view.header_row.itemAt(0).layout()
    identity.insertLayout(0, eyebrow_row)
    identity.setSpacing(5)
    view.run_eyebrow.setFixedHeight(14)
    view.run_title_label.setFixedHeight(46)
    view.run_metadata_label.setFixedHeight(18)
    view.run_title_label.setProperty("uiRole", "pageHeaderTitle")
    view.run_title_label.setWordWrap(False)
    view.run_metadata_label.setProperty("uiRole", "pageSubtitle")
    view.run_library_button = QPushButton("Run Library  ›")
    view.run_library_button.clicked.connect(lambda: view.show_section("Run Library"))
    view.finish_run_button = QPushButton("Finish run")
    view.finish_run_button.setProperty("statusRole", "warning")
    view.finish_run_button.setProperty("buttonRole", "finishRun")
    view.run_library_button.setProperty("buttonRole", "runLibrary")
    for button in (view.finish_run_button, view.run_library_button):
        button.setFixedHeight(35)
    view.finish_run_button.clicked.connect(view._finish_run)
    view.header_row.addWidget(view.finish_run_button, 0, Qt.AlignmentFlag.AlignTop)
    view.header_row.addWidget(view.run_library_button, 0, Qt.AlignmentFlag.AlignTop)
    view.run_back_button = QPushButton("‹  Active run")
    view.run_back_button.clicked.connect(lambda: view.show_section("Dashboard"))
    view.run_back_button.hide()
    view.layout().insertWidget(0, view.run_back_button)
    view.fight_line = FightLine()
    view.fight_line.fight_requested.connect(view._edit_fight)
    view.cap_group.setObjectName("runHero")
    view.cap_group.content.setContentsMargins(28, 24, 28, 18)
    view.cap_group.heading.hide()
    content = view.cap_group.content
    # Reuse the existing factual labels and action buttons inside the hero.
    widgets = []
    layouts = []
    while content.count():
        item = content.takeAt(0)
        if item.widget():
            widgets.append(item.widget())
        elif item.layout():
            layouts.append(item.layout())
    view.hero_run_state = label("ACTIVE RUN", "runActiveState")
    state_row = QHBoxLayout()
    state_row.addWidget(view.hero_run_state)
    state_row.addStretch(1)
    state_row.addWidget(view.cap_progress_label)
    view.cap_progress_label.setProperty("uiRole", "runMicro")
    content.addLayout(state_row)
    content.addStretch(1)
    hero_row = QHBoxLayout()
    copy = QVBoxLayout()
    copy.setSpacing(4)
    view.hero_fight_title = PageHeading()
    view.hero_fight_title.setProperty("fitTitle", True)
    view.hero_fight_title.setProperty("uiRole", "runFightTitle")
    view.hero_fight_title.setFixedHeight(44)
    copy.addWidget(view.hero_fight_title)
    view.cap_category_label.setProperty("uiRole", "runFightDescription")
    view.cap_category_label.setFixedHeight(20)
    copy.addWidget(view.cap_category_label)
    copy.addStretch(1)
    hero_row.addLayout(copy, 1)
    cap_copy = QVBoxLayout()
    cap_copy.setSpacing(0)
    for widget in (label("LEVEL CAP", "runMicro"),):
        widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap_copy.addWidget(widget)
    view.hero_cap_value = label("—", "runCapValue")
    view.hero_cap_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
    cap_copy.addWidget(view.hero_cap_value)
    view.hero_cap_warning = label("", "runCapWarning")
    view.hero_cap_warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
    view.hero_cap_warning.setMinimumWidth(150)
    cap_copy.addWidget(view.hero_cap_warning)
    hero_row.addLayout(cap_copy)
    content.addLayout(hero_row)
    content.addStretch(1)
    content.addWidget(view.fight_line)
    for actions in layouts:
        for index in range(actions.count()):
            button = actions.itemAt(index).widget()
            if button:
                button.setProperty("buttonRole", "runAction")
                button.setMaximumHeight(24)
        content.addLayout(actions)
    content.addWidget(view.mark_won_button)
    for widget in widgets:
        if widget not in {view.cap_category_label, view.cap_progress_label, view.mark_won_button}:
            widget.hide()
    view.readiness = PartyReadiness()
    view.ledger = EncounterLedger()
    view.ledger.edit_requested.connect(view._edit_ledger_location)
    from pokemon_ev_tracker.ui.manage_locations import ManageLocations
    view.manage_locations = ManageLocations(view.store)
    view.sections.addTab(view.manage_locations, "Manage locations")
    view.ledger.manage_requested.connect(view._open_manage_locations)
    view.manage_locations.cancelled.connect(lambda: view.show_section("Dashboard"))
    view.manage_locations.saved.connect(view._locations_saved)
    view._party_cards = {}
    view.cap_category_label.setVisible(True)
    view.encounter_preview.parentWidget().hide()
    # Recompose the dashboard around one hero, readiness and the complete ledger.
    old_columns = view.cap_group.parentWidget().parentWidget()
    view._legacy_dashboard = old_columns
    view._run_content_layout.removeWidget(old_columns)
    old_columns.setParent(view)
    old_columns.hide()
    view.metric_strip.setProperty("uiRole", "panel")
    view.metric_strip.setObjectName("runMetrics")
    old_metrics = view.metric_strip.layout()
    metrics = [old_metrics.takeAt(0).widget() for _ in range(old_metrics.count())]
    from PySide6.QtWidgets import QGridLayout
    QWidget().setLayout(old_metrics)
    metric_grid = QGridLayout(view.metric_strip)
    metric_grid.setContentsMargins(16, 16, 16, 16)
    metric_grid.setSpacing(1)
    for index, widget in enumerate(metrics):
        widget.setObjectName("runMetricCell")
        widget.content.removeWidget(widget.heading)
        widget.content.addWidget(widget.heading)
        widget.content.setContentsMargins(8, 32, 8, 32)
        widget.content.setSpacing(3)
        widget.heading.setProperty("uiRole", "runMetricCaption")
        widget.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        metric_value = view.run_metrics[list(view.run_metrics)[index]]
        metric_value.setProperty("uiRole", "runMetricValue")
        metric_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        metric_grid.addWidget(widget, index // 2, index % 2)
    view.header_row.removeWidget(view.metric_strip)
    view._run_content_layout.insertWidget(1, RunColumns(view.cap_group, view.metric_strip))
    view._run_content_layout.insertWidget(2, view.readiness)
    view._run_content_layout.insertWidget(3, view.ledger)
    from pokemon_ev_tracker.ui.shiny_clause import ShinyClausePanel
    view.shiny_clause = ShinyClausePanel(view)
    view.shiny_clause.add_requested.connect(view._add_shiny_manually)
    view._run_content_layout.insertWidget(4, view.shiny_clause)
    for index, widget in enumerate((view.party_warning_label.parentWidget(), view.recent_events_label.parentWidget(), view.pc_panel, view.run_notes_input.parentWidget())):
        view._run_content_layout.insertWidget(5 + index, widget)
    for widget in (view.cap_default_label, view.healing_item_hint_label, view.party_detail_label,
                   view.party_readiness_label, view.fight_note_label):
        widget.hide()
    view.cap_group.setMinimumHeight(304)
    view.metric_strip.setMinimumHeight(304)
    from pokemon_ev_tracker.ui.run_library_cards import RunLibraryCards
    page = view.run_library_table.parentWidget()
    index = view.sections.indexOf(page)
    view.library_cards = RunLibraryCards(view)
    view.sections.removeWidget(page)
    page.setParent(view)
    page.hide()
    view._legacy_library_page = page
    view.sections.insertWidget(index, view.library_cards)
    view.run_library_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
