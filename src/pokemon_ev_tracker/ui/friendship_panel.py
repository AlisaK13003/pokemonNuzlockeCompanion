"""Friendship Walk controls and presentation; training math lives in core."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.core.friendship_training import (
    ETAStatus,
    FriendshipETA,
    FriendshipWalkSessionStats,
)
from pokemon_ev_tracker.ui.sprite_loader import get_static_sprite


def _duration(seconds: float) -> str:
    minutes, remainder = divmod(int(max(0, seconds)), 60)
    return f"{minutes}m {remainder:02d}s" if minutes else f"{remainder}s"


def _eta_text(eta: FriendshipETA | None) -> str:
    if eta is None or eta.status is ETAStatus.READY:
        return "Start walking to calibrate ETA"
    if eta.status is ETAStatus.CALIBRATING:
        return "Calibrating…"
    if eta.status is ETAStatus.PAUSED:
        return "ETA paused"
    if eta.status is ETAStatus.REACHED:
        return "Goal reached"
    minutes = max(1, round((eta.estimated_seconds or 0) / 60))
    return f"≈ {minutes} min remaining" if minutes < 60 else f"≈ {minutes / 60:.1f} hr remaining"


class FriendshipWalkPanel(QFrame):
    tracked_changed = Signal(object)
    goal_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("friendshipWalk")
        self.setProperty("uiRole", "panel")
        self.setToolTip("Use only in a safe area you have manually confirmed is encounter-free.")
        self._members = {}
        self._selected_identity = None
        self._goal = 160
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        self.heading_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        heading = QLabel("FRIENDSHIP WALK")
        heading.setProperty("uiRole", "sectionHeading")
        self.heading_layout.addWidget(heading, 1)
        self.status = QLabel("Idle")
        self.status.setProperty("walkState", "idle")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.heading_layout.addWidget(self.status, 1)
        root.addLayout(self.heading_layout)

        self.body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.body.setSpacing(12)
        identity = QWidget()
        identity_layout = QHBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        self.sprite = QLabel()
        self.sprite.setFixedSize(48, 48)
        self.sprite.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity_layout.addWidget(self.sprite)
        identity_copy = QVBoxLayout()
        self.selector = QComboBox()
        self.selector.setMinimumWidth(0)
        self.selector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.selector.currentIndexChanged.connect(self._selection_changed)
        identity_copy.addWidget(self.selector)
        self.friendship = QLabel("Friendship — / 255")
        self.friendship.setProperty("uiRole", "muted")
        identity_copy.addWidget(self.friendship)
        identity_layout.addLayout(identity_copy, 1)
        self.body.addWidget(identity, 3)

        goal_box = QWidget()
        goal_layout = QVBoxLayout(goal_box)
        goal_layout.setContentsMargins(0, 0, 0, 0)
        goal_label = QLabel("TARGET")
        goal_label.setProperty("uiRole", "micro")
        goal_layout.addWidget(goal_label)
        self.goal_picker = QComboBox()
        self.goal_picker.addItem("160 · Training goal", 160)
        self.goal_picker.addItem("220 · Friendship evolution", 220)
        self.goal_picker.addItem("255 · Maximum", 255)
        self.goal_picker.addItem("Custom…", "custom")
        self.goal_picker.currentIndexChanged.connect(self._goal_selected)
        goal_layout.addWidget(self.goal_picker)
        self.body.addWidget(goal_box, 2)
        root.addLayout(self.body)

        self.progress_text = QLabel("— → 160")
        self.progress_text.setProperty("uiRole", "pokemonMeta")
        root.addWidget(self.progress_text)
        self.progress = QProgressBar()
        self.progress.setRange(0, 160)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)
        self.telemetry = QLabel("Start walking to calibrate ETA")
        self.telemetry.setProperty("uiRole", "inspectionValue")
        root.addWidget(self.telemetry)
        self.session_gain = QLabel("")
        self.session_gain.setProperty("uiRole", "small")
        root.addWidget(self.session_gain)
        self.moves = QLabel("0 movement · 0 reversals · 0s active")
        self.moves.setProperty("uiRole", "small")
        root.addWidget(self.moves)
        self.controls_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.axis = QComboBox()
        self.axis.addItem("Horizontal", "horizontal")
        self.axis.addItem("Vertical", "vertical")
        self.controls_layout.addWidget(self.axis)
        self.start = QPushButton("START WALK")
        self.start.setProperty("buttonRole", "primary")
        self.start.setEnabled(False)
        self.stop = QPushButton("STOP")
        self.stop.setProperty("buttonRole", "danger")
        self.stop.setToolTip("Release emulator input immediately · Ctrl+Shift+W")
        self.controls_layout.addWidget(self.start)
        self.controls_layout.addWidget(self.stop)
        self.controls_layout.addStretch(1)
        root.addLayout(self.controls_layout)
        self.safety = QLabel("Auto wall reversal · Pauses for battle, stale RAM, link loss, manual input")
        self.safety.setProperty("uiRole", "small")
        self.safety.setWordWrap(True)
        root.addWidget(self.safety)

    @property
    def selected_identity(self):
        return self._selected_identity

    @property
    def selected_pokemon(self):
        return self._members.get(self._selected_identity)

    @property
    def goal(self) -> int:
        return self._goal

    def set_goal(self, target: int, *, evolution_goal: int = 220) -> None:
        self._goal = target
        self.goal_picker.setItemText(1, f"{evolution_goal} · Friendship evolution")
        self.goal_picker.setItemData(1, evolution_goal)
        self.goal_picker.blockSignals(True)
        index = self.goal_picker.findData(target)
        if index < 0:
            self.goal_picker.setItemText(3, f"{target} · Custom…")
            index = 3
        else:
            self.goal_picker.setItemText(3, "Custom…")
        self.goal_picker.setCurrentIndex(index)
        self.goal_picker.blockSignals(False)

    def _goal_selected(self, index: int) -> None:
        value = self.goal_picker.itemData(index)
        if value == "custom":
            value, accepted = QInputDialog.getInt(
                self, "Friendship target", "Target friendship (0–255):", self._goal, 0, 255,
            )
            if not accepted:
                self.set_goal(self._goal)
                return
        if isinstance(value, int):
            self.set_goal(value)
            self.goal_changed.emit(value)

    def refresh_party(self, cards) -> None:
        members = {
            getattr(p, "stable_id", None) or getattr(p.decoded.diagnostics, "pid", slot): p
            for slot, card in cards.items() if (p := card.get("pokemon")) is not None
        }
        selected = self._selected_identity
        self._members = members
        if selected is None and members:
            selected = next(iter(members))
            self._selected_identity = selected
            self.tracked_changed.emit(selected)
        options = [
            (f"{p.nickname or p.species} · {p.species} · Friendship {p.friendship}/255", key)
            for key, p in members.items()
        ]
        if not members:
            options = [("No party Pokémon", None)]
        if selected not in members and selected is not None:
            options.insert(0, ("Tracked Pokémon left party", selected))
        self.selector.blockSignals(True)
        if tuple(self.selector.itemData(i) for i in range(self.selector.count())) != tuple(
            key for _, key in options
        ):
            self.selector.clear()
            for label, key in options:
                self.selector.addItem(label, key)
        else:
            for index, (label, _key) in enumerate(options):
                if self.selector.itemText(index) != label:
                    self.selector.setItemText(index, label)
        self.selector.setCurrentIndex(max(0, self.selector.findData(selected)))
        self.selector.setEnabled(bool(members))
        self.selector.blockSignals(False)
        self._render_identity()

    def _selection_changed(self, *_args) -> None:
        identity = self.selector.currentData()
        if identity == self._selected_identity:
            return
        self._selected_identity = identity
        self._render_identity()
        self.tracked_changed.emit(identity)

    def _render_identity(self) -> None:
        p = self.selected_pokemon
        value = getattr(p, "friendship", None) if getattr(p, "checksum_valid", False) else None
        self.friendship.setText(f"Friendship {value if value is not None else '—'} / 255")
        if p is not None:
            self.sprite.setPixmap(get_static_sprite(p.species_id, 48))
        else:
            self.sprite.clear()

    def render_training(
        self, *, status: str, current: int | None, target: int,
        eta: FriendshipETA | None, session: FriendshipWalkSessionStats | None,
    ) -> None:
        self.status.setText(status)
        self.progress_text.setText(f"{current if current is not None else '—'} → {target}")
        self.progress.setRange(0, max(1, target))
        self.progress.setValue(min(target, current or 0))
        self.telemetry.setText(_eta_text(eta))
        self.session_gain.setText(
            f"+{session.friendship_gained} friendship this session" if session else ""
        )
        self.moves.setText(
            f"{session.movement_units:,} movement · {session.reversals} reversals · "
            f"{_duration(session.active_seconds)} active" if session else
            "0 movement · 0 reversals · 0s active"
        )
        if status in {"Goal reached", "Tracked Pokémon left party"}:
            self.start.setEnabled(False)

    def set_compact(self, compact):
        for widget in (self.sprite, self.moves, self.safety):
            widget.setVisible(not compact)
        self.layout().setContentsMargins(*(8, 6, 8, 6) if compact else (12, 10, 12, 10))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.heading_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if self.width() > 500 else QBoxLayout.Direction.TopToBottom
        )
        self.controls_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if self.width() > 330 else QBoxLayout.Direction.TopToBottom
        )
        self.body.setDirection(
            QBoxLayout.Direction.LeftToRight if self.width() > 720 else QBoxLayout.Direction.TopToBottom
        )

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())
