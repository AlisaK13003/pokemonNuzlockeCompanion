"""Bounded recommendation reuse and byte-for-byte diagnostic presentation."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_training_integration import _party, make_window

from pokemon_ev_tracker.core.ev_targets import EVTarget, EVTargetStore
from pokemon_ev_tracker.core.ev_training import EVTrainingPreference, EVYield, recommend_battle_evs
from pokemon_ev_tracker.ui.battle_recommendations import (
    BattleRecommendationProjector,
    TrainingMember,
)
from pokemon_ev_tracker.ui.diagnostics_presentation import (
    RamBattleDiagnosticObservation,
    format_ram_battle_diagnostics,
)

orchestration_window = make_window


@pytest.mark.parametrize("allowed,status", [
    ({"attack", "defense"}, "good"), ({"speed"}, "avoid"),
    ({"attack"}, "mixed"), (set(), "no-focus"),
])
def test_projector_preserves_combined_classifications(allowed, status):
    member = TrainingMember(1, "a", EVTrainingPreference(frozenset(allowed)))
    projector = BattleRecommendationProjector()
    provider = object()
    yields = (EVYield(attack=1), EVYield(defense=1))
    result = projector.project((member,), yields, available=True, provider=provider)
    assert result.recommendations[1].status.value == status
    assert result.recommendations[1] == recommend_battle_evs(member.preference, yields)
    assert projector.project((member,), yields, available=True, provider=provider) is result


@pytest.mark.parametrize("yields,available,state", [
    ((), True, "idle"), ((EVYield(),), True, "idle"),
    ((None,), True, "unavailable"), ((EVYield(attack=1),), False, "unavailable"),
])
def test_projector_idle_unknown_and_unsupported(yields, available, state):
    result = BattleRecommendationProjector().project(
        (TrainingMember(1, "a", None),), yields, available=available, provider=object())
    assert result.battle_state == state and not result.recommendations


def test_projector_provider_and_members_invalidate_without_history_growth():
    projector = BattleRecommendationProjector()
    provider = object()
    first = projector.project((TrainingMember(1, "a", None),), (EVYield(attack=1),),
                              available=True, provider=provider)
    changed = projector.project((TrainingMember(1, "b", None),), (EVYield(attack=1),),
                                available=True, provider=provider)
    assert first is not changed
    other = projector.project((TrainingMember(1, "b", None),), (EVYield(attack=1),),
                              available=True, provider=object())
    assert changed is not other
    assert projector._result is other
    with pytest.raises(TypeError):
        other.recommendations[2] = other.recommendations[1]


def test_poll_has_one_publication_and_unchanged_inputs_dont_recalculate(orchestration_window, monkeypatch):
    window, source, store = orchestration_window()
    source.opponents(66, 74)
    store.set(0, EVTrainingPreference({"attack"}))
    calculate = Mock(wraps=recommend_battle_evs)
    monkeypatch.setattr("pokemon_ev_tracker.ui.battle_recommendations.recommend_battle_evs", calculate)
    publish = Mock(wraps=window._refresh_training_recommendations)
    window._refresh_training_recommendations = publish
    window._refresh_ram_backend_debug()
    assert calculate.call_count == 2
    original = window.training_recommendations
    for _ in range(30):
        window._refresh_ram_backend_debug()
    assert publish.call_count == 31 and calculate.call_count == 2
    assert window.training_recommendations is original
    # An external preference-store edit is read on the very next poll.
    store.set(0, EVTrainingPreference({"defense"}))
    window._refresh_ram_backend_debug()
    assert calculate.call_count == 4
    assert window.training_recommendations[1].allowed_yields == {"defense": 1}


def test_direct_focus_selection_and_reorder_use_current_identity(orchestration_window):
    window, source, store = orchestration_window()
    source.opponents(66)
    store.set(0, EVTrainingPreference({"attack"}))
    store.set(2, EVTrainingPreference({"defense"}))
    window._refresh_ram_backend_debug()
    first = window.training_recommendations
    window._select_party_slot(2)
    assert window.training_recommendations is first
    assert first[1].status.value == "good" and first[2].status.value == "avoid"
    window._toggle_training_stat(2, "attack")
    assert window.training_recommendations[2].status.value == "good"
    source.party = _party((2, 443), (0, 183))
    window._refresh_ram_backend_debug()
    assert window.training_view.selected_slot == 1
    assert window.tracker_party_cards[1]["pid"] == 2
    assert window.training_recommendations[1].status.value == "good"


def test_target_edit_refreshes_targets_without_changing_preference_classification(
    orchestration_window, tmp_path, monkeypatch,
):
    targets = EVTargetStore(tmp_path / "legacy-targets.json")
    window, source, store = orchestration_window(legacy=targets)
    source.opponents(66)
    store.set(0, EVTrainingPreference({"attack"}))
    window._refresh_ram_backend_debug()
    previous = window.training_recommendations
    from PySide6.QtWidgets import QDialog

    from pokemon_ev_tracker.ui.ev_target_dialog import EVTargetDialog
    monkeypatch.setattr(EVTargetDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(EVTargetDialog, "target", lambda self: EVTarget(attack_target=252))
    window._edit_ev_target(1)
    assert targets.get(0).attack_target == 252
    assert window.tracker_party_cards[1]["ev_target"].attack_target == 252
    assert window.training_recommendations is previous
    window._clear_ev_target(1)
    assert targets.get(0) is None
    assert window.training_recommendations is previous


def test_same_enemy_species_provider_reward_change_is_not_cached(orchestration_window, monkeypatch):
    window, source, store = orchestration_window()
    source.opponents(66)
    store.set(0, EVTrainingPreference({"attack"}))
    window._refresh_ram_backend_debug()
    monkeypatch.setattr(window.provider, "get_training_ev_yield", lambda species: EVYield(defense=2))
    window._refresh_ram_backend_debug()
    assert window.training_recommendations[1].status.value == "avoid"
    assert window.training_recommendations[1].unwanted_yields == {"defense": 2}


def test_enemy_level_updates_even_when_recommendation_projection_is_shared(orchestration_window):
    window, source, store = orchestration_window()
    source.opponents(66)
    store.set(0, EVTrainingPreference({"attack"}))
    window._refresh_ram_backend_debug()
    result = window.training_recommendations
    source.battlers = tuple(replace(battler, level=25) if battler.battler_index == 1
                            else battler for battler in source.battlers)
    window._refresh_ram_backend_debug()
    assert window.training_recommendations is result
    assert window.current_opponent_panel.cards[0].level.text() == "LV 25"


def test_capability_change_publishes_unavailable_without_provider_lookup(orchestration_window, monkeypatch):
    window, source, store = orchestration_window()
    source.opponents(66, 74)
    store.set(0, EVTrainingPreference({"attack"}))
    window._refresh_ram_backend_debug()
    window.provider.capabilities = replace(window.provider.capabilities, ev_yields=False)
    lookup = Mock(side_effect=AssertionError("unsupported lookup"))
    monkeypatch.setattr(window.provider, "get_training_ev_yield", lookup)
    window._refresh_training_recommendations(source.snapshot().details["active_enemy_battlers"])
    assert not window.training_recommendations and window._training_battle_state == "unavailable"
    lookup.assert_not_called()


def test_invalid_stale_disconnected_and_recovery_clear_results(orchestration_window):
    window, source, store = orchestration_window()
    source.opponents(66)
    store.set(0, EVTrainingPreference({"attack"}))
    window._refresh_ram_backend_debug()
    original = source.party
    source.party = replace(original, pokemon=tuple(replace(mon, checksum_valid=False) for mon in original.pokemon))
    window._refresh_ram_backend_debug()
    assert not window.training_recommendations
    source.party = original
    # Data-source freshness validation removes active enemies on stale snapshots.
    source.opponents()
    window._refresh_ram_backend_debug()
    assert window._training_battle_state == "idle" and not window.training_recommendations
    source.opponents(66)
    source.connected = False
    window._refresh_ram_backend_debug()
    assert not window.training_recommendations
    source.connected = True
    window._refresh_ram_backend_debug()
    assert window.training_recommendations[1].status.value == "good"


def test_diagnostics_output_matches_pre_extraction_fixture(orchestration_window):
    window, source, _ = orchestration_window()
    source.opponents(66, 74)
    sample = SimpleNamespace(source="file", payload={"pointer_value": "0x022711C8",
                                                    "frame": 123, "domain": "Main RAM"})
    empty = SimpleNamespace(party_count=0, party_count_valid=True, pokemon=(), error=None, candidate_count=0)
    # The original fixture has no active walk/runtime command fields.
    class Harness:
        from pokemon_ev_tracker.ui.main_window import MainWindow
        _refresh_ram_party_debug = MainWindow._refresh_ram_party_debug
        _friendship_walk_transport_debug_lines = MainWindow._friendship_walk_transport_debug_lines
        provider = window.provider
        ram_party_summary_label = window.ram_party_summary_label
        def _set_ram_party_debug_text(self, text):
            self.text = text
    harness = Harness()
    harness._refresh_ram_party_debug(empty, sample, source.battlers,
                                    source.snapshot().details["active_enemy_battlers"])
    assert harness.text == (Path(__file__).parent / "fixtures/ram-battle-diagnostics.txt").read_text(encoding="utf-8")


def test_pure_diagnostic_formatter_missing_values_and_position():
    observation = RamBattleDiagnosticObservation({}, None, 0, (), (), ())
    lines = format_ram_battle_diagnostics(observation)
    assert "Player X: --" in lines and "Pointer value: --" in lines
    assert "No validated active enemy battlers." in lines
    position = SimpleNamespace(x=0, y=-12, delta_x=None, delta_y=0)
    changed = replace(observation, position=position, walk_moves=3)
    assert "Player X: 0" in format_ram_battle_diagnostics(changed)
    assert "Player Y: -12" in format_ram_battle_diagnostics(changed)
    assert "Delta X: --" in format_ram_battle_diagnostics(changed)
    assert "Delta Y: 0" in format_ram_battle_diagnostics(changed)
    assert changed.payload == {}


def test_diagnostics_still_format_off_page_and_without_party(orchestration_window):
    window, _source, _ = orchestration_window()
    window.set_route("training")
    format_debug = Mock(wraps=window._refresh_ram_party_debug)
    window._refresh_ram_party_debug = format_debug
    window._refresh_ram_backend_debug()
    assert format_debug.call_count == 1
    window._refresh_ram_party_debug(None, None)
    assert "Waiting for party memory payload." in window.ram_party_details.toPlainText()
    assert "Command channel:" in window.ram_party_details.toPlainText()
