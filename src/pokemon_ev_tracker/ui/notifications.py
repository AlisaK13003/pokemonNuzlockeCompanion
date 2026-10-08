"""Companion notification cards shared by real events and Settings previews."""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from pokemon_ev_tracker.ui.icons import sparkles_icon
from pokemon_ev_tracker.ui.inspection_views import label
from pokemon_ev_tracker.ui.party_selector import PokemonSprite
from pokemon_ev_tracker.ui.sprite_loader import get_static_sprite
from pokemon_ev_tracker.ui.training_panels import line_icon


@dataclass(frozen=True)
class NoticeAction:
    text: str
    callback: Callable[[], object]
    primary: bool = False


@dataclass(frozen=True)
class Notice:
    key: str
    kind: str
    kicker: str
    title: str
    detail: str
    actions: tuple[NoticeAction, ...] = ()
    species_id: int | None = None
    shiny: bool = False
    timeout_ms: int = 0


COLORS = {"connection": "#57e5a2", "encounter": "#8fb8ff", "shiny": "#ffcb69",
          "friendship": "#57e5a2", "faint": "#ff718b", "wipe": "#ff718b"}


class NotificationCard(QFrame):
    def __init__(self, notice, dismiss, parent):
        super().__init__(parent)
        self.notice = notice
        self.dismiss = dismiss
        self.setObjectName("companionToast")
        accent = COLORS[notice.kind]
        from pokemon_ev_tracker.ui.theme import readable_stylesheet
        self.setStyleSheet(readable_stylesheet(f'''
            QFrame#companionToast {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #18222f,stop:1 #0e141d); border: 1px solid #354359;
                border-left: 3px solid {accent}; border-radius: 0; }}
            QLabel[uiRole="noticeKicker"] {{ color: #7d8da6; font-family: "DM Mono";
                font-size: 9px; letter-spacing: .6px; }}
            QLabel[uiRole="noticeTitle"] {{ color: #e9eef7; font-family: "Pixelify Sans";
                font-size: 20px; font-weight: 600; }}
            QLabel[uiRole="noticeDetail"] {{ color: #718095; font-family: "Manrope"; font-size: 10px; }}
            QLabel[uiRole="noticeTime"] {{ color: #536074; font-family: "DM Mono"; font-size: 7px; }}
            QFrame#noticeIcon {{ background: #1b2636; border: 1px solid #34445d; border-radius: 0; }}
            QFrame#noticeRule {{ background: #2b3443; border: none; }}
            QPushButton#noticeClose {{ background: transparent; border: none; padding: 0; }}
            QPushButton[noticeAction="true"] {{ background: #182232; border: 1px solid #34455e;
                color: #99acc8; font-family: "Pixelify Sans"; font-size: 11px;
                font-weight: 600; border-radius: 0; padding: 0 10px; }}
            QPushButton[noticeAction="true"][primary="true"] {{ color: {accent};
                border-color: {accent}; background: #202736; }}
            QPushButton[noticeAction="true"]:hover {{ background: #26344a; color: #e9eef7; }}
        '''))
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setColor(QColor(0, 0, 0, 130))
        shadow.setOffset(6, 6)
        shadow.setBlurRadius(0)
        self.setGraphicsEffect(shadow)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 26, 16, 16)
        root.setSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(16)
        icon_frame = QFrame()
        icon_frame.setObjectName("noticeIcon")
        icon_frame.setFixedSize(44, 44)
        icon_layout = QVBoxLayout(icon_frame)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        kind = {"connection": "health", "friendship": "heart", "encounter": "person"}.get(notice.kind, "shield")
        asset = sparkles_icon(20, accent) if notice.kind == "shiny" else line_icon(kind, accent, 20)
        icon.setPixmap(asset.pixmap(QSize(20, 20)))
        icon_layout.addWidget(icon)
        row.addWidget(icon_frame)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        self.kicker = label(notice.kicker, "noticeKicker")
        self.title = label(notice.title, "noticeTitle")
        self.detail = label(notice.detail, "noticeDetail")
        for widget in (self.kicker, self.title, self.detail):
            widget.setTextFormat(Qt.TextFormat.PlainText)
            copy.addWidget(widget)
        row.addLayout(copy, 1)
        if notice.species_id is not None:
            if notice.shiny:
                sprite = QLabel()
                sprite.setFixedSize(62, 62)
                sprite.setAlignment(Qt.AlignmentFlag.AlignCenter)
                sprite.setPixmap(get_static_sprite(notice.species_id, 62, shiny=True))
            else:
                sprite = PokemonSprite(62, padding=0, reference_art=True, reference_bounds=(52, 60))
                sprite.set_species(notice.species_id)
            row.addWidget(sprite)
        root.addLayout(row)
        self.buttons = {}
        if notice.actions:
            rule = QFrame()
            rule.setObjectName("noticeRule")
            rule.setFixedHeight(1)
            root.addWidget(rule)
            actions = QHBoxLayout()
            actions.setSpacing(6)
            actions.addStretch(1)
            for action in notice.actions:
                button = QPushButton(action.text)
                button.setFixedHeight(34)
                button.setProperty("noticeAction", True)
                button.setProperty("primary", action.primary)
                button.clicked.connect(lambda checked=False, action=action: self._act(action))
                actions.addWidget(button)
                self.buttons[action.text] = button
            root.addLayout(actions)
        else:
            now = label("NOW", "noticeTime")
            now.setAlignment(Qt.AlignmentFlag.AlignRight)
            root.addWidget(now)
        self.close_button = QPushButton(self)
        self.close_button.setObjectName("noticeClose")
        self.close_button.setIcon(line_icon("close", "#7d8da6", 14))
        self.close_button.setFixedSize(20, 20)
        self.close_button.setAccessibleName("Dismiss notification")
        self.close_button.clicked.connect(dismiss)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(dismiss)
        if notice.timeout_ms:
            self.timer.start(notice.timeout_ms)

    def _act(self, action):
        if action.callback() is not False:
            self.dismiss()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.close_button.move(self.width() - 26, 7)


class NotificationCenter(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.host = parent
        self.card = None
        self.pending = deque()
        self.seen = set()
        self._seen_order = deque()
        parent.installEventFilter(self)

    def show_notice(self, notice):
        if notice.key in self.seen:
            return
        self.seen.add(notice.key)
        if self.card is not None and self.card.notice.key.startswith("preview:") and not notice.key.startswith("preview:"):
            self.card.timer.stop()
            self.card.hide()
            self.card.deleteLater()
            self.card = None
        self._seen_order.append(notice.key)
        if len(self._seen_order) > 1024:
            self.seen.discard(self._seen_order.popleft())
        self.pending.append(notice)
        if self.card is None:
            self._next()

    def preview(self, kind):
        # Preview buttons never invoke a real game/store action.
        self.pending = deque(n for n in self.pending if not n.key.startswith("preview:"))
        if self.card is not None and self.card.notice.key.startswith("preview:"):
            self.card.timer.stop()
            self.card.hide()
            self.card.deleteLater()
            self.card = None
        self.seen.discard(f"preview:{kind}")
        self.show_notice(preview_notice(kind))

    def dismiss(self):
        if self.card is not None:
            self.card.timer.stop()
            self.card.hide()
            self.card.deleteLater()
            self.card = None
        self._next()

    def discard(self, predicate):
        self.pending = deque(notice for notice in self.pending if not predicate(notice))
        if self.card is not None and predicate(self.card.notice):
            self.dismiss()

    def _next(self):
        if not self.pending:
            return
        self.card = NotificationCard(self.pending.popleft(), self.dismiss, self.host)
        self._position()
        self.card.show()
        self.card.raise_()

    def _position(self):
        if self.card is None:
            return
        width = min(560, max(180, self.host.width() - 32))
        self.card.setFixedWidth(width)
        self.card.adjustSize()
        self.card.move(max(8, self.host.width() - width - 16),
                       max(8, self.host.height() - self.card.height() - 16))

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() == QEvent.Type.Resize:
            self._position()
        return False


def preview_notice(kind):
    examples = {
        "connection": ("CONNECTION RESTORED", "BizHawk is live again", "Pokémon Platinum · Party data is updating.", None, ()),
        "encounter": ("NEW POKÉMON · PARTY", "Ponyta · Lv. 19", "Route 209 already has an encounter.", 77, ("Add as Extra", "Replace Existing", "Ignore")),
        "shiny": ("SHINY DETECTED", "Ponyta · Route 209", "Shiny Clause applies. The original encounter is preserved.", 77, ("Add Shiny", "Review", "Ignore")),
        "friendship": ("FRIENDSHIP GOAL", "Mistral reached 220", "The selected friendship target has been reached.", None, ("View Friendship Walk",)),
        "faint": ("POTENTIAL DEATH", "Nova reached 0 HP", "Matched encounter: Twinleaf Town.", 391, ("Mark Dead", "Ignore")),
    }
    kicker, title, detail, species, actions = examples[kind]
    return Notice(f"preview:{kind}", kind, kicker, title, detail,
                  tuple(NoticeAction(text, lambda: None, index == 0) for index, text in enumerate(actions)),
                  species, kind == "shiny", 8000 if kind == "connection" else 0)
