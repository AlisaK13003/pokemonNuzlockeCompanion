"""Figma Run Library presentation backed by the existing run actions."""


import hashlib

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pokemon_ev_tracker.core.nuzlocke.models import resolved_level_caps, summarize_run
from pokemon_ev_tracker.ui.inspection_views import label
from pokemon_ev_tracker.ui.party_selector import PokemonSprite


def library_label(text="", role="libraryDetail"):
    widget = label(text, role)
    widget.setWordWrap(False)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    return widget

LEGENDARIES = {
    1: (144, 145, 146, 150, 151),
    2: (243, 244, 245, 249, 250, 251),
    3: (377, 378, 379, 380, 381, 382, 383, 384, 385, 386),
    4: (480, 481, 482, 483, 484, 485, 486, 487, 488, 489, 490, 491, 492, 493),
}


def legendary_for_run(run):
    """Choose a stable, varied mascot from the game's generation."""
    game = run.game.casefold().replace('_', '').replace('-', '').replace(' ', '')
    generation = (4 if any(x in game for x in ('platinum', 'diamond', 'pearl', 'heartgold', 'soulsilver'))
                  else 3 if any(x in game for x in ('emerald', 'ruby', 'sapphire', 'firered', 'leafgreen'))
                  else 2 if any(x in game for x in ('gold', 'silver', 'crystal')) else 1)
    pool = LEGENDARIES[generation]
    return pool[int.from_bytes(hashlib.sha256(run.run_id.encode()).digest()[:8], 'big') % len(pool)]


class RunLibraryCards(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self._signature = None
        self.setObjectName('runLibraryPage')
        self.setStyleSheet('''
            QWidget#runLibraryPage { background: transparent; }
            QLabel { background: transparent; }
            QLabel[uiRole="libraryTitle"] { font-family: "Pixelify Sans"; font-size: 40px; font-weight: 600; color: #e9eef7; }
            QLabel[uiRole="libraryHeroTitle"] { font-family: "Pixelify Sans"; font-size: 44px; font-weight: 600; color: #e9eef7; }
            QLabel[uiRole="libraryName"] { font-family: "Pixelify Sans"; font-size: 18px; font-weight: 600; color: #e9eef7; }
            QLabel[uiRole="libraryMicro"] { font-family: "DM Mono"; font-size: 10px; color: #788ba5; letter-spacing: 1px; }
            QLabel[uiRole="libraryDetail"] { font-family: "Manrope"; font-size: 12px; color: #788ba5; }
            QLabel[uiRole="libraryCaption"] { font-family: "Manrope"; font-size: 14px; color: #8193ae; }
            QLabel[uiRole="libraryProgress"] { font-family: "Pixelify Sans"; font-size: 34px; font-weight: 600; color: #e9eef7; }
            QLabel[uiRole="libraryCount"] { font-family: "Pixelify Sans"; font-size: 23px; font-weight: 600; color: #e9eef7; }
            QFrame#libraryHero { border: 1px solid #343f50; border-left: 4px solid #a68be5; background: qradialgradient(cx:0.8,cy:0.2,radius:0.8,fx:0.8,fy:0.2,stop:0 #263246,stop:1 #10151e); }
            QFrame#libraryArchive { border: 1px solid #2b3543; background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #171d28,stop:1 #0e131b); }
            QFrame#libraryRow { border: none; border-bottom: 1px solid #222b37; background: transparent; }
            QFrame#libraryProgress { border: none; border-left: 1px solid #303b4c; background: transparent; }
            QFrame#libraryCount { border: none; border-left: 1px solid #303b4c; background: transparent; }
            QPushButton { font-family: "Pixelify Sans"; font-size: 13px; color: #aebdd5; background: #131b27; border: 1px solid #2d384a; border-radius: 0; padding: 0 12px; min-height: 32px; }
            QPushButton:hover { background: #222e42; color: #e9eef7; }
            QPushButton[primary="true"] { background: #222d40; border-color: #4b6085; color: #d3def3; min-height: 40px; }
            QPushButton[danger="true"] { color: #d48190; }
            QPushButton#libraryBack { background: transparent; border: none; padding: 0; color: #8b9ebd; text-align: left; }
            QProgressBar { background: #29313e; border: none; height: 4px; min-height: 4px; max-height: 4px; }
            QProgressBar::chunk { background: #8fb8ff; border-radius: 0; }
        ''')
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.back = QPushButton('‹  Active run')
        self.back.setObjectName('libraryBack')
        self.back.clicked.connect(lambda: view.show_section('Dashboard'))
        root.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        root.addSpacing(18)
        heading = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.heading = heading
        titles = QVBoxLayout()
        titles.setSpacing(4)
        eyebrow = QHBoxLayout()
        eyebrow_label = library_label('NUZLOCKE ARCHIVE', 'libraryMicro')
        eyebrow_label.setFixedWidth(170)
        eyebrow.addWidget(eyebrow_label)
        rule = QFrame()
        rule.setFixedSize(50, 1)
        rule.setStyleSheet('background: #8fb8ff;')
        eyebrow.addWidget(rule)
        eyebrow.addStretch()
        titles.addLayout(eyebrow)
        titles.addWidget(library_label('Run Library', 'libraryTitle'))
        titles.addWidget(library_label('Review, resume, and manage every recorded run.', 'libraryCaption'))
        heading.addLayout(titles, 1)
        view.create_button.setText('+  Create new run')
        view.create_button.setProperty('primary', True)
        heading.addWidget(view.create_button, 0, Qt.AlignmentFlag.AlignTop)
        view.create_button.show()
        root.addLayout(heading)
        root.addSpacing(32)
        self.hero = QFrame()
        self.hero.setObjectName('libraryHero')
        hero_root = QVBoxLayout(self.hero)
        hero_root.setContentsMargins(32, 28, 32, 24)
        self.hero_columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        left = QVBoxLayout()
        left.setSpacing(4)
        active = library_label('CURRENT ACTIVE RUN', 'libraryMicro')
        active.setFixedWidth(190)
        active.setStyleSheet('color: #57e5a2;')
        active_row = QHBoxLayout()
        dot = QFrame()
        dot.setFixedSize(6, 6)
        dot.setStyleSheet('background: #57e5a2;')
        active_row.addWidget(dot)
        active_row.addWidget(active)
        active_row.addStretch()
        left.addLayout(active_row)
        left.addSpacing(18)
        self.name = library_label('', 'libraryHeroTitle')
        self.name.setWordWrap(True)
        left.addWidget(self.name)
        self.detail = library_label('', 'libraryCaption')
        left.addWidget(self.detail)
        left.addSpacing(24)
        buttons = QHBoxLayout()
        self.open = QPushButton('Open dashboard  ›')
        self.open.setProperty('primary', True)
        self.rename = QPushButton('Rename')
        buttons.addWidget(self.open)
        buttons.addWidget(self.rename)
        buttons.addStretch()
        left.addLayout(buttons)
        left.addStretch()
        self.open.clicked.connect(lambda: self._action(self._active_id, view._open_selected_run))
        self.rename.clicked.connect(lambda: self._action(self._active_id, view._rename_run))
        self.hero_columns.addLayout(left, 6)
        progress = QFrame()
        progress.setObjectName('libraryProgress')
        right = QVBoxLayout(progress)
        right.setContentsMargins(24, 12, 0, 0)
        right.addWidget(library_label('MAJOR FIGHT PROGRESS', 'libraryMicro'))
        self.progress_value = library_label('', 'libraryProgress')
        right.addWidget(self.progress_value)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        right.addWidget(self.progress)
        self.next = library_label('', 'libraryDetail')
        right.addWidget(self.next)
        right.addStretch()
        self.hero_columns.addWidget(progress, 4)
        hero_root.addLayout(self.hero_columns)
        stats = QHBoxLayout()
        stats.addStretch()
        self.counts = {}
        for key, text in (('caught', 'Caught'), ('deaths', 'Deaths'), ('failed', 'Failed')):
            cell = QFrame()
            cell.setObjectName('libraryCount')
            cell.setFixedWidth(92)
            col = QVBoxLayout(cell)
            col.setContentsMargins(14, 0, 14, 0)
            value = library_label('0', 'libraryCount')
            col.addWidget(value)
            col.addWidget(library_label(text, 'libraryDetail'))
            self.counts[key] = value
            stats.addWidget(cell)
        hero_root.addSpacing(22)
        hero_root.addLayout(stats)
        self.hero.setFixedHeight(294)
        root.addWidget(self.hero)
        root.addSpacing(24)
        archive_heading = QHBoxLayout()
        copy = QVBoxLayout()
        copy.addWidget(library_label('PAST RUNS', 'libraryMicro'))
        self.archive_count = library_label('', 'libraryCaption')
        copy.addWidget(self.archive_count)
        archive_heading.addLayout(copy, 1)
        sort_label = library_label('Most recent  ›', 'libraryDetail')
        sort_label.setFixedWidth(100)
        archive_heading.addWidget(sort_label)
        root.addLayout(archive_heading)
        root.addSpacing(16)
        self.archive = QFrame()
        self.archive.setObjectName('libraryArchive')
        self.rows = QVBoxLayout(self.archive)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        root.addWidget(self.archive)
        root.addStretch(1)

    def refresh(self, runs):
        signature = (self.view.store.active_run_id, self.width() < 1100,
                     tuple((r.run_id, r.name, r.status, r.game, r.started_at, r.ended_at,
                            tuple(r.completed_cap_ids), tuple(sorted(r.level_cap_overrides.items())), len(r.deaths),
                            tuple((e.location_id, e.status) for e in r.encounters.values())) for r in runs))
        if signature == self._signature:
            return
        self._signature = signature
        active = self.view.store.active_run
        self._active_id = active.run_id if active else None
        self.hero.setVisible(active is not None)
        self.back.setVisible(active is not None)
        if active:
            summary, caps, game = self._summary(active)
            self.name.setText(active.name)
            self.detail.setText(f'{game}  ·  Started {active.started_at[:10]}')
            self.progress_value.setText(f'{summary.completed_fights} / {summary.total_fights}')
            self.progress.setRange(0, max(1, summary.total_fights))
            self.progress.setValue(summary.completed_fights)
            next_cap = next((cap for cap in caps if cap.cap_id not in active.completed_cap_ids), None)
            self.next.setText(f'Next: {next_cap.name} · Level cap {next_cap.level_cap}' if next_cap else 'All major fights complete')
            for key, value in self.counts.items():
                value.setText(str(getattr(summary, key)))
        archived = sorted((r for r in runs if r.run_id != self._active_id), key=lambda r: r.ended_at or r.started_at, reverse=True)
        self.archive_count.setText(f'{len(archived)} runs in your archive')
        while self.rows.count():
            item = self.rows.takeAt(0)
            item.widget().deleteLater()
        narrow = self.width() < 1100
        if not narrow:
            header = QWidget()
            grid = QGridLayout(header)
            grid.setContentsMargins(24, 12, 24, 12)
            for col, (text, stretch) in enumerate(zip(('RUN', 'OUTCOME', 'SUMMARY', 'PROGRESS', 'ACTIONS'), (28, 13, 20, 12, 24))):
                grid.addWidget(library_label(text, 'libraryMicro'), 0, col)
                grid.setColumnStretch(col, stretch)
            self.rows.addWidget(header)
        for run in archived:
            summary, _, game = self._summary(run)
            card = QFrame()
            card.setObjectName('libraryRow')
            grid = QGridLayout(card)
            grid.setContentsMargins(20, 18, 20, 18)
            identity = QWidget()
            identity_row = QHBoxLayout(identity)
            identity_row.setContentsMargins(0, 0, 0, 0)
            sprite = PokemonSprite(size=60, padding=4)
            sprite.set_species(legendary_for_run(run))
            sprite.setToolTip('Legendary run mascot')
            identity_row.addWidget(sprite)
            names = QVBoxLayout()
            names.setSpacing(3)
            names.addWidget(library_label(run.name, 'libraryName'))
            names.addWidget(library_label(f'{game} · {(run.ended_at or run.started_at)[:10]}', 'libraryDetail'))
            identity_row.addLayout(names, 1)
            outcome = library_label(run.status, 'libraryMicro')
            color = {'WON': '#57e5a2', 'WIPED': '#ff7185', 'ABANDONED': '#ffc865'}.get(run.status, '#8fb8ff')
            outcome.setStyleSheet(f'color: {color}; border: 1px solid {color}; padding: 5px 8px;')
            outcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
            outcome.setFixedWidth(86)
            summary_text = library_label(f'{summary.caught} caught   {summary.deaths} deaths   {summary.failed} failed', 'libraryDetail')
            summary_text.setWordWrap(True)
            fights = QWidget()
            fight_col = QVBoxLayout(fights)
            fight_col.setContentsMargins(0, 0, 0, 0)
            fight_col.addWidget(library_label(f'{summary.completed_fights} / {summary.total_fights}', 'libraryName'))
            fight_col.addWidget(library_label('major fights', 'libraryDetail'))
            actions = QWidget()
            action_row = QHBoxLayout(actions)
            action_row.setContentsMargins(0, 0, 0, 0)
            action_row.setSpacing(5)
            for caption, callback in (('Make active', self.view._set_selected_active), ('Rename', self.view._rename_run), ('Delete', self.view._delete_run)):
                button = QPushButton(caption)
                button.setProperty('danger', caption == 'Delete')
                button.setAccessibleName(f'{caption} {run.name}')
                button.clicked.connect(lambda checked=False, run_id=run.run_id, action=callback: self._action(run_id, action))
                action_row.addWidget(button)
                if caption == 'Make active':
                    button.setEnabled(run.status == 'ACTIVE')
                    button.setToolTip('Open this archived run' if run.status != 'ACTIVE' else 'Track this run')
                    if run.status != 'ACTIVE':
                        button.setText('Open')
                        button.clicked.disconnect()
                        button.clicked.connect(lambda checked=False, run_id=run.run_id: self._action(run_id, self.view._open_selected_run))
            widgets = (identity, outcome, summary_text, fights, actions)
            if narrow:
                for widget, row, col in zip(widgets, (0, 0, 1, 1, 2), (0, 1, 0, 1, 0)):
                    grid.addWidget(widget, row, col, 1, 2 if widget is actions else 1)
                # Completed runs remain reviewable through Open.
                actions.findChildren(QPushButton)[0].setEnabled(True)
            else:
                for col, (widget, stretch) in enumerate(zip(widgets, (28, 13, 20, 12, 24))):
                    grid.addWidget(widget, 0, col)
                    grid.setColumnStretch(col, stretch)
                actions.findChildren(QPushButton)[0].setEnabled(True)
            self.rows.addWidget(card)
        if not archived:
            empty = library_label('No past runs yet.', 'libraryCaption')
            empty.setContentsMargins(24, 24, 24, 24)
            self.rows.addWidget(empty)

    def _summary(self, run):
        profile = self.view.profiles.get(run.game)
        caps = resolved_level_caps(run, profile) if profile else ()
        return summarize_run(run, caps), caps, profile.display_name if profile else run.game

    def _action(self, run_id, callback):
        for row in range(self.view.run_library_table.rowCount()):
            if self.view.run_library_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == run_id:
                self.view.run_library_table.setCurrentCell(row, 0)
                callback()
                return

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.heading.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 650 else QBoxLayout.Direction.LeftToRight)
        self.hero.setFixedHeight(460 if self.width() < 760 else 294)
        self.hero_columns.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 760 else QBoxLayout.Direction.LeftToRight)
        self.refresh(self.view.store.runs)
