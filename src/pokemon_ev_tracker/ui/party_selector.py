"""Six-slot party selection shared by normal and compact workspaces.

Selection is presentation state. It follows the decoder's stable identity across
party reordering and never changes the PID-keyed EV target store.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMovie
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from pokemon_ev_tracker.ui.sprite_loader import (
    get_animated_sprite_size,
    get_static_sprite,
    resolve_sprite_asset,
)
from pokemon_ev_tracker.ui.theme import refresh_style


def pokemon_identity(pokemon) -> object | None:
    if pokemon is None:
        return None
    identity = getattr(pokemon, "stable_id", None)
    if identity:
        return identity
    diagnostics = getattr(getattr(pokemon, "decoded", None), "diagnostics", None)
    return getattr(diagnostics, "pid", None)


def pokemon_name(pokemon) -> str:
    return getattr(pokemon, "nickname", "") or getattr(pokemon, "species", "Pokémon")


class PokemonSprite(QLabel):
    """A lifecycle-safe view of the existing sprite asset loader."""

    def __init__(self, size: int = 64, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("uiRole", "sprite")
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._size = size
        self._asset_key = None
        self._movie = None

    def set_species(self, species_id: int | None) -> None:
        asset = resolve_sprite_asset(species_id) if species_id is not None else None
        key = (species_id, asset)
        if key == self._asset_key:
            return
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
        self.clear()
        self._asset_key = key
        if asset is None:
            return
        if asset.kind == "animated" and asset.path is not None:
            movie = QMovie(str(asset.path), parent=self)
            if movie.isValid():
                movie.setScaledSize(get_animated_sprite_size(asset.path, self._size))
                self.setMovie(movie)
                self._movie = movie
                if self.isVisible():
                    movie.start()
                else:
                    movie.jumpToFrame(0)
                return
            movie.deleteLater()
        self.setPixmap(get_static_sprite(species_id, self._size))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._movie is not None:
            self._movie.start()

    def hideEvent(self, event) -> None:
        if self._movie is not None:
            self._movie.setPaused(True)
        super().hideEvent(event)


class PokemonSlot(QPushButton):
    def __init__(self, slot: int, *, compact: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.slot = slot
        self.compact = compact
        self.setObjectName("pokemonSlot")
        self.setProperty("uiRole", "pokemonSlot")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(42 if compact else 62)
        self.setAccessibleName(f"Select party slot {slot}")
        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.row.setContentsMargins(*(3, 2, 3, 2) if compact else (6, 5, 6, 5))
        self.row.setSpacing(6)
        self.sprite = PokemonSprite(26 if compact else 42)
        self.name = QLabel(f"SLOT {slot}")
        self.name.setProperty("uiRole", "slotName")
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta = QLabel("Empty")
        self.meta.setProperty("uiRole", "micro")
        self.meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.hp = QProgressBar()
        self.hp.setProperty("uiRole", "hpBar")
        self.hp.setTextVisible(False)
        self.hp.setFixedHeight(4)
        self.details = QVBoxLayout()
        self.details.setContentsMargins(0, 0, 0, 0)
        self.details.setSpacing(2)
        self.details.addWidget(self.name)
        self.details.addWidget(self.meta)
        self.details.addWidget(self.hp)
        self.row.addWidget(self.sprite, 0, Qt.AlignmentFlag.AlignCenter)
        self.row.addLayout(self.details, 1)
        for child in (self.sprite, self.name, self.meta, self.hp):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._pokemon = None
        self.set_condensed(compact)
        self.set_pokemon(None)

    def set_condensed(self, condensed: bool) -> None:
        self.row.setDirection(
            QBoxLayout.Direction.TopToBottom if condensed else QBoxLayout.Direction.LeftToRight
        )
        self.row.setSpacing(1 if condensed else 6)
        alignment = Qt.AlignmentFlag.AlignCenter if condensed else Qt.AlignmentFlag.AlignLeft
        self.name.setAlignment(alignment)
        self.meta.setAlignment(alignment)
        self.hp.setVisible(not condensed)
        self.setFixedHeight(66 if self.compact else 76 if condensed else 72)
        self._condensed = condensed
        self._update_text()

    def set_pokemon(self, pokemon) -> None:
        self._pokemon = pokemon
        self.setEnabled(pokemon is not None)
        self.sprite.set_species(getattr(pokemon, "species_id", None))
        self._update_text()
        current, maximum = getattr(pokemon, "current_hp", None), getattr(pokemon, "max_hp", None)
        self.hp.setRange(0, max(1, maximum or 1))
        self.hp.setValue(current or 0)
        self.setToolTip(
            f"{pokemon_name(pokemon)} · {getattr(pokemon, 'species', '')}\n"
            f"HP {current if current is not None else '—'} / {maximum or '—'}"
            if pokemon is not None else f"Party slot {self.slot} is empty"
        )

    def _update_text(self) -> None:
        pokemon = self._pokemon
        self.name.setText(pokemon_name(pokemon) if pokemon is not None else f"SLOT {self.slot}")
        level = getattr(pokemon, "level", None)
        self.meta.setText(
            (f"L{level if level is not None else '—'}" if self._condensed else
             f"{pokemon.species} · Lv. {level if level is not None else '—'}")
            if pokemon is not None else "Empty"
        )


class PartySelector(QFrame):
    selected_slot_changed = Signal(int)

    def __init__(self, *, compact: bool = False, inspection: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("partySelector")
        self.setProperty("uiRole", "panel")
        self.setProperty("inspection", inspection)
        self.compact = compact
        self.selected_slot = 1
        self._selected_identity = None
        self._members = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*(5, 5, 5, 5) if compact else (7, 7, 7, 7))
        layout.setSpacing(4 if compact else 6)
        self.slots = {}
        for slot in range(1, 7):
            button = PokemonSlot(slot, compact=compact, parent=self)
            button.setProperty("inspection", inspection)
            button.clicked.connect(lambda _checked=False, slot=slot: self.set_selected_slot(slot))
            self.slots[slot] = button
            layout.addWidget(button, 1)

    def set_party(self, members) -> None:
        self._members = {pokemon.slot: pokemon for pokemon in members}
        selected = self.selected_slot
        if self._selected_identity is not None:
            selected = next((slot for slot, pokemon in self._members.items()
                             if pokemon_identity(pokemon) == self._selected_identity), selected)
        if selected not in self._members and self._members:
            selected = next(iter(self._members))
        for slot, button in self.slots.items():
            button.set_pokemon(self._members.get(slot))
        self.set_selected_slot(selected)

    def set_selected_slot(self, slot: int) -> None:
        if slot not in self.slots:
            return
        changed = self.selected_slot != slot
        self.selected_slot = slot
        pokemon = self._members.get(slot)
        # Preserve identity through a disconnected/temporarily empty frame.
        if pokemon is not None:
            self._selected_identity = pokemon_identity(pokemon)
        for candidate, button in self.slots.items():
            button.setChecked(candidate == slot and pokemon is not None)
            refresh_style(button)
        if changed:
            self.selected_slot_changed.emit(slot)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for button in self.slots.values():
            button.set_condensed(self.compact or self.width() < 930)
