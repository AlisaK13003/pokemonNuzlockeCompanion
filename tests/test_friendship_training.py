"""Pure session and ETA behavior, independent of Qt or emulator commands."""

from pokemon_ev_tracker.core.friendship_training import (
    ETAStatus,
    FriendshipGoal,
    FriendshipWalkSessionStats,
    estimate_friendship_eta,
)
from pokemon_ev_tracker.pokemon.gen4.friendship import GEN4_FRIENDSHIP_WALK_RULES

RULES = GEN4_FRIENDSHIP_WALK_RULES


def test_eta_reached_calibrates_then_responds_to_live_rate_and_friendship():
    assert estimate_friendship_eta(160, 160, RULES, None).status is ETAStatus.REACHED
    assert estimate_friendship_eta(74, 160, RULES, None).status is ETAStatus.CALIBRATING
    slow = estimate_friendship_eta(74, 160, RULES, 2, active_seconds=10, movement_units=20)
    fast = estimate_friendship_eta(74, 160, RULES, 4, active_seconds=10, movement_units=20)
    closer = estimate_friendship_eta(100, 160, RULES, 2, active_seconds=10, movement_units=20)
    farther = estimate_friendship_eta(60, 160, RULES, 2, active_seconds=10, movement_units=20)
    assert slow.status is ETAStatus.AVAILABLE
    assert slow.estimated_steps == 86 * 256
    assert fast.estimated_seconds < slow.estimated_seconds
    assert closer.estimated_seconds < slow.estimated_seconds < farther.estimated_seconds
    assert estimate_friendship_eta(74, 160, RULES, 0).status is ETAStatus.CALIBRATING
    assert estimate_friendship_eta(74, 160, RULES, -1).status is ETAStatus.CALIBRATING
    assert estimate_friendship_eta(74, 160, RULES, 2, paused=True).status is ETAStatus.PAUSED


def test_observed_gain_is_smooth_and_cannot_turn_eta_negative():
    theoretical = estimate_friendship_eta(100, 160, RULES, 2, active_seconds=200,
                                          movement_units=300)
    observed = estimate_friendship_eta(100, 160, RULES, 2, active_seconds=200,
                                       movement_units=300, observed_gain=20)
    assert observed.status is ETAStatus.AVAILABLE
    assert observed.confidence == "medium"
    assert observed.estimated_seconds < theoretical.estimated_seconds
    assert observed.estimated_seconds >= theoretical.estimated_seconds * 0.8
    assert estimate_friendship_eta(255, 160, RULES, 2).estimated_seconds == 0


def test_session_tracks_time_movement_reversal_gain_pause_and_restart():
    session = FriendshipWalkSessionStats(10, 74, 74)
    session.observe(12, active=True, controller_moves=0)
    session.observe(14, active=True, controller_moves=4, friendship=76)
    session.observe(16, active=False, controller_moves=4, reversal_frame=123)
    session.observe(26, active=False, controller_moves=4, reversal_frame=123)
    session.observe(28, active=True, controller_moves=4)
    session.observe(30, active=True, controller_moves=8, friendship=73)
    assert session.starting_friendship == 74
    assert session.current_friendship == 73
    assert session.friendship_gained == 0
    assert session.active_seconds == 6
    assert session.paused_seconds == 14
    assert session.movement_units == 8
    assert session.reversals == 1
    assert session.movement_rate is not None
    assert session.eta(FriendshipGoal(160), RULES).status is ETAStatus.AVAILABLE
    fresh = FriendshipWalkSessionStats(40, 91, 91)
    assert (fresh.started_at, fresh.friendship_gained, fresh.movement_units) == (40, 0, 0)
