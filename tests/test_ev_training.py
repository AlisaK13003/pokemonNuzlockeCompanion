from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pokemon_ev_tracker.core.ev_training import (
    EV_STAT_KEYS,
    BattleEVRecommendation,
    EVTrainingPreference,
    EVYield,
    RecommendationStatus,
    recommend_battle_evs,
)


def test_preference_freezes_selection_without_numeric_target_semantics() -> None:
    selected = {"hp", "attack"}
    preference = EVTrainingPreference(selected)
    selected.add("speed")
    assert preference.allowed_stats == frozenset({"hp", "attack"})
    with pytest.raises(FrozenInstanceError):
        preference.allowed_stats = frozenset()


@pytest.mark.parametrize("stats", [
    {"special_atk"}, {"HP"}, {"attack", "unknown"}, {1}, "attack", None, 1,
    {"attack": True}, [["attack"]],
])
def test_preference_rejects_unknown_or_malformed_selection(stats) -> None:
    with pytest.raises(ValueError):
        EVTrainingPreference(stats)


def test_empty_and_all_six_preferences_are_valid() -> None:
    assert EVTrainingPreference().allowed_stats == frozenset()
    assert EVTrainingPreference(EV_STAT_KEYS).allowed_stats == frozenset(EV_STAT_KEYS)


@pytest.mark.parametrize("value", [-1, 1.0, True, "1", None])
def test_yield_rejects_negative_or_noninteger_values_for_every_stat(value) -> None:
    for stat in EV_STAT_KEYS:
        with pytest.raises(ValueError, match="nonnegative integer"):
            EVYield(**{stat: value})


def test_yield_has_independent_factual_stat_values() -> None:
    reward = EVYield(hp=1, attack=2, defense=3, special_attack=4, special_defense=5, speed=6)
    assert reward.as_mapping() == dict(zip(EV_STAT_KEYS, range(1, 7)))
    values = reward.as_mapping()
    values["attack"] = 999
    assert reward.attack == 2


@pytest.mark.parametrize("preference", [None, EVTrainingPreference()])
def test_no_saved_or_empty_preference_is_no_focus(preference) -> None:
    result = recommend_battle_evs(preference, [EVYield(attack=1), EVYield(defense=1)])
    assert result.status is RecommendationStatus.NO_FOCUS
    assert result.allowed_yields == {}
    assert result.unwanted_yields == {}


@pytest.mark.parametrize("preference,rewards,status,allowed,unwanted", [
    (EVTrainingPreference({"attack", "speed"}), [EVYield(attack=1)],
     RecommendationStatus.GOOD, {"attack": 1}, {}),
    (EVTrainingPreference({"attack", "speed"}), [EVYield(special_attack=1)],
     RecommendationStatus.AVOID, {}, {"special_attack": 1}),
    (EVTrainingPreference({"attack"}), [EVYield(attack=1, defense=2)],
     RecommendationStatus.MIXED, {"attack": 1}, {"defense": 2}),
    (EVTrainingPreference(EV_STAT_KEYS), [EVYield(attack=1, defense=2, speed=1)],
     RecommendationStatus.GOOD, {"attack": 1, "defense": 2, "speed": 1}, {}),
])
def test_recommendation_classifies_positive_yields(preference, rewards, status, allowed, unwanted):
    result = recommend_battle_evs(preference, rewards)
    assert result.status is status
    assert result.allowed_yields == allowed
    assert result.unwanted_yields == unwanted


@pytest.mark.parametrize("stats,status,allowed,unwanted", [
    ({"special_attack"}, RecommendationStatus.MIXED, {"special_attack": 1}, {"defense": 1}),
    ({"special_attack", "defense"}, RecommendationStatus.GOOD,
     {"special_attack": 1, "defense": 1}, {}),
    ({"attack"}, RecommendationStatus.AVOID, {}, {"special_attack": 1, "defense": 1}),
])
def test_double_battle_includes_both_opponents(stats, status, allowed, unwanted) -> None:
    result = recommend_battle_evs(
        EVTrainingPreference(stats), [EVYield(special_attack=1), EVYield(defense=1)],
    )
    assert result.status is status
    assert result.allowed_yields == allowed
    assert result.unwanted_yields == unwanted


def test_all_opponents_are_summed_including_duplicate_positive_stats() -> None:
    rewards = (EVYield(attack=1), EVYield(attack=2, speed=1), EVYield(defense=2))
    result = recommend_battle_evs(EVTrainingPreference({"attack", "speed"}), iter(rewards))
    assert result.status is RecommendationStatus.MIXED
    assert result.allowed_yields == {"attack": 3, "speed": 1}
    assert result.unwanted_yields == {"defense": 2}
    assert recommend_battle_evs(EVTrainingPreference({"attack", "speed"}), reversed(rewards)) == result


@pytest.mark.parametrize("rewards", [[], [EVYield()], [EVYield(), EVYield()]])
def test_zero_reward_is_vacuously_good_for_a_nonempty_preference(rewards) -> None:
    result = recommend_battle_evs(EVTrainingPreference({"attack"}), rewards)
    assert result.status is RecommendationStatus.GOOD
    assert result.allowed_yields == {}
    assert result.unwanted_yields == {}


def test_result_reasons_are_immutable_snapshots() -> None:
    allowed = {"attack": 1}
    result = BattleEVRecommendation(RecommendationStatus.GOOD, allowed, {})
    allowed["attack"] = 2
    assert result.allowed_yields == {"attack": 1}
    with pytest.raises(TypeError):
        result.allowed_yields["attack"] = 3
    with pytest.raises(FrozenInstanceError):
        result.status = RecommendationStatus.AVOID


def test_engine_rejects_old_numeric_or_untyped_inputs() -> None:
    with pytest.raises(TypeError, match="Preference"):
        recommend_battle_evs({"attack_target": 252}, [EVYield(attack=1)])
    with pytest.raises(TypeError, match="EVYield"):
        recommend_battle_evs(EVTrainingPreference({"attack"}), [{"attack": 1}])
