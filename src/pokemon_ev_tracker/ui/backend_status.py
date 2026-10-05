"""Compact live BizHawk status and detailed RAM diagnostic fields."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from pokemon_ev_tracker.ui.theme import refresh_style


class BackendStatusWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.compact_label = QLabel("● DISCONNECTED")
        self.compact_label.setProperty("connectionState", "disconnected")
        layout.addWidget(self.compact_label)
        self.details = {}
        self.details_widget = QWidget()
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        for row, (key, caption, initial) in enumerate(
            (
                ("backend", "Backend", "BizHawk RAM"),
                ("connection", "Connection", "DISCONNECTED"),
                ("domain", "Memory domain", "--"),
                ("last_update", "Last update", "Never"),
                ("frame", "Frame", "--"),
            )
        ):
            value = QLabel(initial)
            if key == "connection":
                value.setProperty("connectionState", "disconnected")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.details[key] = value
            if key in {"domain", "last_update", "frame"}:
                grid.addWidget(QLabel(caption), row, 0)
                grid.addWidget(value, row, 1)
        self.details_widget.setLayout(grid)
        layout.addWidget(self.details_widget)

    def set_connection(self, backend: str, connected: bool, heartbeat, age: float | None) -> None:
        status = "CONNECTED" if connected else "DISCONNECTED"
        state = "connected" if connected else "disconnected"
        self.compact_label.setText("● LIVE" if connected else "● DISCONNECTED")
        self.compact_label.setToolTip(f"{backend} • {status}")
        self.compact_label.setProperty("connectionState", state)
        refresh_style(self.compact_label)
        self.details["backend"].setText(backend)
        self.details["connection"].setText(status)
        self.details["connection"].setProperty("connectionState", state)
        refresh_style(self.details["connection"])
        payload = heartbeat.payload if heartbeat is not None else {}
        self.details["domain"].setText(str(payload.get("active_domain") or "--"))
        self.details["frame"].setText(str(payload.get("frame", "--")))
        self.details["last_update"].setText(
            f"{age:.1f}s ago via {heartbeat.source}"
            if heartbeat is not None and age is not None
            else "Waiting for heartbeat"
            if connected
            else "Never"
        )
