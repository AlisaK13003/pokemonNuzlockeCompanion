"""Reference focus-card presentation; persistence and EV decisions stay in the owner."""

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.app_shell import PageHeading
from pokemon_ev_tracker.ui.icons import reset_icon
from pokemon_ev_tracker.ui.party_card import EV_STAT_LABELS
from pokemon_ev_tracker.ui.party_selector import PokemonSprite, pokemon_name
from pokemon_ev_tracker.ui.theme import refresh_style


def text(value, role):
    widget = QLabel(value)
    widget.setProperty("uiRole", role)
    return widget


def divider():
    line = QFrame()
    line.setObjectName("focusDivider")
    line.setFixedHeight(1)
    return line


class FocusIdentity(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("focusIdentity")
        self.setFixedHeight(190)
        row = QHBoxLayout(self)
        row.setContentsMargins(28, 24, 28, 17)
        row.setSpacing(22)
        self.sprite = PokemonSprite(136, padding=0, reference_art=True, reference_bounds=(106, 112))
        self.sprite.setFixedSize(130, 136)
        row.addWidget(self.sprite)
        details = QWidget()
        details.setFixedSize(230, 108)
        body = QVBoxLayout(details)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        kicker = text("TRAINING FOCUS", "focusKicker")
        kicker.setFixedHeight(13)
        body.addWidget(kicker)
        body.addSpacing(6)
        self.name = PageHeading("Select a Pokémon")
        self.name.setProperty("uiRole", "focusIdentityName")
        self.name.setFixedHeight(33)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        body.addWidget(self.name)
        self.species = text("Waiting for live party data", "focusSpecies")
        self.species.setFixedHeight(17)
        body.addWidget(self.species)
        body.addSpacing(15)
        hp = QHBoxLayout()
        hp.setSpacing(8)
        hp.addWidget(text("HP", "focusHPLabel"))
        self.hp_bar = QProgressBar()
        self.hp_bar.setProperty("uiRole", "focusHPBar")
        self.hp_bar.setTextVisible(False)
        self.hp_bar.setFixedHeight(3)
        hp.addWidget(self.hp_bar, 1)
        self.hp = text("— / —", "focusHPNumber")
        hp.addWidget(self.hp)
        body.addLayout(hp)
        details_host = QWidget()
        details_host.setFixedSize(230, 122)
        inset = QVBoxLayout(details_host)
        inset.setContentsMargins(0, 14, 0, 0)
        inset.setSpacing(0)
        inset.addWidget(details)
        row.addWidget(details_host)
        row.addStretch(1)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        glow = QRadialGradient(QPointF(self.width() * 0.12, self.height() * 0.65), self.width() * 0.36)
        glow.setColorAt(0, QColor(141, 169, 255, 23))
        glow.setColorAt(1, QColor(141, 169, 255, 0))
        painter.fillRect(self.rect(), glow)

    def refresh(self, pokemon):
        self.sprite.set_species(getattr(pokemon, "species_id", None))
        self.name.setText(pokemon_name(pokemon) if pokemon else "Select a Pokémon")
        self.species.setText(f"{pokemon.species}  ·  Level {pokemon.level or '—'}" if pokemon else "Waiting for live party data")
        current = getattr(pokemon, "current_hp", None)
        maximum = getattr(pokemon, "max_hp", None)
        self.hp.setText(f"{current if current is not None else '—'} / {maximum or '—'}")
        self.hp_bar.setRange(0, max(maximum or 1, 1))
        self.hp_bar.setValue(current or 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        small = self.width() < 420
        self.layout().setSpacing(12 if small else 22)
        self.sprite.setFixedWidth(80 if small else 130)


class FocusChoice(QPushButton):
    def __init__(self, name):
        super().__init__()
        self.setCheckable(True)
        self.setProperty("buttonRole", "focusChoice")
        self.setFixedHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 9, 10, 9)
        row.setSpacing(10)
        self.check = QLabel()
        self.check.setFixedSize(16, 16)
        row.addWidget(self.check)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        self.name = text(name, "focusChoiceName")
        self.state = text("Avoid", "focusChoiceState")
        self.name.setFixedHeight(14)
        self.state.setFixedHeight(12)
        copy.addWidget(self.name)
        copy.addWidget(self.state)
        row.addLayout(copy, 1)
        for widget in (self.check, self.name, self.state):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.set_focus(False)

    def set_focus(self, allowed):
        self.state.setText("Allowed" if allowed else "Avoid")
        self.state.setProperty("allowed", allowed)
        refresh_style(self.state)
        self.setAccessibleName(f"{self.name.text()} · {'Allowed' if allowed else 'Avoid'}")
        self.check.update()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.check.geometry().adjusted(0, 0, -1, -1)
        painter.setPen(QPen(QColor("#8fb8ff" if self.isChecked() else "#394355"), 1))
        painter.setBrush(QColor("#8fb8ff") if self.isChecked() else Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        if self.isChecked():
            painter.setPen(QPen(QColor("#0b1017"), 1))
            x, y = rect.x(), rect.y()
            painter.drawLine(x + 4, y + 8, x + 7, y + 11)
            painter.drawLine(x + 7, y + 11, x + 11, y + 4)


class FocusCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("trainingFocusCard")
        self.setProperty("uiRole", "panel")
        self.grids = []

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for grid, widgets, wide, narrow in self.grids:
            columns = narrow if self.width() < 440 else wide
            for column in range(wide):
                grid.setColumnStretch(column, 1 if column < columns else 0)
            for index, widget in enumerate(widgets):
                grid.removeWidget(widget)
                grid.addWidget(widget, index // columns, index % columns)


class SavedCheck(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(8, 11)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#57dda2"), 1))
        painter.drawLine(0, 5, 2, 8)
        painter.drawLine(2, 8, 7, 1)


def install_training_focus(view):
    view.ev_panel = FocusCard()
    root = QVBoxLayout(view.ev_panel)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    view.identity = FocusIdentity()
    root.addWidget(view.identity)
    root.addWidget(divider())
    ev_section = QWidget()
    ev = QVBoxLayout(ev_section)
    ev.setContentsMargins(28, 19, 28, 19)
    ev.setSpacing(0)
    title_host = QWidget()
    title_host.setFixedHeight(42)
    title = QHBoxLayout(title_host)
    title.setContentsMargins(0, 0, 0, 0)
    title.setSpacing(4)
    copy = QVBoxLayout()
    copy.setSpacing(7)
    copy.addWidget(text("CURRENT EFFORT VALUES", "focusKicker"))
    copy.addWidget(text("Live values read from party memory", "focusDescription"))
    title.addLayout(copy, 1)
    view.total = QLabel("— / 510 total", view.ev_panel)
    view.total.hide()
    view.total_value = text("—", "focusEVTotal")
    title.addWidget(view.total_value, 0, Qt.AlignmentFlag.AlignTop)
    title.addWidget(text("/ 510 total", "focusEVCap"), 0, Qt.AlignmentFlag.AlignTop)
    ev.addWidget(title_host)
    ev.addSpacing(16)
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(6)
    view.ev_values, view.ev_rows, view.ev_meters = {}, {}, {}
    for index, (stat, short) in enumerate(zip((key for key, _ in EV_STAT_LABELS), ("HP", "ATK", "DEF", "SPA", "SPD", "SPE"))):
        cell = QFrame()
        cell.setObjectName("focusEVCell")
        cell.setFixedHeight(85)
        cell.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        stack = QVBoxLayout(cell)
        stack.setContentsMargins(10, 10, 10, 10)
        stack.setSpacing(0)
        stat_label = text(short, "focusEVLabel")
        stat_label.setFixedHeight(22)
        stack.addWidget(stat_label)
        stack.addSpacing(4)
        value = text("—", "focusEVValue")
        value.setFixedHeight(24)
        stack.addWidget(value)
        stack.addSpacing(9)
        meter = QProgressBar()
        meter.setRange(0, 255)
        meter.setTextVisible(False)
        meter.setProperty("uiRole", "focusEVMeter")
        meter.setFixedHeight(2)
        stack.addWidget(meter)
        stack.addStretch(1)
        grid.addWidget(cell, 0, index)
        grid.setColumnStretch(index, 1)
        view.ev_rows[stat], view.ev_values[stat], view.ev_meters[stat] = cell, value, meter
    ev.addLayout(grid)
    root.addWidget(ev_section)
    root.addWidget(divider())
    preferences = QWidget()
    pref = QVBoxLayout(preferences)
    pref.setContentsMargins(28, 18, 28, 19)
    pref.setSpacing(0)
    preference_heading = QWidget()
    preference_heading.setFixedHeight(42)
    heading = QVBoxLayout(preference_heading)
    heading.setContentsMargins(0, 0, 0, 0)
    heading.setSpacing(7)
    row = QHBoxLayout()
    view.focus_title = text("ALLOWED EV TYPES", "focusKicker")
    row.addWidget(view.focus_title, 1)
    view.clear_button = QPushButton("Reset")
    view.clear_button.setIcon(reset_icon())
    view.clear_button.setIconSize(QSize(12, 12))
    view.clear_button.setProperty("buttonRole", "focusReset")
    view.clear_button.setFixedHeight(14)
    view.clear_button.clicked.connect(lambda: view.clear_focus_requested.emit(view.selected_slot))
    row.addWidget(view.clear_button)
    heading.addLayout(row)
    heading.addWidget(text("Select every stat you're happy to gain", "focusDescription"))
    pref.addWidget(preference_heading)
    pref.addSpacing(15)
    choices = QGridLayout()
    choices.setContentsMargins(0, 0, 0, 0)
    choices.setSpacing(7)
    view.focus_chips = {}
    for index, (stat, name) in enumerate(EV_STAT_LABELS):
        chip = FocusChoice(name)
        chip.setToolTip(f"{name} · allowed when selected; otherwise unwanted")
        chip.clicked.connect(lambda checked=False, key=stat: view.focus_stat_requested.emit(view.selected_slot, key))
        choices.addWidget(chip, index // 3, index % 3)
        view.focus_chips[stat] = chip
    pref.addLayout(choices)
    pref.addSpacing(12)
    view.focus_summary = text("Select every stat you're happy to gain", "focusSavedNote")
    view.focus_summary.setFixedHeight(11)
    view.focus_summary.setWordWrap(True)
    saved = QHBoxLayout()
    saved.setSpacing(7)
    view.saved_check = SavedCheck()
    saved.addWidget(view.saved_check)
    saved.addWidget(view.focus_summary, 1)
    pref.addLayout(saved)
    root.addWidget(preferences)
    view.ev_panel.grids = [(grid, list(view.ev_rows.values()), 6, 3),
                          (choices, list(view.focus_chips.values()), 3, 2)]
    # Keep the existing modal adapter available to internal callers.
    hidden = QWidget(view.ev_panel)
    view.actions_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, hidden)
    view.edit_button = QPushButton("Set training focus")
    view.edit_button.clicked.connect(lambda: view.edit_focus_requested.emit(view.selected_slot))
    view.actions_layout.addWidget(view.edit_button)
    hidden.hide()
    view.ram_state = QLabel("Waiting for party data", view.ev_panel)
    view.ram_state.hide()
