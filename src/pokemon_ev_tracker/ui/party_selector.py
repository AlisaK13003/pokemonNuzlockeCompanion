"""Six-slot party selection shared by normal and compact workspaces.

Selection is presentation state. It follows the decoder's stable identity across
party reordering and never changes the PID-keyed EV target store.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImageReader, QLinearGradient, QMovie, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGraphicsDropShadowEffect,
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

    def __init__(self, size: int = 64, parent=None, *, padding: int = 12,
                 reference_art: bool = False, reference_bounds=(41, 54)) -> None:
        super().__init__(parent)
        self.setProperty("uiRole", "sprite")
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._size = size
        self._padding = padding
        self._reference_art = reference_art
        self._reference_bounds = reference_bounds
        self._asset_key = None
        self._movie = None

    def set_species(self, species_id: int | None) -> None:
        asset = resolve_sprite_asset(species_id, companion=True) if species_id is not None else None
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
                movie.setScaledSize(get_animated_sprite_size(asset.path, max(16, self._size - self._padding)))
                if self._reference_art:
                    source = QImageReader(str(asset.path)).size()
                    width, height = self._reference_bounds
                    movie.setScaledSize(source if source.width() <= width and source.height() <= height
                                        else source.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio))
                self.setMovie(movie)
                self._movie = movie
                if self.isVisible():
                    movie.start()
                else:
                    movie.jumpToFrame(0)
                return
            movie.deleteLater()
        self.setPixmap(get_static_sprite(species_id, max(16, self._size - self._padding)))

    def paintEvent(self, event) -> None:
        if self._reference_art and self._asset_key and self._asset_key[0] is not None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(self.width() / 2, self.height() * 0.82)
            painter.scale(1, 0.4)
            radius = max(20, self.width() * 0.36)
            glow = QRadialGradient(0, 0, radius)
            glow.setColorAt(0, QColor(134, 161, 221, 31))
            glow.setColorAt(1, QColor(134, 161, 221, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(-radius, -radius, radius * 2, radius * 2)
            painter.end()
        super().paintEvent(event)

    def set_size(self, size):
        if size == self._size:
            return
        species = self._asset_key[0] if self._asset_key else None
        self._size = size
        self.setFixedSize(size, size)
        self._asset_key = None
        self.set_species(species)

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
        self.row.setContentsMargins(*(3, 2, 3, 2) if compact else (9, 0, 13, 0))
        self.row.setSpacing(8)
        self.position = QLabel(f"{slot:02d}")
        self.position.setProperty("uiRole", "rosterPosition")
        self.position.setFixedWidth(24 if not compact else 16)
        self.row.addWidget(self.position)
        self.sprite = PokemonSprite(26 if compact else 54, padding=12 if compact else 0,
                                    reference_art=not compact)
        if not compact:
            self.sprite.setFixedSize(50, 54)
        self.name = QLabel(f"SLOT {slot}")
        self.name.setProperty("uiRole", "rosterName")
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta = QLabel("Empty")
        self.meta.setProperty("uiRole", "rosterSpecies")
        self.meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.hp = QProgressBar()
        self.hp.setProperty("uiRole", "rosterHP")
        self.hp.setTextVisible(False)
        self.hp.setFixedHeight(3)
        self.info = QFrame()
        self.info.setFixedHeight(32 if compact else 68)
        self.details = QVBoxLayout(self.info)
        self.details.setContentsMargins(0, 0, 0, 0)
        self.details.setSpacing(2)
        self.details.addWidget(self.name)
        self.details.addWidget(self.meta)
        self.name.setFixedHeight(18 if compact else 24)
        self.meta.setFixedHeight(15 if compact else 18)
        health = QHBoxLayout()
        health.setContentsMargins(0, 5, 0, 0)
        health.setSpacing(7)
        self.hp_value = QLabel("— / —")
        self.hp_value.setProperty("uiRole", "rosterHPValue")
        self.hp_value.setFixedHeight(10 if compact else 15)
        health.addWidget(self.hp, 1)
        health.addWidget(self.hp_value)
        self.details.addLayout(health)
        self.row.addWidget(self.sprite, 0, Qt.AlignmentFlag.AlignCenter)
        self.row.addWidget(self.info, 1, Qt.AlignmentFlag.AlignVCenter)
        self.level_column = QFrame()
        self.level_column.setFixedHeight(30 if compact else 42)
        level_layout = QVBoxLayout(self.level_column)
        level_layout.setContentsMargins(0, 0, 0, 0)
        level_layout.setSpacing(0)
        self.level_caption = QLabel("LV")
        self.level_caption.setProperty("uiRole", "rosterLevelCaption")
        self.level_caption.setFixedHeight(10 if compact else 14)
        self.level_value = QLabel("—")
        self.level_value.setProperty("uiRole", "rosterLevel")
        self.level_value.setFixedHeight(20 if compact else 26)
        self.level_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.level_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        level_layout.addWidget(self.level_caption)
        level_layout.addWidget(self.level_value)
        self.row.addWidget(self.level_column, 0, Qt.AlignmentFlag.AlignVCenter)
        self.chevron = QLabel("›")
        self.chevron.setProperty("uiRole", "rosterChevron")
        self.chevron.setFixedWidth(17)
        self.chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.row.addWidget(self.chevron)
        for child in (self.position, self.sprite, self.name, self.meta, self.hp,
                      self.hp_value, self.info, self.level_column, self.chevron):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._pokemon = None
        self.set_condensed(compact)
        self.set_pokemon(None)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.isChecked() and not self.compact:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.save()
            painter.translate(0, self.height() / 2)
            painter.scale(1, (self.height() - 10) / 20)
            glow = QRadialGradient(0, 0, 10)
            glow.setColorAt(0, QColor(143, 184, 255, 36))
            glow.setColorAt(0.35, QColor(143, 184, 255, 14))
            glow.setColorAt(0.7, QColor(143, 184, 255, 3))
            glow.setColorAt(1, QColor(143, 184, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(-10, -10, 20, 20)
            painter.restore()
            stripe = QLinearGradient(0, 11, 0, self.height() - 11)
            stripe.setColorAt(0, QColor(143, 184, 255, 0))
            stripe.setColorAt(0.15, QColor(143, 184, 255, 185))
            stripe.setColorAt(0.85, QColor(143, 184, 255, 185))
            stripe.setColorAt(1, QColor(143, 184, 255, 0))
            painter.fillRect(0, 11, 2, self.height() - 22, stripe)

    def set_condensed(self, condensed: bool) -> None:
        self.row.setDirection(
            QBoxLayout.Direction.TopToBottom if condensed else QBoxLayout.Direction.LeftToRight
        )
        self.row.setSpacing(1 if condensed else 8)
        alignment = Qt.AlignmentFlag.AlignCenter if condensed else Qt.AlignmentFlag.AlignLeft
        self.name.setAlignment(alignment)
        self.meta.setAlignment(alignment)
        self.hp.setVisible(not condensed)
        self.level_column.setVisible(not condensed)
        self.chevron.setVisible(not condensed)
        self.setFixedHeight(66 if self.compact else 90 if condensed else 100)
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
        self.hp_value.setText(f"{current if current is not None else '—'}/{maximum or '—'}")
        self.hp_value.setVisible(not self.compact)
        self.setToolTip(
            f"{pokemon_name(pokemon)} · {getattr(pokemon, 'species', '')}\n"
            f"HP {current if current is not None else '—'} / {maximum or '—'}"
            if pokemon is not None else f"Party slot {self.slot} is empty"
        )

    def _update_text(self) -> None:
        pokemon = self._pokemon
        self.name.setText(pokemon_name(pokemon) if pokemon is not None else f"SLOT {self.slot}")
        level = getattr(pokemon, "level", None)
        self.level_value.setText(str(level) if level is not None else "—")
        self.meta.setText(
            (f"L{level if level is not None else '—'}" if self._condensed else
             pokemon.species)
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
        if not compact:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(0)
            shadow.setOffset(4, 4)
            shadow.setColor(QColor(3, 5, 9, 199))
            self.setGraphicsEffect(shadow)
        self.selected_slot = 1
        self._selected_identity = None
        self._members = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(5, 5, 5, 5) if compact else (1, 1, 1, 1))
        layout.setSpacing(4 if compact else 0)
        self.heading = QLabel("YOUR PARTY")
        self.heading.setProperty("uiRole", "rosterHeading")
        self.party_count = QLabel("0 / 6")
        self.party_count.setProperty("uiRole", "rosterCount")
        self.header = QFrame()
        self.header.setObjectName("partyRosterHeader")
        self.header.setFixedHeight(42)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(12, 0, 12, 6)
        header.addWidget(self.heading)
        header.addStretch(1)
        header.addWidget(self.party_count)
        layout.addWidget(self.header)
        self.slots = {}
        for slot in range(1, 7):
            button = PokemonSlot(slot, compact=compact, parent=self)
            button.setProperty("inspection", inspection)
            button.clicked.connect(lambda _checked=False, slot=slot: self.set_selected_slot(slot))
            self.slots[slot] = button
            layout.addWidget(button)
        layout.addStretch(1)

    def set_party(self, members) -> None:
        self._members = {pokemon.slot: pokemon for pokemon in members}
        self.party_count.setText(f"{len(self._members)} / 6")
        selected = self.selected_slot
        if self._selected_identity is not None:
            selected = next((slot for slot, pokemon in self._members.items()
                             if pokemon_identity(pokemon) == self._selected_identity), selected)
        if selected not in self._members and self._members:
            selected = next(iter(self._members))
        for slot, button in self.slots.items():
            button.set_pokemon(self._members.get(slot))
            button.setVisible(slot in self._members)
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
            button.set_condensed(self.compact)
