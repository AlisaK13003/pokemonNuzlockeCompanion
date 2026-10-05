# Friendship Walk ETA

Friendship Walk tracks one selected **current party Pokémon**. Walking affects
the whole party, but progress, target, gain, and ETA refer to that selection.
Targets are 160, 220 (Platinum friendship evolution), 255, or a custom value
from 0–255. The chosen target is saved by the Pokémon's stable identity.
Elapsed time, observed movement, reversals, and friendship gains exist only
for the current application session. Start after an explicit Stop begins a new
session; Start after a safety pause resumes the current session.

## Platinum rule and approximation

Platinum's [`Pokemon_UpdateFriendship`](https://github.com/pret/pokeplatinum/blob/main/src/pokemon.c)
has a `FRIENDSHIP_EVENT_WALK_CYCLE` row of `+1` and returns on half of the
random-number outcomes. The walking cycle occurs every 128 steps in Generation
IV. The rule object stores **128 steps, 50% chance, +1 friendship**, so the
theoretical mean is approximately 256 steps per friendship point. The
[Gen IV walking rule](https://bulbapedia.bulbagarden.net/wiki/Friendship#Generation_IV)
also documents the 128-step interval. Luxury Ball, location, and held-item
bonuses can affect an actual gain; the estimator learns cautiously from live
friendship changes instead of claiming that every gain came from walking.

The RAM payload currently provides player coordinates, not the game's exact
128-step counter. The companion counts useful coordinate changes as a
**movement proxy**, not a guaranteed step total. A fast emulator or a delayed
poll can make one observed change cover multiple game steps. ETA is therefore
shown with `≈` and rounded to minutes. It is an estimate of **active walking
time remaining**, not a promised finish time.

## Calibration and pauses

The estimator waits for at least five active seconds and four observed
movement changes. Until then it shows **Calibrating…**. It smooths observed
movement per wall-clock second, including active stalls. After enough live
friendship gain and movement history, it blends a bounded portion of the
observed gain rate into the theoretical model. This limits swings from random
checks or friendship changes caused by other actions. Negative friendship
changes increase remaining work; the displayed session gain never becomes
negative.

Battle, manual input, stale party RAM, missing coordinates, and command-channel
pauses stop the ETA clock. The panel shows **ETA paused** until walking resumes.
Wall-reversal transitions are excluded from useful rate samples; the existing
controller still owns reversal and command behavior. When a **fresh, valid**
party record shows the tracked Pokémon at or above the target, the window calls
the existing Stop path to release injected input once and displays **Goal
reached**. Stale or invalid RAM cannot trigger completion. If the tracked
Pokémon leaves the party, movement is released and the selection is retained
until the user chooses another current party member.
