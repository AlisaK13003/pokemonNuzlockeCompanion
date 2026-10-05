"""Preference-only editor for the EV stats a Pokémon may gain."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from pokemon_ev_tracker.core.ev_training import EV_STAT_KEYS, EVTrainingPreference

STAT_LABELS = {
    "hp": "HP",
    "attack": "Attack",
    "defense": "Defense",
    "special_attack": "Sp. Atk",
    "special_defense": "Sp. Def",
    "speed": "Speed",
}


class TrainingFocusDialog(QDialog):
    """Collect a selection; callers persist only after an accepted result."""

    def __init__(self, species: str, preference: EVTrainingPreference | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Training focus: {species}")
        self.setMinimumWidth(340)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)
        heading = QLabel(species)
        heading.setProperty("uiRole", "pokemonName")
        heading.setWordWrap(True)
        root.addWidget(heading)
        description = QLabel("Choose the EV stats this Pokémon is allowed to gain. Unselected stats are unwanted.")
        description.setProperty("uiRole", "muted")
        description.setWordWrap(True)
        root.addWidget(description)
        grid = QGridLayout()
        grid.setSpacing(8)
        allowed = preference.allowed_stats if preference is not None else frozenset()
        self.stat_buttons = {}
        for index, stat in enumerate(EV_STAT_KEYS):
            button = QPushButton(STAT_LABELS[stat])
            button.setCheckable(True)
            button.setChecked(stat in allowed)
            button.setProperty("buttonRole", "segmented")
            button.setMinimumHeight(38)
            button.setAccessibleName(f"Allow {STAT_LABELS[stat]} EVs")
            grid.addWidget(button, index // 2, index % 2)
            self.stat_buttons[stat] = button
        root.addLayout(grid)
        empty_hint = QLabel("No stats selected means No Focus.")
        empty_hint.setProperty("uiRole", "small")
        root.addWidget(empty_hint)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def preference(self) -> EVTrainingPreference:
        return EVTrainingPreference(frozenset(
            stat for stat, button in self.stat_buttons.items() if button.isChecked()))
