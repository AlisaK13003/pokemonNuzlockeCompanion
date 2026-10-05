"""Purpose-built run HUD backed by the normal Nuzlocke presentation source."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from pokemon_ev_tracker.core.nuzlocke.acquisition import pending_acquisition_events
from pokemon_ev_tracker.core.nuzlocke.models import (
    resolved_level_caps,
    summarize_run,
)
from pokemon_ev_tracker.ui.nuzlocke_panels import RunPanel, run_label


class CompactNuzlockeView(QWidget):
    """Compact layout only; navigation and always-on-top live in the shell."""

    review_requested = Signal(str)
    rediscover_pc_requested = Signal()

    def __init__(self, source, parent=None) -> None:
        super().__init__(parent)
        self.source = source
        self.setObjectName("compactNuzlockeView")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll_area)
        body = QWidget()
        self.scroll_area.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        identity = RunPanel("Run HUD")
        self.run_name_label = run_label("No active Nuzlocke run", "pokemonName")
        self.run_state_label = run_label()
        self.counts_label = run_label()
        identity.content.addWidget(self.run_name_label)
        identity.content.addWidget(self.run_state_label)
        identity.content.addWidget(self.counts_label)
        layout.addWidget(identity)

        next_fight = RunPanel("Next fight")
        row = QHBoxLayout()
        copy = QVBoxLayout()
        self.next_fight_label = run_label("No active run", "sectionHeading")
        self.fight_detail_label = run_label()
        copy.addWidget(self.next_fight_label)
        copy.addWidget(self.fight_detail_label)
        row.addLayout(copy, 1)
        cap = RunPanel("Cap")
        cap.setProperty("uiRole", "raisedPanel")
        self.cap_label = run_label("—", "metric")
        cap.content.addWidget(self.cap_label)
        row.addWidget(cap)
        next_fight.content.addLayout(row)
        self.party_warning_label = run_label()
        self.party_warning_label.setProperty("nuzlockeRole", "warning")
        next_fight.content.addWidget(self.party_warning_label)
        layout.addWidget(next_fight)

        encounter = RunPanel("Latest encounter")
        self.latest_encounter_label = run_label("No acquisitions recorded.")
        self.pending_label = run_label()
        encounter.content.addWidget(self.latest_encounter_label)
        actions = QHBoxLayout()
        actions.addWidget(self.pending_label, 1)
        self.review_button = QPushButton("Review")
        self.review_button.clicked.connect(self._review)
        actions.addWidget(self.review_button)
        encounter.content.addLayout(actions)
        layout.addWidget(encounter)

        pc = RunPanel("PC box monitor")
        self.pc_panel = pc
        self.pc_status_label = run_label()
        pc.content.addWidget(self.pc_status_label)
        self.rediscover_button = QPushButton("Rediscover PC Layout")
        self.rediscover_button.clicked.connect(self.rediscover_pc_requested.emit)
        pc.content.addWidget(self.rediscover_button)
        layout.addWidget(pc)
        layout.addStretch(1)
        source.presentation_changed.connect(self.refresh)
        self.refresh()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def _review(self) -> None:
        self.review_requested.emit("Encounters" if self.source.store.active_run else "Run Library")

    def refresh(self) -> None:
        run = self.source.store.active_run
        self.pc_status_label.setText(self.source.pc_monitor_label.text())
        if run is None:
            self.run_name_label.setText("No active Nuzlocke run")
            self.run_state_label.setText("Create or select a run in the Run Library.")
            self.counts_label.clear()
            self.next_fight_label.setText("No active run")
            self.fight_detail_label.clear()
            self.cap_label.setText("—")
            self.party_warning_label.setText("No run is being tracked.")
            self.latest_encounter_label.setText("No acquisitions recorded.")
            self.pending_label.clear()
            self.review_button.setText("Run Library")
            return
        profile = self.source.profiles.get(run.game)
        caps = resolved_level_caps(run, profile) if profile else ()
        summary = summarize_run(run, caps)
        self.run_name_label.setText(run.name)
        game = profile.display_name if profile else run.game
        state = "Completed" if run.completed else "Active"
        self.run_state_label.setText(
            f"{state.upper()} · {game} · {summary.completed_fights}/{summary.total_fights} fights"
        )
        self.counts_label.setText(
            f"CAUGHT {summary.caught}    DEATHS {summary.deaths}    "
            f"FAILED {summary.failed}    SKIPPED {summary.skipped}"
        )
        timeline = self.source.fight_timeline()
        cap = timeline.next_fight if timeline else None
        if cap:
            self.next_fight_label.setText(cap.name)
            self.fight_detail_label.setText(
                f"{cap.category} · Fight {cap.order}/{cap.total_fights} · "
                f"Opponent healing: {cap.healing_text}")
            self.cap_label.setText(str(cap.effective_level_cap))
            if cap.party:
                warning = f"{cap.ready_count} ready · {cap.over_count} over cap"
                over = [member for member in cap.party if member.over_by]
                if over:
                    warning += " · Above cap: " + ", ".join(
                        f"{member.name} Lv. {member.level}" for member in over)
                self.party_warning_label.setText(warning)
            else:
                self.party_warning_label.setText("Party levels unavailable.")
        else:
            self.next_fight_label.setText(
                "All major fights complete" if profile else "Game profile unavailable"
            )
            self.fight_detail_label.setText(
                f"{timeline.completed_count}/{timeline.total_fights} fights" if timeline else "")
            self.cap_label.setText("—")
            self.party_warning_label.setText("No current level cap.")
        pending = pending_acquisition_events(run)
        self.review_button.setText("Review" if pending else "Open Ledger")
        self.pending_label.setText(f"{len(pending)} pending" if pending else "No pending encounters")
        if run.acquisition_events:
            event = run.acquisition_events[-1]
            name = event.nickname or event.species_name
            location = event.suggested_location_name or event.met_location_name or "Location unknown"
            level = event.met_level or event.level
            self.latest_encounter_label.setText(
                f"{name} · {event.species_name}" + (f" · Lv. {level}" if level else "")
                + f"\n{event.source_location} · {location} · {event.confidence.lower()} confidence"
            )
        else:
            self.latest_encounter_label.setText("No acquisitions recorded.")
