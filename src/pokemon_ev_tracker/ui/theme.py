"""Central Trainer Lab Terminal colors, typography and semantic widget roles."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QWidget

COLORS = {
    "canvas": "#091117",
    "shell": "#0e171d",
    "panel": "#111d24",
    "raised_panel": "#142129",
    "surface": "#111d24",
    "surface_raised": "#142129",
    "surface_card": "#172832",
    "surface_hover": "#223b46",
    "surface_input": "#071015",
    "border": "#2d4a57",
    "border_soft": "#213842",
    "border_strong": "#527483",
    "text": "#e9f2f3",
    "text_secondary": "#afc1c8",
    "text_muted": "#8ea5ad",
    "text_dim": "#617983",
    "accent": "#78dccb",
    "inspection": "#9bbeff",
    "neutral": "#8a9aa2",
    "accent_hover": "#99eddf",
    "accent_deep": "#214852",
    "accent_border": "#78dccb",
    "accent_hover_surface": "#2a5b62",
    "success": "#58d38b",
    "warning": "#e7c15b",
    "danger": "#f07178",
    "hp": "#f07178",
    "attack": "#e7a044",
    "defense": "#e7c15b",
    "special_attack": "#69b0f5",
    "special_defense": "#59cf9d",
    "speed": "#a984d8",
    "danger_surface": "#4b3435",
    "danger_border": "#725052",
    "danger_hover": "#5a3b3d",
    "recent_change": "#3b5145",
    "recent_change_border": "#4e6c5a",
    "nature_up": "#dfa0a0",
    "nature_down": "#91b6d1",
    "selection": "#223b46",
    "selection_text": "#f2f6f2",
    "progress_track": "#213842",
    "table_alt": "#172832",
    "status_caught": "#365143",
    "status_caught_border": "#567663",
    "status_failed": "#493536",
    "status_failed_border": "#705052",
    "status_dead": "#503638",
    "status_dead_border": "#795052",
    "status_skipped": "#353e42",
    "status_dupes": "#3b4142",
}

METRICS = {
    "radius_small": "4px",
    "radius_medium": "6px",
    "radius_card": "8px",
    "control_padding": "6px 10px",
}

COLORS.update({
    "canvas": "#0b0f16", "shell": "#0d1119", "panel": "#111620",
    "raised_panel": "#171d29", "surface": "#111620", "surface_raised": "#171d29",
    "surface_card": "#101620", "surface_hover": "#20293a", "surface_input": "#0c1119",
    "border": "#303642", "border_soft": "#252c38", "border_strong": "#4c586d",
    "text": "#e9eef7", "text_secondary": "#9ca7b9", "text_muted": "#707c90",
    "text_dim": "#505c70", "accent": "#a6baff", "inspection": "#b9c8ec",
    "accent_hover": "#c5d1ff", "accent_deep": "#222d41", "accent_border": "#8da9ff",
    "accent_hover_surface": "#28344c", "success": "#57dda2", "warning": "#ffcb69",
    "danger": "#ff718b", "hp": "#57dda2", "selection": "#1e2738",
    "selection_text": "#eef3fc", "progress_track": "#252d39", "table_alt": "#121823",
    "status_caught": "#16382c", "status_caught_border": "#366853",
    "status_failed": "#3b3030", "status_dead": "#3b222d",
})
METRICS.update({"radius_small": "2px", "radius_medium": "3px", "radius_card": "3px"})
_TOKENS = {**COLORS, **METRICS}

_STYLESHEET = """
QMainWindow, QWidget {
    background-color: $canvas;
    color: $text;
    font-family: "Manrope", "Segoe UI", sans-serif;
    font-size: 9pt;
}
QMainWindow { background-color: $canvas; }
QWidget#appRoot, QWidget#trackerPage, QWidget#ramDebugPage { background: $canvas; }
QWidget#applicationToolbar {
    background: $surface;
    border: 1px solid $border_soft;
    border-radius: $radius_card;
}
QLabel { background: transparent; }
QLabel#applicationTitle {
    color: $text;
    font-size: 16pt;
    font-weight: 650;
}
QLabel[uiRole="sectionHeading"] {
    color: $accent;
    font-size: 8pt;
    font-weight: 650;
    padding: 2px 0;
}
QLabel[uiRole="pokemonName"] {
    color: $text;
    font-size: 10pt;
    font-weight: 650;
}
QLabel[uiRole="pokemonMeta"] { color: $text_secondary; font-weight: 600; }
QLabel[uiRole="muted"] { color: $text_secondary; }
QLabel[uiRole="small"] { color: $text_secondary; font-size: 8pt; }
QLabel[uiRole="micro"] { color: $text_muted; font-size: 7.5pt; }
QLabel[uiRole="heldItem"] { color: $text; }
QLabel[uiRole="tableHeader"] { color: $text_muted; font-size: 8pt; font-weight: 650; }
QLabel[uiRole="emptyState"] { color: $text_muted; font-style: italic; padding: 5px; }
QLabel[uiRole="sprite"] {
    background: $surface_raised;
    border: 1px solid $border_soft;
    border-radius: $radius_medium;
}

QTabWidget::pane {
    background: $canvas;
    border: 1px solid $border_soft;
    border-radius: $radius_card;
    top: -1px;
}
QTabBar::tab {
    background: $surface;
    color: $text_secondary;
    border: 1px solid $border_soft;
    border-bottom: 2px solid transparent;
    padding: 7px 14px;
    margin-right: 4px;
    border-top-left-radius: $radius_medium;
    border-top-right-radius: $radius_medium;
}
QTabBar::tab:selected {
    background: $surface_raised;
    color: $text;
    border-bottom-color: $accent;
}
QTabBar::tab:hover:!selected { background: $surface_hover; color: $text; }
QTabBar::tab:disabled { color: $text_dim; }

QPushButton, QToolButton {
    background: $surface_raised;
    color: $text;
    border: 1px solid $border;
    border-radius: $radius_medium;
    padding: $control_padding;
    font-weight: 550;
}
QPushButton:hover, QToolButton:hover {
    background: $surface_hover;
    border-color: $border_strong;
}
QPushButton:pressed, QToolButton:pressed {
    background: $surface;
    border-color: $accent_border;
}
QPushButton:disabled, QToolButton:disabled {
    color: $text_dim;
    background: $surface;
    border-color: $border_soft;
}
QPushButton:default, QPushButton[buttonRole="primary"] {
    background: $accent_deep;
    border-color: $accent_border;
    color: $text;
}
QPushButton:default:hover, QPushButton[buttonRole="primary"]:hover {
    background: $accent_hover_surface;
    border-color: $accent;
}
QPushButton[buttonRole="danger"] {
    background: $danger_surface;
    border-color: $danger_border;
}
QPushButton[buttonRole="danger"]:hover {
    background: $danger_hover;
    border-color: $danger;
}
QPushButton[buttonRole="segmented"] { border-radius: $radius_small; }
QPushButton[buttonRole="segmented"]:checked {
    background: $accent_deep;
    border-color: $accent_border;
    color: $text;
}
QPushButton[buttonRole="compactToggle"]:checked {
    background: $accent_deep;
    border-color: $accent_border;
}

QGroupBox {
    background: $surface;
    border: 1px solid $border_soft;
    border-radius: $radius_card;
    margin-top: 12px;
    padding: 10px 8px 7px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 1px 6px;
    color: $text_secondary;
    background: $surface;
}
QGroupBox#currentlyBattling { background: $surface; border-color: $border; }
QGroupBox#nextLevelCap { background: $surface_raised; }

QFrame#partyCard, QWidget[uiRole="opponentCard"] {
    background: $surface_card;
    border: 1px solid $border;
    border-radius: $radius_card;
}
QFrame#partyCard:hover { border-color: $border_strong; }
QWidget[uiRole="evRow"] {
    background: $surface_raised;
    border: 1px solid transparent;
    border-radius: $radius_small;
}
QWidget[recentChange="true"] {
    background: $recent_change;
    border: 1px solid $recent_change_border;
    border-radius: $radius_small;
}
QFrame#partyCard[cardSize="small"] { border-color: $border_soft; }
QFrame#partyCard[cardSize="large"] { border-color: $border_strong; }
QWidget#friendshipWalk {
    background: $surface;
    border: 1px solid $border_soft;
    border-radius: $radius_card;
    padding: 3px;
}
QWidget#evChangeLog {
    background: $surface;
    border: 1px solid $border_soft;
    border-radius: $radius_card;
}
QListWidget#evChangeHistory {
    background: $surface_input;
    alternate-background-color: $table_alt;
    border: 1px solid $border_soft;
    border-radius: $radius_medium;
    padding: 5px;
}
QListWidget#evChangeHistory::item { padding: 4px 6px; border-radius: $radius_small; }
QListWidget#evChangeHistory::item:selected {
    background: $selection;
    color: $selection_text;
}

QToolButton#sectionToggle {
    background: transparent;
    color: $text_secondary;
    border: 1px solid transparent;
    border-left: 2px solid transparent;
    border-radius: $radius_small;
    padding: 5px 7px;
    text-align: left;
    font-weight: 600;
}
QToolButton#sectionToggle:hover {
    background: $surface_raised;
    color: $text;
}
QToolButton#sectionToggle:checked {
    background: $surface_raised;
    color: $accent_hover;
    border-left-color: $accent;
}

QLabel[natureRole="up"] { color: $nature_up; font-weight: 600; }
QLabel[natureRole="down"] { color: $nature_down; font-weight: 600; }
QLabel[natureRole="neutral"] { color: $text_secondary; }
QLabel[targetSeverity="warning"] { color: $danger; font-weight: 600; }
QLabel[targetSeverity="normal"] { color: $text_secondary; }
QLabel[ramState="valid"] { color: $success; }
QLabel[ramState="warning"] { color: $warning; }
QLabel[ramState="invalid"] { color: $danger; font-weight: 600; }
QLabel[statusRole="warning"] { color: $warning; }
QLabel[statusRole="error"] { color: $danger; }
QLabel[statusRole="connected"] { color: $success; }
QLabel[statusRole="info"] { color: $text_secondary; }
QLabel[walkState="active"] { color: $success; font-weight: 600; }
QLabel[walkState="reached"] { color: $success; font-weight: 700; }
QLabel[walkState="pending"], QLabel[walkState="paused"] { color: $warning; font-weight: 600; }
QLabel[connectionState="connected"] { color: $success; font-weight: 600; }
QLabel[connectionState="disconnected"] { color: $danger; font-weight: 600; }

QProgressBar {
    background: $progress_track;
    border: 1px solid $border_soft;
    border-radius: 4px;
    min-height: 5px;
    max-height: 10px;
}
QProgressBar::chunk { background: $accent; border-radius: 3px; }

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background: $surface_input;
    color: $text;
    border: 1px solid $border;
    border-radius: $radius_small;
    padding: 5px 7px;
    selection-background-color: $selection;
    selection-color: $selection_text;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover {
    border-color: $border_strong;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border-color: $accent_border;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: $surface_raised;
    color: $text;
    border: 1px solid $border;
    selection-background-color: $selection;
}
QCheckBox { spacing: 7px; color: $text_secondary; }
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    background: $surface_input;
    border: 1px solid $border_strong;
    border-radius: 4px;
}
QCheckBox::indicator:checked { background: $accent_deep; border-color: $accent; }

QTableWidget {
    background: $surface_input;
    alternate-background-color: $table_alt;
    color: $text;
    gridline-color: $border_soft;
    border: 1px solid $border_soft;
    border-radius: $radius_medium;
    selection-background-color: $selection;
    selection-color: $selection_text;
    outline: 0;
}
QTableWidget::item { padding: 4px 6px; border: none; }
QTableWidget::item:hover { background: $surface_hover; }
QHeaderView::section {
    background: $surface_raised;
    color: $text_secondary;
    border: none;
    border-right: 1px solid $border_soft;
    border-bottom: 1px solid $border;
    padding: 7px 8px;
    font-weight: 650;
}
QTableCornerButton::section { background: $surface_raised; border: none; }

QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: $canvas; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: $border_strong;
    min-height: 26px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: $text_muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: $canvas; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: $border_strong;
    min-width: 26px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover { background: $text_muted; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QStatusBar#statusBar {
    background: $surface;
    color: $text_secondary;
    border-top: 1px solid $border_soft;
}
QStatusBar::item { border: none; }
QLabel#nuzlockeEmpty { color: $text_muted; padding: 24px; }
QLabel[nuzlockeRole="capTitle"] { color: $text; font-size: 12pt; font-weight: 650; }
QLabel[nuzlockeRole="muted"] { color: $text_muted; font-size: 8pt; }
QLabel[nuzlockeRole="warning"] { color: $warning; }
QLabel[nuzlockeRole="summary"] { color: $text_secondary; }
QComboBox[encounterStatus="CAUGHT"] {
    background: $status_caught;
    border-color: $status_caught_border;
}
QComboBox[encounterStatus="FAILED"] {
    background: $status_failed;
    border-color: $status_failed_border;
}
QComboBox[encounterStatus="DEAD"] {
    background: $status_dead;
    border-color: $status_dead_border;
}
QComboBox[encounterStatus="SKIPPED"] { background: $status_skipped; }
QComboBox[encounterStatus="DUPES"] { background: $status_dupes; }
"""

_STYLESHEET += """
QWidget#applicationToolbar {
    background: $panel; border: none; border-bottom: 1px solid $border_soft;
    border-radius: 0;
}
QLabel#applicationTitle { font-size: 11pt; font-weight: 700; }
QFrame#navigationRail { background: $shell; border: 1px solid $border_soft; }
QFrame#compactSwitcher { background: $shell; border-bottom: 1px solid $border_soft; }
QPushButton[buttonRole="navigation"] { text-align: left; background: $raised_panel; border-color: $border_soft; }
QPushButton[buttonRole="navigation"]:checked {
    background: $accent; border-color: $accent; color: $surface_input; font-weight: 700;
}
QPushButton[buttonRole="segmented"][inspection="true"]:checked { border-color: $inspection; color: $inspection; }
QPushButton[buttonRole="navigation"][inspection="true"]:checked {
    background: $inspection; border-color: $inspection;
}
QFrame[uiRole="panel"], QWidget#friendshipWalk, QWidget#evChangeLog {
    background: $panel; border: 1px solid $border; border-radius: 8px;
}
QFrame[uiRole="raisedPanel"], QFrame[uiRole="moveCard"] {
    background: $raised_panel; border: 1px solid $border_soft; border-radius: 6px;
}
QLabel[uiRole="moveTypeBadge"], QLabel[uiRole="moveCategoryBadge"] {
    background: $surface_input; border: 1px solid $border_soft; border-radius: 4px;
    padding: 2px 6px; color: $text_muted; font-size: 8pt; font-weight: 700;
}
QLabel[uiRole="moveCategoryBadge"][moveCategory="physical"] { color: #D9A17E; }
QLabel[uiRole="moveCategoryBadge"][moveCategory="special"] { color: #9FBBE7; }
QLabel[uiRole="moveCategoryBadge"][moveCategory="status"] { color: #B9B9C8; }
QLabel[uiRole="moveTypeBadge"][moveType="water"] { color: #91C7EF; border-color: #4389AE; }
QLabel[uiRole="moveTypeBadge"][moveType="fire"] { color: #F2A681; border-color: #B46144; }
QLabel[uiRole="moveTypeBadge"][moveType="grass"] { color: #A7D5A1; border-color: #639767; }
QLabel[uiRole="moveTypeBadge"][moveType="electric"] { color: #ECD68A; border-color: #AD9850; }
QLabel[uiRole="moveTypeBadge"][moveType="psychic"] { color: #E5A8CC; border-color: #A8618D; }
QLabel[uiRole="moveTypeBadge"][moveType="ghost"] { color: #B7A7DA; border-color: #79699D; }
QLabel[uiRole="moveTypeBadge"][moveType="normal"] { color: #C6C5BB; border-color: #88877C; }
QLabel[uiRole="moveTypeBadge"][moveType="fighting"] { color: #D9A18D; border-color: #9E6553; }
QLabel[uiRole="moveTypeBadge"][moveType="ice"] { color: #A6DBDF; border-color: #5C9BA3; }
QLabel[uiRole="moveTypeBadge"][moveType="dragon"] { color: #B09DF0; border-color: #7460B1; }
QLabel[uiRole="moveTypeBadge"][moveType="dark"] { color: #B5A7A2; border-color: #756962; }
QLabel[uiRole="moveTypeBadge"][moveType="steel"] { color: #B9C9D4; border-color: #718993; }
QLabel[uiRole="moveTypeBadge"][moveType="poison"] { color: #C9A1D7; border-color: #8C629D; }
QLabel[uiRole="moveTypeBadge"][moveType="ground"] { color: #D7C398; border-color: #998357; }
QLabel[uiRole="moveTypeBadge"][moveType="flying"] { color: #B4C3E7; border-color: #778BB3; }
QLabel[uiRole="moveTypeBadge"][moveType="bug"] { color: #BBCB8E; border-color: #82904E; }
QLabel[uiRole="moveTypeBadge"][moveType="rock"] { color: #D1BE91; border-color: #93805A; }
QFrame[uiRole="metricRow"] { background: $surface_card; border: 1px solid $border_soft; border-radius: 5px; }
QLabel[uiRole="technicalHeading"] { color: $accent; font-size: 8pt; font-weight: 700; }
QLabel[uiRole="identityName"] { font-size: 19pt; font-weight: 700; }
QLabel[uiRole="slotName"] { font-size: 8pt; font-weight: 600; }
QLabel[uiRole="metricValue"] { font-size: 12pt; font-weight: 700; }
QLabel[uiRole="inspectionValue"] { color: $inspection; font-size: 12pt; font-weight: 700; }
QLabel[uiRole="systemSummary"] {
    background: $surface_input; border: 1px solid $border_soft; border-radius: 6px;
    color: $text_muted; font-size: 8pt; padding: 9px;
}
QPushButton#pokemonSlot { background: $raised_panel; border: 1px solid $border_soft; padding: 5px; }
QPushButton#pokemonSlot:checked { background: $selection; border: 2px solid $accent; }
QPushButton#pokemonSlot[inspection="true"]:checked { border-color: $inspection; }
QLabel[uiRole="focusChip"] { background: $surface_input; border: 1px solid $border_soft; border-radius: 5px; padding: 5px; font-size: 8pt; color: $text_muted; }
QLabel[uiRole="focusChip"][evFocused="true"] { color: $accent; background: $accent_deep; border-color: $accent; }
QFrame[uiRole="metricRow"][evFocused="true"] { background: $accent_deep; border-color: $accent; }
QLabel[uiRole="pageTitle"] { font-size: 17pt; font-weight: 700; }
QLabel[uiRole="metric"] { color: $accent; font-size: 19pt; font-weight: 700; }
QLabel[uiRole="statusBadge"] { background: $surface_input; border: 1px solid $border; border-radius: 4px; padding: 3px 6px; color: $text_muted; font-size: 8pt; }
QLabel[status="good"] { color: $success; border-color: $success; }
QLabel[status="avoid"] { color: $danger; border-color: $danger; }
QLabel[status="mixed"] { color: $warning; border-color: $warning; }
QPushButton[buttonRole="primary"] { background: $accent; color: $surface_input; border-color: $accent; }
QPushButton[buttonRole="primary"]:hover { background: $accent_hover; }
QPushButton[buttonRole="primary"]:disabled, QPushButton[buttonRole="danger"]:disabled {
    background: $panel; color: $text_dim; border-color: $border_soft;
}
QWidget[inspection="true"] QLabel[uiRole="technicalHeading"],
QWidget[inspection="true"] QLabel[uiRole="sectionHeading"] { color: $inspection; }
QProgressBar[statKey="hp"]::chunk { background: $hp; }
QProgressBar[statKey="attack"]::chunk { background: $attack; }
QProgressBar[statKey="defense"]::chunk { background: $defense; }
QProgressBar[statKey="special_attack"]::chunk { background: $special_attack; }
QProgressBar[statKey="special_defense"]::chunk { background: $special_defense; }
QProgressBar[statKey="speed"]::chunk { background: $speed; }
QPlainTextEdit { font-family: "Cascadia Mono", "Consolas", monospace; }
"""

_STYLESHEET += """
QWidget { background: transparent; }
QMainWindow, QWidget#appRoot { background: $canvas; }
QLabel[uiRole="sprite"] { background: transparent; border: none; }
QProgressBar[uiRole="hpBar"] { background: $progress_track; border: none; }
QProgressBar[uiRole="hpBar"]::chunk { background: $success; border-radius: 0; }
QPushButton[buttonRole="ledgerRow"] { text-align: left; padding: 16px 12px; }
QPushButton#settingsCog { font-family: "Segoe UI Symbol"; font-size: 15pt; }
QLabel[connectionState] { font-family: "Segoe UI"; }
QLabel[compactBadge="true"] { font-size: 6pt; padding: 2px; }
QLabel[uiRole="metric"] { color: $text; font-size: 29pt; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QLabel#applicationTitle, QLabel[uiRole="pageTitle"], QLabel[uiRole="identityName"],
QLabel[uiRole="pokemonName"], QLabel[uiRole="slotName"], QLabel[uiRole="metric"],
QPushButton[buttonRole="topNavigation"], QPushButton[buttonRole="segmented"] {
    font-family: "Pixelify Sans", "Consolas";
}
QLabel#applicationTitle { font-size: 13pt; letter-spacing: 2px; }
QLabel[uiRole="pageTitle"] { font-size: 27pt; }
QLabel[uiRole="identityName"] { font-size: 27pt; }
QLabel[uiRole="pokemonName"] { font-size: 17pt; }
QLabel[uiRole="technicalHeading"], QLabel[uiRole="sectionHeading"] {
    color: $text_secondary; font-family: "Consolas"; font-size: 8pt;
}
QLabel[uiRole="rosterHeading"] {
    color: #8999b4; font-family: "Pixelify Sans";
    font-size: 13px; font-weight: 500; letter-spacing: 1.58px;
}
QFrame[uiRole="panel"], QWidget#friendshipWalk, QWidget#evChangeLog {
    border-radius: 3px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #171d29,stop:1 #0d121b);
}
QFrame[uiRole="raisedPanel"], QFrame[uiRole="metricRow"] { border-radius: 2px; }
QPushButton[buttonRole="topNavigation"] {
    border: none; border-top: 2px solid transparent; background: transparent;
    color: $text_muted; padding: 12px 16px; font-size: 12pt;
}
QPushButton[buttonRole="topNavigation"]:checked {
    border-top-color: $accent; color: $text; background: $surface_raised;
}
QPushButton[buttonRole="segmented"]:checked {
    background: $selection; color: $text; border: 1px solid $border_strong;
}
QPushButton[buttonRole="focusToggle"] { text-align: left; padding: 12px; }
QPushButton[buttonRole="focusToggle"]:checked {
    background: $accent_deep; border-color: $accent; color: $text;
}
QPushButton[buttonRole="primary"] { background: $accent_deep; color: $text; }
QPushButton#pokemonSlot { border-radius: 0; padding: 4px; background: transparent; }
QPushButton#pokemonSlot:checked { border: 1px solid $accent; background: $selection; }
/* Measured from the Figma Make party-list and party-row elements. */
QFrame#partySelector {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #151b26,stop:1 #0d1119);
    border: 1px solid rgba(181, 198, 224, 43); border-radius: 3px;
}
QFrame#partyRosterHeader { background: transparent; border: none; }
QPushButton#pokemonSlot, QPushButton#pokemonSlot[inspection="true"] {
    background: transparent; border: none;
    border-bottom: 1px solid rgba(181, 198, 224, 20);
    border-left: 2px solid transparent; padding: 0; border-radius: 0;
}
QPushButton#pokemonSlot:hover { background: rgba(115, 142, 211, 15); }
QPushButton#pokemonSlot:checked, QPushButton#pokemonSlot[inspection="true"]:checked {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 rgba(115, 142, 211, 33),stop:1 rgba(115, 142, 211, 8));
    border: none; border-bottom: 1px solid rgba(181, 198, 224, 20);
    border-left: 2px solid transparent;
}
QPushButton#pokemonSlot QLabel { background: transparent; border: none; padding: 0; }
QLabel[uiRole="rosterName"] {
    color: #e9eef7; font-family: "Pixelify Sans"; font-size: 15px; font-weight: 600;
}
QLabel[uiRole="rosterSpecies"] { color: #6f7b8e; font-family: "Manrope"; font-size: 10px; }
QLabel[uiRole="rosterPosition"] { color: #475164; font-family: "DM Mono"; font-size: 10px; }
QLabel[uiRole="rosterCount"] {
    color: #4f596b; font-family: "Pixelify Sans"; font-size: 13px; letter-spacing: 1.58px;
}
QLabel[uiRole="rosterHPValue"] { color: #657084; font-family: "DM Mono"; font-size: 9px; }
QLabel[uiRole="rosterLevel"] { color: #cad2e1; font-family: "DM Mono"; font-size: 15px; font-weight: 500; }
QLabel[uiRole="rosterLevelCaption"] { color: #555f70; font-family: "DM Mono"; font-size: 8px; font-weight: 500; }
QLabel[uiRole="rosterChevron"] { color: #3e4858; font-family: "Manrope"; font-size: 20px; }
QProgressBar[uiRole="rosterHP"] {
    background: #252c37; border: none; border-radius: 0; min-height: 3px; max-height: 3px;
}
QProgressBar[uiRole="rosterHP"]::chunk { background: #57e5a2; border-radius: 0; }
QProgressBar[ivQuality="low"]::chunk { background: $danger; }
QProgressBar[ivQuality="average"]::chunk { background: $warning; }
QProgressBar[ivQuality="excellent"]::chunk { background: $success; }
QLabel[statusRole="success"] { color: $success; }
QLabel[uiRole="statusBadge"][status="no-focus"] { color: $text_muted; }
QFrame#applicationToolbar { background: #090d13; border-bottom: 1px solid $border_soft; }
QLabel[uiRole="pageEyebrow"] {
    color: #9caccc; font-family: "Pixelify Sans"; font-size: 12px;
    font-weight: 500; letter-spacing: 1.8px;
}
QFrame#headingAccentRule {
    border: none;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #7388aa,stop:1 transparent);
}
QLabel[uiRole="pageHeaderTitle"] {
    color: #e9eef7; font-family: "Pixelify Sans"; font-size: 42px;
    font-weight: 600; letter-spacing: -1.05px;
}
QLabel[uiRole="pageSubtitle"] { color: #828da0; font-family: "Manrope"; font-size: 13px; }
QFrame#trackerTabGroup {
    background: rgba(17, 22, 32, 219); border: 1px solid rgba(181, 198, 224, 33);
    border-radius: 0;
}
QPushButton[buttonRole="trackerTab"] {
    color: #6f7a8d; background: transparent; border: none; border-radius: 0;
    font-family: "Pixelify Sans"; font-size: 13px; font-weight: 500; padding: 0;
}
QPushButton[buttonRole="trackerTab"]:checked {
    color: #f0f4ff; border: none; border-top: 1px solid rgba(255, 255, 255, 20);
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #2b3547,stop:1 #1d2432);
}
QPushButton[buttonRole="trackerTab"]:hover:!checked { color: #bcc8df; background: #171d29; }
QFrame#trainingFocusCard {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #151b26,stop:1 #0d1119);
    border: 1px solid rgba(181, 198, 224, 43); border-radius: 3px;
}
QFrame#focusIdentity { background: transparent; border: none; }
QFrame#focusDivider {
    border: none;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 transparent,
        stop:0.12 rgba(181,198,224,33),stop:0.88 rgba(181,198,224,33),stop:1 transparent);
}
QLabel[uiRole="focusKicker"] {
    color: #8c9cba; font-family: "Pixelify Sans"; font-size: 11px;
    font-weight: 500; letter-spacing: 1.32px;
}
QLabel[uiRole="focusIdentityName"] {
    color: #e9eef7; font-family: "Pixelify Sans"; font-size: 35px; font-weight: 600;
}
QLabel[uiRole="focusSpecies"] { color: #7e899c; font-family: "Manrope"; font-size: 11px; }
QLabel[uiRole="focusHPLabel"] { color: #57e5a2; font-family: "DM Mono"; font-size: 8px; }
QLabel[uiRole="focusHPNumber"] { color: #8996ad; font-family: "DM Mono"; font-size: 8px; }
QProgressBar[uiRole="focusHPBar"] {
    background: #252c37; border: none; border-radius: 0; min-height: 3px; max-height: 3px;
}
QProgressBar[uiRole="focusHPBar"]::chunk { background: #57e5a2; border-radius: 0; }
QLabel[uiRole="focusDescription"] { color: #68768f; font-family: "Manrope"; font-size: 9px; }
QLabel[uiRole="focusEVTotal"] { color: #e9eef7; font-family: "DM Mono"; font-size: 20px; font-weight: 500; }
QLabel[uiRole="focusEVCap"] { color: #5f697a; font-family: "DM Mono"; font-size: 8px; padding-top: 9px; }
QFrame#focusEVCell { background: rgba(8,11,17,82); border: 1px solid rgba(181,198,224,31); border-radius: 0; }
QLabel[uiRole="focusEVLabel"] { color: #647084; font-family: "DM Mono"; font-size: 8px; }
QLabel[uiRole="focusEVValue"] { color: #e9eef7; font-family: "DM Mono"; font-size: 18px; font-weight: 500; }
QProgressBar[uiRole="focusEVMeter"] {
    background: #232a36; border: none; border-radius: 0; min-height: 2px; max-height: 2px;
}
QProgressBar[uiRole="focusEVMeter"]::chunk { background: #8fb8ff; border-radius: 0; }
QPushButton[buttonRole="focusChoice"] {
    background: rgba(8,11,17,77); border: 1px solid rgba(181,198,224,33); border-radius: 0; padding: 0;
}
QPushButton[buttonRole="focusChoice"]:checked {
    background: rgba(102,130,198,31); border: 1px solid rgba(141,169,255,97);
}
QPushButton[buttonRole="focusChoice"]:hover { border-color: #6c7fa3; }
QLabel[uiRole="focusChoiceName"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 12px; font-weight: 500; }
QLabel[uiRole="focusChoiceState"] { color: #566277; font-family: "Manrope"; font-size: 8px; }
QLabel[uiRole="focusChoiceState"][allowed="true"] { color: #8294c2; }
QPushButton[buttonRole="focusReset"] { color: #798598; font-family: "Manrope"; font-size: 9px; border: none; background: transparent; padding: 0; }
QPushButton[buttonRole="focusReset"]:hover { color: #e9eef7; }
QLabel[uiRole="focusSavedNote"] { color: #566277; font-family: "DM Mono"; font-size: 8px; }
QLabel[uiRole="focusSavedCheck"] { color: #57dda2; font-family: "Segoe UI"; font-size: 9px; }
QLabel[uiRole="trainingMicro"] {
    color: #65748e; font-family: "DM Mono"; font-size: 7px; letter-spacing: 0.5px;
}
QLabel[uiRole="walkStatus"] { color: #697589; font-family: "Pixelify Sans"; font-size: 10px; letter-spacing: 1px; }
QLabel[uiRole="walkStatus"][walkState="active"] { color: #57e5a2; font-weight: 600; letter-spacing: .8px; }
QLabel[uiRole="walkStatus"][walkState="pending"], QLabel[uiRole="walkStatus"][walkState="paused"] { color: #ffcb69; }
QLabel[uiRole="walkStatus"][walkState="reached"] { color: #57e5a2; }
QFrame#inspectionCard { background: #0f141c; border: 1px solid #303846; border-radius: 2px; }
QWidget#inspectionHeader { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #151c27,stop:.45 #10151d,stop:1 #0e131a); border: none; }
QWidget#inspectionDetails { border: none; border-top: 1px solid #252d38; }
QLabel[uiRole="inspectionSpecies"] { color: #67758c; font-family: "Manrope"; font-size: 9px; }
QLabel[uiRole="inspectionName"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 23px; font-weight: 600; }
QLabel[uiRole="inspectionMeta"], QLabel[uiRole="inspectionItemName"] { color: #8095b1; font-family: "Manrope"; font-size: 9px; }
QLabel[uiRole="inspectionKicker"], QLabel[uiRole="inspectionLegend"] { color: #67758c; font-family: "DM Mono"; font-size: 8px; }
QLabel[uiRole="inspectionKicker"] { letter-spacing: .5px; }
QLabel[uiRole="inspectionFact"] { color: #e9eef7; font-family: "Manrope"; font-size: 14px; font-weight: 700; }
QPushButton[buttonRole="notificationPreview"] { background: #111923; border: 1px solid #303c50; border-radius: 0; color: #8c9cba; font-family: "Pixelify Sans"; font-size: 11px; padding: 0 10px; }
QPushButton[buttonRole="notificationPreview"]:hover { color: #e9eef7; background: #1d293a; }
QWidget#notificationSetting, QWidget#notificationPreviews { border: none; border-bottom: 1px solid #232b36; }
QWidget#notificationSettingsHeader { border: none; border-bottom: 1px solid #2b3441; }
QLabel[uiRole="notificationSettingTitle"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 14px; font-weight: 600; }
QLabel[uiRole="inspectionNatureUp"], QLabel[uiRole="inspectionNatureDown"] { font-family: "Manrope"; font-size: 10px; font-weight: 400; }
QLabel[uiRole="inspectionNatureUp"] { color: #8fb8ff; }
QLabel[uiRole="inspectionNatureDown"] { color: #ff718b; }
QLabel[uiRole="inspectionIvValue"] { font-family: "DM Mono"; font-size: 10px; }
QProgressBar#inspectionHpBar { border: none; border-radius: 0; padding: 0; min-height: 0; max-height: 3px; background: #252d38; }
QProgressBar#inspectionHpBar::chunk { background: #57e5a2; border-radius: 0; }
QFrame#inspectionItemBadge { background: #0d1219; border: 1px solid #252d38; border-radius: 0; }
QLabel#inspectionItemIcon { background: #0e141c; border: 1px solid #252d38; }
QPushButton#inspectionToggle { color: #67758c; font-size: 20px; background: transparent; border: none; padding: 0; }
QPushButton#inspectionToggle:hover { color: #b9c8ec; }
QFrame#inspectionFactDivider, QFrame#inspectionDetailRule { background: #252d38; border: none; }
QFrame#inspectionIvRow { background: #111720; border: 1px solid #283240; border-radius: 0; }
QProgressBar#inspectionIvBar { background: #212a36; border: 1px solid #303a49; border-radius: 0; padding: 0; min-height: 0; max-height: 7px; }
QProgressBar#inspectionIvBar::chunk { border: none; border-radius: 0; }
QProgressBar#inspectionIvBar[ivQuality="low"]::chunk { background: #ff718b; }
QProgressBar#inspectionIvBar[ivQuality="average"]::chunk { background: #ffcb69; }
QProgressBar#inspectionIvBar[ivQuality="excellent"]::chunk { background: #57e5a2; }
QFrame#friendshipOverview { background: rgba(8,11,17,60); border: 1px solid #252c38; }
QFrame#friendshipScore { background: transparent; border: none; border-left: 1px solid #252c38; border-right: 1px solid #252c38; }
QComboBox[uiRole="walkPokemonSelect"] {
    background: transparent; color: #e9eef7; border: none; padding: 0;
    font-family: "Pixelify Sans"; font-size: 14px; font-weight: 600;
}
QComboBox[uiRole="walkPokemonSelect"]::drop-down { border: none; width: 0; }
QLabel[uiRole="walkSpecies"] { color: #566277; font-family: "Manrope"; font-size: 7px; }
QLabel[uiRole="walkValue"] { color: #e9eef7; font-family: "DM Mono"; font-size: 22px; font-weight: 500; }
QLabel[uiRole="walkTarget"] { color: #64748e; font-family: "DM Mono"; font-size: 8px; }
QLabel[uiRole="walkETA"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 16px; font-weight: 500; }
QProgressBar[uiRole="walkProgress"] { border: 1px solid #303746; background: #222a38; border-radius: 0; min-height: 4px; max-height: 4px; }
QProgressBar[uiRole="walkProgress"]::chunk { background: #c98bc8; border-radius: 0; }
QPushButton[buttonRole="walkSetting"] {
    color: #697589; background: #101620; border: 1px solid #29303e; border-radius: 0;
    font-family: "Pixelify Sans"; font-size: 10px; font-weight: 500; padding: 0 4px;
}
QPushButton[buttonRole="walkSetting"]:checked { color: #d6e0f8; background: #202c42; border-color: #5a76a8; }
QPushButton[buttonRole="walkStart"] {
    color: #57e5a2; background: rgba(38,103,82,38); border: 1px solid #347259; border-radius: 0;
    font-family: "Pixelify Sans"; font-size: 12px; font-weight: 500; padding: 0 8px;
}
QPushButton[buttonRole="walkStart"]:disabled { color: #526d60; border-color: #283e35; }
QPushButton[buttonRole="walkStop"] { color: #ff718b; border: 1px solid #824455; background: #2c1d27; border-radius: 0; font-family: "Pixelify Sans"; font-size: 12px; font-weight: 500; padding: 0 8px; }
QLabel[uiRole="walkSafety"] { color: #596578; font-family: "Manrope"; font-size: 7px; }
QPushButton[buttonRole="trainingText"] { color: #65748e; font-family: "Manrope"; font-size: 7px; background: transparent; border: none; padding: 0; min-height: 0; }
QFrame#readoutRow { background: transparent; border: none; border-top: 1px solid rgba(181,198,224,15); }
QLabel[uiRole="readoutName"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 12px; font-weight: 500; }
QLabel[uiRole="readoutDetail"] { color: #566277; font-family: "Manrope"; font-size: 7px; }
QLabel[uiRole="readoutBadge"] { color: #75839a; font-family: "DM Mono"; font-size: 7px; padding: 3px 4px; border: 1px solid #2a3444; }
QLabel[uiRole="readoutBadge"][status="good"] { color: #57e5a2; border-color: #275545; background: rgba(29,65,51,40); }
QLabel[uiRole="readoutBadge"][status="avoid"] { color: #ff718b; border-color: #593542; background: rgba(65,29,42,40); }
QLabel[uiRole="readoutBadge"][status="mixed"] { color: #ffcb69; border-color: #6b5837; background: rgba(65,53,29,40); }
QLabel[uiRole="battleHeading"] { color: #8999b4; font-family: "Pixelify Sans"; font-size: 12px; font-weight: 500; letter-spacing: 1.44px; }
QLabel[uiRole="battleLive"] { color: #ff718b; font-family: "Pixelify Sans"; font-size: 12px; letter-spacing: 1px; }
QLabel[uiRole="battleName"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 25px; font-weight: 600; }
QLabel[uiRole="battleYield"] { color: #c0d5ff; font-family: "Manrope"; font-size: 9px; font-weight: 700; }
QListWidget#evChangeHistory { background: transparent; border: none; padding: 0; }
QLabel#historySprite { background: #1a2432; border: 1px solid #253343; border-radius: 14px; }
QLabel[uiRole="historyGain"] { color: #57e5a2; font-family: "DM Mono"; font-size: 9px; font-weight: 500; }
QLabel[uiRole="historyAge"] { color: #526079; font-family: "DM Mono"; font-size: 7px; }
/* Remaining companion workspaces share the measured reference typography. */
QFrame#activeRunHeader { background: transparent; border: none; }
QFrame#runHero {
    background: qradialgradient(cx:0.85,cy:0.2,radius:0.95,fx:0.85,fy:0.2,stop:0 #27283b,stop:0.5 #191e29,stop:1 #0f141d);
    border: 1px solid #303846; border-radius: 8px;
}
QFrame#runMetrics { background: #242c38; border: 1px solid #3b4555; border-radius: 8px; }
QFrame#runMetricCell { background: #10151e; border: none; border-radius: 0; }
QLabel[uiRole="runMetricValue"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 35px; font-weight: 600; }
QLabel[uiRole="runMetricCaption"] { color: #8190aa; font-family: "Pixelify Sans"; font-size: 11px; letter-spacing: 1px; }
QLabel[uiRole="runKicker"] { color: #8c9cba; font-family: "Pixelify Sans"; font-size: 11px; letter-spacing: 1.32px; }
QLabel[uiRole="runMicro"] { color: #67758c; font-family: "DM Mono"; font-size: 8px; }
QLabel[uiRole="runHint"] { color: #68768a; font-family: "Manrope"; font-size: 9px; }
QLabel[uiRole="runActiveState"] { color: #57e5a2; font-family: "DM Mono"; font-size: 9px; }
QLabel[uiRole="runFightTitle"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 38px; font-weight: 600; }
QLabel[uiRole="runFightDescription"] { color: #7b879c; font-family: "Manrope"; font-size: 12px; }
QLabel[uiRole="runCapValue"] { color: #e9eef7; font-family: "Manrope"; font-size: 60px; font-weight: 600; }
QLabel[uiRole="runCapWarning"] { color: #ffcb69; font-family: "Manrope"; font-size: 9px; }
QPushButton[buttonRole="runAction"] { background: transparent; border: none; color: #7888a3; font-family: "Manrope"; font-size: 9px; padding: 0 6px; min-height: 0; }
QPushButton[buttonRole="runAction"]:hover { color: #e9eef7; }
QPushButton[buttonRole="finishRun"] { color: #ffcb69; background: rgba(95,77,35,26); border: 1px solid #5a4b31; font-family: "Pixelify Sans"; font-size: 12px; padding: 0 12px; border-radius: 0; }
QPushButton[buttonRole="runLibrary"] { color: #c3d1ed; background: #1b2436; border: 1px solid #526587; font-family: "Pixelify Sans"; font-size: 12px; padding: 0 12px; border-radius: 0; }
QFrame#runReadiness, QFrame#encounterLedger { border-radius: 0; }
QFrame#readinessTile { background: #111720; border: 1px solid #252d3a; border-radius: 0; }
QLabel[uiRole="readinessName"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 11px; font-weight: 600; }
QLabel[uiRole="readinessMeta"] { color: #5f6c80; font-family: "Manrope"; font-size: 6px; }
QLabel[uiRole="readinessState"] { color: #5f6c80; font-family: "DM Mono"; font-size: 5px; }
QLabel[uiRole="readinessState"][status="good"] { color: #57e5a2; }
QLabel[uiRole="readinessState"][status="mixed"] { color: #ffcb69; }
QLabel[uiRole="ledgerTitle"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 24px; font-weight: 600; }
QPushButton[buttonRole="ledgerFilter"] { font-family: "Pixelify Sans"; font-size: 10px; color: #74839c; background: #111720; border: 1px solid #2a3342; border-radius: 0; padding: 0 6px; }
QPushButton[buttonRole="ledgerFilter"]:checked { color: #d9e5fc; background: #222e43; border-color: #526d9b; }
QPushButton[buttonRole="ledgerRow"] { background: transparent; border: none; border-top: 1px solid #212936; border-left: 2px solid transparent; padding: 0; border-radius: 0; }
QPushButton[buttonRole="ledgerRow"][openEncounter="true"] { background: rgba(37,73,68,16); }
QPushButton[buttonRole="ledgerRow"]:hover { background: #1a2433; }
QLabel[uiRole="ledgerLocation"], QLabel[uiRole="ledgerName"] { color: #c2cddd; font-family: "Pixelify Sans"; font-size: 11px; }
QLabel[uiRole="ledgerName"] { color: #e9eef7; font-weight: 600; }
QLabel[uiRole="ledgerBadge"] { color: #aac9ff; border: 1px solid #344861; font-family: "DM Mono"; font-size: 8px; padding: 4px 9px; }
QLabel[uiRole="ledgerBadge"][status="NOT_ENCOUNTERED"] { color: #57e5a2; border-color: #275c4c; }
QLabel[uiRole="ledgerBadge"][status="DEAD"], QLabel[uiRole="ledgerBadge"][status="FAILED"] { color: #ff718b; border-color: #603345; }
QFrame#encounterLedger { background: #10151d; border: 1px solid #2a3342; }
QFrame#ledgerFooter { background: #0e131b; border: none; border-top: 1px solid #2a3342; }
QFrame#ledgerFilters { background: #131922; border: none; border-top: 1px solid #2a3342; border-bottom: 1px solid #2a3342; }
QWidget#ledgerHeading { background: #0e141c; }
QLabel[uiRole="ledgerFooterText"] { color: #5f6b7f; font-family: "DM Mono"; font-size: 7px; }
QLabel[uiRole="ledgerSpecies"] { color: #5f6b7d; font-family: "Manrope"; font-size: 7px; }
QLabel[uiRole="ledgerNotes"] { color: #687589; font-family: "Manrope"; font-size: 8px; }
QLabel[uiRole="ledgerLocation"] { color: #b5bfce; font-weight: 500; }
QLabel[uiRole="ledgerBadge"] { font-size: 7px; padding: 5px 7px; min-width: 53px; }
QLabel[uiRole="locationPageTitle"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 31px; font-weight: 600; }
QWidget#ledgerHeader { background: qradialgradient(cx:.8,cy:.2,radius:.4,fx:.8,fy:.2,stop:0 #14211f,stop:1 #10151d); }
QPushButton[buttonRole="ledgerAction"] { color: #8292af; background: #111720; border: 1px solid #283242; border-radius: 0; font-family: "Pixelify Sans"; font-size: 10px; font-weight: 500; padding: 0 10px; min-height: 25px; }
QPushButton[buttonRole="ledgerAction"][primary="true"], QPushButton[buttonRole="ledgerAction"]:checked { color: #c1cde4; background: #1c2638; border-color: #415674; }
QPushButton[buttonRole="ledgerAction"]:hover { border-color: #6f89b3; }
QLabel[uiRole="ledgerActionText"] { color: #8292af; font-family: "Pixelify Sans"; font-size: 10px; font-weight: 500; }
QPushButton[primary="true"] QLabel[uiRole="ledgerActionText"] { color: #c1cde4; }
QFrame#locationSummary { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #151c26,stop:1 #10151d); border: 1px solid #2a3342; border-radius: 0; }
QFrame#locationCount { background: transparent; border: none; border-left: 1px solid #2a3342; }
QLabel[uiRole="locationCountValue"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 23px; font-weight: 600; }
QFrame#locationEditor { background: #111720; border: 1px solid #29313e; border-left: 3px solid #8fb8ff; border-radius: 0; }
QFrame#locationEditor[status="NOT_ENCOUNTERED"] { border-left-color: #57e5a2; }
QFrame#locationEditor[status="FAILED"], QFrame#locationEditor[status="DEAD"] { border-left-color: #ff718b; }
QFrame#locationEditorHeader { background: transparent; border: none; border-bottom: 1px solid #29313e; }
QLabel[uiRole="locationMicro"] { color: #59667a; font-family: "DM Mono"; font-size: 6px; }
QLabel[uiRole="locationTitle"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 15px; font-weight: 600; }
QLineEdit[uiRole="locationInput"] { color: #b5bfce; background: #0e141c; border: 1px solid #252d3a; border-radius: 0; padding: 0 9px; font-family: "Manrope"; font-size: 9px; min-width: 0; }
QLineEdit[uiRole="locationInput"]:focus { border-color: #5975a5; }
QPushButton[buttonRole="locationStatus"] { background: #111720; border: 1px solid #252d3a; border-radius: 0; padding: 0 2px; min-width: 0; font-family: "Pixelify Sans"; font-size: 8px; color: #697991; }
QPushButton[buttonRole="locationStatus"]:checked { background: #222e43; border-color: #526d9b; color: #d9e5fc; }
QPushButton[buttonRole="locationReset"], QPushButton[buttonRole="locationBack"] { background: transparent; border: none; border-radius: 0; padding: 0; color: #657287; font-family: "Pixelify Sans"; font-size: 8px; min-height: 12px; }
QPushButton[buttonRole="locationBack"] { font-size: 12px; }
QLabel[uiRole="locationError"] { color: #ff718b; font-family: "Manrope"; font-size: 11px; }
QFrame#disconnectedView { background: transparent; border: none; }
QLabel[uiRole="offlineTitle"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 39px; font-weight: 600; }
QLabel[uiRole="offlineKicker"] { color: #ff7185; font-family: "DM Mono"; font-size: 9px; font-weight: 500; letter-spacing: 1.2px; }
QLabel[uiRole="offlineDescription"] { color: #5e748d; font-family: "Manrope"; font-size: 10px; }
QLabel[uiRole="offlineMicro"] { color: #536a84; font-family: "DM Mono"; font-size: 7px; }
QFrame#offlineGuide { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #151b26,stop:1 #0d1119); border: 1px solid #2b3340; border-radius: 0; }
QFrame#offlineGuideHeader { background: transparent; border: none; border-bottom: 1px solid #252d3a; }
QLabel[uiRole="offlineGuideKicker"] { color: #819cc5; font-family: "Pixelify Sans"; font-size: 11px; letter-spacing: 1.2px; }
QLabel[uiRole="offlineGuideTitle"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 21px; font-weight: 600; }
QLabel[uiRole="offlineMenuPath"] { color: #7f9ccc; background: #0d131c; border: 1px solid #263245; font-family: "DM Mono"; font-size: 7px; padding: 8px 10px; }
QFrame#offlineStep { background: #111720; border: 1px solid #242d3b; border-radius: 0; }
QLabel[uiRole="offlineStepNumber"] { color: #8fb8ff; font-family: "DM Mono"; font-size: 7px; font-weight: 500; }
QLabel[uiRole="offlineStepTitle"] { color: #e1e9f7; font-family: "Pixelify Sans"; font-size: 12px; font-weight: 500; }
QLabel[uiRole="offlineStepCopy"] { color: #566b86; font-family: "Manrope"; font-size: 8px; }
QFrame#offlineFileHint { background: #101620; border: 1px dashed #2b3d59; border-radius: 0; }
QLabel[uiRole="offlineScriptPath"] { color: #8fb8ff; font-family: "DM Mono"; font-size: 8px; font-weight: 500; }
QLabel[uiRole="offlineFootnote"] { color: #526780; font-family: "Manrope"; font-size: 7px; }
QFrame#offlineGuideFooter { background: transparent; border: none; border-top: 1px solid #252d3a; }
QPushButton[buttonRole="offlineRetry"] { background: #202a3d; border: 1px solid #415675; border-radius: 0; color: #c1d1f0; font-family: "Pixelify Sans"; font-size: 11px; font-weight: 500; padding: 0 12px; min-height: 38px; max-height: 38px; }
QPushButton[buttonRole="offlineRetry"]:hover { background: #29374e; border-color: #7193c6; }
QLabel#connectionBadge { font-family: "DM Mono"; font-size: 10px; padding: 0; border-radius: 1px; }
QLabel#connectionBadge[connectionState="disconnected"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #19121a,stop:1 #100e14); border: 1px solid #50303c; color: #ff7185; }
QLabel#connectionBadge[connectionState="connected"] { background: #0d1717; border: 1px solid #2b5846; color: #57dda2; }
QPushButton[buttonRole="compactToggle"] { background: #111720; border: 1px solid #26313f; border-radius: 1px; font-family: "Manrope"; font-size: 12px; padding: 0 12px; min-height: 0; }
QPushButton[buttonRole="compactToggle"]:disabled { color: #46505f; background: #0c1118; border-color: #171e29; }
QPushButton#settingsCog { background: #111720; border: 1px solid #2b3543; border-radius: 1px; padding: 0; min-height: 0; }
QPushButton#settingsCog:hover { background: #1b2432; border-color: #516582; }
QFrame#diagnosticsHero { border-radius: 8px; }
QFrame#healthField, QFrame#diagnosticParty { border-radius: 8px; }
QFrame#diagnosticSnapshotHeader { background: transparent; border: none; border-bottom: 1px solid #29313f; }
QLabel[uiRole="healthCaption"] { color: #7f9ccc; font-family: "Pixelify Sans"; font-size: 11px; letter-spacing: 1.1px; }
QLabel[uiRole="healthHeroTitle"] { color: #e9eef7; font-family: "Pixelify Sans"; font-size: 26px; font-weight: 600; }
QLabel[uiRole="healthHint"] { color: #657a9c; font-family: "Manrope"; font-size: 9px; }
QLabel[uiRole="healthPartyName"] { color: #e9eef7; font-family: "Manrope"; font-size: 11px; font-weight: 700; }
QLabel[uiRole="healthCheck"] { font-family: "Segoe UI Symbol"; font-size: 12px; }
QFrame#diagnosticParty QFrame[uiRole="raisedPanel"] { background: #101720; border: none; border-radius: 0; }
QFrame#healthField QLabel[uiRole="metricValue"] { color: #e9eef7; font-family: "Manrope"; font-size: 16px; }
QFrame#healthField QLabel[uiRole="small"] { color: #657a9c; font-family: "Manrope"; font-size: 9px; }
QLabel#connectionHealthIcon { background: rgba(41,91,74,30); border: 1px solid #2b5a4c; }
QLabel#connectionHealthIcon[statusRole="warning"] { background: rgba(91,73,41,30); border-color: #6b5837; }
QPushButton[buttonRole="advancedSwitch"] { color: #6d809e; font-family: "Manrope"; font-size: 10px; border: none; background: transparent; padding: 0 4px; min-height: 14px; }
QPushButton[buttonRole="advancedSwitch"]:checked { color: #a8c7ff; }
QPushButton[buttonRole="topNavigation"] { border-top: none; border-bottom: 2px solid transparent; }
QPushButton[buttonRole="topNavigation"]:checked { border-top: none; border-bottom: 2px solid #8fb8ff; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 transparent,stop:1 #141c29); }
QPushButton[buttonRole="topNavigation"][tight="true"] { padding: 12px 7px; font-size: 13px; }
"""

def readable_stylesheet(stylesheet: str) -> str:
    """One full-window type scale, also used by panels with local stylesheets."""
    sizes = {5: 9, 6: 10, 7: 10, 8: 11, 9: 12, 10: 13, 11: 14,
             12: 15, 13: 16, 14: 17, 15: 18, 16: 19, 18: 21,
             20: 24, 21: 25, 23: 27, 24: 28, 26: 30, 39: 46}

    def enlarge(match):
        value, unit = int(match[1]), match[2]
        size = sizes.get(value, value) if unit == "px" else value+2 if value <= 12 else value
        return f"font-size: {size}{unit}"

    return re.sub(r"font-size:\s*(\d+)(px|pt)", enlarge, stylesheet)


COMPACT_STYLESHEET = _STYLESHEET
for _token, _value in sorted(_TOKENS.items(), key=lambda item: len(item[0]), reverse=True):
    COMPACT_STYLESHEET = COMPACT_STYLESHEET.replace(f"${_token}", _value)
APP_STYLESHEET = readable_stylesheet(COMPACT_STYLESHEET)


_FONT_IDS = []
_STYLE_GENERATION = 0


def apply_theme(widget: QWidget) -> None:
    global _STYLE_GENERATION
    if not _FONT_IDS:
        for path in (Path(__file__).resolve().parents[1] / "assets/fonts").glob("*.ttf"):
            _FONT_IDS.append(QFontDatabase.addApplicationFont(str(path)))
    compact = getattr(widget, "compact_mode", False) or type(widget).__name__.startswith("Compact")
    stylesheet = COMPACT_STYLESHEET if compact else APP_STYLESHEET
    provider = getattr(widget, "provider", None)
    if provider is not None:
        stylesheet = stylesheet.replace(COLORS["accent"], provider.theme.accent_primary)
        stylesheet = stylesheet.replace(COLORS["inspection"], provider.theme.accent_secondary)
    widget.setStyleSheet(stylesheet)
    _STYLE_GENERATION += 1


def refresh_style(widget: QWidget) -> None:
    # Dynamic-property selectors need repolishing only when their inputs change.
    # Qt's private bookkeeping properties can change during polishing itself.
    signature = (
        _STYLE_GENERATION, widget.objectName(), widget.styleSheet(),
        tuple((bytes(name), repr(widget.property(bytes(name).decode())))
              for name in widget.dynamicPropertyNames() if not bytes(name).startswith(b"_q_")),
    )
    if getattr(widget, "_last_style_signature", None) == signature:
        return
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget._last_style_signature = signature
