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

See [the first engineering audit and measurements](refactor-roadmap.md) for the
baseline, bounded initial implementation, regression gates and later phases.

Add another game under `src/pokemon_ev_tracker/games/<game>/`, implement its explicit memory profile and decoder, and supply sanitized fixtures. Keep emulator transport and shared tracker models independent of game-specific offsets. The Lua reader remains at `bizhawk/ev_tracker.lua`; it enumerates EmuHawk memory domains at runtime and currently reports Pokémon Platinum RAM.
