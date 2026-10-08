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
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.core.ev_training import BattleEVRecommendation
from pokemon_ev_tracker.core.nuzlocke.fights import FightTimeline
from pokemon_ev_tracker.ui.compact_training import CompactTrainingView  # noqa: F401
from pokemon_ev_tracker.ui.inspection_views import PartyStatsView

CompactPartyStatsView = PartyStatsView

from pokemon_ev_tracker.ui.party_card import EV_STAT_LABELS
from pokemon_ev_tracker.ui.party_selector import PartySelector, PokemonSprite, pokemon_name
from pokemon_ev_tracker.ui.theme import refresh_style
from pokemon_ev_tracker.ui.training_focus_card import install_training_focus

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
        self.sprite = PokemonSprite(48 if compact else 120)
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
    focus_stat_requested = Signal(int, str)

    def __init__(self, parent=None, *, compact=False) -> None:
        super().__init__(compact=compact, parent=parent)
        self.setObjectName("compactTrainingView" if compact else "trainingView")
        self.body = QWidget()
        self.body_layout = QGridLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10 if compact else 12)
        install_training_focus(self)
        # One page scroll contains the roster, focus/friendship stack, and battle column.
        self.layout().removeWidget(self.selector)
        self.selector.setMaximumWidth(360)
        self.body_layout.addWidget(self.selector, 0, 0, Qt.AlignmentFlag.AlignTop)
        self.focus_stack = QWidget()
        self.focus_stack_layout = QVBoxLayout(self.focus_stack)
        self.focus_stack_layout.setContentsMargins(0, 0, 0, 0)
        self.focus_stack_layout.setSpacing(16)
        self.focus_stack_layout.addWidget(self.ev_panel)
        self.body_layout.addWidget(self.focus_stack, 0, 1, Qt.AlignmentFlag.AlignTop)
        self.runtime_side = QWidget()
        side = QBoxLayout(QBoxLayout.Direction.TopToBottom, self.runtime_side)
        self.side_layout = side
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(10)
        self.runtime_hosts = {}
        for name in ("opponent", "recommendations", "history"):
            host = QWidget()
            host.setObjectName(f"{name}Host")
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            host_layout.setSpacing(0)
            host_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            side.addWidget(host)
            self.runtime_hosts[name] = host
        side.addStretch(1)
        self.body_layout.addWidget(self.runtime_side, 0, 2, Qt.AlignmentFlag.AlignTop)
        fight_panel, fight_layout = _panel("raisedPanel")
        self.fight_panel = fight_panel
        fight_panel.setParent(self.content)
        fight_layout.addWidget(_label("NEXT FIGHT", "technicalHeading"))
        self.next_fight_name = _label("No active run", "pokemonName")
        self.next_fight_detail = _label("Level cap unavailable", "small")
        fight_layout.addWidget(self.next_fight_name)
        fight_layout.addWidget(self.next_fight_detail)
        fight_panel.hide()
        self.content_layout.addWidget(self.body)
        friendship = QWidget()
        friendship.setObjectName("friendshipHost")
        friendship_layout = QVBoxLayout(friendship)
        friendship_layout.setContentsMargins(0, 0, 0, 0)
        self.runtime_hosts["friendship"] = friendship
        self.focus_stack_layout.addWidget(friendship)
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
        self.total_value.setText(str(sum(evs.values())) if valid else "—")
        for stat, _ in EV_STAT_LABELS:
            self.ev_values[stat].setText(str(evs.get(stat, 0)) if valid else "—")
            self.ev_meters[stat].setValue(evs.get(stat, 0) if valid else 0)
            focused = stat in allowed_stats
            self.focus_chips[stat].setProperty("evFocused", focused)
            self.focus_chips[stat].blockSignals(True)
            self.focus_chips[stat].setChecked(focused)
            self.focus_chips[stat].set_focus(focused)
            self.focus_chips[stat].setEnabled(valid)
            self.focus_chips[stat].blockSignals(False)
            refresh_style(self.focus_chips[stat])
        focus = [name for stat, name in EV_STAT_LABELS if stat in allowed_stats]
        self.saved_check.setVisible(bool(focus) and pokemon is not None)
        self.focus_summary.setText(f"Preferences saved to {pokemon_name(pokemon)}'s identity" if focus and pokemon else "Select every stat you're happy to gain")
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
        self._reflow()
        self.actions_layout.setDirection(
            QBoxLayout.Direction.TopToBottom if self.compact and self.width() < 450
            else QBoxLayout.Direction.LeftToRight
        )

    def _reflow(self):
        if not hasattr(self, "focus_stack"):
            return
        for widget in (self.selector, self.focus_stack, self.runtime_side):
            self.body_layout.removeWidget(widget)
        for column in range(3):
            self.body_layout.setColumnStretch(column, 0)
        if self.width() >= 1280:
            for column, (widget, stretch) in enumerate(((self.selector, 2), (self.focus_stack, 4), (self.runtime_side, 2))):
                self.body_layout.addWidget(widget, 0, column, Qt.AlignmentFlag.AlignTop)
                self.body_layout.setColumnStretch(column, stretch)
            self.side_layout.setDirection(QBoxLayout.Direction.TopToBottom)
        elif self.width() >= 760:
            self.body_layout.addWidget(self.selector, 0, 0, Qt.AlignmentFlag.AlignTop)
            self.body_layout.addWidget(self.focus_stack, 0, 1, Qt.AlignmentFlag.AlignTop)
            self.body_layout.addWidget(self.runtime_side, 1, 0, 1, 2, Qt.AlignmentFlag.AlignTop)
            self.body_layout.setColumnStretch(0, 1)
            self.body_layout.setColumnStretch(1, 2)
            self.side_layout.setDirection(QBoxLayout.Direction.LeftToRight)
        else:
            for row, widget in enumerate((self.selector, self.focus_stack, self.runtime_side)):
                self.body_layout.addWidget(widget, row, 0, Qt.AlignmentFlag.AlignTop)
            self.side_layout.setDirection(QBoxLayout.Direction.TopToBottom)
            self.body_layout.setColumnStretch(0, 1)
        self.selector.setMaximumWidth(360 if self.width() >= 760 else 16777215)
        horizontal = self.side_layout.direction() == QBoxLayout.Direction.LeftToRight
        for index in range(3):
            self.side_layout.setStretch(index, 1 if horizontal else 0)
        self.side_layout.setStretch(3, 0 if horizontal else 1)


def _recommendation_reason(result: BattleEVRecommendation) -> str:
    """Format core-provided reasons; all yield comparison stays in the core."""
    parts = []
    for values, description in ((result.allowed_yields, "allowed"),
                                (result.unwanted_yields, "unwanted")):
        for stat, _name in EV_STAT_LABELS:
            if values.get(stat, 0):
                parts.append(f"+{values[stat]} {dict(EV_STAT_LABELS)[stat]} {description}")
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
        self.setObjectName("partyReadout")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        header = QWidget()
        header.setFixedHeight(40)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(12, 0, 12, 0)
        self.heading = _label("PARTY READOUT", "trainingMicro")
        header_row.addWidget(self.heading, 1)
        header_row.addWidget(_label("Against active foe", "readoutDetail"))
        layout.addWidget(header)
        self.empty = _label("Waiting for live party data", "emptyState", wrap=True)
        layout.addWidget(self.empty)
        self.rows = {}
        for slot in range(1, 7):
            row = QFrame()
            row.setObjectName("readoutRow")
            row.setFixedHeight(50)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 0, 12, 0)
            row_layout.setSpacing(6)
            sprite = PokemonSprite(34, padding=0, reference_art=True, reference_bounds=(26, 34))
            name = _label("", "readoutName", wrap=True)
            name.setMinimumWidth(35)
            badge = _label("", "readoutBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedHeight(18)
            detail = _label("", "readoutDetail", wrap=True)
            row_layout.addWidget(sprite)
            copy_widget = QWidget()
            copy_widget.setFixedHeight(34)
            copy = QVBoxLayout(copy_widget)
            copy.setContentsMargins(0, 0, 0, 0)
            copy.setSpacing(2)
            name.setFixedHeight(18)
            detail.setFixedHeight(14)
            copy.addWidget(name)
            copy.addWidget(detail)
            row_layout.addWidget(copy_widget, 1, Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(badge)
            layout.addWidget(row)
            self.rows[slot] = (row, sprite, name, badge, detail)
            row.hide()
        self.note = _label("Allowed training EVs · items and Pokérus may alter gains.", "micro", wrap=True)
        layout.addWidget(self.note)
        self.note.hide()

    def set_compact(self, compact: bool, selected_slot: int = 1) -> None:
        self._compact = compact
        self._selected_slot = selected_slot
        self.heading.setVisible(not compact)
        self.note.hide()
        self.setToolTip("Recommendations use allowed training EVs; items and Pokérus may alter gains.")
        self.layout().setContentsMargins(*((6, 4, 6, 4) if compact else (1, 1, 1, 1)))
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
