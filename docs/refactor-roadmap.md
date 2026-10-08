# First engineering refactor: audit and roadmap

Audit date: 2026-10-08. Scope: the existing working tree, including the recent UI,
starter and backup changes. This is a refactor, not a new design or game integration.
Only phase A below is authorized for this implementation pass.

## Architecture and execution paths

`main.main` loads settings, creates QApplication/MainWindow, connects backup shutdown,
and enters the Qt event loop. MainWindow resolves a `GameProvider`, wraps the source
in `GameSession`, composes views/observers, starts the source, starts the 250 ms UI
timer, and starts the independent 300,000 ms backup timer. Its closeEvent stops
backups, polling and walking, releases emulator input, stops the source, and saves
window preferences. Only Platinum is registered; unsupported capabilities stay off.

Lua's single distributed reader emits NDJSON via localhost TCP or a temporary-file
fallback. Party, PC and heartbeat emission intervals are 30, 60 and 120 emulator
frames; these are frame intervals, not wall-clock guarantees. The final loop services
walk commands and advances chunked diagnostic jobs before `emu.frameadvance()`.
Do not split the Lua runtime or change protocol/interval/ack semantics in this pass.

`BizHawkDebugServer._record_line` parses/routes messages. TCP and file readers run
on background threads. Latest heartbeat/party/PC messages use `_lock`; discovery,
save diagnostics, commands and SRAM have additional locks. Discovery finalization
is dispatched to an analysis worker by `_begin_pc_discovery_analysis_locked`.
`BizHawkRamDataSource.snapshot` collects that state, decodes through the provider's
profile, stabilizes display samples by PID, and projects battle/PC changes. MainWindow
then validates snapshots, reconciles events, runs observers, coordinates recovery/
walking/notifications, and updates widgets. Business logic belongs to `core`;
checksums/structures to `pokemon/gen4`; Platinum rules to `games/platinum`.

Strengths worth preserving:

- Explicit provider capabilities and a single supported game, no synthetic live data.
- Checksum and structural validation, PID-matching display fallback, stream baselines,
  conservative acquisition/death/wipe handling, and command acknowledgement guards.
- Existing immutable decoded records, cached battle/PC decoding, sprite caches,
  signature-based row reuse, and distinct domain stores and UI modules.
- Nuzlocke JSON preserves unknown fields, uses replacement of a temporary file,
  and rolls back domain changes when saving fails. Backups are independent, copy-only,
  verified, scheduled on their own timer, and run on a worker.
- Broad regression coverage: protocol/fallback/retries, Lua mock execution, game
  capabilities, stale RAM, target identity, selected identity, run history and widgets.

## Confirmed issues and distinctions

References are function names plus audit line numbers (which change during extraction).

| Evidence / reference | Assessment and next step |
| --- | --- |
| `ui/main_window.py` (5,464 lines, 118 functions), `_refresh_ram_backend_debug` (1364, 485 lines) | Coordination, diagnostic formatting, recovery state, walking and presentation share one owner. Size is an architectural concern, not evidence that every method is slow. |
| `_refresh_tracker_party` (1850), `_refresh_tracker_stats_metadata` (2009), `_refresh_party_workspaces` (550) | Six hidden PartyCard adapters still format/update stat fields every poll. Move slots resolve again even when decoded IDs/PP/PP Ups are unchanged. The first slice extracts these responsibilities without deleting the compatibility cards. |
| `_refresh_ram_backend_debug` and `_refresh_party_workspaces` | Battle recommendation work runs twice per full poll (60 calls over 30 fixture polls). Consolidate later, after characterizing selection and direct-edit paths; it is not blindly removed now. |
| `data_sources/bizhawk.py::_decode_party_payload` (699), `_stabilize_display_party` (595) | Party bytes are decoded and display records replaced on repeat polls of the same message. Battle/PC decoding already uses message-identity caches. A subsequent slice should cache only decoding/stabilization, keeping freshness and event checks live. |
| `transport/bizhawk_server.py` (5,090 lines, 86 functions) | Transport currently imports Platinum discovery/SRAM/parser services and owns game-related offsets. The earlier documentation's “no game offsets” description is no longer literally true. Separate protocol routing from game analysis through injected services, rather than add more offsets to transport. |
| `_record_pc_discovery_event` (3350), `_record_pc_pokemon_search_event` (4427) | Large protocol-specific state machines and shared dictionaries. Extract one workflow at a time with golden protocol traces; preserve timeout, retransmission, command order and fallback contracts. |
| `BizHawkDebugServer.stop` (187) | Closes active sockets and sets stop event, but does not join all receiver/analysis threads. Restart/shutdown overlap warrants explicit lifecycle tests before changing this. |
| `games/platinum/pc_discovery.py::_process_broad_records` (323) | Copies a 136-byte record at every four-byte alignment before rejection. A 4 MiB deterministic scan is measurable; inspect allocation reductions separately without relaxing any checks. |
| `NuzlockeStore.save` (607), MainWindow event reconciliation | Full run history is serialized synchronously when mutations occur. Many no-op paths already avoid saves. Measure large histories and write counts before proposing batching; async persistence must preserve rollback/error and event ordering. |
| `_refresh_ram_party_debug` (2511), `_refresh_pc_storage_discovery_results` (4324), DiagnosticsView | Detailed diagnostics remain a main-thread responsibility even when the visible route is Training. Extract pure text formatters, then evaluate input-key guards; do not hide safety telemetry or skip observers with route-based polling. |
| `ui/party_selector.py::PokemonSprite`, MainWindow legacy sprite methods | Both visible and compatibility sprites have movie lifecycles. Existing caches help; hidden animated adapters deserve lifecycle measurement before removal. |
| `core/save_backups.py::backup_now`, `stop` | Background work protects UI responsiveness; shutdown joins the worker. Cancellation/slow network paths need lifecycle characterization. Keep five-minute scheduling and verified snapshot publication intact. |
| Legacy runtime-relative PC hypotheses, save/SRAM diagnostic commands, hidden target adapters | Compatibility-sensitive, not proven dead code. Existing tests still consume them. They are retained until an explicit equivalent contract replaces them. |

## Baseline and measurements

Before production edits: full suite **740 passed, 1 failed in 124.22 s**. The failure
was `test_fight_card_no_run_and_all_complete`: Windows `PermissionError` replacing
an isolated `runs.json.tmp`. Its unchanged focused rerun passed in 0.42 s. Record
this as a baseline intermittent filesystem failure, not a refactor regression.
Ruff was clean. Six new presentation characterization tests pass against the old
implementation before extraction.

`benchmarks/runtime_fixtures.py` uses deterministic decoded test records, isolated
scratch stores, disabled real backup startup and mocked settings saving. No live
transport or emulator is started. It measures warm repeated synchronous method
calls, not end-user frame rate. Run from the repository root:

```powershell
.venv/Scripts/python.exe benchmarks/runtime_fixtures.py --output .test-scratch/refactor-before.json
```

Host: Windows 11 / Python 3.12.10 / Qt offscreen. Five rounds of 30 iterations,
three warmups; PC scan: three single-iteration rounds after three warmups. Traced
PC memory is measured separately because tracing distorts timing. Scratch files
are retained under ignored `.test-scratch`, consistent with this project's tests.
The party fixture has six valid members with empty move slots. It measures redundant
slot projection/dispatch, not a reduction in expensive nonempty catalog queries.

| Baseline metric | Result |
| --- | ---: |
| MainWindow fixture construction + first show (single observation; not a trend) | 790.26 ms |
| Six-member party adapter + downstream views, unchanged | 4.6675 ms median |
| Full unchanged UI poll | 6.5249 ms median |
| Six-party decode, same input message | 1.5306 ms median |
| Transport JSON party message routing | 0.0091 ms median |
| Broad 4 MiB scan, deterministic nonzero filler + a checksummed occupied record | 1,228.77 ms median |
| Broad scan peak Python-traced allocations (input buffer allocated before tracing) | 4,322,888 bytes |
| `resolve_move` / `refresh_style` calls in 30 full polls | 720 / 7,050 |

These cases deliberately exclude network latency, emulator frame timing, large
live boxes/run archives, dialogs, painting latency, full cold-process import time,
SaveRAM availability and real disk contention. The UI fixture has no active run
or command traffic. It exposes redundant presentation work; it does not represent
worst-case gameplay. No live BizHawk session was used. Do not attribute unrelated
startup/transport/PC variations to the presentation extraction.

## Prioritized independently reviewable phases

| Phase | Scope / intended boundary | Impact, risk, effort |
| --- | --- | --- |
| A — this pass | Pure legacy party metadata/stat/move projection in `ui/party_presentation.py`; skip equivalent stat/metadata updates and cache one derived move projection per existing card. Keep card dictionaries and MainWindow callback signatures. | Removes repeated hidden Qt work and clarifies ownership; low risk; small. |
| B | Identity-keyed decoded party projection in the data-source adapter. Freshness, elapsed time, raw/current vs display state and observers remain evaluated each poll. | High-value ~1.5 ms fixture decode cost; medium risk around stale fallback/stream reset; small–medium. |
| C | Snapshot-to-UI orchestration controller with explicit projection inputs; consolidate repeated battle recommendations; pure diagnostics formatting. | High maintainability/responsiveness value; medium risk, selection and direct-edit regression tests required; medium. |
| D | Extract PC discovery coordinator and parsing helpers from server; inject Platinum analysis through provider boundary; characterize shutdown/join and cancellation. | High architectural value; high protocol/lifecycle risk; multiple medium slices. |
| E | Allocation-focused PC scan optimization after before/after profiling (buffer views, early rejection candidates). | Measurable throughput opportunity; high checksum/structure risk; medium. |
| F | Store serialization/write-count instrumentation for large archives, then only demonstrated no-op reductions. | Variable disk latency impact; medium–high persistence risk; medium. |
| G | Lua profiling in BizHawk: frame budgets, ROM/session invalidation, serialization and scans. Preserve one-file loading workflow, NLua limits, bounded discovery advancement. | Potential emulator responsiveness value; requires live validation; medium–high. |

## Regression gates and implementation limits

Phase A characterization covers stat text/roles, invalid checksum masking, distinct
stale messages, compact metadata and in-place widget identity. Add tests for move
slot positions/unknown IDs/PP and PP-Up changes, invalidation by identity/catalog/
mode, disconnect/reconnect and repeated equivalent inputs. Never cache or bypass
acquisition, death, wipe, elapsed-time freshness, command acknowledgement, target
store or run selection. Leave EV target formatting and sprite lifecycle unchanged.

Before each later slice, require focused protocol/model/Qt tests plus the full suite
and Ruff, comparable fixture measurements and relevant architecture notes. Do not
equate fewer lines or fewer function calls with improved live gameplay. The first
pass introduces no dependencies, transport/Lua changes, background threads, domain
schema changes or new supported game. Do not proceed automatically to phase B.

Live smoke gates before larger changes: reorder and switch party identities; brief
invalid/checksum reads; load state/ROM-session changes; capture into party/full PC;
single/double battles and PP/EV gains; fresh/stale commands and emergency release;
run switch/history/shiny/death/wipe review; compact/full transitions; TCP/file
fallback and discovery retries; backup rollover and recovery using copied saves.

## Phase A implementation and comparison

Implemented after writing this roadmap and passing the six characterization cases:

- `ui/party_presentation.py`: frozen stat/metadata values, pure projection functions,
  legacy widget application and bounded move-display reuse. Each existing card owns
  at most one stat value, one metadata value and one move input/result pair. No global
  cache or additional domain state is introduced.
- MainWindow delegates its old metadata callback and the stat/checksum block to the
  module, and calls `refresh_move_displays` from `_refresh_party_workspaces`. Existing
  callback signatures/card fields remain; preferences, visible views, observers and
  clock-based checks still run. EV target formatting, EV bars and sprite ownership
  are deliberately untouched, preventing stale targets or changed movie lifetimes.
- Move keys include stable identity, the first four IDs, current PP, PP Ups and the
  provider catalog callable. Invalid/missing records clear the cached move result.
  Catalogs are immutable for a session; replacing the callable invalidates the cache.
  Stat/metadata equivalence compares the actual displayed values rather than a raw
  payload object, so fresh decoded instances do not force identical hidden updates.
- `tests/test_party_presentation.py`: six prior-behavior cases plus three tests for
  equivalent projections, field/mode changes, slot/identity/catalog invalidation,
  missing PP arrays, invalid data and absent catalog behavior.
- `benchmarks/runtime_fixtures.py` and [raw results](../benchmarks/results/party-presentation.json)
  make the before/after comparison repeatable without launching BizHawk or writing
  user settings/saves. Benchmark outputs are opt-in, not production instrumentation.

| Metric | Before | After | Interpretation |
| --- | ---: | ---: | --- |
| Unchanged party adapter + downstream views, median | 4.6675 ms | 4.1216 ms | 11.7% lower in this fixture; limited to this path. |
| Full unchanged UI poll, median | 6.5249 ms | 6.2991 ms | Small 3.5% difference; round ranges overlap, so no broad speed claim. |
| Move resolutions in 30 warmed polls | 720 | 0 | Deterministic elimination of repeated equivalent slot projection. |
| Style-refresh calls in 30 polls | 7,050 | 4,710 | 33.2% fewer calls; the style helper already guarded actual repolishing. |
| Fixture construction/first show, one observation | 790.26 ms | 797.69 ms | No startup improvement claimed. |
| Party decode | 1.5306 ms | 1.4913 ms | Unchanged code; sampling variation. |
| Transport party routing | 0.0091 ms | 0.0076 ms | Unchanged code; sampling variation. |
| Broad PC scan | 1,228.77 ms | 1,180.93 ms | Unchanged code; sampling variation. |
| PC scan traced peak bytes | 4,322,888 | 4,322,888 | No allocation improvement claimed. |

The before and after runs used the same script, interpreter, fixtures, timing loops
and host, sequentially without overlapping test/build jobs. Other system load is
uncontrolled. Changed-input timing, live rendering, worst-case diagnostics and real
emulator/network behavior are not benchmarked. Functional tests protect changed
inputs; these timing results do not imply zero cost or identical live frame rates.

Recommended next slice is phase B: cache decoded/stabilized party projections by
received message/stream identity, with explicit tests for freshness expiration,
pointer/run/core changes, invalid-to-valid transitions, PID reorder and last-good
sample lifetime. Do not cache the entire data-source snapshot or skip event/command
processing. Thread lifecycle and transport extraction remain separate higher-risk work.

## Final verification

- Pre-extraction characterization: 6 passed. Post-extraction characterization/cache
  coverage: 9 passed. Focused party/training/workspace/compact regression run:
  65 passed in 24.74 s (before adding the three final cache cases).
- Final full suite: **750 passed in 154.15 s**. The baseline filesystem failure
  did not repeat; its separate unchanged rerun also passed. Test-suite duration is
  not an application performance measurement.
- `python -m ruff check .`: clean. `git diff --check`: clean.
- `python -m build --no-isolation`: source archive and wheel built successfully.
  The build required access to Python's temporary build directories in this sandbox.
- No live BizHawk session or real save/history/settings writes were used for testing.
  Lua, transport, data sources, game decoding, stores and backup behavior were not
  edited in this slice. No commits or pushes were made.

Changed responsibilities/files for this pass: MainWindow presentation delegation;
new `ui/party_presentation.py`; `tests/test_party_presentation.py`; opt-in benchmark
script and recorded JSON; this roadmap; `architecture.md`, `ui_refactor_audit.md`
and the README development link. Existing unrelated working-tree edits were retained.

## Phase B: party decoding and display reuse

Implemented only Phase B on October 8, 2026. Phase A's final 750-test run is the
prior baseline. No Phase C production changes are included.

### Lifecycle traced before implementation

The Lua reader emits a new `party_memory` dictionary with a receipt timestamp and
transport source. Its `run_id` changes on detected ROM changes, core changes and
frame rollback (`bizhawk/ev_tracker.lua` session loop). The transport retains the
latest message under its lock. The heartbeat wrapper is frozen, but its dictionary
is mutable; object identity alone is therefore insufficient for safe party reuse.

`BizHawkRamDataSource.snapshot` reads all current transport/diagnostic messages,
decodes the primary party and candidate blocks with the selected profile, stabilizes
display by PID, joins battle and PC projections, and evaluates freshness and RAM
age. MainWindow separately consumes raw `party_state` for validity and observations
and `display_party_state` for presentation. Acquisition/shiny reconciliation, HP and
wipe observers, EV tracking, run selection, friendship safety/acknowledgements,
PC monitoring/discovery and provider capability guards remain in their existing
polling paths. Reusing party records does not skip those paths.

Battle decoding used message-object identity; PC decoding and stabilization also
used object identity plus their scan-specific state. Party reuse instead compares
decoder inputs because receipt time, frame, walking acknowledgements and player
position can change independently of party bytes. PID or slot alone cannot describe
evolution, HP, PP, EV, friendship or ordering changes.

### Strategy and boundaries

- `data_sources/bizhawk.py::_decode_party_payload` retains one input/result pair.
  Inputs include primary hex/address/fallback count and ordered candidate
  hex/address/fallback counts. A small copied key detects in-place dictionary or
  candidate edits; immutable hex strings are shared. `_decode_party_inputs` holds
  the original candidate selection and RAM validation logic. Malformed candidate
  containers are treated as absent; malformed hex/counts still reach validation.
- `_prepare_party_context` invalidates decode, display and PID history on selected
  profile/decoder changes, transport source, run/Lua identity, optional ROM session,
  core, domain or party pointer changes, missing messages or decreasing frames.
  Changed raw bytes always decode again even when PID and slot remain unchanged.
  Stop and disconnected polling clear reuse. No transport, Lua or provider protocol
  was changed; ROM/load-state invalidation is limited to observable existing fields.
- `_stabilize_display_party` retains one raw-state/receipt-time display projection.
  Valid records already have clear stale flags and are reused directly. Invalid
  checksums still stay invalid in raw state while presentation may temporarily use
  a matching PID with its current slot. Invalid battle tails retain only sane prior
  level/HP/stats and never renew their lifetime.
- Expiration is evaluated on every call, before any display-cache hit. Last-good
  timestamps come from the received valid sample, not polling time. New receipt
  timestamps renew valid data without requiring another decode. Legacy direct
  callers without timestamps use first observation time. At most six last-good
  records/timestamps are retained, including during mixed invalid parties; old
  complete raw/display states are released after replacement.
- **Intentional correctness adjustment:** the prior stabilizer had no expiry and
  could display a last-good PID indefinitely. This phase implements the requested
  lifetime using `server.stale_after_seconds` (normally five seconds). Continued
  invalid reads do not refresh it. On expiry, unverified members are omitted and
  invalid battle values are withheld. Current/raw validity never becomes valid
  merely because a display fallback exists.
- Entire `DataSourceSnapshot` instances and their detail dictionaries remain new
  each poll. Clocks, connection checks, command fields, position, battle and PC
  processing remain live. No persistence formats, UI design, backups, dependencies,
  public interfaces or supported-game list changed.

### Comparable measurements

The extended `benchmarks/runtime_fixtures.py` runs the same six-member synthetic
party through actual data-source snapshots and full MainWindow polling. Its clock
and connection status are deterministic; it opens no sockets or emulator and writes
only isolated scratch files. Before/after runs were sequential, without concurrent
test/build jobs. The before run used the unchanged pre-Phase-B data-source source;
the Phase B source was restored immediately afterward. Other system load remains
uncontrolled. Warmups: three; timings: five rounds of 30 iterations. Pair timings
below include **both** snapshots, not one snapshot.

| Metric | Before | After |
| --- | ---: | ---: |
| Unchanged six-member data-source snapshot, median | 1.5496 ms | 0.0175 ms |
| Direct repeated party decode lookup, median | 1.4521 ms | 0.0016 ms |
| Changed valid snapshot pair, median | 3.1746 ms | 3.0634 ms |
| Invalid-to-valid snapshot pair, median | 3.1825 ms | 3.0957 ms |
| Full unchanged UI poll through RAM data source, median | 8.0118 ms | 6.0948 ms |
| Raw party decodes / Pokémon decodes, 30 warm unchanged polls | 30 / 180 | 0 / 0 |
| Dataclass replacements, 30 warm unchanged polls | 210 | 0 |
| Raw party decodes, 30 changed valid pairs | 60 | 59 |
| Raw party decodes, 30 invalid/valid pairs | 60 | 60 |
| Incremental retained / peak traced bytes, 300 warm unchanged polls | 36,061 / 140,182 | 280 / 2,880 |
| Cold source + first snapshot retained / peak traced bytes | 33,833 / 104,836 | 34,265 / 104,852 |
| Last-good entries after warm fixture | 6 | 6 |

Unchanged polls show 30 decode hits after warmup. The changed-pair trace starts with
one already-cached valid message, explaining 59 misses and one hit; invalid/valid
pairs have 60 misses. Retention checks also verify that replaced raw states can be
garbage-collected and mixed invalid parties cannot grow history beyond six entries.
Warm traced memory measures incremental allocations after warming, **not total
cache size**. The cold measurement includes the source and first projection and
shows a small added retention cost, rather than a zero-memory cache.

Changed/recovery timing ranges overlap; no meaningful changed-input speedup is
claimed. The unchanged snapshot and UI timings support reduced repeated work in
this fixture. They do not establish live rendering/FPS improvements, network
performance, large-history behavior or active-command responsiveness. PC scans and
transport parsing remain unchanged. Raw rounds and metadata are in
[party-snapshots.json](../benchmarks/results/party-snapshots.json).

### Regression coverage and verification

`tests/test_party_snapshot_reuse.py` adds 30 cases covering identical content in
distinct messages, same-PID evolution/stat/EV/HP/PP/PP-Up/friendship/level changes,
reorder, replacement, candidate mutation and addresses, malformed/partial data,
checksum failure/recovery, invalid battle tails, freshness and fallback expiration,
fresh receipt renewal, restart/run/core/domain/pointer/ROM/frame/source changes,
disconnect/reconnect, profile replacement and unsupported providers, raw/display
separation, bounded history and released previous results. Its UI integration test
verifies HP/acquisition/wipe observers still run, invalid raw input reaches them,
run switching is observed, and acknowledgements update even when party decoding
inputs are unchanged. Existing shiny, EV, friendship, PC and provider suites remain
part of the focused/full regression gates.

Focused initial run: 276 passed in 28.17 s (before three additional regression
cases); final new regression module: 30 passed. Final full suite:
**780 passed in 122.89 s**. Ruff and `git diff --check` passed. Source archive and
wheel built successfully with `python -m build --no-isolation`; the sandbox allowed
access to Python's temporary build directories for this check. Test duration is
not an application performance measurement.

Changed files/responsibilities: `data_sources/bizhawk.py` owns bounded projection
reuse and fallback lifetime; the new regression module protects invalidation and
observer inputs; the opt-in benchmark and JSON record fixture comparisons; this
roadmap and `architecture.md` document the boundary. Prior working-tree edits were
preserved. No commits or pushes were made.

Live BizHawk checks still needed: actual load-state/ROM changes and TCP/file
fallback; repeated partial RAM writes beyond the five-second window; reorder and
evolution during battles; real EV/HP/PP/friendship changes, shiny acquisition,
death/wipe confirmations, friendship stop/ack safety and PC discovery alongside
polling. A state load that changes neither reported session/frame/pointer nor raw
bytes cannot be identified separately under the existing protocol.

Phase C remains the appropriate next slice: separate runtime orchestration/pure
diagnostics and remove duplicate recommendation work with targeted behavior tests.
It is not implemented or started automatically in this pass.
