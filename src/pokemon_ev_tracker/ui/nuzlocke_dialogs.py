"""Small editors used by the game-independent Nuzlocke view."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.core.nuzlocke.fights import FightDisplayData, build_fight_timeline
from pokemon_ev_tracker.core.nuzlocke.models import (
    DeathRecord,
    LevelCap,
    NuzlockeGameProfile,
    NuzlockeRun,
)


def _heading(layout, title: str, detail: str) -> None:
    heading = QLabel(title)
    heading.setProperty("uiRole", "pageTitle")
    heading.setWordWrap(True)
    description = QLabel(detail)
    description.setProperty("uiRole", "muted")
    description.setWordWrap(True)
    if isinstance(layout, QFormLayout):
        layout.addRow(heading)
        layout.addRow(description)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    else:
        layout.addWidget(heading)
        layout.addWidget(description)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(12)


class NewRunDialog(QDialog):
    def __init__(self, profiles: tuple[NuzlockeGameProfile, ...], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Nuzlocke Run")
        self.profiles = profiles
        layout = QFormLayout(self)
        _heading(layout, "Create Nuzlocke run", "Choose a game profile for encounter locations and ordered level caps.")
        self.resize(480, 260)
        self.name_input = QLineEdit("New Run")
        self.game_input = QComboBox()
        for profile in profiles:
            self.game_input.addItem(profile.display_name, profile.game_id)
        layout.addRow("Run name", self.name_input)
        layout.addRow("Game", self.game_input)
        self.active_run_notice = QLabel("Existing party and PC Pokémon are baselined when live monitoring resumes.")
        self.active_run_notice.setWordWrap(True)
        layout.addRow(self.active_run_notice)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create Run")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("buttonRole", "primary")
        layout.addRow(buttons)

    def values(self) -> tuple[str, NuzlockeGameProfile]:
        profile = next(profile for profile in self.profiles if profile.game_id == self.game_input.currentData())
        return self.name_input.text(), profile


class FinishRunDialog(QDialog):
    def __init__(self, run_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Finish run")
        self.resize(600, 380)
        layout = QVBoxLayout(self)
        _heading(layout, f"Finish {run_name}", "Choose how this run ended. Encounters, deaths and notes will be preserved in the library.")
        self.outcome = QComboBox()
        self.outcome.hide()
        choices = QHBoxLayout()
        group = QButtonGroup(self)
        self.outcome_buttons = []
        for title, value in (("Won — final challenge completed", "WON"), ("Wiped — active party defeated", "WIPED"), ("Abandoned — ended without a win or wipe", "ABANDONED")):
            self.outcome.addItem(title, value)
            name, description = title.split(" — ")
            button = QPushButton(f"{name.upper()}\n{description}")
            button.setCheckable(True)
            button.setMinimumHeight(76)
            button.setProperty("buttonRole", "segmented")
            button.setProperty("status", {"WON": "good", "WIPED": "avoid", "ABANDONED": "mixed"}[value])
            index = len(self.outcome_buttons)
            button.clicked.connect(lambda checked=False, index=index: self.outcome.setCurrentIndex(index))
            group.addButton(button)
            choices.addWidget(button, 1)
            self.outcome_buttons.append(button)
        self.outcome.currentIndexChanged.connect(lambda index: self.outcome_buttons[index].setChecked(True))
        self.outcome_buttons[0].setChecked(True)
        layout.addLayout(choices)
        layout.addWidget(QLabel("FINAL RUN NOTES · OPTIONAL"))
        self.notes = QTextEdit()
        self.notes.setPlaceholderText("What happened? Add anything you want to remember.")
        layout.addWidget(self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Confirm finish run")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class RunHistoryDialog(QDialog):
    """Read-only history for a finished run."""

    def __init__(self, run: NuzlockeRun, profile: NuzlockeGameProfile | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Run History: {run.name}")
        self.resize(760, 570)
        layout = QVBoxLayout(self)
        _heading(layout, run.name, (
            f"{run.status} · {profile.display_name if profile else run.game} · "
            f"Started {run.started_at[:10]} · Ended {(run.ended_at or '—')[:10]}"))
        if run.outcome_note:
            layout.addWidget(QLabel(f"Outcome note: {run.outcome_note}"))
        if run.notes:
            notes = QTextEdit()
            notes.setReadOnly(True)
            notes.setPlainText(run.notes)
            notes.setMaximumHeight(80)
            layout.addWidget(notes)
        tabs = QTabWidget()
        encounters = tuple(run.encounters.values())
        tabs.addTab(self._table(("Location", "Status", "Pokémon", "Nickname", "Level", "Notes"), (
            (item.location, item.status.replace("_", " ").title(), item.species,
             item.nickname, str(item.level) if item.level is not None else "—", item.notes)
            for item in encounters
        )), "Encounters")
        tabs.addTab(self._table(("Pokémon", "Nickname", "Level", "Location / fight", "Notes"), (
            (item.species, item.nickname,
             str(item.level_at_death) if item.level_at_death is not None else "—",
             item.location_or_fight, item.notes)
            for item in run.deaths
        )), "Deaths")
        timeline = build_fight_timeline(run, profile)
        tabs.addTab(self._table(("#", "Fight", "Category", "Cap", "Status", "Notes"), (
            (str(fight.order), fight.name, fight.category,
             str(fight.effective_level_cap), fight.status, fight.notes)
            for fight in timeline.fights
        ) if timeline else ()), "Fights")
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _table(headers: tuple[str, ...], rows) -> QTableWidget:
        rows = tuple(rows)
        table = QTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                table.setItem(row, column, cell)
        table.resizeColumnsToContents()
        return table


class EncounterDialog(QDialog):
    def __init__(self, location: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Encounter: {location}")
        form = QFormLayout(self)
        _heading(form, "Edit encounter", location)
        self.resize(480, 360)
        self.species_input = QLineEdit()
        self.nickname_input = QLineEdit()
        self.level_input = QSpinBox()
        self.level_input.setRange(0, 100)
        self.level_input.setSpecialValueText("—")
        self.status_input = QComboBox()
        for value, label in (
            ("NOT_ENCOUNTERED", "Not encountered"),
            ("CAUGHT", "Caught"),
            ("FAILED", "Failed"),
            ("DEAD", "Dead"),
            ("SKIPPED", "Skipped"),
            ("DUPES", "Dupes clause"),
        ):
            self.status_input.addItem(label, value)
        self.notes_input = QTextEdit()
        self.notes_input.setFixedHeight(74)
        form.addRow("Status", self.status_input)
        form.addRow("Pokémon", self.species_input)
        form.addRow("Nickname", self.nickname_input)
        form.addRow("Level", self.level_input)
        form.addRow("Notes", self.notes_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class LevelCapsDialog(QDialog):
    def __init__(self, caps: tuple[LevelCap, ...], overrides: dict[str, int], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Level Caps")
        self._cap_rows: list[tuple[LevelCap, QCheckBox, QSpinBox]] = []
        layout = QVBoxLayout(self)
        _heading(layout, "Edit level caps", "Enable Custom to override a fight's cap for this run. Clear Custom to restore its profile value.")
        self.table = QTableWidget(len(caps), 3)
        self.table.setHorizontalHeaderLabels(("Fight", "Category", "Cap"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 130)
        for row, cap in enumerate(caps):
            self.table.setItem(row, 0, QTableWidgetItem(cap.name))
            self.table.setItem(row, 1, QTableWidgetItem(cap.category))
            custom = QCheckBox("Custom")
            spin = QSpinBox()
            spin.setRange(1, 100)
            spin.setValue(overrides.get(cap.cap_id, cap.level_cap))
            custom.setChecked(cap.cap_id in overrides)
            spin.setEnabled(custom.isChecked())
            custom.toggled.connect(spin.setEnabled)
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(4, 1, 4, 1)
            cell_layout.addWidget(spin)
            cell_layout.addWidget(custom)
            self.table.setCellWidget(row, 2, cell)
            self._cap_rows.append((cap, custom, spin))
        self.table.resizeRowsToContents()
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(520, min(680, 150 + len(caps) * 31))

    def overrides(self) -> dict[str, int | None]:
        return {
            cap.cap_id: spin.value() if custom.isChecked() else None
            for cap, custom, spin in self._cap_rows
        }


class FightDetailsDialog(QDialog):
    """Edit one run's cap and free-form note without changing profile definitions."""

    def __init__(self, fight: FightDisplayData, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Fight details: {fight.name}")
        form = QFormLayout(self)
        _heading(form, fight.name, f"{fight.category} · Fight {fight.order} / {fight.total_fights}")
        form.addRow("Default cap", QLabel(f"Lv. {fight.default_level_cap}"))
        self.custom_input = QCheckBox("Use custom cap")
        self.custom_input.setChecked(fight.overridden)
        self.cap_input = QSpinBox()
        self.cap_input.setRange(1, 100)
        self.cap_input.setValue(fight.effective_level_cap)
        self.cap_input.setEnabled(fight.overridden)
        self.custom_input.toggled.connect(self.cap_input.setEnabled)
        form.addRow(self.custom_input)
        form.addRow("Effective cap", self.cap_input)
        self.notes_input = QTextEdit()
        self.notes_input.setPlainText(fight.notes)
        self.notes_input.setPlaceholderText("Optional notes for this fight")
        self.notes_input.setMinimumHeight(100)
        form.addRow("Notes", self.notes_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(430, 340)

    def values(self) -> tuple[int | None, str]:
        return (self.cap_input.value() if self.custom_input.isChecked() else None,
                self.notes_input.toPlainText())


class DeathDialog(QDialog):
    def __init__(self, locations: tuple[str, ...], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record a Death")
        form = QFormLayout(self)
        _heading(form, "Record a death", "Add a manual record without changing an encounter slot.")
        self.resize(480, 360)
        self.species_input = QLineEdit()
        self.nickname_input = QLineEdit()
        self.level_input = QSpinBox()
        self.level_input.setRange(0, 100)
        self.level_input.setSpecialValueText("—")
        self.location_input = QComboBox()
        self.location_input.setEditable(True)
        self.location_input.addItems(locations)
        self.notes_input = QTextEdit()
        self.notes_input.setFixedHeight(70)
        form.addRow("Pokémon", self.species_input)
        form.addRow("Nickname", self.nickname_input)
        form.addRow("Level at death", self.level_input)
        form.addRow("Location / fight", self.location_input)
        form.addRow("Notes", self.notes_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def record(self) -> DeathRecord:
        from datetime import UTC, datetime
        from uuid import uuid4

        return DeathRecord(
            death_id=uuid4().hex,
            species=self.species_input.text().strip(),
            nickname=self.nickname_input.text().strip(),
            level_at_death=self.level_input.value() or None,
            location_or_fight=self.location_input.currentText().strip(),
            timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
            notes=self.notes_input.toPlainText().strip(),
        )
