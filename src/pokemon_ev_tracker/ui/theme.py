"""Central Trainer Lab Terminal colors, typography and semantic widget roles."""

from __future__ import annotations

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

_TOKENS = {**COLORS, **METRICS}

_STYLESHEET = """
QMainWindow, QWidget {
    background-color: $canvas;
    color: $text;
    font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif;
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

APP_STYLESHEET = _STYLESHEET
for _token, _value in sorted(_TOKENS.items(), key=lambda item: len(item[0]), reverse=True):
    APP_STYLESHEET = APP_STYLESHEET.replace(f"${_token}", _value)


def apply_theme(widget: QWidget) -> None:
    widget.setStyleSheet(APP_STYLESHEET)


def refresh_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
