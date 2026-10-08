"""Friendship Walk controls and presentation; training math lives in core."""

from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QInputDialog,
)

from pokemon_ev_tracker.core.friendship_training import (
    ETAStatus,
    FriendshipETA,
    FriendshipWalkSessionStats,
)
from pokemon_ev_tracker.ui.party_selector import pokemon_identity
from pokemon_ev_tracker.ui.theme import refresh_style
from pokemon_ev_tracker.ui.training_panels import install_friendship


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
        install_friendship(self)

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
        for value, button in self.goal_buttons.items():
            button.setChecked((evolution_goal if value == 220 else value) == target)
        self.goal_buttons[220].setText(f"{evolution_goal}\nEVOLVE")

    def _choose_goal(self, value):
        if value == 220:
            value = self.goal_picker.itemData(1)
        self.goal_picker.setCurrentIndex(self.goal_picker.findData(value))

    def _axis_changed(self, index):
        for key, button in self.axis_buttons.items():
            button.setChecked(key == index)

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

    def select_pokemon(self, pokemon) -> None:
        """Follow an explicit party selection using the same identity as the walk target."""
        index = self.selector.findData(pokemon_identity(pokemon))
        if index >= 0:
            self.selector.setCurrentIndex(index)

    def _render_identity(self) -> None:
        p = self.selected_pokemon
        value = getattr(p, "friendship", None) if getattr(p, "checksum_valid", False) else None
        self.friendship.setText(f"Friendship {value if value is not None else '—'} / 255")
        if p is not None:
            self.sprite.set_species(p.species_id)
            self.species.setText(p.species)
        else:
            self.sprite.set_species(None)
            self.species.setText("—")

    def render_training(
        self, *, status: str, current: int | None, target: int,
        eta: FriendshipETA | None, session: FriendshipWalkSessionStats | None,
    ) -> None:
        walking = status.startswith(("Walking", "Moving", "Blocked"))
        self.status.setText(status.upper() if walking else status)
        self.status_indicator.setVisible(walking)
        self.status.setProperty("walkState", "active" if walking else "idle" if status == "Idle" else "paused")
        refresh_style(self.status)
        self.progress_text.setText(f"{current if current is not None else '—'} → {target}")
        self.progress.setRange(0, max(1, target))
        self.progress.setValue(min(target, current or 0))
        self.telemetry.setText(_eta_text(eta))
        self.current_value.setText(str(current) if current is not None else "—")
        self.target_value.setText(f"/ {target}")
        self.percentage.setText(f"{min(100, round((current or 0) * 100 / max(1, target)))}% to goal" if current is not None else "— to goal")
        self.eta_detail.setText("Starts when walking" if eta is None or eta.status is ETAStatus.READY else "Based on this walking session")
        show_stop = self.stop.isEnabled() and status.startswith(("Walking", "Starting", "Moving", "Blocked", "Paused"))
        self.action_stack.setCurrentWidget(self.stop if show_stop else self.start)
        self.session_gain.setText(
            f"+{session.friendship_gained} friendship this session" if session else ""
        )
        self.moves.setText(
            f"{session.movement_units:,} movement · {session.reversals} reversals · "
            f"{_duration(session.active_seconds)} active" if session else
            "0 movement · 0 reversals · 0s active"
        )
        self.moves.setVisible(session is not None)
        self.session_gain.setVisible(session is not None)
        if status in {"Goal reached", "Tracked Pokémon left party"}:
            self.start.setEnabled(False)

    def set_compact(self, compact):
        for widget in (self.sprite, self.moves, self.safety):
            widget.setVisible(not compact)
        self.layout().setContentsMargins(*(8, 6, 8, 6) if compact else (28, 20, 28, 22))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.heading_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if self.width() > 500 else QBoxLayout.Direction.TopToBottom
        )
        self.controls_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if self.width() > 520 else QBoxLayout.Direction.TopToBottom
        )
        self.body.setDirection(
            QBoxLayout.Direction.LeftToRight if self.width() > 500 else QBoxLayout.Direction.TopToBottom
        )

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())
