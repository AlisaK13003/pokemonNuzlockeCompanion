"""Factual party and PC inspection, using provider-owned decoded data only."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.icons import chevron_icon
from pokemon_ev_tracker.ui.item_sprite_loader import get_item_sprite
from pokemon_ev_tracker.ui.party_card import EV_STAT_LABELS
from pokemon_ev_tracker.ui.party_selector import PokemonSprite, pokemon_identity, pokemon_name
from pokemon_ev_tracker.ui.theme import refresh_style

IV_LOW_MAX = 15
IV_EXCELLENT_MIN = 26


def iv_quality(value):
    return "low" if value <= IV_LOW_MAX else "excellent" if value >= IV_EXCELLENT_MIN else "average"


def label(text="", role="small"):
    widget = QLabel(text)
    widget.setProperty("uiRole", role)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    return widget


class InspectionCard(QFrame):
    expanded = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("uiRole", "panel")
        self.pokemon = None
        self.setObjectName("inspectionCard")
        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header_widget = QWidget()
        self.header_widget.setObjectName("inspectionHeader")
        self.header = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.header_widget)
        self.header.setContentsMargins(24, 11, 24, 11)
        self.header.setSpacing(20)
        self.sprite = PokemonSprite(64, padding=0, reference_art=True, reference_bounds=(52, 60))
        self.header.addWidget(self.sprite)
        names = QVBoxLayout()
        names.setSpacing(3)
        self.species = label("", "inspectionSpecies")
        self.name = label("", "inspectionName")
        self.meta = label("", "inspectionMeta")
        for widget in (self.species, self.name, self.meta):
            names.addWidget(widget)
        self.header.addLayout(names, 1)
        self.hp_widget = QWidget()
        self.hp_widget.setFixedWidth(178)
        hp = QVBoxLayout(self.hp_widget)
        hp.setContentsMargins(0, 0, 48, 0)
        hp.setSpacing(10)
        self.hp = label("", "inspectionMeta")
        self.hp_bar = QProgressBar()
        self.hp_bar.setObjectName("inspectionHpBar")
        self.hp_bar.setTextVisible(False)
        self.hp_bar.setFixedSize(130, 3)
        hp.addWidget(self.hp)
        hp.addWidget(self.hp_bar)
        self.header.addWidget(self.hp_widget)
        self.item_badge = QFrame()
        self.item_badge.setObjectName("inspectionItemBadge")
        self.item_badge.setFixedSize(175, 46)
        item_row = QHBoxLayout(self.item_badge)
        item_row.setContentsMargins(5, 4, 5, 4)
        item_row.setSpacing(6)
        self.item_icon = QLabel()
        self.item_icon.setObjectName("inspectionItemIcon")
        self.item_icon.setFixedSize(28, 28)
        self.item_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.item_name = label("No held item", "inspectionItemName")
        item_row.addWidget(self.item_icon)
        item_row.addWidget(self.item_name, 1)
        self.header.addWidget(self.item_badge)
        self.toggle = QPushButton()
        self.toggle.setIcon(chevron_icon())
        self.toggle.setObjectName("inspectionToggle")
        self.toggle.setCheckable(True)
        self.toggle.setFixedSize(20, 28)
        self.toggle.setAccessibleName("Expand Pokémon details")
        self.toggle.toggled.connect(self._toggle)
        self.header.addWidget(self.toggle)
        root.addWidget(self.header_widget)
        self.details = QWidget()
        self.details.setObjectName("inspectionDetails")
        detail = QVBoxLayout(self.details)
        detail.setContentsMargins(24, 18, 24, 24)
        detail.setSpacing(0)
        self.facts = QWidget()
        self.facts_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.facts)
        self.facts_layout.setContentsMargins(0, 0, 0, 0)
        self.facts_layout.setSpacing(0)
        self.fact_values = {}
        self.fact_columns = []
        self.fact_dividers = []
        for key, caption in (("nature", "NATURE"), ("ability", "ABILITY"),
                             ("friendship", "FRIENDSHIP"), ("item", "HELD ITEM")):
            cell = QWidget()
            column = QVBoxLayout(cell)
            column.setContentsMargins(16, 0, 16, 0)
            column.setSpacing(10)
            column.addWidget(label(caption, "inspectionKicker"))
            value = label("", "inspectionFact")
            self.fact_values[key] = value
            if key == "nature":
                nature_row = QHBoxLayout()
                nature_row.setSpacing(12)
                nature_row.addWidget(value)
                self.nature_increase = label("", "inspectionNatureUp")
                self.nature_decrease = label("", "inspectionNatureDown")
                self.nature_increase.setWordWrap(False)
                self.nature_decrease.setWordWrap(False)
                nature_row.addWidget(self.nature_increase)
                nature_row.addWidget(self.nature_decrease)
                nature_row.addStretch(1)
                column.addLayout(nature_row)
            elif key == "item":
                item_value = QHBoxLayout()
                self.detail_item_icon = QLabel()
                self.detail_item_icon.setFixedSize(22, 22)
                self.detail_item_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                item_value.addWidget(self.detail_item_icon)
                item_value.addWidget(value, 1)
                column.addLayout(item_value)
            else:
                column.addWidget(value)
            self.fact_columns.append(cell)
            self.facts_layout.addWidget(cell, 1)
            if key != "item":
                divider = QFrame()
                divider.setObjectName("inspectionFactDivider")
                divider.setFixedSize(1, 40)
                self.facts_layout.addWidget(divider)
                self.fact_dividers.append(divider)
        detail.addWidget(self.facts)
        rule = QFrame()
        rule.setObjectName("inspectionDetailRule")
        rule.setFixedHeight(1)
        detail.addSpacing(18)
        detail.addWidget(rule)
        detail.addSpacing(18)
        iv_header = QHBoxLayout()
        iv_header.addWidget(label("INDIVIDUAL VALUES", "inspectionKicker"), 1)
        self.legend = QWidget()
        legend = QHBoxLayout(self.legend)
        legend.setContentsMargins(0, 0, 0, 0)
        legend.setSpacing(10)
        for caption, color in (("Low", "#ff718b"), ("Average", "#ffcb69"), ("Excellent", "#57e5a2")):
            entry = QHBoxLayout()
            entry.setSpacing(5)
            dot = QLabel()
            dot.setFixedSize(6, 6)
            dot.setStyleSheet(f"background: {color}; border-radius: 3px;")
            entry.addWidget(dot)
            entry.addWidget(label(caption, "inspectionLegend"))
            legend.addLayout(entry)
        iv_header.addWidget(self.legend)
        detail.addLayout(iv_header)
        detail.addSpacing(10)
        self.iv_grid = QGridLayout()
        self.iv_grid.setHorizontalSpacing(6)
        self.iv_grid.setVerticalSpacing(6)
        self.iv_rows, self.iv_values, self.iv_bars = {}, {}, {}
        captions = {"hp": "HP", "attack": "Attack", "defense": "Defense",
                    "special_attack": "Sp. Atk", "special_defense": "Sp. Def", "speed": "Speed"}
        for index, (stat, _) in enumerate(EV_STAT_LABELS):
            row = QFrame()
            row.setObjectName("inspectionIvRow")
            row.setFixedHeight(42)
            contents = QHBoxLayout(row)
            contents.setContentsMargins(10, 0, 10, 0)
            contents.setSpacing(10)
            title = label(captions[stat], "inspectionKicker")
            title.setFixedWidth(56)
            contents.addWidget(title)
            bar = QProgressBar()
            bar.setObjectName("inspectionIvBar")
            bar.setRange(0, 31)
            bar.setTextVisible(False)
            bar.setFixedHeight(7)
            bar.setMinimumWidth(40)
            contents.addWidget(bar, 1)
            value = label("— / 31", "inspectionIvValue")
            value.setFixedWidth(48)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            contents.addWidget(value)
            self.iv_grid.addWidget(row, index // 2, index % 2)
            self.iv_rows[stat], self.iv_values[stat], self.iv_bars[stat] = row, value, bar
        detail.addLayout(self.iv_grid)
        root.addWidget(self.details)
        self.details.hide()

    def _toggle(self, checked):
        self.details.setVisible(checked)
        self.toggle.setIcon(chevron_icon(checked))
        if checked:
            self.expanded.emit()

    def refresh(self, pokemon):
        self.pokemon = pokemon
        valid = bool(getattr(pokemon, "checksum_valid", False))
        self.sprite.set_species(getattr(pokemon, "species_id", None))
        self.name.setText(pokemon_name(pokemon))
        self.species.setText(getattr(pokemon, "species", "").upper())
        level = getattr(pokemon, "level", None)
        nature = getattr(pokemon, "nature_name", None) if valid else None
        up, down = getattr(pokemon, "nature_increased_stat", None), getattr(pokemon, "nature_decreased_stat", None)
        caption = dict(EV_STAT_LABELS)
        self.meta.setText(f"Lv. {level if level is not None else '—'} · {nature or 'Unknown nature'}")
        self.meta.setToolTip(f"+{caption.get(up, up)} / −{caption.get(down, down)}" if up and down else "Neutral nature")
        current, maximum = getattr(pokemon, "current_hp", None), getattr(pokemon, "max_hp", None)
        self.hp.setText(f"{current} / {maximum} HP" if valid and current is not None and maximum else "HP unavailable")
        self.hp_bar.setVisible(valid and current is not None and bool(maximum))
        self.hp_bar.setRange(0, max(1, maximum or 1))
        self.hp_bar.setValue(current or 0)
        item = getattr(pokemon, "held_item_name", None) if valid else None
        self.item_name.setText(item or "No held item")
        self.item_icon.setPixmap(get_item_sprite(item, 20))
        self.detail_item_icon.setPixmap(get_item_sprite(item, 20))
        friendship = getattr(pokemon, "friendship", None) if valid else None
        ability = getattr(pokemon, "ability_name", None) if valid else None
        self.fact_values["nature"].setText(nature or "Unavailable")
        has_modifiers = bool(valid and up and down)
        self.nature_increase.setText(f"+ {caption.get(up, up)}" if has_modifiers else "")
        self.nature_decrease.setText(f"− {caption.get(down, down)}" if has_modifiers else "")
        self.nature_increase.setVisible(has_modifiers)
        self.nature_decrease.setVisible(has_modifiers)
        self.fact_values["ability"].setText(ability or "Unavailable")
        self.fact_values["friendship"].setText(
            f'<span style="color:#e9eef7">{friendship if friendship is not None else "—"}</span>'
            '<span style="color:#8595ad;font-size:13px;font-weight:400"> / 255</span>')
        self.fact_values["item"].setText(item or "None")
        for stat, _ in EV_STAT_LABELS:
            value = getattr(pokemon, f"{stat}_iv", None) if valid else None
            quality = iv_quality(value) if value is not None else "unknown"
            color = {"low": "#ff718b", "average": "#ffcb69", "excellent": "#57e5a2"}.get(quality, "#67758c")
            self.iv_values[stat].setText(
                f'<span style="color:{color};font-size:13px;font-weight:600">{value if value is not None else "—"}</span>'
                ' <span style="color:#8595ad;font-size:11px"> /31</span>')
            bar = self.iv_bars[stat]
            bar.setValue(value or 0)
            bar.setProperty("ivQuality", iv_quality(value) if value is not None else "unknown")
            refresh_style(bar)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.header.setDirection(QBoxLayout.Direction.LeftToRight if self.width() >= 800 else QBoxLayout.Direction.TopToBottom)
        self.facts_layout.setDirection(QBoxLayout.Direction.LeftToRight if self.width() >= 800 else QBoxLayout.Direction.TopToBottom)
        self.facts_layout.setSpacing(0 if self.width() >= 800 else 12)
        for divider in self.fact_dividers:
            divider.setVisible(self.width() >= 800)
        columns = 2 if self.width() >= 620 else 1
        for index, row in enumerate(self.iv_rows.values()):
            self.iv_grid.addWidget(row, index // columns, index % columns)


class PartyStatsView(QWidget):
    selected_slot_changed = Signal(int)

    def __init__(self, parent=None, *, compact=False):
        super().__init__(parent)
        self.selected_slot = 1
        self._identity = None
        self.cards = {}
        self._members = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        self.empty = label("Waiting for a valid party snapshot.")
        self.content_layout.addWidget(self.empty)
        self.content_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll)

    def refresh(self, cards, connected=True):
        members = {slot: card["pokemon"] for slot, card in cards.items()
                   if connected and card.get("pokemon") is not None}
        self._members = members
        for slot in list(self.cards):
            if slot not in members:
                widget = self.cards.pop(slot)
                self.content_layout.removeWidget(widget)
                widget.deleteLater()
        for slot, pokemon in members.items():
            if slot not in self.cards:
                widget = InspectionCard()
                widget.expanded.connect(lambda key=slot: self._select(key))
                self.cards[slot] = widget
                self.content_layout.insertWidget(self.content_layout.count() - 1, widget)
            self.cards[slot].refresh(pokemon)
        self.empty.setVisible(not members)
        if self._identity is not None:
            self.selected_slot = next((slot for slot, p in members.items()
                                       if pokemon_identity(p) == self._identity), self.selected_slot)
        if members and self.selected_slot not in members:
            self.selected_slot = next(iter(members))
        self.set_selected_slot(self.selected_slot)

    def _select(self, slot):
        self.set_selected_slot(slot)
        self.selected_slot_changed.emit(slot)

    def set_selected_slot(self, slot):
        self.selected_slot = slot
        if slot in self._members:
            self._identity = pokemon_identity(self._members[slot])
        for key, widget in self.cards.items():
            widget.toggle.blockSignals(True)
            widget.toggle.setChecked(key == slot)
            widget.details.setVisible(key == slot)
            widget.toggle.setIcon(chevron_icon(key == slot))
            widget.toggle.blockSignals(False)


class BoxView(PartyStatsView):
    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.box_index = 1
        self._records = ()
        self._ready = False
        self.box_count = getattr(provider.pc_storage, "box_count", 0)
        self.slots_per_box = getattr(provider.pc_storage, "slots_per_box", 0)
        controls = QFrame()
        controls.setProperty("uiRole", "panel")
        row = QHBoxLayout(controls)
        row.setContentsMargins(20, 16, 20, 16)
        row.addWidget(label("LIVE PC STORAGE\nSomeone's PC", "pokemonName"), 1)
        self.previous = QPushButton("‹")
        self.next = QPushButton("›")
        self.current_box = label("BOX 01", "pokemonName")
        self.previous.clicked.connect(lambda: self.change_box(-1))
        self.next.clicked.connect(lambda: self.change_box(1))
        row.addWidget(self.previous)
        row.addWidget(self.current_box)
        row.addWidget(self.next)
        self.monitor = label("Waiting for PC monitoring")
        row.addWidget(self.monitor, 1)
        self.content_layout.insertWidget(0, controls)

    def change_box(self, delta):
        self.box_index = min(max(1, self.box_index + delta), max(1, self.box_count))
        self._render_box()

    def refresh_storage(self, state, ready):
        self._ready = bool(ready)
        self._records = getattr(state, "valid_pokemon", ()) if ready else ()
        self.box_count = getattr(state, "box_count", self.box_count)
        self.slots_per_box = getattr(state, "slots_per_box", self.slots_per_box)
        self._render_box()

    def _render_box(self):
        records = tuple(p for p in self._records if p.box_index == self.box_index)
        project = getattr(self.provider, "boxed_inspection", None)
        cards = {p.slot_index: {"pokemon": project(p)} for p in records} if project else {}
        self.refresh(cards, connected=self._ready)
        self.current_box.setText(f"BOX {self.box_index:02d}")
        self.monitor.setText(f"● Monitoring · {len(records)} / {self.slots_per_box} occupied" if self._ready else "Monitoring unavailable or stale")
        self.empty.setText("This box is empty." if self._ready else "Waiting for a fresh, validated PC snapshot.")
        self.previous.setEnabled(self.box_index > 1)
        self.next.setEnabled(self.box_index < self.box_count)
