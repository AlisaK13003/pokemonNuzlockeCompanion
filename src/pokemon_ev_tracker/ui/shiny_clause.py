"""Shiny-clause bonus catches, separate from the route encounter ledger."""

from datetime import UTC, datetime
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.core.nuzlocke.models import ShinyEncounter
from pokemon_ev_tracker.games.platinum.species import load_gen4_species
from pokemon_ev_tracker.ui.icons import sparkles_icon
from pokemon_ev_tracker.ui.inspection_views import label
from pokemon_ev_tracker.ui.sprite_loader import get_static_sprite
from pokemon_ev_tracker.ui.training_panels import line_icon


class ShinyClausePanel(QFrame):
    add_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("shinyClause")
        from pokemon_ev_tracker.ui.theme import readable_stylesheet
        self.setStyleSheet(readable_stylesheet('''
            QFrame#shinyClause { background: #10151d; border: 1px solid #40382a; }
            QWidget#shinyHeader { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #141a24,stop:1 #1c1c1d); border-bottom: 1px solid #2b3039; }
            QLabel[uiRole="shinyIcon"] { color: #efbd55; background: #201e1c;
                border: 1px solid #5b4930; font-size: 24px; }
            QLabel[uiRole="shinyCount"] { color: #efbd55; font-family: "Pixelify Sans";
                font-size: 30px; font-weight: 600; }
            QLabel[uiRole="shinyMicro"] { color: #897e62; font-family: "DM Mono";
                font-size: 8px; letter-spacing: .5px; }
            QLabel[uiRole="shinyBadge"] { color: #efbd55; border: 1px solid #57452b;
                background: #211e18; font-family: "DM Mono"; font-size: 8px;
                padding: 6px 9px; }
            QLabel[uiRole="shinyPreserved"] { color: #57e5a2; font-size: 9px; }
            QLabel[uiRole="shinyName"] { color: #e9eef7; font-family: "Pixelify Sans";
                font-size: 16px; font-weight: 600; }
            QWidget#shinyRow { border-bottom: 1px solid #232a34; }
            QWidget#shinyFooter { border-top: 1px solid #2b3039; }
            QPushButton#addShiny { color: #c8a45e; border: none; background: transparent;
                font-family: "Pixelify Sans"; font-size: 11px; padding: 6px; }
            QPushButton#addShiny:hover { color: #ffda87; background: #211e18; }
        '''))
        self._signature = None
        self._run = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QWidget()
        header.setObjectName("shinyHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(20, 20, 20, 18)
        row.setSpacing(14)
        icon = label("", "shinyIcon")
        icon.setFixedSize(40, 40)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(sparkles_icon(20).pixmap(QSize(20, 20)))
        row.addWidget(icon)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        copy.addWidget(label("SHINY CLAUSE", "runKicker"))
        copy.addWidget(label("Shiny encounters", "ledgerTitle"))
        copy.addWidget(label("Tracked separately as bonus catches without replacing the location's original encounter.", "runHint"))
        row.addLayout(copy, 1)
        divider = QFrame()
        divider.setFixedSize(1, 44)
        divider.setStyleSheet("background: #353535; border: none;")
        row.addWidget(divider)
        count = QVBoxLayout()
        self.count = label("0", "shinyCount")
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight)
        count.addWidget(self.count)
        count.addWidget(label("Clause catches", "shinyMicro"))
        row.addLayout(count)
        root.addWidget(header)
        self.entries = QVBoxLayout()
        self.entries.setContentsMargins(14, 0, 14, 0)
        self.entries.setSpacing(0)
        root.addLayout(self.entries)
        footer = QWidget()
        footer.setObjectName("shinyFooter")
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(20, 10, 16, 10)
        shield = QLabel()
        shield.setPixmap(line_icon("shield", "#c8a45e", 12).pixmap(QSize(12, 12)))
        foot.addWidget(shield)
        foot.addWidget(label("Shiny catches never consume an unused location or overwrite an existing encounter.", "runHint"), 1)
        self.add_button = QPushButton("Add shiny manually")
        self.add_button.setIcon(sparkles_icon(12, "#c8a45e"))
        self.add_button.setObjectName("addShiny")
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.clicked.connect(self.add_requested)
        foot.addWidget(self.add_button)
        root.addWidget(footer)

    def refresh(self, run):
        self._run = run
        records = tuple(run.shiny_encounters) if run else ()
        originals = tuple((r.location_id, r.nickname, r.species, r.status)
                          for r in run.encounters.values()) if run else ()
        signature = (run.run_id if run else None, self.width() < 700, records, originals)
        if signature == self._signature:
            return
        self._signature = signature
        self.count.setText(str(len(records)))
        self.add_button.setEnabled(run is not None)
        while self.entries.count():
            item = self.entries.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not records:
            empty = label("No shiny catches recorded yet. Shinies detected in your party or PC appear here automatically.", "runHint")
            empty.setContentsMargins(18, 25, 18, 25)
            self.entries.addWidget(empty)
        for record in records:
            entry = QWidget()
            entry.setObjectName("shinyRow")
            row = QBoxLayout(QBoxLayout.Direction.TopToBottom if self.width() < 700
                             else QBoxLayout.Direction.LeftToRight, entry)
            row.setContentsMargins(14, 18, 10, 18)
            row.setSpacing(20)
            identity = QHBoxLayout()
            sprite = QLabel()
            sprite.setFixedSize(56, 56)
            sprite.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sprite.setPixmap(get_static_sprite(record.species_id, 56, shiny=True))
            identity.addWidget(sprite)
            copy = QVBoxLayout()
            copy.setSpacing(3)
            copy.addWidget(label(f"SHINY {record.species_name.upper()}", "shinyMicro"))
            copy.addWidget(label(record.nickname or record.species_name, "shinyName"))
            level = record.level or record.met_level
            copy.addWidget(label(f"{'Lv. ' + str(level) + ' · ' if level else ''}Detected from {record.source_location}", "runHint"))
            identity.addLayout(copy, 1)
            row.addLayout(identity, 3)
            found = QVBoxLayout()
            found.setSpacing(4)
            found.addWidget(label("FOUND AT", "shinyMicro"))
            found.addWidget(label(record.location_name, "shinyName"))
            if record.met_level:
                found.addWidget(label(f"Met at Lv. {record.met_level}", "runHint"))
            row.addLayout(found, 2)
            original = run.encounters.get(record.location_id)
            preserved = QVBoxLayout()
            preserved.setSpacing(4)
            preserved.addWidget(label("ORIGINAL ROUTE ENCOUNTER", "shinyMicro"))
            if original and original.species:
                caption = f"{original.nickname} · {original.species}" if original.nickname else original.species
            elif original:
                caption = original.status.replace("_", " ").title()
            else:
                caption = "Location not in ledger"
            preserved.addWidget(label(caption, "shinyName"))
            preserved.addWidget(label("Preserved", "shinyPreserved"))
            row.addLayout(preserved, 3)
            badge = label("EXTRA ENCOUNTER", "shinyBadge")
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
            entry.setToolTip(record.notes)
            self.entries.addWidget(entry)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh(self._run)


class AddShinyDialog(QDialog):
    def __init__(self, run, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add shiny encounter")
        self.setMinimumWidth(360)
        self.setModal(True)
        root = QVBoxLayout(self)
        root.addWidget(label("Shiny clause · bonus catch", "ledgerTitle"))
        root.addWidget(label("This catch is recorded separately and preserves the route encounter.", "runHint"))
        form = QFormLayout()
        self.species = QComboBox()
        for species in load_gen4_species():
            self.species.addItem(species.name, species.national_dex_number)
        self.nickname = QLineEdit()
        self.level = QSpinBox()
        self.level.setRange(1, 100)
        self.location = QComboBox()
        self.location.addItem("Unknown / other location", None)
        for record in run.encounters.values():
            self.location.addItem(record.location, record.location_id)
        self.notes = QLineEdit()
        for caption, widget in (("Species", self.species), ("Nickname", self.nickname),
                                ("Met level", self.level), ("Location", self.location),
                                ("Notes", self.notes)):
            form.addRow(caption, widget)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def record(self):
        return ShinyEncounter(
            stable_id=f"manual:{uuid4().hex}", species_id=self.species.currentData(),
            species_name=self.species.currentText(), nickname=self.nickname.text().strip(),
            level=self.level.value(), met_level=self.level.value(),
            location_id=self.location.currentData(), location_name=self.location.currentText(),
            source_location="MANUAL", detected_at=datetime.now(UTC).isoformat(timespec="seconds"),
            notes=self.notes.text().strip(),
        )
