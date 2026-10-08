"""Small native line icons for companion chrome."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def settings_icon():
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#9ca7b9"), 1.5))
    painter.translate(12, 12)
    painter.drawEllipse(-7, -7, 14, 14)
    painter.drawEllipse(-2, -2, 4, 4)
    for _ in range(8):
        painter.drawLine(0, -7, 0, -10)
        painter.rotate(45)
    painter.end()
    return QIcon(pixmap)


def chevron_icon(expanded=False):
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor("#67758c"), 1.2, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    points = ((6, 8, 10, 12), (10, 12, 14, 8)) if expanded else ((8, 6, 12, 10), (12, 10, 8, 14))
    for segment in points:
        painter.drawLine(*segment)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def tracker_icon(route):
    icon = QIcon()
    for state, color in ((QIcon.State.Off, "#6f7a8d"), (QIcon.State.On, "#f0f4ff")):
        pixmap = QPixmap(28, 28)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(2, 2)
        painter.setPen(QPen(QColor(color), 1.1, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if route == "training":
            bolt = QPainterPath()
            bolt.moveTo(8, 0.5)
            for x, y in ((2.5, 8), (6.5, 8), (5.5, 13.5), (11.5, 5.5), (7.5, 5.5)):
                bolt.lineTo(x, y)
            bolt.closeSubpath()
            painter.drawPath(bolt)
        else:
            painter.drawRoundedRect(1.5, 2.5, 11, 9, 1, 1)
            painter.drawLine(4, 5, 10, 5)
            painter.drawLine(4, 8, 7 if route == "box" else 10, 8)
        painter.end()
        pixmap.setDevicePixelRatio(2)
        icon.addPixmap(pixmap, QIcon.Mode.Normal, state)
    return icon


def reset_icon():
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor("#798598"), 1, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawArc(2, 2, 8, 8, 35 * 16, 290 * 16)
    painter.drawLine(10, 1, 10, 5)
    painter.drawLine(10, 5, 6, 5)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def sort_order_icon():
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor("#7f93b5"), 1))
    for segment in ((5, 3, 5, 13), (2, 6, 5, 3), (5, 3, 8, 6),
                    (11, 3, 11, 13), (8, 10, 11, 13), (11, 13, 14, 10)):
        painter.drawLine(*segment)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def sparkles_icon(size=18, color="#efbd55"):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 8, size / 8)
    painter.setPen(QPen(QColor(color), 1, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    for x, y, radius in ((6, 6, 4), (12, 11, 2)):
        path = QPainterPath()
        path.moveTo(x, y - radius)
        for px, py in ((x + radius * .3, y - radius * .3), (x + radius, y),
                       (x + radius * .3, y + radius * .3), (x, y + radius),
                       (x - radius * .3, y + radius * .3), (x - radius, y),
                       (x - radius * .3, y - radius * .3)):
            path.lineTo(px, py)
        path.closeSubpath()
        painter.drawPath(path)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)
