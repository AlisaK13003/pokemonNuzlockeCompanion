# Pokémon Companion redesign handoff

## Implemented views

The PySide6 application now follows the Figma Make reference's dark panels, pixel headings, horizontal navigation, silver/blue/lavender Platinum accents, and consistent semantic colors. Bundled Pixelify Sans and Manrope fonts include their OFL licenses. Qt layouts adapt the browser reference to a resizable desktop window.

- Training: vertical live roster, identity-selected focus, direct persistent multi-stat EV preferences, single/double battle yield, per-member recommendations, relative-time EV history, and a separate Friendship Walk card.
- Inspection: expandable Party Stats and Box cards with padded Pokémon/item sprites, nature, ability, friendship, and six quality-colored IV bars. No moves or battle-stat table.
- Compact: always-on-top, Tracker-only battle HUD with equal-width party tiles, wrapping grids, no internal scrolling, and geometry restoration across expansion/reconnection.
- Nuzlocke: cap/fight hero, four metrics, three-node timeline, party readiness, complete profile-driven responsive location ledger, existing warnings/events/notes, Run Library, Create and Finish flows.
- Settings: persisted window, Tracker, automation, notification, recovery, and developer preferences connected to actual existing behavior.
- Connection/Diagnostics: connection hero, four health cards, sprite-based two-column party snapshot, and actionable recovery events. The header switch opens existing advanced tools when enabled in Settings; healthy status requires verified commands and current party/PC monitoring.

## Architecture and persistence

The existing path remains Lua → transport/data source → game provider and decoder → domain services/observers → Qt views. `MainWindow` binds signals and projects snapshots; UI widgets do not resolve RAM addresses or decide acquisition confidence. Existing `NuzlockeStore`, `EVTrainingPreferenceStore`, and `AppSettings` remain the persistence APIs. Safety behavior in acquisition/death/wipe detection, PC discovery, reconnect baselines, and Friendship Walk is retained.

The Platinum provider owns its visual skin, location area labels, and boxed inspection projection. The shared Gen IV boxed decoder adds factual fields from the existing decrypted payload without replacing identity/checksum validation. Unsupported capabilities remain gated.

Settings are additive with defaults for old files. No run-schema migration or user-data reset is required. Existing legacy encounter statuses remain readable and are not silently rewritten; new ledger filters use Not encountered, Caught, Failed, and Dead. Reset Companion Preferences preserves runs and Pokémon EV preferences.

## Principal files

| Responsibility | Files under `src/pokemon_ev_tracker/` |
| --- | --- |
| Shell and runtime binding | `ui/app_shell.py`, `ui/main_window.py`, `ui/backend_status.py`, `ui/icons.py` |
| Visual system | `ui/theme.py`, `assets/ui/terminal_mark.svg`, `assets/fonts/` |
| Training and automation presentation | `ui/tracker_views.py`, `ui/training_focus_card.py`, `ui/training_panels.py`, `ui/party_selector.py`, `ui/opponent_panel.py`, `ui/ev_change_log.py`, `ui/friendship_panel.py` |
| Inspection and compact | `ui/inspection_views.py`, `ui/compact_training.py` |
| Settings and diagnostics | `config/settings.py`, `ui/preferences_view.py`, `ui/diagnostics_view.py` |
| Run dashboard and dialogs | `ui/run_design.py`, `ui/run_library_cards.py`, `ui/nuzlocke_view.py`, `ui/nuzlocke_workspace.py`, `ui/nuzlocke_dialogs.py` |
| Game-owned metadata | `games/platinum/provider.py`, `games/platinum/nuzlocke.py`, `core/nuzlocke/models.py`, `pokemon/gen4/structure.py` |

Packaging and documentation changes: `pyproject.toml`, `README.md`, `THIRD_PARTY_NOTICES.md`, this handoff, and `architecture.md`. Regression coverage includes `tests/test_companion_redesign.py` and updated existing Qt/settings tests. Backend tests remain in the suite.

## Practical limits and setup

- The Make preview was visually accessible, but the Figma source resource endpoint was unavailable. The implementation was compared with the rendered reference; it does not claim a pixel-exact browser rendering in Qt.
- Automatic reconnect is transport-managed. Settings documents it rather than exposing an unwired switch.
- Box records contain no live level or HP. Inspection explicitly shows unavailable values instead of substituting met level.
- Area type describes profile geography; the ledger does not calculate encounters remaining or infer encounter methods.
- Validation uses isolated decoded fixtures, not a running BizHawk session. Final emulator verification requires opening BizHawk with Platinum and loading `bizhawk/ev_tracker.lua` via Tools → Lua Console → Script → Open Script. Keep the Lua Console open. No extra migration is needed.

## Verification

The prescribed commands are `python -m pytest -q`, `python -m ruff check .`, and `python -m build` using the repository virtual environment. Offscreen Qt renders cover Training, Stats, Box, Nuzlocke, Settings, and Diagnostics at 1440, 900, and 620 pixels; compact at 1120, 860, 500, and 320 pixels; disconnected and finish states are also inspected. Fixtures and preview runs exist only in ignored scratch output, never in production runtime.

Validation result: 572 tests passed; Ruff and `git diff --check` passed. The isolated package build produced both a wheel and source archive. A focused Qt regression run also verifies the final notification presentation polish.

No commit or push was performed.
