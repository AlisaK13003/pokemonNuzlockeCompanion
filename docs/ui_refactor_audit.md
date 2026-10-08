# Trainer Lab Terminal UI scope audit

Phase 1 EV training preferences and live battle recommendations were added later;
see `docs/ev_training_preferences.md`. The target-based Training notes below
record the original UI refactor boundary and are superseded for the visible
Training workflow.

The interface is based on the supplied Figma file `09xm3gXTzxKzlPCj6vyU6g`.
The implementation preserves the existing runtime readers, transport, observers,
identity behavior, and persistence. A visible mockup value is not an available
runtime capability.

## Baseline

Before UI changes, `.venv\Scripts\python.exe -m pytest -q
--basetemp=.pytest-audit-temp` passed **395 tests in 27.97 seconds**. This included
RAM decoding, emulator transport, PC discovery/acquisition, death detection,
friendship commands, Nuzlocke persistence, EV targets/history, and UI tests.

## Existing data and actions

| Area | Existing source and behavior |
| --- | --- |
| Party identity | `PartyState` / `PartyPokemon` expose slot, stable ID, species, nickname, level, HP, held item, nature, ability, friendship, battle stats, IVs, EVs, checksum validity, and stale-sample flags. Selection may follow stable identity. |
| EV targets | `EVTargetStore` deliberately persists numeric spreads by **PID**, not party slot or stable-ID string. Existing validation permits 0–252 per stat and at most 510 total. Existing edit/clear behavior and target progress remain authoritative. |
| Current battle | Existing decoded active enemy battlers support one or two opponents. Platinum reference data provides base species EV yield; held items/Pokérus can affect actual gains. |
| EV history | Existing `EVChangeTracker` and history rendering provide observed changes, Pokémon identity, and clear-history behavior. |
| Friendship Walk | Existing controller provides start/stop, horizontal/vertical axis, frame-based movement, direction changes, successful movement count, and detailed idle/starting/walking/reversing/paused states. MainWindow handles command acknowledgements, timeouts, shortcut stop, and explicit release. |
| Move slots | Decoded `move_ids` preserves all four positions. Existing offline reference data resolves names only. `PartyPokemon.moves` omits empty IDs; use the decoded IDs if slot positions matter. |
| Nuzlocke runs | Store supports create, select, rename, delete, notes, and a `completed` boolean. No richer outcome enum exists. |
| Encounters | Existing acquisition events and correction flows support accept, location override, occupied-location replace/extra/ignore, and opt-in unambiguous encounter recording. Party and box paths use existing deduplication and baseline protections. |
| Deaths | Existing death candidates, manual records, linked encounters, confirmation, and opt-in automatic confirmation remain the source of truth. |
| Fights | `LevelCap` already has name, category, order, cap, and opponent healing-item data. Runs have completed fight IDs and cap overrides; existing party-level checks provide over-cap warnings. |
| Diagnostics | Snapshot exposes connection, heartbeat, party/PC payloads and freshness, decoded party/PC data, RAM age/source, PC monitor changes/debug, discovery progress, and movement command acknowledgement fields. Existing UI owns resolver lifecycle, monitoring readiness, recovery messages, and last PC event. |
| Developer tools | Existing coordinate discovery/preview, RAM search, anchor recovery, identity inspection, pointer experiments, raw diagnostics, SRAM and save-file experiments are retained under Advanced. |
| Preferences | Existing compact mode, tracker training/stats view, normal/current geometry, walk axis, and PC anchor preferences are preserved. A UI-only `workspace_view` preference remembers all four shell routes, with legacy tracker-view fallback. No domain/store schema changed. |

## Implemented architecture and fully wired concepts

- `AppShell` provides the header, connection indicator, navigation rail, shared
  detection notice, and stacked normal/compact workspaces. The downloaded Figma
  brand SVG is bundled locally; runtime rendering needs no Figma connection.
- `PartySelector`, `TrainingView`, and `PartyStatsView` show six small selectors
  and one selected Pokémon. Selection follows stable identity across reorder and
  disconnect/reconnect. Stats, IVs, held items, nature, ability, friendship, HP,
  sprites, EV totals, and four decoded move positions use existing data.
- Training retains numeric PID-owned target editing/clearing and observed EV
  history. Focus indicators reflect nonzero saved targets. Good/Avoid/Mixed/
  No Focus compare existing base species yields with those targets, including
  combined yields in double battles. This is a presentation comparison; held
  items and Pokérus can change actual gains.
- `FriendshipWalkPanel` uses the existing start/stop/axis/controller state,
  movement count, acknowledgements, pause rules, and emergency stop shortcut.
  Its independent Pokémon selector displays current decoded friendship and
  preserves selection without resetting the list every polling interval.
- Nuzlocke now has Dashboard, Encounters, Deaths, Fights, and Run Library.
  Existing run management, notes, completion flag, encounter corrections,
  acquisition prompts, death review, level-cap overrides/progression, over-cap
  warnings, and PC recovery controls remain connected to their original paths.
- Diagnostics has a concise Status page and scrollable Advanced tools. Widget
  construction moved to `diagnostics_tools.py`; callback ownership remains in
  MainWindow. Existing legacy experiments are isolated in their own section.
- Compact Training, Party Stats, and Nuzlocke have dedicated layouts and a direct
  three-way switcher. Always-on-top, normal geometry restoration, route selection,
  tracker-view preference, and compact-mode persistence are retained.
- Theme tokens and semantic QSS roles define the common visual system. Normal
  layouts reflow/scroll down to 620×480; compact layouts allow 320×300 and use
  internal scrolling at that size. The default compact window is 720×620.

MainWindow still owns runtime orchestration. Six old PartyCard instances remain
hidden as a transitional formatting adapter for established formatting, targets,
and sprite behavior; they are never mounted as visible workspaces. The new views
consume their presentation fields, avoiding a rewrite of tested runtime paths.

Engineering follow-up (2026-10-08): stat/checksum, metadata and move projection now
live in `ui/party_presentation.py`, with per-card equivalence guards. The compatibility
cards, field names, callbacks, PID-target formatting and movie ownership remain.
This extraction does not remove the adapters or redesign the visible views. See
the [refactor roadmap](refactor-roadmap.md) for measured scope and later work.

## Files created or modified

Created presentation modules under `src/pokemon_ev_tracker/ui/`:

- `app_shell.py`
- `party_selector.py`
- `tracker_views.py`
- `friendship_panel.py`
- `nuzlocke_panels.py`
- `nuzlocke_workspace.py`
- `compact_nuzlocke.py`
- `diagnostics_view.py`
- `diagnostics_tools.py`

Modified presentation modules under `src/pokemon_ev_tracker/ui/`:

- `main_window.py`
- `theme.py`
- `backend_status.py`
- `opponent_panel.py`
- `ev_change_log.py`
- `nuzlocke_view.py`
- `nuzlocke_dialogs.py`

Other application changes:

- Modified `src/pokemon_ev_tracker/config/settings.py` for the shell route preference.
- Created `src/pokemon_ev_tracker/assets/ui/terminal_mark.svg` from the Figma asset.
- Modified `pyproject.toml` to package that local SVG.
- Created this audit and handoff document, `docs/ui_refactor_audit.md`.

Created tests: `tests/test_tracker_workspaces.py`,
`tests/test_nuzlocke_workspaces.py`, `tests/test_diagnostics_view.py`.

Updated tests: `tests/test_settings.py`, `tests/test_compact_mode.py`,
`tests/test_party_stats_view.py`, `tests/test_bizhawk_opponent.py`,
`tests/test_opponent_panel.py`, `tests/test_pc_storage_acquisition_ui.py`.

No files under core, data_sources, games, or transport were modified.

## Designed but not yet wired / requires feature validation

- Friendship target configuration, target-reached automation, time remaining,
  session friendship gain, and accumulated reversal counts have no existing
  tested source. A controller `direction_changed` flag is not a session counter.
  No new ETA algorithm or movement/command behavior belongs in this refactor.
- Rich move type/category, generation-correct power/accuracy, current/max PP,
  PP Ups, and priority need an independently validated data source. The UI
  must not invent metadata or introduce a new reader/database to fill them.
- Independent allowed-focus checkboxes do not have an existing store. The wired
  recommendation adapter compares already-stored numeric EV targets with existing
  base species EV yields. Separate focus persistence, actual reward prediction,
  and richer recommendation semantics remain unimplemented.
- Rich fight readiness beyond the existing over-cap check needs validation.
  Per-fight notes are not stored; existing notes belong to the run.
- Party wipe detection and automatic run-ending/restarting do not exist.
  Individual death detection must not be repurposed as a new wipe detector.
- Won/Wiped/Abandoned outcomes are not supported by the existing `completed`
  flag and must not cause a persistence migration during this UI task.
- No-run automatic Nuzlocke creation or gameplay-start guessing does not exist.
- Additional games, game RAM profiles, or game-specific runtime behavior are
  outside this refactor.
- Command readiness must remain unknown until existing acknowledgements provide
  evidence. A live heartbeat does not prove fresh party RAM, validated PC layout,
  command-channel readiness, or that any automation is safe to run.
- Unavailable diagnostic fields and fabricated recovery event timelines must
  remain empty. Status shows real received messages; it does not synthesize
  successful scans, cache validation, acquisition decisions, or health claims.

## Test contracts affected by the new information architecture

Several old tests explicitly expected six expanded party cards/accordions,
three-column card geometry, and hidden Nuzlocke tabs in compact mode. Those
presentation assertions describe the layout being replaced. Their replacements
should verify one selected workspace, six selectors, direct compact view
switching, resizing/scrolling, and preserved data behavior.

Behavior assertions remain valuable: reorder-attached stats, nature highlights,
in-place friendship/move updates, fixed status-footer warnings, history retention,
tracker-view persistence, always-on-top and geometry restoration. Friendship
acknowledgement/deadline and safe-release tests, plus PC baseline/session/recovery
tests, protect runtime paths that must not change for the UI.

## Final verification

- Full suite: **432 passed in 58.45 seconds** using
  `.venv\Scripts\python.exe -m pytest -q --basetemp=.test-scratch/terminal-release`.
- `.venv\Scripts\python.exe -m ruff check src tests`: **all checks passed**.
- `git diff --check`: clean.
- Native Qt offscreen renders inspected for disconnected/empty states, decoded
  fixture party data, normal Training/Stats/Nuzlocke/Diagnostics, narrow layouts,
  and all three compact screens. Fixture values exist only in test/scratch data;
  production views have no sample Pokémon or invented telemetry.
- Targeted review confirmed that extracted diagnostic callbacks are retained.
  Friendship commands/acknowledgements/safe release, PC resolver/acquisition,
  and death processing methods remain structurally unchanged.
- Additional regression checks cover all four routes across restart, compact
  Nuzlocke restart/expansion, malformed preferences, selector stability during
  polling/reorder/disconnect, double-battle rendering, and narrow compact layouts.

These are automated and offscreen checks. No live BizHawk gameplay session was
performed; use the following checklist before relying on the refactor in a run.

## BizHawk smoke checks

1. Connect the existing Platinum Lua script; inspect heartbeat, party freshness,
   command status, PC layout, and box count independently in Diagnostics.
2. Reorder the party while Training or Party Stats is selected. Confirm identity,
   sprites, stats, and PID-owned EV targets remain attached correctly.
3. Enter single and double battles; verify both active opponents and base yields.
   Gain EVs and confirm history and target progress update without losing records.
4. Start Friendship Walk on each axis. Confirm matching acknowledgement, wall
   reversal, battle/manual-input pauses, Ctrl+Shift+W stop, and released inputs.
5. Disconnect/reconnect and load a save state. Check freshness warnings and that
   existing baseline protections prevent false acquisition/death events.
6. Catch into the party and into a full-party PC slot. Confirm occupied-location
   resolution, event deduplication, PC rediscovery, and recovery controls work.
7. Confirm death candidates through the existing flow, update a cap, manage run
   notes, switch runs, and restart the app to verify persisted data/preferences.
8. Switch compact Training/Party Stats/Nuzlocke directly. Restore normal mode,
   then inspect Advanced tools and legacy experiments for availability.
