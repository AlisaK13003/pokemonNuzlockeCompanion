"""Native presentation helpers for Training's monitoring and walking cards."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.ui.party_selector import PokemonSprite


def caption(value, role="trainingMicro"):
    label = QLabel(value)
    label.setProperty("uiRole", role)
    return label


def line_icon(kind, color="#8999b4", size=14):
    canvas = QPixmap(size * 2, size * 2)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 7, size / 7)
    painter.setPen(QPen(QColor(color), 0.8, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if kind == "live":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(5, 5, 4, 4)
    elif kind == "heart":
        path = QPainterPath()
        path.moveTo(7, 12)
        path.cubicTo(3, 8, 0, 6, 2, 3)
        path.cubicTo(4, 1, 6, 2, 7, 4)
        path.cubicTo(9, 1, 12, 1, 13, 4)
        path.cubicTo(14, 7, 10, 10, 7, 12)
        painter.drawPath(path)
    elif kind in {"horizontal", "vertical"}:
        if kind == "vertical":
            painter.translate(14, 0)
            painter.rotate(90)
        painter.drawLine(1, 7, 13, 7)
        for x, direction in ((1, 1), (13, -1)):
            painter.drawLine(x, 7, x + 3 * direction, 4)
            painter.drawLine(x, 7, x + 3 * direction, 10)
    elif kind == "person":
        painter.drawEllipse(5, 1, 4, 4)
        path = QPainterPath()
        path.moveTo(2, 13)
        path.cubicTo(2, 5, 12, 5, 12, 13)
        painter.drawPath(path)
    elif kind == "walk":
        painter.drawEllipse(6, 1, 3, 3)
        painter.drawLine(7, 5, 6, 9)
        painter.drawLine(6, 6, 3, 8)
        painter.drawLine(7, 5, 10, 7)
        painter.drawLine(6, 9, 3, 13)
        painter.drawLine(6, 9, 10, 13)
    elif kind == "close":
        painter.drawLine(3, 3, 11, 11)
        painter.drawLine(3, 11, 11, 3)
    elif kind == "check":
        painter.drawLine(2, 7, 5, 10)
        painter.drawLine(5, 10, 12, 3)
    elif kind in {"health", "pulse"}:
        if kind == "health":
            painter.drawEllipse(1, 1, 12, 12)
        path = QPainterPath()
        path.moveTo(3, 7)
        for x, y in ((5, 7), (6, 4), (8, 10), (9, 6), (11, 6)):
            path.lineTo(x, y)
        painter.drawPath(path)
    else:
        path = QPainterPath()
        path.moveTo(7, 1)
        for x, y in ((12, 3), (11, 9), (7, 13), (3, 9), (2, 3)):
            path.lineTo(x, y)
        path.closeSubpath()
        painter.drawPath(path)
    painter.end()
    canvas.setDevicePixelRatio(2)
    return QIcon(canvas)


class ScanlineFrame(QFrame):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor(181, 198, 224, 7))
        for y in range(2, self.height(), 6):
            painter.drawLine(1, y, self.width() - 2, y)


class TrackedSelector(QComboBox):
    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = option.currentText.split(" · ", 1)[0]
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


class WalkETA(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        value = "Ready" if self.text() == "Start walking to calibrate ETA" else self.text().removesuffix(" remaining")
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)


class GoalChoice(QPushButton):
    def paintEvent(self, event):
        if "\n" not in self.text():
            super().paintEvent(event)
            return
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        font = self.font()
        painter.setFont(font)
        painter.setPen(self.palette().buttonText().color())
        value, hint = self.text().split("\n", 1)
        painter.drawText(self.rect().adjusted(0, 2, 0, -10), Qt.AlignmentFlag.AlignCenter, value)
        font.setPixelSize(9)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, 17, 0, -2), Qt.AlignmentFlag.AlignCenter, hint)


def install_friendship(panel):
    root = QVBoxLayout(panel)
    root.setContentsMargins(28, 20, 28, 22)
    root.setSpacing(0)
    panel.heading_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
    title = QVBoxLayout()
    title.setSpacing(7)
    title.addWidget(caption("FRIENDSHIP WALK", "focusKicker"))
    title.addWidget(caption("Automated movement with wall detection", "focusDescription"))
    panel.heading_layout.addLayout(title, 1)
    panel.status = caption("Idle", "walkStatus")
    panel.status.setProperty("walkState", "idle")
    panel.status.setWordWrap(True)
    status_group = QWidget()
    status_row = QHBoxLayout(status_group)
    status_row.setContentsMargins(0, 0, 0, 0)
    status_row.setSpacing(6)
    panel.status_indicator = QLabel()
    panel.status_indicator.setFixedSize(6, 6)
    panel.status_indicator.setStyleSheet("background: #57e5a2; border: none;")
    glow = QGraphicsDropShadowEffect(panel.status_indicator)
    glow.setColor(QColor("#57e5a2"))
    glow.setBlurRadius(8)
    glow.setOffset(0, 0)
    panel.status_indicator.setGraphicsEffect(glow)
    panel.status_indicator.hide()
    status_row.addWidget(panel.status_indicator, 0, Qt.AlignmentFlag.AlignVCenter)
    status_row.addWidget(panel.status)
    panel.heading_layout.addWidget(status_group, 0, Qt.AlignmentFlag.AlignTop)
    root.addLayout(panel.heading_layout)
    root.addSpacing(14)
    panel.overview = QFrame()
    panel.overview.setObjectName("friendshipOverview")
    panel.overview.setMinimumHeight(75)
    panel.body = QBoxLayout(QBoxLayout.Direction.LeftToRight, panel.overview)
    panel.body.setContentsMargins(0, 9, 0, 9)
    panel.body.setSpacing(0)
    identity = QWidget()
    identity_row = QHBoxLayout(identity)
    identity_row.setContentsMargins(10, 0, 10, 0)
    identity_row.setSpacing(7)
    panel.sprite = PokemonSprite(49, padding=0, reference_art=True, reference_bounds=(37, 49))
    panel.sprite.setFixedSize(45, 49)
    identity_row.addWidget(panel.sprite)
    copy = QVBoxLayout()
    copy.setSpacing(0)
    copy.addWidget(caption("TRACKING"))
    panel.selector = TrackedSelector()
    panel.selector.setProperty("uiRole", "walkPokemonSelect")
    panel.selector.setMinimumWidth(0)
    panel.selector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    panel.selector.setFixedHeight(24)
    panel.selector.currentIndexChanged.connect(panel._selection_changed)
    copy.addWidget(panel.selector)
    panel.species = caption("—", "walkSpecies")
    copy.addWidget(panel.species)
    identity_row.addLayout(copy, 1)
    panel.body.addWidget(identity, 40)
    score = QFrame()
    score.setObjectName("friendshipScore")
    score.setFixedHeight(43)
    score_row = QHBoxLayout(score)
    score_row.setContentsMargins(10, 0, 10, 0)
    score_row.setSpacing(3)
    heart = QLabel()
    heart.setPixmap(line_icon("heart", "#c98bc8", 18).pixmap(QSize(18, 18)))
    score_row.addWidget(heart)
    panel.current_value = caption("—", "walkValue")
    panel.target_value = caption("/ 160", "walkTarget")
    score_row.addWidget(panel.current_value)
    score_row.addWidget(panel.target_value)
    score_row.addStretch(1)
    panel.body.addWidget(score, 27, Qt.AlignmentFlag.AlignVCenter)
    eta = QWidget()
    eta.setFixedHeight(58)
    eta_box = QVBoxLayout(eta)
    eta_box.setContentsMargins(17, 0, 0, 0)
    eta_box.setSpacing(0)
    eta_box.addWidget(caption("ESTIMATED TIME"))
    panel.telemetry = WalkETA("Ready")
    panel.telemetry.setProperty("uiRole", "walkETA")
    panel.telemetry.setFixedHeight(26)
    panel.telemetry.setWordWrap(True)
    eta_box.addWidget(panel.telemetry)
    panel.eta_detail = caption("Starts when walking", "walkSpecies")
    eta_box.addWidget(panel.eta_detail)
    panel.body.addWidget(eta, 33, Qt.AlignmentFlag.AlignVCenter)
    root.addWidget(panel.overview)
    root.addSpacing(10)
    progress = QHBoxLayout()
    progress.setSpacing(10)
    panel.progress = QProgressBar()
    panel.progress.setRange(0, 160)
    panel.progress.setTextVisible(False)
    panel.progress.setProperty("uiRole", "walkProgress")
    panel.progress.setFixedHeight(4)
    progress.addWidget(panel.progress, 1)
    panel.percentage = caption("— to goal", "walkSpecies")
    progress.addWidget(panel.percentage)
    root.addLayout(progress)
    root.addSpacing(16)
    panel.controls_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
    panel.controls_layout.setSpacing(12)
    goal_box = QWidget()
    goal = QVBoxLayout(goal_box)
    goal.setContentsMargins(0, 0, 0, 0)
    goal.setSpacing(5)
    label_row = QHBoxLayout()
    label_row.addWidget(caption("GOAL"), 1)
    custom = QPushButton("Custom")
    custom.setProperty("buttonRole", "trainingText")
    custom.setFixedHeight(18)
    custom.clicked.connect(lambda: panel.goal_picker.setCurrentIndex(3))
    label_row.addWidget(custom)
    goal.addLayout(label_row)
    panel.goal_picker = QComboBox(panel)
    for label, value in (("160", 160), ("220", 220), ("255", 255), ("Custom…", "custom")):
        panel.goal_picker.addItem(label, value)
    panel.goal_picker.currentIndexChanged.connect(panel._goal_selected)
    panel.goal_picker.hide()
    panel.goal_buttons = {}
    choices = QHBoxLayout()
    choices.setSpacing(3)
    for value, label in ((160, "160"), (220, "220\nEVOLVE"), (255, "255")):
        button = GoalChoice(label)
        button.setCheckable(True)
        button.setProperty("buttonRole", "walkSetting")
        button.setFixedHeight(38)
        button.clicked.connect(lambda checked=False, value=value: panel._choose_goal(value))
        choices.addWidget(button, 1)
        panel.goal_buttons[value] = button
    goal.addLayout(choices)
    panel.controls_layout.addWidget(goal_box, 2)
    axis_box = QWidget()
    axis_layout = QVBoxLayout(axis_box)
    axis_layout.setContentsMargins(0, 0, 0, 0)
    axis_layout.setSpacing(5)
    axis_layout.addWidget(caption("WALK AXIS"))
    axis_choices = QHBoxLayout()
    axis_choices.setSpacing(3)
    panel.axis = QComboBox(panel)
    panel.axis.addItem("Horizontal", "horizontal")
    panel.axis.addItem("Vertical", "vertical")
    panel.axis.hide()
    panel.axis_buttons = {}
    for index, (name, kind) in enumerate((("Horizontal", "horizontal"), ("Vertical", "vertical"))):
        button = QPushButton(name)
        button.setIcon(line_icon(kind))
        button.setIconSize(QSize(12, 12))
        button.setProperty("buttonRole", "walkSetting")
        button.setCheckable(True)
        button.setFixedHeight(38)
        button.clicked.connect(lambda checked=False, index=index: panel.axis.setCurrentIndex(index))
        axis_choices.addWidget(button, 1)
        panel.axis_buttons[index] = button
    axis_layout.addLayout(axis_choices)
    panel.axis.currentIndexChanged.connect(panel._axis_changed)
    panel._axis_changed(panel.axis.currentIndex())
    panel.controls_layout.addWidget(axis_box, 3)
    actions = QVBoxLayout()
    actions.setSpacing(3)
    actions.addStretch(1)
    panel.start = QPushButton("Start walk")
    panel.start.setIcon(line_icon("walk", "#57e5a2"))
    panel.start.setProperty("buttonRole", "walkStart")
    panel.start.setMinimumWidth(140)
    panel.start.setFixedHeight(40)
    panel.start.setEnabled(False)
    panel.stop = QPushButton("Stop walk")
    panel.stop.setIcon(line_icon("close", "#ff718b"))
    panel.stop.setProperty("buttonRole", "walkStop")
    panel.stop.setMinimumWidth(140)
    panel.stop.setFixedHeight(40)
    panel.stop.setToolTip("Release emulator input immediately · Ctrl+Shift+W")
    from PySide6.QtWidgets import QStackedWidget
    panel.action_stack = QStackedWidget()
    panel.action_stack.setFixedSize(140, 40)
    panel.action_stack.addWidget(panel.start)
    panel.action_stack.addWidget(panel.stop)
    actions.addWidget(panel.action_stack)
    panel.controls_layout.addLayout(actions)
    root.addLayout(panel.controls_layout)
    panel.session_gain = caption("", "walkSpecies")
    panel.moves = caption("", "walkSpecies")
    root.addWidget(panel.session_gain)
    root.addWidget(panel.moves)
    root.addSpacing(13)
    safety = QHBoxLayout()
    safety.setSpacing(6)
    shield = QLabel()
    shield.setPixmap(line_icon("shield", "#596578", 11).pixmap(QSize(11, 11)))
    safety.addWidget(shield)
    panel.safety = caption("Automatically pauses for battles, stale RAM, or manual input.", "walkSafety")
    panel.safety.setWordWrap(True)
    panel.safety.setToolTip("Auto wall reversal; also pauses for connection or command-channel loss.")
    safety.addWidget(panel.safety, 1)
    root.addLayout(safety)
    # Existing bindings and tests retain factual adapters.
    panel.friendship = QLabel("Friendship — / 255", panel)
    panel.progress_text = QLabel("— → 160", panel)
    panel.friendship.hide()
    panel.progress_text.hide()
