# Architecture

The Companion redesign retains these backend boundaries and adds responsive Qt projections. See [the redesign handoff](companion-redesign.md) for the new view modules, persistence decisions, and verification limits.

```text
EmuHawk Lua reader
    -> localhost TCP NDJSON or system-temp file fallback
    -> BizHawk data source
    -> Pokémon Platinum memory profile and decoder
    -> shared EV target/change models
    -> PySide6 Tracker and RAM Debug UI
```

## Project Layout

```text
bizhawk/ev_tracker.lua
src/pokemon_ev_tracker/
  config/                 User settings and platform data paths
  core/                   PID-keyed targets and EV change tracking
  data_sources/           Emulator transport adapter
  games/platinum/         Platinum RAM map, decoder, and game data
  pokemon/gen4/           Reusable Generation IV structure and crypto
  transport/              Local TCP and fallback-file IPC
  ui/                     Tracker, diagnostics, and sprite loaders
  assets/sprites/         Installed sprite and item resources
tests/                    RAM, model, resource, and Qt tests
```

## Boundaries

- `transport` owns emulator communication and timestamps incoming messages. Existing
  PC diagnostic workflows also import Platinum analysis and hold game-specific
  constants; the [refactor roadmap](refactor-roadmap.md) records this coupling and
  the planned provider-injected boundary.
- `data_sources` joins transport messages to a selected game profile.
  `BizHawkRamDataSource` retains one party decoding input/result and one display
  projection. Content, provider and stream changes invalidate reuse; display
  fallback expires using the server's stale-sample window. Complete snapshots,
  freshness checks, transport status and event observers are never cached.
- `games/platinum` owns Platinum-specific addresses, item IDs, species names, and party interpretation.
- `pokemon/gen4` owns reusable Generation IV encryption, block ordering, checksums, and party-record decoding.
- `core` owns game-independent EV targets and EV-change tracking.
- `ui` presents decoded state and manages sprite/movie lifetimes; it does not read emulator memory.

`ui/party_presentation.py` projects decoded party metadata, stats, validity messages
and moves for the hidden legacy cards. Its frozen display values and per-card move
keys avoid equivalent Qt updates and repeated move projection. MainWindow retains
application composition, validation, observers, signal wiring and the compatibility
callbacks. This cache contains derived presentation only; freshness, acknowledgements,
run decisions, PID-owned targets and preferences are evaluated independently.

`ui/battle_recommendations.py` owns one latest-input recommendation projection per
window. MainWindow resolves current validated opponents through the provider and
reads current party preferences; both normal and compact renderers consume the
same member results. Selection controls rendering, while numeric targets remain
independent of preference-based classification. Polling publishes recommendations
once after the party views and current opponents have been refreshed.

`ui/diagnostics_presentation.py` formats already-observed coordinate/pointer and
battle data without Qt or live-state dependencies. MainWindow still gathers and
validates observations, computes command/safety telemetry, resolves acquisition
context and recovery actions, and updates widgets even when diagnostics are hidden.

`ui/pokemon_diagnostics.py` formats the existing decoded party/boxed models. A
small frozen observation supplies acquisition results and item-resource facts
resolved by MainWindow. Party lines stream into its existing text collector;
boxed records use caller-computed addresses. `ui/pc_inspection_presentation.py`
formats received anchor-inspection results against a supplied species mapping.
Neither module reads RAM, decodes records, checks files, dispatches commands or
changes observer/baseline state. MainWindow's diagnostic callbacks remain the
owners of live context, scheduling, freshness, safety and widget updates.

`core/diagnostic_health.py` defines frozen connection, party-read, command-channel
and PC-monitoring observations. It observes snapshot facts without validating RAM,
parsing emulator protocol or controlling recovery. Raw validity remains distinct
from display fallback validity. MainWindow builds one current health projection
from runtime flags, resolver/session state and the existing snapshot; it does not
read status labels or button state back into that projection.

`ui/diagnostic_health_presentation.py` formats these facts. DiagnosticsView consumes
typed health via `refresh_health`; the compact/Nuzlocke/system status mirrors use
the same PC observation. The isolated `diagnostic_health_compatibility.py` adapter
retains older snapshot-plus-presentation-mapping previews. Its string-derived
claims are display-only and never feed application safety or runtime controllers.

`core/nuzlocke/party_lifecycle.py` owns raw-party HP baselines, wipe arming and
session prompt suppression through `PartyLifecycleCoordinator`. MainWindow passes
current raw records/payload and run identity into `process`, together with narrowly
scoped synchronous death/wipe/prompt handlers. The coordinator validates and maps
samples once, sequences existing observers, and retains only their internal state.
`run_ended` resets the transition observers and suppresses session prompting after
an accepted wipe. Dialogs, persistence, notification presentation, acquisition
reconciliation and scheduling remain with their existing owners. Shared unchanged
raw validation/frame contracts live in `core/nuzlocke/party_snapshot.py`.

See [the first engineering audit and measurements](refactor-roadmap.md) for the
baseline, bounded initial implementation, regression gates and later phases.

Add another game under `src/pokemon_ev_tracker/games/<game>/`, implement its explicit memory profile and decoder, and supply sanitized fixtures. Keep emulator transport and shared tracker models independent of game-specific offsets. The Lua reader remains at `bizhawk/ev_tracker.lua`; it enumerates EmuHawk memory domains at runtime and currently reports Pokémon Platinum RAM.
