"""Compact live BizHawk status and detailed RAM diagnostic fields."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from pokemon_ev_tracker.ui.theme import refresh_style


class ConnectionBadge(QLabel):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        connected = self.property("connectionState") == "connected"
        icon_only = bool(self.property("iconOnly"))
        dot_x = self.width() / 2 if icon_only else 12 if connected else 18
        text_x = 26 if connected else 32
        color = QColor("#57dda2" if self.property("connectionState") == "connected" else "#ff718b")
        center = self.height() / 2
        for radius, alpha in ((9, 12), (7, 25), (5, 45)):
            glow = QColor(color)
            glow.setAlpha(alpha)
            painter.setBrush(glow)
            painter.drawEllipse(dot_x-radius, center-radius, radius*2, radius*2)
        painter.setBrush(color)
        painter.drawEllipse(dot_x-4, center-4, 8, 8)
        if icon_only:
            return
        status = "Live" if connected else "Offline"
        status_font = QFont(self.font())
        status_font.setItalic(False)
        status_font.setWeight(QFont.Weight.Bold)
        painter.setFont(status_font)
        painter.setPen(color)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(QRectF(text_x, 0, self.width()-text_x-12, self.height()), flags, status)
        if not connected and not self.property("hideDetail"):
            status_width = QFontMetricsF(status_font, painter.device()).horizontalAdvance(status)
            divider = text_x + status_width + 10
            painter.fillRect(QRectF(divider, center-7, 1, 14), QColor("#3c2b34"))
            detail_font = QFont(status_font)
            detail_font.setWeight(QFont.Weight.Normal)
            painter.setFont(detail_font)
            painter.setPen(QColor("#896371"))
            painter.drawText(QRectF(divider+11, 0, self.width()-divider-23, self.height()),
                             flags, "No heartbeat")

    def set_status(self, connected):
        # Paint both labels and their separator together so rich-text font metrics
        # cannot put the line inside the status label.
        self.setText("")
        self.setAccessibleName("Live" if connected else "Offline · No heartbeat")
        self.update()


class BackendStatusWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.compact_label = ConnectionBadge("Offline  |  No heartbeat")
        self.compact_label.setObjectName("connectionBadge")
        self.compact_label.setContentsMargins(26, 0, 12, 0)
        self.compact_label.setTextFormat(Qt.TextFormat.RichText)
        self.compact_label.set_status(False)
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
        self.compact_label.set_status(connected)
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
