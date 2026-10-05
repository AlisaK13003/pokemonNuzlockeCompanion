"""Presentation checks for selection, real data boundaries, and compact workspaces."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QBoxLayout, QLabel

from pokemon_ev_tracker.core.ev_targets import EVTarget
from pokemon_ev_tracker.core.ev_training import (
    BattleEVRecommendation,
    EVTrainingPreference,
    RecommendationStatus,
)
from pokemon_ev_tracker.core.friendship_training import (
    ETAStatus,
    FriendshipETA,
    FriendshipWalkSessionStats,
)
from pokemon_ev_tracker.core.moves import PokemonMoveState, resolve_move
from pokemon_ev_tracker.pokemon.gen4.moves import get_gen4_move_definition
from pokemon_ev_tracker.ui.friendship_panel import FriendshipWalkPanel
from pokemon_ev_tracker.ui.party_selector import PartySelector
from pokemon_ev_tracker.ui.theme import apply_theme
from pokemon_ev_tracker.ui.tracker_views import (
    CompactPartyStatsView,
    CompactTrainingView,
    PartyStatsView,
    RecommendationPanel,
    TrainingView,
)

STATS = ("hp", "attack", "defense", "special_attack", "special_defense", "speed")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pokemon(slot=1, stable_id="pid:00000001:ot:0001:0001", nickname="Sprout"):
    return SimpleNamespace(
        slot=slot, stable_id=stable_id, nickname=nickname, species="Bulbasaur", species_id=1,
        level=12, current_hp=25, max_hp=30, checksum_valid=True,
        evs={stat: index * 4 for index, stat in enumerate(STATS)},
        nature_name="Modest", nature_increased_stat="special_attack",
        nature_decreased_stat="attack", ability_name="Overgrow", ability_id=65,
        friendship=100, held_item_name=None,
        decoded=SimpleNamespace(move_ids=(33, 0, 45, 0), diagnostics=SimpleNamespace(pid=1)),
    )


def _card(pokemon, preference=None):
    card = {
        "pokemon": pokemon,
        "training_preference": preference,
        "evs": {stat: QLabel(f"{pokemon.evs[stat]} / 252") for stat in STATS},
        "ev_annotations": {stat: QLabel("") for stat in STATS},
        "stat_values": {stat: QLabel(str(30 + index)) for index, stat in enumerate(STATS)},
        "iv_values": {stat: QLabel(str(index * 5)) for index, stat in enumerate(STATS)},
        "target_summary": QLabel("No EV target set"),
        "item_name": QLabel("No held item"),
        "checksum": QLabel("RAM data valid"),
    }
    card["move_displays"] = tuple(
        resolve_move(PokemonMoveState(move_id, pp, ups), get_gen4_move_definition)
        for move_id, pp, ups in zip((33, 0, 45, 0), (20, 0, 30, 0), (1, 0, 0, 0))
    )
    return card


def test_selector_follows_full_stable_identity_when_party_reorders(app):
    selector = PartySelector()
    first = _pokemon(1, "pid:00000001:ot:0001:0001", "First")
    second = _pokemon(2, "pid:00000001:ot:0002:0002", "Second")
    selector.set_party((first, second))
    selector.set_selected_slot(2)
    first.slot, second.slot = 2, 1

    selector.set_party((second, first))

    assert selector.selected_slot == 1
    assert selector.slots[1].isChecked()
    assert selector.slots[1].name.text() == "Second"
    assert sum(slot.isChecked() for slot in selector.slots.values()) == 1


def test_selector_retains_identity_across_disconnected_frame(app):
    selector = PartySelector()
    pokemon = _pokemon(slot=3)
    selector.set_party((pokemon,))
    selector.set_party(())
    assert all(not button.isEnabled() for button in selector.slots.values())
    pokemon.slot = 5

    selector.set_party((pokemon,))

    assert selector.selected_slot == 5
    assert selector.slots[5].isChecked()


def test_friendship_selection_survives_polling_reorder_and_disconnect(app):
    panel = FriendshipWalkPanel()
    first = _pokemon(1, "first", "First")
    second = _pokemon(2, "second", "Second")
    cards = {1: _card(first), 2: _card(second)}
    panel.refresh_party(cards)
    panel.selector.setCurrentIndex(1)
    model_resets = []
    panel.selector.model().modelReset.connect(lambda: model_resets.append(True))
    second.friendship = 123

    panel.refresh_party(cards)

    assert not model_resets
    assert panel.selector.currentData() == "second"
    assert panel.friendship.text() == "Friendship 123 / 255"
    first.slot, second.slot = 2, 1
    panel.refresh_party({1: _card(second), 2: _card(first)})
    assert panel.selector.currentData() == "second"
    panel.refresh_party({})
    assert not panel.selector.isEnabled()
    assert panel.friendship.text() == "Friendship — / 255"
    panel.refresh_party({1: _card(second), 2: _card(first)})
    assert panel.selector.currentData() == "second"
    second.checksum_valid = False
    panel.refresh_party({1: _card(second), 2: _card(first)})
    assert panel.friendship.text() == "Friendship — / 255"


def test_friendship_goal_custom_and_compact_eta_render(app, monkeypatch):
    panel = FriendshipWalkPanel()
    panel.refresh_party({1: _card(_pokemon())})
    monkeypatch.setattr(
        "pokemon_ev_tracker.ui.friendship_panel.QInputDialog.getInt",
        lambda *args, **kwargs: (207, True),
    )
    selected = []
    panel.goal_changed.connect(selected.append)
    panel.goal_picker.setCurrentIndex(3)
    assert panel.goal == 207
    assert selected == [207]
    session = FriendshipWalkSessionStats(0, 74, 91)
    session.movement_units = 1842
    session.reversals = 8
    session.active_seconds = 222
    panel.render_training(
        status="Walking Right", current=91, target=207,
        eta=FriendshipETA(ETAStatus.AVAILABLE, estimated_seconds=360), session=session,
    )
    assert panel.telemetry.text() == "≈ 6 min remaining"
    assert panel.session_gain.text() == "+17 friendship this session"
    assert "1,842 movement" in panel.moves.text()
    panel.set_compact(True)
    assert panel.moves.isHidden()
    assert panel.telemetry.text() == "≈ 6 min remaining"


@pytest.mark.parametrize("view_type", (TrainingView, CompactTrainingView))
def test_training_actions_follow_selected_member_and_disable_when_disconnected(app, view_type):
    view = view_type()
    cards = {1: _card(_pokemon()), 2: _card(_pokemon(2, "second"), EVTrainingPreference({"speed"}))}
    edits, clears = [], []
    view.edit_focus_requested.connect(edits.append)
    view.clear_focus_requested.connect(clears.append)
    view.refresh(cards)
    view.set_selected_slot(2)
    view.edit_button.click()
    view.clear_button.click()
    assert edits == [2]
    assert clears == [2]
    assert view.focus_chips["speed"].property("evFocused") is True
    assert view.focus_chips["attack"].property("evFocused") is False

    view.refresh(cards, connected=False)

    assert not view.edit_button.isEnabled()
    assert not view.clear_button.isEnabled()
    assert view.total.text() == "— / 510 total"
    assert all(label.text() == "—" for label in view.ev_values.values())


@pytest.mark.parametrize("view_type", (TrainingView, CompactTrainingView))
def test_training_values_are_factual_and_ignore_legacy_numeric_targets(app, view_type):
    view = view_type()
    pokemon = _pokemon()
    card = _card(pokemon)
    card["ev_target"] = EVTarget(hp_target=252)
    view.refresh({1: card})

    assert not hasattr(view, "ev_bars")
    assert {stat: label.text() for stat, label in view.ev_values.items()} == {
        stat: str(value) for stat, value in pokemon.evs.items()}
    assert view.total.text() == "60 / 510 total"
    assert view.focus_summary.text() == "No focus set"
    assert view.edit_button.text() == "Set training focus"
    assert not view.clear_button.isEnabled()
    assert all(not chip.property("evFocused") for chip in view.focus_chips.values())
    assert all(not chip.isHidden() for chip in view.focus_chips.values())

    card["training_preference"] = EVTrainingPreference({"hp", "attack"})
    view.refresh({1: card})

    assert view.focus_summary.text() == "HP · Attack"
    assert view.edit_button.text() == "Edit training focus"
    assert view.clear_button.text() == "Clear Focus"
    assert view.clear_button.isEnabled()
    assert view.ev_values["attack"].text() == "4"
    assert view.ev_rows["attack"].property("evFocused") is None
    pokemon.checksum_valid = False
    view.refresh({1: card})
    assert not view.edit_button.isEnabled()
    assert not view.clear_button.isEnabled()


@pytest.mark.parametrize("preference", (None, EVTrainingPreference(frozenset())))
def test_training_missing_and_empty_preference_show_no_focus(app, preference):
    view = TrainingView()
    view.refresh({1: _card(_pokemon(), preference)})
    assert view.focus_summary.text() == "No focus set"
    assert view.edit_button.text() == "Set training focus"
    assert all(not chip.property("evFocused") for chip in view.focus_chips.values())


@pytest.mark.parametrize("view_type", (PartyStatsView, CompactPartyStatsView))
def test_stats_preserves_four_move_positions_and_renders_gen4_metadata(app, view_type):
    view = view_type()
    view.refresh({1: _card(_pokemon())})

    assert [label.text() for label in view.move_names] == [
        "01  Tackle", "02  Empty move slot", "03  Growl", "04  Empty move slot"
    ]
    assert view.move_pp[0].text() == "20 / 42 PP"
    assert view.move_types[0].text() == "Normal"
    if view_type is PartyStatsView:
        assert "Power 35" in view.move_details[0].text()
        assert "Accuracy 95%" in view.move_details[0].text()
    assert view.friendship.text() == "Friendship 100 / 255"
    assert view.iv_values["speed"].text() == "25 IV"
    assert view.ev_values["speed"].text() == "20"

    view.refresh({}, connected=False)

    assert all("Empty move slot" in label.text() for label in view.move_names)
    assert view.friendship.text() == "Friendship — / 255"


def test_recommendations_render_supplied_core_result_and_clear_on_battle_end(app):
    panel = RecommendationPanel()
    cards = {1: _card(_pokemon(), EVTrainingPreference({"defense"}))}
    mixed = BattleEVRecommendation(RecommendationStatus.MIXED, {"defense": 1}, {"special_attack": 1})
    panel.refresh(cards, {1: mixed}, battle_state="active")
    _row, _sprite, _name, badge, detail = panel.rows[1]
    assert badge.text() == "MIXED"
    assert badge.property("status") == "mixed"
    assert "+1 DEF allowed" in detail.text()
    assert "+1 SPA unwanted" in detail.text()
    assert panel.results[1] is mixed

    panel.refresh(cards, {1: mixed}, battle_state="idle")

    assert badge.text() == "IDLE"
    assert badge.property("status") == "idle"
    assert detail.text() == "No current battle"
    assert panel.results == {}

    panel.refresh(cards, {1: mixed}, battle_state="unavailable")
    assert badge.text() == "UNAVAILABLE"
    assert "allowed" not in detail.text()
    assert panel.results == {}


def test_recommendations_withhold_invalid_party_data(app):
    panel = RecommendationPanel()
    pokemon = _pokemon()
    pokemon.checksum_valid = False
    cards = {1: _card(pokemon, EVTrainingPreference({"defense"}))}
    good = BattleEVRecommendation(RecommendationStatus.GOOD, {"defense": 1}, {})
    panel.refresh(cards, {1: good}, battle_state="active")
    assert panel.rows[1][3].text() == "UNAVAILABLE"
    assert panel.results == {}


@pytest.mark.parametrize("preference", (None, EVTrainingPreference(frozenset())))
@pytest.mark.parametrize("battle_state", ("active", "idle", "unavailable"))
def test_recommendations_no_focus_never_keep_stale_yield_reasons(app, preference, battle_state):
    panel = RecommendationPanel()
    cards = {1: _card(_pokemon(), preference)}
    stale = BattleEVRecommendation(RecommendationStatus.AVOID, {}, {"special_attack": 1})
    panel.refresh(cards, {1: stale}, battle_state=battle_state)
    assert panel.rows[1][3].text() == "NO FOCUS"
    assert panel.rows[1][4].text() == "No focus set"


def test_compact_recommendations_retain_name_and_use_the_same_core_result(app):
    panel = RecommendationPanel()
    card = _card(_pokemon(nickname="Shizuku"), EVTrainingPreference({"attack"}))
    good = BattleEVRecommendation(RecommendationStatus.GOOD, {"attack": 1}, {})
    avoid = BattleEVRecommendation(RecommendationStatus.AVOID, {}, {"special_attack": 1})
    panel.refresh({1: card}, {1: good}, battle_state="active")
    normal_text = panel.rows[1][4].text()
    panel.set_compact(True, 1)

    assert panel.results[1] is good
    assert panel.rows[1][2].text() == "Shizuku"
    assert not panel.rows[1][2].isHidden()
    assert panel.rows[1][3].text() == "GOOD"
    assert panel.rows[1][4].text() == normal_text

    panel.refresh({1: card}, {1: avoid}, battle_state="active")

    assert panel.results[1] is avoid
    assert panel.rows[1][3].text() == "AVOID"
    assert panel.rows[1][3].property("status") == "avoid"


def test_recommendation_panel_clears_departed_party_members(app):
    panel = RecommendationPanel()
    panel.refresh({1: _card(_pokemon())})
    assert not panel.rows[1][0].isHidden()
    panel.refresh({})
    assert all(row[0].isHidden() for row in panel.rows.values())
    assert not panel.empty.isHidden()


def test_runtime_widgets_move_between_full_and_compact_without_duplicate_hosts(app):
    normal, compact = TrainingView(), CompactTrainingView()
    opponent = QLabel("Existing opponent controller")
    normal.set_runtime_widgets(opponent=opponent)
    assert normal.runtime_hosts["opponent"].layout().count() == 1

    compact.set_runtime_widgets(opponent=opponent)

    assert normal.runtime_hosts["opponent"].layout().count() == 0
    assert compact.runtime_hosts["opponent"].layout().count() == 1
    compact.set_runtime_widgets(opponent=opponent)
    assert compact.runtime_hosts["opponent"].layout().count() == 1


def test_compact_recommendations_follow_selection_and_restore_full_party(app):
    panel = RecommendationPanel()
    cards = {slot: _card(_pokemon(slot, f"member-{slot}")) for slot in (1, 2, 3)}
    panel.refresh(cards)
    panel.set_compact(True, 2)
    assert [slot for slot, row in panel.rows.items() if not row[0].isHidden()] == [2]

    panel.set_compact(True, 3)
    panel.refresh(cards)
    assert [slot for slot, row in panel.rows.items() if not row[0].isHidden()] == [3]

    panel.set_compact(False)
    assert [slot for slot, row in panel.rows.items() if not row[0].isHidden()] == [1, 2, 3]


def test_compact_selector_keeps_all_six_slots_within_320_pixels(app):
    selector = PartySelector(compact=True)
    apply_theme(selector)
    selector.set_party(tuple(_pokemon(slot, f"member-{slot}") for slot in range(1, 7)))
    selector.resize(320, 80)
    selector.show()
    app.processEvents()
    assert selector.width() <= 320
    assert all(button.geometry().right() < selector.width() for button in selector.slots.values())
    assert all(button.height() == 66 for button in selector.slots.values())
    selector.close()


def test_compact_training_reflows_before_two_columns_overflow(app):
    view = CompactTrainingView()
    apply_theme(view)
    view.set_runtime_widgets(opponent=QLabel("Current opponent"))
    view.refresh({1: _card(_pokemon())})
    view.resize(720, 620)
    view.show()
    app.processEvents()
    assert view.body_layout.direction() == QBoxLayout.Direction.LeftToRight
    view.resize(600, 620)
    app.processEvents()
    assert view.body_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert view.content.width() <= view.scroll.viewport().width()
    view.close()


def test_compact_stats_keeps_inspection_and_ev_snapshot_visible_at_720_by_620(app):
    view = CompactPartyStatsView()
    apply_theme(view)
    view.refresh({1: _card(_pokemon())})
    view.resize(720, 620)
    view.show()
    app.processEvents()
    assert view.identity_row.direction() == QBoxLayout.Direction.LeftToRight
    assert view.mid_layout.direction() == QBoxLayout.Direction.LeftToRight
    assert view.scroll.verticalScrollBar().maximum() == 0
    assert all(label.isHidden() for label in view.move_details)
    assert view.move_pp[0].text() == "20 / 42 PP"
    view.close()
