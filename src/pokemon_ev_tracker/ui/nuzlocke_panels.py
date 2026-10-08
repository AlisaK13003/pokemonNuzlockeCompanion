"""Small presentation primitives for the run workspaces."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QBoxLayout, QFrame, QLabel, QVBoxLayout, QWidget


class RunCount(QLabel):
    """Pad the displayed count while retaining its numeric text adapter."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignCenter, self.text().zfill(2))


class RunPanel(QFrame):
    """A technical panel; title() preserves the notification widget's old API."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("uiRole", "panel")
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(12, 10, 12, 10)
        self.content.setSpacing(8)
        self.heading = QLabel(title.upper())
        self.heading.setProperty("uiRole", "sectionHeading")
        self.content.addWidget(self.heading)
        self._title = title

    def title(self) -> str:
        return self._title


class RunColumns(QWidget):
    """Use dashboard columns when there is room, otherwise stack them."""

    def __init__(self, left: QWidget, right: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.columns.setContentsMargins(0, 0, 0, 0)
        self.columns.setSpacing(12)
        self.columns.addWidget(left, 3, Qt.AlignmentFlag.AlignTop)
        self.columns.addWidget(right, 2, Qt.AlignmentFlag.AlignTop)
        self.setMinimumWidth(0)

    def resizeEvent(self, event) -> None:
        direction = (
            QBoxLayout.Direction.TopToBottom
            if event.size().width() < 820
            else QBoxLayout.Direction.LeftToRight
        )
        if self.columns.direction() != direction:
            self.columns.setDirection(direction)
        super().resizeEvent(event)


def run_label(text: str = "", role: str = "muted") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("uiRole", role)
    label.setMinimumWidth(0)
    return label
