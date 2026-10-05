"""Selected-Pokémon Training and Party Stats presentation workspaces.

The existing card fields are a transitional presentation adapter: their decoded
Pokémon and training preferences stay owned by MainWindow. These widgets do not
read RAM, write preferences, or calculate battle recommendations.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.core.ev_training import BattleEVRecommendation
from pokemon_ev_tracker.core.nuzlocke.fights import FightTimeline
from pokemon_ev_tracker.ui.item_sprite_loader import get_item_sprite
from pokemon_ev_tracker.ui.party_card import EV_STAT_LABELS
from pokemon_ev_tracker.ui.party_selector import PartySelector, PokemonSprite, pokemon_name
from pokemon_ev_tracker.ui.theme import refresh_style

SHORT_STATS = dict(zip((key for key, _ in EV_STAT_LABELS), ("HP", "ATK", "DEF", "SPA", "SPD", "SPE")))


def _label(text: str = "", role: str = "muted", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("uiRole", role)
    label.setWordWrap(wrap)
    label.setMinimumWidth(0)
    return label


def _panel(role: str = "panel") -> tuple[QFrame, QVBoxLayout]:
    panel = QFrame()
    panel.setProperty("uiRole", role)
    panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(8)
    return panel, layout


def _bar(maximum: int = 1, stat: str | None = None) -> QProgressBar:
    result = QProgressBar()
    result.setRange(0, maximum)
    result.setTextVisible(False)
    result.setFixedHeight(6)
    if stat:
        result.setProperty("statKey", stat)
    return result


def _text(card: Mapping, key: str, default: str = "—") -> str:
    value = card.get(key)
    return value.text() if value is not None and hasattr(value, "text") else default


class _Identity(QWidget):
    def __init__(self, *, compact=False, parent=None) -> None:
        super().__init__(parent)
        self.compact = compact
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.sprite = PokemonSprite(48 if compact else 88)
        layout.addWidget(self.sprite)
        details = QVBoxLayout()
        details.setSpacing(3)
        self.name = _label("Select a Pokémon", "pokemonName" if compact else "identityName", wrap=True)
        self.species = _label("Waiting for live party data", "pokemonMeta", wrap=True)
        self.hp = _label("HP — / —", "small")
        self.hp_bar = _bar(1)
        self.hp_bar.setProperty("uiRole", "hpBar")
        for widget in (self.name, self.species, self.hp, self.hp_bar):
            details.addWidget(widget)
        layout.addLayout(details, 1)

    def refresh(self, pokemon) -> None:
        self.sprite.set_species(getattr(pokemon, "species_id", None))
        if pokemon is None:
            self.name.setText("Select a Pokémon")
            self.species.setText("Waiting for live party data")
            self.hp.setText("HP — / —")
            self.hp_bar.setValue(0)
            return
        self.name.setText(pokemon_name(pokemon))
        level = getattr(pokemon, "level", None)
        self.species.setText(f"{pokemon.species} · Lv. {level if level is not None else '—'}")
        current, maximum = getattr(pokemon, "current_hp", None), getattr(pokemon, "max_hp", None)
        self.hp.setText(f"HP {current if current is not None else '—'} / {maximum or '—'}")
        self.hp_bar.setRange(0, max(maximum or 1, 1))
        self.hp_bar.setValue(current or 0)


class _PartyWorkspace(QWidget):
    selected_slot_changed = Signal(int)

    def __init__(self, *, compact=False, inspection=False, parent=None) -> None:
        super().__init__(parent)
        self.compact = compact
        self._cards = {}
        self._connected = False
        self.setProperty("compact", compact)
        self.setProperty("inspection", inspection)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10 if compact else 12)
        self.selector = PartySelector(compact=compact, inspection=inspection)
        self.selector.selected_slot_changed.connect(self._selection_changed)
        root.addWidget(self.selector)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10 if compact else 12)
        self.empty = _label("Connect BizHawk to see your party.", "emptyState", wrap=True)
        self.content_layout.addWidget(self.empty)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

    @property
    def selected_slot(self) -> int:
        return self.selector.selected_slot

    def set_selected_slot(self, slot: int) -> None:
        self.selector.set_selected_slot(slot)
        self._render_selected()

    def _selection_changed(self, slot: int) -> None:
        self._render_selected()
        self.selected_slot_changed.emit(slot)

    def refresh(self, cards: Mapping[int, Mapping], connected: bool = True) -> None:
        self._cards = cards
        self._connected = connected
        members = tuple(card["pokemon"] for card in cards.values()
                        if connected and card.get("pokemon") is not None)
        self.selector.set_party(members)
        self.empty.setVisible(not members)
        self.empty.setText("No Pokémon in party. Waiting for a valid party snapshot." if connected
                           else "Connect BizHawk to see your party.")
        self._render_selected()

    def _selected_card(self) -> Mapping:
        card = self._cards.get(self.selected_slot, {})
        return card if self._connected and card.get("pokemon") is not None else {}

    def _render_selected(self) -> None:
        pass


class TrainingView(_PartyWorkspace):
    """One operational EV workspace alongside the existing live runtime panels."""

    edit_focus_requested = Signal(int)
    clear_focus_requested = Signal(int)

    def __init__(self, parent=None, *, compact=False) -> None:
        super().__init__(compact=compact, parent=parent)
        self.setObjectName("compactTrainingView" if compact else "trainingView")
        self.body = QWidget()
        self.body_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10 if compact else 12)
        self.ev_panel, left = _panel()
        self.identity = _Identity(compact=compact)
        left.addWidget(self.identity)
        title = QHBoxLayout()
        title.addWidget(_label("EFFORT VALUES", "technicalHeading"))
        title.addStretch()
        self.total = _label("— / 510 total", "small")
        title.addWidget(self.total)
        left.addLayout(title)
        self.ev_values, self.ev_rows = {}, {}
        for stat, name in EV_STAT_LABELS:
            row = QFrame()
            row.setProperty("uiRole", "metricRow")
            row.setProperty("statKey", stat)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 2 if compact else 9, 8, 2 if compact else 9)
            row_layout.setSpacing(8)
            label = _label(SHORT_STATS[stat] if compact else name.upper(), "micro")
            label.setFixedWidth(30 if compact else 68)
            value = _label("—", "pokemonMeta" if compact else "metricValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            value.setMinimumWidth(52)
            row_layout.addWidget(label)
            row_layout.addStretch(1)
            row_layout.addWidget(value)
            left.addWidget(row)
            self.ev_rows[stat], self.ev_values[stat] = row, value
        self.focus_title = _label("TRAINING EVS", "technicalHeading")
        left.addWidget(self.focus_title)
        focus_row = QHBoxLayout()
        focus_row.setSpacing(4)
        self.focus_chips = {}
        for stat, name in EV_STAT_LABELS:
            chip = _label(SHORT_STATS[stat], "focusChip")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setToolTip(f"{name} · allowed when selected; otherwise unwanted")
            focus_row.addWidget(chip, 1)
            self.focus_chips[stat] = chip
        left.addLayout(focus_row)
        self.focus_summary = _label("No focus set", "small", wrap=True)
        left.addWidget(self.focus_summary)
        actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.actions_layout = actions
        self.edit_button = QPushButton("Set training focus")
        self.edit_button.setProperty("buttonRole", "primary")
        self.clear_button = QPushButton("Clear Focus")
        self.edit_button.clicked.connect(lambda: self.edit_focus_requested.emit(self.selected_slot))
        self.clear_button.clicked.connect(lambda: self.clear_focus_requested.emit(self.selected_slot))
        actions.addWidget(self.edit_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        left.addLayout(actions)
        self.ram_state = _label("Waiting for party data", "micro", wrap=True)
        left.addWidget(self.ram_state)
        if not compact:
            left.addStretch(1)
        self.body_layout.addWidget(self.ev_panel, 3)
        self.runtime_side = QWidget()
        side = QVBoxLayout(self.runtime_side)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(10)
        self.runtime_hosts = {}
        for name in ("opponent", "recommendations", "history"):
            host = QWidget()
            host.setObjectName(f"{name}Host")
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            host_layout.setSpacing(0)
            side.addWidget(host)
            self.runtime_hosts[name] = host
        side.addStretch(1)
        self.body_layout.addWidget(self.runtime_side, 2)
        fight_panel, fight_layout = _panel("raisedPanel")
        fight_layout.addWidget(_label("NEXT FIGHT", "technicalHeading"))
        self.next_fight_name = _label("No active run", "pokemonName")
        self.next_fight_detail = _label("Level cap unavailable", "small")
        fight_layout.addWidget(self.next_fight_name)
        fight_layout.addWidget(self.next_fight_detail)
        self.content_layout.addWidget(fight_panel)
        self.content_layout.addWidget(self.body)
        friendship = QWidget()
        friendship.setObjectName("friendshipHost")
        friendship_layout = QVBoxLayout(friendship)
        friendship_layout.setContentsMargins(0, 0, 0, 0)
        self.runtime_hosts["friendship"] = friendship
        self.content_layout.addWidget(friendship)
        self.content_layout.addStretch(1)
        self._runtime_widgets = {}
        self._render_selected()

    def set_fight_timeline(self, timeline: FightTimeline | None) -> None:
        if timeline is None:
            self.next_fight_name.setText("No active run")
            self.next_fight_detail.setText("Level cap unavailable")
        elif timeline.next_fight is None:
            self.next_fight_name.setText("All major fights complete")
            self.next_fight_detail.setText(
                f"{timeline.completed_count} / {timeline.total_fights}")
        else:
            fight = timeline.next_fight
            self.next_fight_name.setText(fight.name)
            self.next_fight_detail.setText(
                f"Cap {fight.effective_level_cap} · {fight.over_count} over"
                if self.compact else
                f"{fight.category} · Cap {fight.effective_level_cap} · "
                f"Fight {fight.order}/{fight.total_fights} · "
                f"{fight.ready_count} ready · {fight.over_count} over cap"
            )

    def set_runtime_widgets(self, *, opponent=None, recommendations=None,
                            history=None, friendship=None) -> None:
        """Reparent existing widgets without duplicating connections or controller state."""
        for name, widget in (("opponent", opponent), ("recommendations", recommendations),
                             ("history", history), ("friendship", friendship)):
            if widget is not None:
                self.runtime_hosts[name].layout().addWidget(widget)
                self._runtime_widgets[name] = widget
                if name == "recommendations" and hasattr(widget, "set_compact"):
                    widget.set_compact(self.compact, self.selected_slot)
                if name == "history" and hasattr(widget, "list"):
                    widget.list.setMinimumHeight(45 if self.compact else 100)
                    widget.list.setMaximumHeight(72 if self.compact else 160)
                if name == "friendship" and hasattr(widget, "set_compact"):
                    widget.set_compact(self.compact)
                widget.show()
        self.runtime_side.setVisible(any(self.runtime_hosts[name].layout().count()
                                         for name in ("opponent", "recommendations", "history")))

    def _render_selected(self) -> None:
        if not hasattr(self, "identity"):
            return
        card = self._selected_card()
        pokemon = card.get("pokemon")
        self.identity.refresh(pokemon)
        valid = pokemon is not None and getattr(pokemon, "checksum_valid", False)
        preference = card.get("training_preference")
        allowed_stats = preference.allowed_stats if preference is not None else frozenset()
        evs = getattr(pokemon, "evs", {}) if valid else {}
        self.total.setText(f"{sum(evs.values()) if valid else '—'} / 510 total")
        for stat, _ in EV_STAT_LABELS:
            self.ev_values[stat].setText(str(evs.get(stat, 0)) if valid else "—")
            focused = stat in allowed_stats
            self.focus_chips[stat].setProperty("evFocused", focused)
            refresh_style(self.focus_chips[stat])
        focus = [name for stat, name in EV_STAT_LABELS if stat in allowed_stats]
        self.focus_summary.setText(" · ".join(focus) if focus else "No focus set")
        self.edit_button.setText("Edit training focus" if allowed_stats else "Set training focus")
        self.edit_button.setEnabled(valid)
        self.clear_button.setEnabled(valid and preference is not None)
        self.ram_state.setText(_text(card, "checksum", "Waiting for party data"))
        if self.compact and pokemon is not None:
            level = getattr(pokemon, "level", None)
            self.identity.name.setText(
                f"{pokemon_name(pokemon)} · {pokemon.species} · L{level if level else '—'}")
        recommendations = getattr(self, "_runtime_widgets", {}).get("recommendations")
        if (recommendations is not None and
                recommendations.parentWidget() is self.runtime_hosts["recommendations"] and
                hasattr(recommendations, "set_compact")):
            recommendations.set_compact(self.compact, self.selected_slot)
        # The compact HUD keeps all six factual EV values, with short labels and
        # small controls, rather than a shrunken six-card grid.

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        threshold = 700 if self.compact else 850
        self.body_layout.setDirection(QBoxLayout.Direction.LeftToRight if self.width() >= threshold
                                      else QBoxLayout.Direction.TopToBottom)
        self.actions_layout.setDirection(
            QBoxLayout.Direction.TopToBottom if self.compact and self.width() < 450
            else QBoxLayout.Direction.LeftToRight
        )


class CompactTrainingView(TrainingView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, compact=True)
        self.ram_state.hide()
        self.identity.sprite.hide()
        self.identity.species.hide()
        self.identity.hp.hide()
        self.identity.hp_bar.hide()
        self.ev_panel.layout().setContentsMargins(8, 8, 8, 8)
        self.ev_panel.layout().setSpacing(4)


class PartyStatsView(_PartyWorkspace):
    """Inspection view with a single identity panel, stats, and four move slots."""

    def __init__(self, parent=None, *, compact=False) -> None:
        super().__init__(compact=compact, inspection=True, parent=parent)
        self.setObjectName("compactPartyStatsView" if compact else "partyStatsView")
        identity_panel, identity_layout = _panel()
        self.identity_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.identity_row.setSpacing(12)
        self.identity = _Identity(compact=compact)
        self.identity_row.addWidget(self.identity, 3)
        item_panel, item_layout = _panel("raisedPanel")
        item_row = QHBoxLayout()
        self.item_icon = QLabel()
        self.item_icon.setFixedSize(30 if compact else 42, 30 if compact else 42)
        self.item_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        item_row.addWidget(self.item_icon)
        item_copy = QVBoxLayout()
        item_copy.addWidget(_label("HELD ITEM", "micro"))
        self.item_name = _label("—", "heldItem", wrap=True)
        item_copy.addWidget(self.item_name)
        item_row.addLayout(item_copy, 1)
        item_layout.addLayout(item_row)
        self.identity_row.addWidget(item_panel, 2)
        facts, facts_layout = _panel("raisedPanel")
        self.nature = _label("Nature —", "pokemonMeta", wrap=True)
        self.ability = _label("Ability —", "small", wrap=True)
        self.friendship = _label("Friendship — / 255", "small", wrap=True)
        self.friendship_bar = _bar(255)
        self.friendship_bar.setProperty("uiRole", "friendshipBar")
        for widget in (self.nature, self.ability, self.friendship, self.friendship_bar):
            facts_layout.addWidget(widget)
        self.identity_row.addWidget(facts, 3)
        identity_layout.addLayout(self.identity_row)
        self.content_layout.addWidget(identity_panel)
        self.mid = QWidget()
        self.mid_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.mid)
        self.mid_layout.setContentsMargins(0, 0, 0, 0)
        self.mid_layout.setSpacing(12)
        self.stats_panel, stats_layout = _panel()
        stats_layout.addWidget(_label("STATS / IV" if compact else "BATTLE STATS + IVS", "technicalHeading"))
        self.stat_values, self.iv_values, self.stat_bars, self.stat_names = {}, {}, {}, {}
        for stat, name in EV_STAT_LABELS:
            row, row_layout = _panel("metricRow")
            row_layout.setContentsMargins(8, 3 if compact else 9, 8, 3 if compact else 9)
            contents = QHBoxLayout()
            label = _label(SHORT_STATS[stat] if compact else name.upper(), "micro")
            label.setMinimumWidth(30 if compact else 65)
            value = _label("—", "pokemonMeta" if compact else "metricValue")
            meter = _bar(stat=stat)
            iv = _label("— IV", "small" if compact else "inspectionValue")
            iv.setAlignment(Qt.AlignmentFlag.AlignRight)
            contents.addWidget(label)
            contents.addWidget(value)
            contents.addWidget(meter, 1)
            contents.addWidget(iv)
            row_layout.addLayout(contents)
            stats_layout.addWidget(row)
            self.stat_values[stat], self.iv_values[stat] = value, iv
            self.stat_bars[stat], self.stat_names[stat] = meter, label
            meter.setVisible(not compact)
        stats_layout.addStretch(1)
        self.mid_layout.addWidget(self.stats_panel, 2)
        self.moves_panel, moves_layout = _panel()
        moves_layout.addWidget(_label("MOVES · FOUR SLOTS", "technicalHeading"))
        self.move_names, self.move_details = [], []
        self.move_types, self.move_categories, self.move_pp, self.move_cards = [], [], [], []
        for slot in range(4):
            move, move_layout = _panel("moveCard")
            move_layout.setContentsMargins(9, 6 if compact else 9, 9, 6 if compact else 9)
            move_layout.setSpacing(3)
            name = _label(f"{slot + 1:02d}  Empty move slot", "pokemonMeta", wrap=True)
            pp = _label("", "inspectionValue")
            pp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            title = QHBoxLayout()
            title.addWidget(name, 1)
            title.addWidget(pp)
            move_layout.addLayout(title)
            badges = QHBoxLayout()
            move_type = _label("", "moveTypeBadge")
            category = _label("", "moveCategoryBadge")
            badges.addWidget(move_type)
            badges.addWidget(category)
            badges.addStretch(1)
            move_layout.addLayout(badges)
            details = _label("", "micro", wrap=True)
            move_layout.addWidget(details)
            if compact:
                details.hide()
                category.hide()
            moves_layout.addWidget(move)
            self.move_names.append(name)
            self.move_details.append(details)
            self.move_types.append(move_type)
            self.move_categories.append(category)
            self.move_pp.append(pp)
            self.move_cards.append(move)
        moves_layout.addStretch(1)
        self.mid_layout.addWidget(self.moves_panel, 3)
        self.content_layout.addWidget(self.mid)
        snapshot, snapshot_layout = _panel()
        snapshot_heading = QHBoxLayout()
        snapshot_heading.addWidget(_label("EV SNAPSHOT", "technicalHeading"))
        if not compact:
            snapshot_heading.addWidget(_label("Factual values", "micro"))
        snapshot_heading.addStretch(1)
        self.ev_total = _label("— / 510", "small")
        snapshot_heading.addWidget(self.ev_total)
        snapshot_layout.addLayout(snapshot_heading)
        snapshot_values = QHBoxLayout()
        snapshot_values.setSpacing(5)
        self.ev_values = {}
        for stat, _ in EV_STAT_LABELS:
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(_label(SHORT_STATS[stat], "micro"))
            value = _label("—", "metricValue")
            value.setProperty("statKey", stat)
            column.addWidget(value)
            snapshot_values.addLayout(column, 1)
            self.ev_values[stat] = value
        snapshot_layout.addLayout(snapshot_values)
        self.ram_state = _label("Waiting for party data", "micro", wrap=True)
        snapshot_layout.addWidget(self.ram_state)
        self.content_layout.addWidget(snapshot)
        self.content_layout.addStretch(1)
        if compact:
            self.item_icon.hide()
            self.friendship_bar.hide()
            self.identity.hp_bar.hide()
            self.ram_state.hide()
            item_layout.setContentsMargins(8, 6, 8, 6)
            facts_layout.setContentsMargins(8, 6, 8, 6)
            facts_layout.setSpacing(3)
            for layout in (identity_layout, stats_layout, moves_layout, snapshot_layout):
                layout.setContentsMargins(8, 8, 8, 8)
                layout.setSpacing(4)
        self._render_selected()

    def _render_selected(self) -> None:
        if not hasattr(self, "identity"):
            return
        card = self._selected_card()
        pokemon = card.get("pokemon")
        self.identity.refresh(pokemon)
        valid = pokemon is not None and getattr(pokemon, "checksum_valid", False)
        self.item_name.setText(_text(card, "item_name") if valid else "—")
        self.item_icon.clear()
        if valid:
            self.item_icon.setPixmap(get_item_sprite(getattr(pokemon, "held_item_name", None), 30))
        nature = getattr(pokemon, "nature_name", "—") if valid else "—"
        increased = getattr(pokemon, "nature_increased_stat", None) if valid else None
        decreased = getattr(pokemon, "nature_decreased_stat", None) if valid else None
        change = (f" · +{SHORT_STATS.get(increased, increased)} −{SHORT_STATS.get(decreased, decreased)}"
                  if increased and decreased else "")
        self.nature.setText(f"{nature}{change}" if self.compact else f"Nature · {nature}{change}")
        ability = (getattr(pokemon, "ability_name", None) or f"Unknown #{pokemon.ability_id}") if valid else "—"
        self.ability.setText(f"Ability · {ability}")
        friendship = getattr(pokemon, "friendship", None) if valid else None
        self.friendship.setText(f"Friendship {friendship if friendship is not None else '—'} / 255")
        self.friendship_bar.setValue(friendship or 0)
        values = card.get("stat_values", {})
        numeric = [int(label.text()) for label in values.values() if label.text().isdigit()]
        scale = max(numeric, default=1)
        for stat, _ in EV_STAT_LABELS:
            value = values.get(stat)
            text = value.text() if value is not None and pokemon is not None else "—"
            self.stat_values[stat].setText(text)
            iv = card.get("iv_values", {}).get(stat)
            self.iv_values[stat].setText(f"{iv.text() if valid and iv is not None else '—'} IV")
            self.iv_values[stat].setToolTip("Individual value · 0–31")
            self.stat_bars[stat].setRange(0, scale)
            self.stat_bars[stat].setValue(int(text) if text.isdigit() else 0)
            self.stat_bars[stat].setToolTip("Relative to this Pokémon's highest current battle stat")
            self.stat_names[stat].setProperty("natureRole", "up" if stat == increased else "down" if stat == decreased else "neutral")
            refresh_style(self.stat_names[stat])
            self.ev_values[stat].setText(str(pokemon.evs.get(stat, 0)) if valid else "—")
        self.ev_total.setText(f"{sum(pokemon.evs.values()) if valid else '—'} / 510")
        moves = card.get("move_displays", ()) if valid else ()
        for index, label in enumerate(self.move_names):
            move = moves[index] if index < len(moves) else None
            name = (move.name if move is not None else
                    "Unavailable" if pokemon is not None and not valid else "Empty move slot")
            label.setText(f"{index + 1:02d}  {name}")
            type_badge = self.move_types[index]
            category_badge = self.move_categories[index]
            pp_label = self.move_pp[index]
            detail = self.move_details[index]
            if move is None:
                type_badge.hide()
                category_badge.hide()
                pp_label.clear()
                detail.clear()
                self.move_cards[index].setToolTip("")
                continue
            type_badge.setText(move.type_name or f"ID {move.move_id}")
            type_badge.setProperty("moveType", (move.type_name or "unknown").lower())
            refresh_style(type_badge)
            type_badge.show()
            category_badge.setText(move.category or "Unknown")
            category_badge.setProperty("moveCategory", (move.category or "unknown").lower())
            refresh_style(category_badge)
            category_badge.setVisible(not self.compact)
            pp_text = f"{move.current_pp} / {move.max_pp if move.max_pp is not None else '--'} PP"
            pp_label.setText(pp_text)
            power = move.power if move.power is not None else "—"
            accuracy = f"{move.accuracy}%" if move.accuracy is not None else "—"
            priority = (f"  ·  Priority {move.priority:+d}" if move.priority else "")
            detail.setText(f"Power {power}  ·  Accuracy {accuracy}{priority}")
            self.move_cards[index].setToolTip(
                f"{move.name} · {move.type_name or f'ID {move.move_id}'} · "
                f"{move.category or 'Unknown'} · {detail.text()} · {pp_text}"
            )
        self.ram_state.setText(_text(card, "checksum", "Waiting for party data"))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.mid_layout.setDirection(QBoxLayout.Direction.LeftToRight if self.width() >= 580
                                     else QBoxLayout.Direction.TopToBottom)
        self.identity_row.setDirection(QBoxLayout.Direction.LeftToRight
                                       if self.width() >= (600 if self.compact else 760)
                                       else QBoxLayout.Direction.TopToBottom)


class CompactPartyStatsView(PartyStatsView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, compact=True)


def _recommendation_reason(result: BattleEVRecommendation) -> str:
    """Format core-provided reasons; all yield comparison stays in the core."""
    parts = []
    for values, description in ((result.allowed_yields, "allowed"),
                                (result.unwanted_yields, "unwanted")):
        for stat, _name in EV_STAT_LABELS:
            if values.get(stat, 0):
                parts.append(f"+{values[stat]} {SHORT_STATS[stat]} {description}")
    return " · ".join(parts) or "No positive EV yield"


class RecommendationPanel(QFrame):
    """Shared normal/compact renderer for game-neutral core recommendations."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._compact = False
        self._selected_slot = 1
        self._active_slots = set()
        self.results: dict[int, BattleEVRecommendation] = {}
        self.setProperty("uiRole", "panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        self.heading = _label("PARTY RECOMMENDATIONS", "technicalHeading")
        layout.addWidget(self.heading)
        self.empty = _label("Waiting for live party data", "emptyState", wrap=True)
        layout.addWidget(self.empty)
        self.rows = {}
        for slot in range(1, 7):
            row = QFrame()
            row.setProperty("uiRole", "metricRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(5, 3, 5, 3)
            row_layout.setSpacing(6)
            sprite = PokemonSprite(24)
            name = _label("", "slotName", wrap=True)
            name.setMinimumWidth(35)
            badge = _label("", "statusBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            detail = _label("", "micro", wrap=True)
            row_layout.addWidget(sprite)
            row_layout.addWidget(name, 2)
            row_layout.addWidget(badge)
            row_layout.addWidget(detail, 3)
            layout.addWidget(row)
            self.rows[slot] = (row, sprite, name, badge, detail)
            row.hide()
        self.note = _label("Allowed training EVs · items and Pokérus may alter gains.", "micro", wrap=True)
        layout.addWidget(self.note)

    def set_compact(self, compact: bool, selected_slot: int = 1) -> None:
        self._compact = compact
        self._selected_slot = selected_slot
        self.heading.setVisible(not compact)
        self.note.setVisible(not compact)
        self.setToolTip("Recommendations use allowed training EVs; items and Pokérus may alter gains.")
        self.layout().setContentsMargins(*((6, 4, 6, 4) if compact else (10, 10, 10, 10)))
        for slot, (row, sprite, name, _badge, _detail) in self.rows.items():
            row.setVisible(slot in self._active_slots and (not compact or slot == selected_slot))
            sprite.setVisible(not compact)
            name.show()

    def refresh(self, cards: Mapping, recommendations: Mapping[int, BattleEVRecommendation] | None = None,
                *, battle_state: str = "idle") -> None:
        count = 0
        self._active_slots.clear()
        self.results.clear()
        recommendations = recommendations or {}
        for slot, (row, sprite, name, badge, detail) in self.rows.items():
            card = cards.get(slot, {})
            pokemon = card.get("pokemon")
            row.setVisible(pokemon is not None and
                           (not self._compact or slot == self._selected_slot))
            if pokemon is None:
                sprite.set_species(None)
                name.clear()
                badge.clear()
                badge.setProperty("status", "idle")
                detail.clear()
                continue
            count += 1
            self._active_slots.add(slot)
            sprite.set_species(pokemon.species_id)
            name.setText(pokemon_name(pokemon))
            preference = card.get("training_preference")
            result = recommendations.get(slot)
            if not getattr(pokemon, "checksum_valid", False):
                state, reason = "unavailable", "Waiting for valid party data"
            elif preference is None or not preference.allowed_stats:
                state, reason = "no-focus", "No focus set"
            elif battle_state == "idle":
                state, reason = "idle", "No current battle"
            elif battle_state != "active" or result is None:
                state, reason = "unavailable", "Battle EV data unavailable"
            else:
                state = result.status.value
                reason = "No focus set" if state == "no-focus" else _recommendation_reason(result)
                self.results[slot] = result
            badge.setText(state.replace("-", " ").upper())
            badge.setProperty("status", state)
            refresh_style(badge)
            detail.setText(reason)
        self.empty.setVisible(not count)
