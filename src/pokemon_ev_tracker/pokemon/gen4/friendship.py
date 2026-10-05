"""Platinum/Generation IV walking friendship assumptions for ETA.

Walking checks every 128 steps, with a 50% chance of +1 friendship for each
party member. Platinum's Pokemon_UpdateFriendship confirms the +1 walk-cycle
row and random 50% gate:
https://github.com/pret/pokeplatinum/blob/main/src/pokemon.c
The Generation IV 128-step interval is documented at
https://bulbapedia.bulbagarden.net/wiki/Friendship#Generation_IV . The exact
step counter is not exposed by this companion, so ETA uses coordinate changes
as a documented step proxy and remains approximate.
"""

from pokemon_ev_tracker.core.friendship_training import FriendshipWalkRules

GEN4_FRIENDSHIP_WALK_RULES = FriendshipWalkRules(
    movement_interval_steps=128,
    friendship_gain_probability=0.5,
    friendship_gain_amount=1,
    max_friendship=255,
    evolution_goal=220,
)

MAX_FRIENDSHIP = 255


def friendship_label(value: int) -> str:
    if not 0 <= value <= MAX_FRIENDSHIP:
        raise ValueError("Friendship must be between 0 and 255.")
    if value == MAX_FRIENDSHIP:
        return "Max"
    if value >= 200:
        return "Very High"
    if value >= 150:
        return "High"
    if value >= 100:
        return "Neutral"
    if value >= 50:
        return "Low"
    return "Very Low"
