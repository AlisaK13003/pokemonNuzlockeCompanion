"""Tracker-only battle HUD; all recommendations are calculated by the core."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from pokemon_ev_tracker.ui.inspection_views import label
from pokemon_ev_tracker.ui.party_selector import PokemonSprite, pokemon_name
from pokemon_ev_tracker.ui.theme import refresh_style


class CompactTrainingView(QWidget):
    selected_slot_changed = Signal(int)
    edit_focus_requested = Signal(int)
    clear_focus_requested = Signal(int)
    focus_stat_requested = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_slot = 1
        self._cards = {}
        self._results = {}
        self._battle_state = "idle"
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.content = QWidget()
        self.content.setMaximumWidth(1120)
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        self.opponent_host = QWidget()
        QVBoxLayout(self.opponent_host).setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.opponent_host)
        self.party = QWidget()
        self.grid = QGridLayout(self.party)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.tiles = {}
        for slot in range(1, 7):
            tile = QFrame()
            tile.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            tile.setProperty("uiRole", "panel")
            row = QHBoxLayout(tile)
            row.setContentsMargins(8, 10, 8, 10)
            sprite = PokemonSprite(48)
            row.addWidget(sprite)
            copy = QVBoxLayout()
            name = label("", "slotName")
            species = label("", "micro")
            copy.addWidget(name)
            copy.addWidget(species)
            row.addLayout(copy, 1)
            badge = label("", "statusBadge")
            badge.setProperty("compactBadge", True)
            row.addWidget(badge)
            self.tiles[slot] = tile, sprite, name, species, badge
            tile.hide()
        body.addWidget(self.party)
        self.empty = label("Waiting for live party data.")
        body.addWidget(self.empty)
        root.addWidget(self.content, 0, Qt.AlignmentFlag.AlignTop)
        root.addStretch(1)

    def set_selected_slot(self, slot):
        self.selected_slot = slot

    def set_fight_timeline(self, timeline):
        pass

    def set_runtime_widgets(self, *, opponent=None, **kwargs):
        if opponent is not None:
            self.opponent_host.layout().addWidget(opponent)
            opponent.show()

    def refresh(self, cards, connected=True):
        self._cards = cards if connected else {}
        for slot, (tile, sprite, name, species, badge) in self.tiles.items():
            pokemon = self._cards.get(slot, {}).get("pokemon")
            tile.setVisible(pokemon is not None)
            sprite.set_species(getattr(pokemon, "species_id", None))
            if pokemon:
                name.setText(pokemon_name(pokemon))
                species.setText(f"{pokemon.species} · Lv. {pokemon.level or '—'}")
        self.empty.setVisible(not any(card.get("pokemon") for card in self._cards.values()))
        self.refresh_recommendations(self._results)
        self._reflow()

    def refresh_recommendations(self, results, *, battle_state=None):
        self._results = results
        if battle_state is not None:
            self._battle_state = battle_state
        for slot, (tile, sprite, name, species, badge) in self.tiles.items():
            card = self._cards.get(slot, {})
            pokemon = card.get("pokemon")
            preference = card.get("training_preference")
            state = ("unavailable" if pokemon and not pokemon.checksum_valid else
                     results[slot].status.value if slot in results else
                     "no-focus" if preference is None or not preference.allowed_stats else
                     "no battle" if self._battle_state == "idle" else "unavailable")
            badge.setText(state.replace("-", " ").upper())
            badge.setProperty("status", state)
            refresh_style(badge)

    def _reflow(self):
        columns = 6 if self.width() >= 1000 else 3 if self.width() >= 580 else 2
        for column in range(6):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        index = 0
        for slot, (tile, sprite, name, species, badge) in self.tiles.items():
            small = self.width() < 420
            tile.layout().setContentsMargins(*(4, 8, 4, 8) if small else (8, 10, 8, 10))
            tile.layout().setSpacing(3 if small else 6)
            sprite.set_size(30 if small else 48)
            pokemon = self._cards.get(slot, {}).get("pokemon")
            if pokemon:
                species.setText(f"Lv. {pokemon.level or '—'}" if small else f"{pokemon.species} · Lv. {pokemon.level or '—'}")
            self.grid.removeWidget(tile)
            if not tile.isHidden():
                self.grid.addWidget(tile, index // columns, index % columns)
                index += 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()
