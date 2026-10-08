"""Connection waiting screen, with a responsive guide and stable heartbeat animation."""

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.icons import reset_icon, tracker_icon
from pokemon_ev_tracker.ui.inspection_views import label
from pokemon_ev_tracker.ui.training_panels import line_icon


class OfflineEmblem(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(120, 120)
        self.setAccessibleName("No emulator connection")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for inset, color in ((1, "#2c1e28"), (14, "#1e1b26")):
            painter.setPen(QPen(QColor(color), 1))
            painter.drawPolygon(QPolygonF([QPointF(60, inset), QPointF(120-inset, 60),
                                           QPointF(60, 120-inset), QPointF(inset, 60)]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#582934"))
        painter.drawPie(QRectF(37, 37, 46, 46), 0, 180*16)
        painter.setBrush(QColor("#14151b"))
        painter.drawPie(QRectF(37, 37, 46, 46), 180*16, 180*16)
        painter.setPen(QPen(QColor("#70404c"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(37, 37, 46, 46))
        painter.drawLine(QPointF(37, 60), QPointF(83, 60))
        painter.setBrush(QColor("#17171d"))
        painter.drawEllipse(QRectF(55, 55, 10, 10))
        painter.setPen(QPen(QColor("#ff7185"), 2))
        painter.drawLine(QPointF(36, 84), QPointF(84, 36))


class WaitingTitle(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        font = self.font()
        while QFontMetricsF(font).horizontalAdvance(self.text()) > self.width() and font.pixelSize() > 20:
            font.setPixelSize(font.pixelSize()-1)
        painter.setFont(font)
        flags = Qt.AlignmentFlag.AlignCenter
        painter.setPen(QColor("#05070b"))
        painter.drawText(self.contentsRect().translated(3, 3), flags, self.text())
        painter.setPen(QColor("#e9eef7"))
        painter.drawText(self.contentsRect(), flags, self.text())


class HeartbeatPulse(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(26, 14)
        self.phase = 0

    def advance(self):
        self.phase = (self.phase+1) % 3
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        for index in range(3):
            height = (6, 9, 12)[(index+self.phase) % 3]
            painter.fillRect(index*9, (14-height)//2, 4, height,
                             QColor(("#ff7185", "#c75d6e", "#8c4955")[index]))


class DisconnectedView(QFrame):
    retry_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("disconnectedView")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 24, 0, 24)
        outer.setSpacing(0)
        self.content = QWidget()
        self.content.setMaximumWidth(1280)
        self.content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(self.content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.emblem = OfflineEmblem()
        root.addWidget(self.emblem, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(6)
        kicker = label("NO EMULATOR CONNECTION", "offlineKicker")
        kicker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(kicker)
        root.addSpacing(9)
        title = WaitingTitle("Waiting for BizHawk")
        title.setProperty("uiRole", "offlineTitle")
        title.setFixedHeight(58)
        title.setMinimumWidth(0)
        root.addWidget(title)
        root.addSpacing(7)
        detail = label("No live game data is available yet. Connect the companion Lua script through BizHawk to begin reading your party, battles, boxes, and run events.", "offlineDescription")
        detail.setMaximumWidth(760)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = detail
        root.addWidget(detail, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(18)
        listening = QWidget()
        listening_layout = QHBoxLayout(listening)
        listening_layout.setContentsMargins(0, 0, 0, 0)
        listening_layout.setSpacing(5)
        self.pulse = HeartbeatPulse()
        listening_layout.addWidget(self.pulse)
        listening_text = label("Listening for Lua heartbeat", "offlineMicro")
        listening_text.setWordWrap(False)
        listening_layout.addWidget(listening_text)
        root.addWidget(listening, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(34)
        self.guide = QFrame()
        self.guide.setObjectName("offlineGuide")
        shadow = QGraphicsDropShadowEffect(self.guide)
        shadow.setBlurRadius(0)
        shadow.setOffset(4, 4)
        shadow.setColor(QColor("#05070b"))
        self.guide.setGraphicsEffect(shadow)
        guide = QVBoxLayout(self.guide)
        guide.setContentsMargins(0, 0, 0, 0)
        guide.setSpacing(0)
        header = QFrame()
        header.setObjectName("offlineGuideHeader")
        self.header_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, header)
        self.header_layout.setContentsMargins(20, 20, 20, 16)
        copy = QVBoxLayout()
        copy.setSpacing(8)
        copy.addWidget(label("CONNECTION GUIDE", "offlineGuideKicker"))
        copy.addWidget(label("Load the companion script", "offlineGuideTitle"))
        self.header_layout.addLayout(copy, 1)
        path = label("Tools  →  Lua Console  →  Script  →  Open Script", "offlineMenuPath")
        path.setWordWrap(False)
        self.header_layout.addWidget(path)
        guide.addWidget(header)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 20, 20, 20)
        body_layout.setSpacing(18)
        self.steps_layout = QGridLayout()
        self.steps_layout.setSpacing(6)
        self.steps = []
        for number, title, description in (
            ("01", "Open BizHawk", "Launch the emulator and load your supported Pokémon game."),
            ("02", "Open the Lua Console", "From the BizHawk menu, choose Tools → Lua Console."),
            ("03", "Open the companion script", "In the Lua Console, choose Script → Open Script."),
            ("04", "Choose the BizHawk integration file", "Navigate to the companion's BizHawk folder and open ev_tracker.lua."),
        ):
            card = QFrame()
            card.setObjectName("offlineStep")
            card.setMinimumHeight(150)
            shadow = QGraphicsDropShadowEffect(card)
            shadow.setBlurRadius(0)
            shadow.setOffset(2, 2)
            shadow.setColor(QColor("#05070b"))
            card.setGraphicsEffect(shadow)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(13, 18, 13, 16)
            layout.setSpacing(7)
            layout.addWidget(label(number, "offlineStepNumber"))
            layout.addSpacing(10)
            layout.addWidget(label(title, "offlineStepTitle"))
            layout.addWidget(label(description, "offlineStepCopy"))
            layout.addStretch(1)
            self.steps.append(card)
        body_layout.addLayout(self.steps_layout)
        file_hint = QFrame()
        file_hint.setObjectName("offlineFileHint")
        file_layout = QHBoxLayout(file_hint)
        file_layout.setContentsMargins(16, 16, 16, 16)
        icon = QLabel()
        icon.setPixmap(tracker_icon("box").pixmap(16, 16))
        file_layout.addWidget(icon)
        file_copy = QVBoxLayout()
        file_copy.setSpacing(4)
        file_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.file_row = file_row
        file_caption = label("SELECT THE COMPANION LUA FILE", "offlineMicro")
        file_caption.setWordWrap(False)
        file_row.addWidget(file_caption)
        self.script_path = label("… / bizhawk / ev_tracker.lua", "offlineScriptPath")
        project_script = Path(__file__).resolve().parents[3] / "bizhawk" / "ev_tracker.lua"
        self.script_path.setToolTip(str(project_script))
        self.script_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        file_row.addWidget(self.script_path, 1)
        file_copy.addLayout(file_row)
        file_copy.addWidget(label("The exact parent folder depends on where you installed the companion.", "offlineFootnote"))
        file_layout.addLayout(file_copy, 1)
        body_layout.addWidget(file_hint)
        guide.addWidget(body)
        footer = QFrame()
        footer.setObjectName("offlineGuideFooter")
        self.footer_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, footer)
        self.footer_layout.setContentsMargins(20, 16, 20, 16)
        shield = QLabel()
        shield.setPixmap(line_icon("shield").pixmap(14, 14))
        self.footer_layout.addWidget(shield)
        self.footer_layout.addWidget(label("Keep the Lua Console open while playing. The companion reconnects automatically after brief interruptions.", "offlineFootnote"), 1)
        self.retry = QPushButton("I've opened the script — retry")
        self.retry.setProperty("buttonRole", "offlineRetry")
        self.retry.setIcon(reset_icon())
        self.retry.setFixedHeight(40)
        self.retry.clicked.connect(self.retry_requested)
        self.footer_layout.addWidget(self.retry)
        guide.addWidget(footer)
        root.addWidget(self.guide)
        root.addSpacing(14)
        bottom = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.bottom_layout = bottom
        bottom.addWidget(label("Still not connecting? Confirm the script is running and that BizHawk has not paused Lua execution.", "offlineFootnote"), 1)
        bottom.addWidget(label("TRANSPORT · WAITING", "offlineMicro"))
        root.addLayout(bottom)
        centered = QHBoxLayout()
        centered.setContentsMargins(0, 0, 0, 0)
        centered.addStretch(1)
        centered.addWidget(self.content, 100)
        centered.addStretch(1)
        outer.addLayout(centered)
        outer.addStretch(1)
        self._columns = None
        self._reflow()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.pulse.advance)

    def _reflow(self):
        width = min(self.width(), 1280)
        columns = 4 if width >= 1100 else 2 if width >= 620 else 1
        if columns != self._columns:
            for card in self.steps:
                self.steps_layout.removeWidget(card)
            for column in range(4):
                self.steps_layout.setColumnStretch(column, 1 if column < columns else 0)
            for index, card in enumerate(self.steps):
                self.steps_layout.addWidget(card, index//columns, index%columns)
            self._columns = columns
        direction = QBoxLayout.Direction.TopToBottom if width < 700 else QBoxLayout.Direction.LeftToRight
        for layout in (self.header_layout, self.footer_layout, self.bottom_layout, self.file_row):
            layout.setDirection(direction)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.detail.setFixedWidth(min(760, max(180, event.size().width()-20)))
        self.detail.setFixedHeight(self.detail.heightForWidth(self.detail.width()))
        self._reflow()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
