# EV training preferences and live battle recommendations

Training now stores which EV stats each Pokémon is allowed to gain. These are
preferences, not numeric goals. The displayed EV values and total remain decoded
facts from party RAM. Selecting zero stats is allowed and means No Focus.

## Data flow

1. `EVTrainingPreference` validates the six canonical EV stat keys. The separate
   `EVTrainingPreferenceStore` saves selections by 32-bit Pokémon PID in the normal
   application data directory as `ev_training_preferences.json`.
2. `games/platinum/ev_yields.py` adapts the existing offline Platinum species
   catalog to the game-neutral `EVYield` type. Missing catalog entries stay
   unavailable. Battle rewards are **base species yields**; held items, Pokérus,
   participation, and actual awarded EVs are outside this recommendation.
3. `recommend_battle_evs` combines every active opponent's EV yield, ignores zero
   stats, and returns a structured GOOD, AVOID, MIXED, or NO_FOCUS result with
   separate allowed and unwanted positive yields.
4. `MainWindow` resolves each current party member's PID, uses the store and game
   adapter, and passes one result per party slot to the shared recommendation
   panel. Normal and compact Training render the same results. Editing, clearing,
   a party change, an opponent change, or a battle ending updates the panel.

The previous `ev_targets.json` is left untouched and is not read or migrated by
the new Training workflow. Hidden legacy card formatting and tests remain for a
later cleanup pass; the visible Training views do not show target completion,
remaining EVs, or progress toward 252.

The focus dialog has six toggles and Save/Cancel. Save uses the PID captured when
the dialog opened, so a live party reorder during editing cannot assign its
selection to the Pokémon that took the original slot. It then refreshes all
current party members. Clear Focus removes the stored preference for the selected
PID. Selections survive normal restart, party reorder, and evolution with the
same PID. Preferences remain distinct when a party member is replaced by a
Pokémon with a different PID.

## Verification

- Pure core tests cover absent/empty and all-six preferences, GOOD/AVOID/MIXED,
  multiple allowed stats, zero yields, and combined double-battle yields.
- Store tests cover save/load/clear, PID bounds, reorder/evolution, malformed
  files, failed writes, and preserving the old target file.
- Dialog and end-to-end UI tests cover Save/Cancel/Clear, immediate recommendation
  updates, opponent changes, double battles, compact mode, restart, party reorder,
  evolution, replacement, disconnected/unknown yield state, and reordering while
  the edit dialog is open.
- Native offscreen renders were checked at 1440×980, 720×620, and compact 400×700.
  Fixture Pokémon shown in those screenshots exist only in the test script under
  `.test-scratch`; no sample party is bundled into the product.

Live BizHawk validation still needs a gameplay session: save HP + Attack for one
Pokémon, restart, battle an Attack-yielding opponent (GOOD), then a Sp. Atk-yielding
opponent (AVOID), then a double battle with both (MIXED). Reorder and evolve the
Pokémon, confirm its selection follows its PID, and check the same state in
Compact Training. End the battle and confirm the previous yield reasons clear.
