"""Compact display for the currently active opposing Pokémon."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImageReader, QMovie
from PySide6.QtWidgets import (
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.games.registry import default_game_provider
from pokemon_ev_tracker.ui.sprite_loader import (
    get_animated_sprite_size,
    get_static_sprite,
    resolve_sprite_asset,
)
from pokemon_ev_tracker.ui.training_panels import ScanlineFrame, caption, line_icon

LOGGER = logging.getLogger(__name__)


class _OpponentCard(QWidget):
    def __init__(self, parent=None, ev_yield_summary=None) -> None:
        super().__init__(parent)
        self._ev_yield_summary = ev_yield_summary or default_game_provider().format_ev_yield_summary
        self.setProperty("uiRole", "opponentCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._species_id: int | None = None
        self._asset = None
        self._movie: QMovie | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 8, 16, 12)
        root.setSpacing(0)
        self.meta = QWidget()
        meta = QHBoxLayout(self.meta)
        meta.setContentsMargins(0, 0, 0, 0)
        meta.addWidget(caption("ACTIVE BATTLE"), 1)
        self.level = caption("LV —")
        meta.addWidget(self.level)
        root.addWidget(self.meta)
        layout = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.content_layout = layout
        layout.setSpacing(0)
        root.addLayout(layout)
        self.sprite = QLabel()
        self.sprite.setFixedSize(120, 120)
        self.sprite.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sprite.setProperty("uiRole", "sprite")
        layout.addWidget(self.sprite, 0, Qt.AlignmentFlag.AlignCenter)
        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(4)
        self.name = QLabel("Not currently battling", self)
        self.name.hide()
        self.display_name = caption("", "battleName")
        self.display_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.yield_heading = caption("EV YIELD")
        self.yield_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ev_yield = QLabel(self)
        self.ev_yield.hide()
        self.display_yield = caption("", "battleYield")
        self.display_yield.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_yield.setToolTip("Base species EV yield; held items and Pokérus can change awarded EVs.")
        details.addWidget(self.display_name)
        details.addWidget(self.yield_heading)
        details.addWidget(self.display_yield)
        layout.addWidget(self.details)

    def set_opponent(self, pokemon) -> None:
        if pokemon is None:
            self.clear()
            return

        species_id = pokemon.species_id
        asset = resolve_sprite_asset(species_id, companion=True)
        if self._species_id != species_id or self._asset != asset:
            self._set_sprite(species_id, asset)

        level = pokemon.level if pokemon.level is not None else "--"
        self.name.setText(f"{pokemon.species} • Lv. {level}")
        self.ev_yield.setText(self._ev_yield_summary(species_id))
        self.display_name.setText(self.name.text() if self.parentWidget()._compact else pokemon.species)
        self.level.setText(f"LV {level}")
        self.display_yield.setText(self.ev_yield.text().removesuffix(" EV"))

    def clear(self) -> None:
        self._clear_sprite()
        self.name.clear()
        self.ev_yield.clear()
        self.display_name.clear()
        self.display_yield.clear()

    def _set_sprite(self, species_id: int, asset) -> None:
        self._clear_sprite()
        if asset.kind == "animated" and asset.path is not None:
            path = asset.path
            movie = QMovie(str(path), parent=self.sprite)
            if movie.isValid():
                source = QImageReader(str(path)).size()
                bounds = self.sprite.width() - (16 if self.parentWidget()._compact else 0)
                movie.setScaledSize(source if source.width() <= bounds and source.height() <= bounds
                                    else get_animated_sprite_size(path, bounds))
                self.sprite.setMovie(movie)
                self._movie = movie
                movie.start()
            else:
                movie.deleteLater()
                self.sprite.setPixmap(get_static_sprite(species_id, self.sprite.width() - 16))
        elif asset.kind == "static":
            self.sprite.setPixmap(get_static_sprite(species_id, self.sprite.width() - 16))
        self._species_id = species_id
        self._asset = asset

    def _clear_sprite(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
        self.sprite.clear()
        self._species_id = None
        self._asset = None


class CurrentOpponentPanel(ScanlineFrame):
    def __init__(self, parent=None, ev_yield_summary=None) -> None:
        super().__init__(parent)
        self._compact = False
        self.setObjectName("currentlyBattling")
        self.setProperty("uiRole", "panel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)
        header = QWidget()
        header.setFixedHeight(32)
        heading_row = QHBoxLayout(header)
        heading_row.setContentsMargins(12, 0, 12, 0)
        heading = caption("CURRENTLY BATTLING", "battleHeading")
        heading_row.addWidget(heading, 1)
        self.live = caption("LIVE", "battleLive")
        self.live_dot = QLabel()
        self.live_dot.setPixmap(line_icon("live", "#ff718b", 8).pixmap(QSize(8, 8)))
        heading_row.addWidget(self.live_dot)
        heading_row.addWidget(self.live)
        self.live.hide()
        self.live_dot.hide()
        root.addWidget(header)
        layout = QHBoxLayout()
        self.opponents_layout = layout
        root.addLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.empty_label = QLabel("Not currently battling")
        self.empty_label.setProperty("uiRole", "emptyState")
        layout.addWidget(self.empty_label)
        self.cards = [
            _OpponentCard(self, ev_yield_summary),
            _OpponentCard(self, ev_yield_summary),
        ]
        for card in self.cards:
            card.hide()
            layout.addWidget(card, 1)
        self._set_layout_direction()
        self.setMaximumHeight(78)

        # Keep the first-opponent labels available for callers and diagnostics.
        self.sprite = self.cards[0].sprite
        self.name = self.cards[0].name
        self.ev_yield = self.cards[0].ev_yield
        self.combined_yield = QLabel()
        self.combined_yield.setWordWrap(True)
        self.combined_yield.setProperty("uiRole", "pokemonMeta")
        root.addWidget(self.combined_yield)
        self.combined_yield.hide()

    def set_compact(self, compact):
        self._compact = compact
        for card in self.cards:
            card.content_layout.setDirection(QBoxLayout.Direction.LeftToRight if compact else QBoxLayout.Direction.TopToBottom)
            card.sprite.setFixedSize(64 if compact else 120, 64 if compact else 120)
            card.meta.setVisible(not compact)
            card.display_name.setText(card.name.text() if compact else card.name.text().split(" • ")[0])
            card.yield_heading.setVisible(not compact)
            card.display_name.setAlignment(Qt.AlignmentFlag.AlignLeft if compact else Qt.AlignmentFlag.AlignCenter)
            card.display_yield.setAlignment(Qt.AlignmentFlag.AlignLeft if compact else Qt.AlignmentFlag.AlignCenter)
            if card._species_id is not None:
                card._set_sprite(card._species_id, card._asset)
        self._set_layout_direction()
        self._update_panel_height()

    def title(self) -> str:
        return "Currently Battling"

    def set_opponent(self, pokemon) -> None:
        self.set_opponents((pokemon,) if pokemon is not None else ())

    def set_unavailable(self) -> None:
        self.set_opponents(())
        self.empty_label.setText("Live battle analysis unavailable for this game")

    def set_opponents(self, pokemon_list) -> None:
        opponents = tuple(pokemon_list or ())[:2]
        self._set_layout_direction()
        self.empty_label.setVisible(not opponents)
        self.live.setVisible(bool(opponents))
        self.live_dot.setVisible(bool(opponents))
        for index, card in enumerate(self.cards):
            if index < len(opponents):
                card.set_opponent(opponents[index])
                card.show()
            else:
                card.clear()
                card.hide()
        self._update_panel_height()
        visible_cards = sum(not card.isHidden() for card in self.cards)
        content_visible = self.isVisible() and (
            all(card.isVisible() for card in self.cards[: len(opponents)])
            if opponents
            else self.empty_label.isVisible()
        )
        LOGGER.debug(
            "OpponentPanel received: %d; cards rendered: %d; layout count after render: %d; "
            "panel visible: %s; panel height: %d; content visible: %s; visible cards: %d",
            len(opponents),
            visible_cards,
            self.opponents_layout.count(),
            self.isVisible(),
            self.height(),
            content_visible,
            sum(card.isVisible() for card in self.cards),
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._set_layout_direction()
        self._update_panel_height()

    def _update_panel_height(self) -> None:
        visible_cards = sum(not card.isHidden() for card in self.cards)
        if visible_cards == 0:
            self.setMaximumHeight(78)
        elif not self._compact:
            self.setMaximumHeight(510 if visible_cards == 2 and self.width() < 650 else 244)
        elif visible_cards == 2 and self.width() < 650:
            self.setMaximumHeight(220)
        else:
            self.setMaximumHeight(145)

    def _set_layout_direction(self) -> None:
        layout = self.opponents_layout
        layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if self.width() < 650 and not self._compact
            else QBoxLayout.Direction.LeftToRight
        )
