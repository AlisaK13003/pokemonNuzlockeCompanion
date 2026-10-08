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

## Phase C1: recommendation orchestration and diagnostic formatting

Implemented the first bounded portion of Phase C only. Phase B's data-source
freshness, decode/display reuse and fallback expiration are unchanged. No transport
refactor, input-safety change, persistence change, UI redesign or new feature is
included.

### Execution trace and dependencies

Before this slice the full RAM poll called `_refresh_tracker_party`, which called
`_refresh_party_workspaces`, which refreshed recommendations using retained yields
from the **previous** poll. Later in the same poll `_refresh_training_recommendations`
ran again with the current validated active enemies. Thus 30 polls invoked the
recommendation update 60 times. With six checksum-valid party members in an active
battle, that meant **360 individual core classifications**, not merely 60 calls.
On changed enemies the first and second updates could differ; this was not safe to
solve by simply deleting the final/current-opponent update.

The other workspace refresh callers are `_toggle_training_stat`,
`_edit_training_focus`, `_clear_training_focus`, `_edit_ev_target` and
`_clear_ev_target`, plus standalone `_refresh_tracker_party` callers. Those paths
must keep an immediate refresh using the most recently polled opponent yields.
`_select_party_slot` updates all workspace selectors and rendering. The selected
member affects which result compact presentation shows, not the all-member core
classification. `tracker_views.RecommendationPanel` and
`compact_training.CompactTrainingView` consume results; they do not invoke the
core calculator. `opponent_panel` displays current battlers and provider EV labels,
also without classifying training recommendations.

`core.ev_training.recommend_battle_evs` depends only on the allowed-stat preference
and the sum of positive opponent yields. Numeric targets, current EV totals,
level/HP, species and held items do not affect this existing function. Numeric
targets therefore continue updating their own presentation on direct edits without
invalidating an otherwise equivalent training classification. Single/double battles
retain Good/Avoid/Mixed/No Focus semantics and the existing idle/unavailable states.

Diagnostics inspection covered `diagnostics_view` (health widgets consuming current
snapshot and runtime strings), `diagnostics_tools` (widget construction/command
wiring), and MainWindow's detailed formatter. The first extracted section contains
only coordinates, pointer fields, raw battler fields and already-formatted provider
EV labels. It is independent of safety decisions and acquisition/store state.

### Bounded implementation

- `ui/battle_recommendations.py`: `TrainingMember` and
  `BattleRecommendationPresentation` are small frozen input/output values.
  `project_battle_recommendations` classifies eligible members and prepares combined
  yield values. `BattleRecommendationProjector` owns exactly one latest input/result
  pair; there are no per-view or per-opponent caches. Keys include member slot,
  stable identity and preference, immutable provider-resolved yields, capability
  availability and provider identity. Eligibility is rebuilt from current cards,
  so checksum-invalid or removed members invalidate the member set.
- MainWindow stays the composition root. The full poll refreshes party workspaces
  with recommendation publication deferred, then publishes once with the current
  validated enemies. Standalone party updates and direct focus/target edit handlers
  retain their default immediate publication. Provider yield lookups happen on each
  update; no enemy objects or receipt timestamps are retained by the projector.
  Stale snapshots expose no active enemies as before; disconnect supplies empty
  yields. Unsupported capabilities bypass lookup and publish unavailable results.
- Normal and compact consumers receive the same `training_recommendations` dict,
  retaining the existing compatibility attribute. Values come from the immutable
  shared projection. The dictionary is replaced only when that projection changes.
  Widgets still render each publication, so selection, names, evolution, enemy
  level changes and other presentation details update even without classification.
  A changed focus preference is read immediately by direct UI handlers and on the
  next poll for external store edits. Target edits never rewrite training focus.
- `ui/diagnostics_presentation.py`: `RamBattleDiagnosticObservation` carries a small
  snapshot-derived observation into `format_ram_battle_diagnostics`. The formatter
  has no Qt imports, clocks, validation, provider lookup, state mutation or commands.
  MainWindow supplies position and EV labels and retains live command telemetry,
  acquisition classification, missing-party handling and widget/scroll updates.
  Detailed formatting and safety telemetry remain active off the diagnostics page.
- Approximately 100 lines of pure formatting moved out of MainWindow. Remaining
  per-Pokémon/acquisition, PC discovery/SRAM, coordinate analysis and walk transport
  telemetry are deliberately not moved in this first bounded slice.

### Measurements

Extended the existing opt-in benchmark with active six-member double-battle polls,
predecoded single/double opponent transitions, calculation call counts and a
diagnostic harness with a no-op text sink. Each timing uses three warmups and five
rounds of 30 iterations. Changed-opponent timings include **two full polls** per
iteration and exclude fixture battler construction. The diagnostic measurement
includes the existing orchestration/telemetry formatter and the extracted text
section, with an empty party, four battler records and two active enemies; it is
not a whole-diagnostics-page or pure-helper-only measurement.

Before/after runs used the same fixtures, script, interpreter and host, sequentially
without overlapping test/build jobs. Other system load remains uncontrolled.
[Raw results](../benchmarks/results/ui-orchestration.json) retain all timing rounds
and diagnostic output; output matches the pre-extraction text exactly.

| Metric | Before | After |
| --- | ---: | ---: |
| Recommendation publications, 30 warm active polls | 60 | 30 |
| Individual core classifications, 30 warm active polls | 360 | 0 |
| RAM diagnostic updates, 30 warm active polls | 30 | 30 |
| Full unchanged active-battle UI poll, median | 6.6915 ms | 6.2312 ms |
| Changed single/double opponent pair, median | 23.6798 ms | 25.4989 ms |
| Diagnostic formatting harness, median | 0.0435 ms | 0.0429 ms |
| Full unchanged idle UI poll, median | 6.7605 ms | 6.0397 ms |
| Full unchanged poll through Phase B RAM source, median | 6.5047 ms | 7.0124 ms |

The reduction in publications/classifications is deterministic. After warming,
equivalent polls reuse the same six member results; changed yields/preferences/
membership still classify immediately. Timing ranges overlap, and some medians
are higher after the change. These measurements establish less redundant work,
**not a reliable broad responsiveness or changed-input speed improvement**.
Diagnostic extraction is justified by separation/testability, not by a timing gain.
Live FPS, active commands, large archives and real network/emulator timing were not
measured. No percentage speedup is claimed.

### Regression coverage, verification and next slice

`tests/test_ui_orchestration.py` adds 19 regression cases: all four classifications,
empty/zero/unknown yields and unsupported capabilities; bounded projection/provider
invalidation; one publication per poll and no repeated equivalent classifications;
external preference edits, direct focus/target edits, selection and reorder;
changed provider rewards and enemy levels; invalid data, stale-opponent clearing,
disconnect/reconnect; missing diagnostic values, zero/negative coordinates and
absent deltas; hidden-page telemetry and missing-party output. A captured
pre-extraction diagnostic fixture protects text, ordering and raw missing fields.
Existing single/double battle, modal edit/reorder, compact and game-provider tests
also passed. Phase B's 30 snapshot reuse tests remain in the full suite.

- Final focused run: **143 passed in 36.37 s**.
- Complete pytest run: **799 passed in 148.99 s**; this duration is a verification
  measurement, not a UI performance metric.
- Ruff and `git diff --check`: passed.
- Source archive and wheel: built successfully with `python -m build --no-isolation`.

Changed responsibilities are limited to MainWindow publication/delegation, the two
new UI modules, new regression tests/golden fixture, benchmark script/results and
architecture/roadmap documentation. No commit or push was requested or performed.

Live smoke testing remains necessary for real single/double battle transitions,
selection during roster changes, direct focus edits during polling, stale/recovered
RAM, compact/full rendering and walk acknowledgements with diagnostics hidden.
The projection does not add freshness policy: direct edits use the latest polled
validated opponent yields, as before; the data source/poll retains ownership of
elapsed-time freshness and enemy removal.

Recommended next slice is a bounded C2 extraction of per-Pokémon diagnostic text
after resolving acquisition observations in MainWindow, followed by a small typed
diagnostics-health observation replacing the current runtime string mapping.
Characterize store/run/observer and safety telemetry inputs first. Do not move
recovery decisions or transport operations, and do not start the transport refactor
automatically.

## Phase C2: per-Pokemon diagnostic presentation

Implemented C2 only, preserving the existing C1 work in the working tree. This
slice adds no feature, UI design, protocol, identity, RAM validation, persistence,
game integration, cache policy or backup/input-safety change.

### Inspection and characterization

Reviewed MainWindow's detailed party diagnostics, PC storage record text, anchor
inspection results and discovery sample helper; C1's diagnostic formatter;
`diagnostics_view` health rows and missing/stale handling; `diagnostics_tools` widget
construction and command wiring; provider acquisition/species/PC APIs; decoded
Gen IV party and box models; and existing nickname, party, PC and diagnostic tests.

The detailed party block contains PID/address/identity, checksum/permutation,
species/nickname bytes and offsets, nature/ability/friendship, held-item resource
facts, IVs/packed flags, acquisition metadata and raw party-tail stats. EVs are
withheld on failed checksum. Acquisition classification, suggested location and
already-observed status depend on the active provider/run/store and must remain
live. Item slug/path/existence checks inspect resources and are observations, not
pure formatting. PC record addresses depend on provider box layout. Anchor
inspection results are already-observed transport dictionaries containing identity
copies, spacing, references, movement summaries and raw byte dumps.

The detailed log has **no existing move/PP section**, and no new section was added.
Move slots/PP remain in the existing Phase A projection and tracker views. Likewise,
the detailed raw-record formatter does not add new stale markers: existing checksum
and tail validation text is preserved, while health rows and live transport telemetry
continue displaying snapshot freshness and withheld HP in their established paths.

Before extraction, `tests/test_pokemon_diagnostics.py` captured complete text in
`tests/fixtures/pokemon-diagnostics.json` and passed **21 characterization cases**.
Item asset facts were fixed for portability; no absolute user-specific paths,
actual save records or live emulator inputs are in the fixture. Cases include valid
and invalid party records, unknown metadata, missing addresses/nickname terminator,
stale display flags, invalid battle tails, empty/missing party and invalid counts;
valid/invalid/unknown boxed records and empty/missing storage; and valid/empty/
malformed/movement anchor results.

### Extracted responsibilities

- `ui/pokemon_diagnostics.py::format_party_pokemon_diagnostics` consumes the existing
  `PartyPokemon` model and a frozen `PartyPokemonDiagnosticObservation`. It streams
  unchanged text lines into the caller's existing collector, avoiding an extra
  complete intermediate list/tuple of party lines. Generation-specific offsets
  reuse the existing Gen IV constants; there is no duplicated decoder or validation.
- `format_boxed_change_diagnostics` and `format_boxed_record_diagnostics` consume
  existing `BoxedPokemon` models and return their original literal text tuples.
  The caller supplies the calculated address. They preserve the separate boxed
  contract: no party-tail HP/stats, with raw 136-byte record and stored/calculated
  checksum fields on the record detail path.
- `ui/pc_inspection_presentation.py::format_pc_pokemon_inspection` takes the received
  inspection mapping and provider-resolved species mapping, retaining the original
  list-of-lines return and text ordering. Parsing numeric labels and rendering hex
  dumps remain display operations; no Pokemon decoding, checksum decisions or
  movement baseline updates occur here. MainWindow keeps its compatibility wrapper.
- MainWindow still resolves acquisition candidates, classification/location and
  observed status on every refresh; gathers item-resource facts; computes boxed
  addresses from the provider; and owns active session/run coordination, raw
  snapshot observation, commands, scheduling, recovery/safety and widget/scroll
  updates. `diagnostics_view` health/state/widget logic and `diagnostics_tools`
  construction/command wiring are unchanged. No new mutable runtime cache/state
  was introduced.
- The discovery sample helper still invokes the provider's existing box decoder
  where that input is raw rather than a decoded model. It was deliberately not
  moved into presentation: interpreting raw records is not pure text formatting.
  C2 adds no decoding to any extracted path.

Source-span counts from the AST, including blank lines, illustrate responsibility
movement rather than a runtime performance or maintainability score:

| MainWindow source span | Before | After |
| --- | ---: | ---: |
| MainWindow class | 5,068 lines | 4,726 lines |
| `_refresh_ram_party_debug` | 216 lines | 90 lines |
| `_pc_storage_debug_text` | 238 lines | 206 lines |
| `_pc_pokemon_inspection_lines` | 186 lines | 2 lines |

### Measurements and verification

Extended `benchmarks/runtime_fixtures.py` with one decoded party member, one boxed
record/change and a supplied anchor inspection payload, all using no-op text sinks.
Fixture decoding occurs before the timed callbacks. Party timing includes live
acquisition preparation and fixed item-resource observations; boxed timing includes
the surrounding existing PC diagnostic formatter. These are not pure-helper-only
or live rendering measurements. Three warmups, five rounds of 30 iterations, same
host/interpreter/script/fixtures, sequential before/after runs without overlapping
tests/builds. Other system load remains uncontrolled. Formatting-only source style
cleanup afterward did not change the measured operations.

| Fixture median | Before | After |
| --- | ---: | ---: |
| Party diagnostic refresh | 0.0732 ms | 0.0816 ms |
| Boxed diagnostic refresh | 0.0142 ms | 0.0172 ms |
| Anchor inspection formatting | 0.0126 ms | 0.0119 ms |
| Full unchanged poll through Phase B RAM source | 6.6493 ms | 6.2772 ms |

Timing ranges overlap. Some medians increased; **no formatting speedup, allocation
improvement or broad responsiveness improvement is claimed**. This is a separation
and correctness refactor. Outputs consume existing models and bounded transient
context rather than copying/redecoding them or adding persistent caches. Existing
unchanged-text guards still prevent redundant editor updates. C1's 30 warm active
polls still produced 30 recommendation updates, zero repeated member classifications
and 30 RAM diagnostic updates in both runs. Raw rounds and metadata are in
[pokemon-diagnostics.json](../benchmarks/results/pokemon-diagnostics.json).

The final new module has **26 cases**: the 21 pre-extraction characterizations plus
pure formatter/no-decoding/input-preservation checks, unsupported acquisition
capabilities, anchor byte dump output/input preservation, equivalent editor text
and cursor preservation, and stale/invalid/empty health-row presentation. Checks
cover PID/identity, missing/unknown metadata, party/box differences, checksum failure,
stale flags, move slots/current PP and invalid-read masking, and live observed status
changes on the same decoded record. Existing Phase B, C1, nickname, provider, PC
and diagnostics tests remain part of the regression gates.

- Focused suite: **211 passed in 40.75 s**.
- Complete pytest suite: **825 passed in 149.08 s**. Test duration is not an
  application performance measurement.
- Ruff and `git diff --check`: passed.
- Source archive and wheel built successfully with `python -m build --no-isolation`.
  The packaging check used approved access to Python's temporary build directories.

Changed C2 responsibilities/files: MainWindow delegation and live observation
preparation; new `ui/pokemon_diagnostics.py` and
`ui/pc_inspection_presentation.py`; new characterization/regression module and JSON
fixture; benchmark callbacks/results; architecture and roadmap notes. Earlier C1
working-tree edits were preserved. No commit or push was requested or performed.

Remaining architectural concerns: health presentation still receives runtime strings
read from widgets; MainWindow retains substantial PC discovery/SRAM orchestration,
thread/scheduling and command/safety telemetry. The raw discovery-sample decoder
should not be hidden inside a pure presentation API. Large anchor hex dumps remain
potentially expensive, but changing their rendering/limits would require separate
profiling and behavior characterization.

Recommended next bounded slice: replace the diagnostics health runtime string
mapping with a small typed observation prepared from live state, preserving current
freshness/recovery decisions and command safety. Characterize raw/display/run and
missing capability cases before extraction. Do not proceed automatically to that
slice or to the transport refactor.

Live verification remains necessary for real PC inspection/movement experiments,
active-run acquisition labels during catches/run switches, stale/recovered RAM,
nickname/item resources and text scrolling alongside walk/PC commands. No live
BizHawk session or real user save data was used in this pass.

## Phase C3 audit (before implementation)

The selected boundary is diagnostic dashboard observation/presentation and its
related compact/system status mirrors. Existing controllers remain authoritative.

| Dependency | Origin and consumers | Planned boundary |
| --- | --- | --- |
| Six PC label texts read in the RAM poll | `_refresh_pc_storage_compact_status` writes status/layout/count/acquisition/event/recovery labels; the poll reads them for DiagnosticsView, Nuzlocke PC summary and shell status | Return an immutable PC observation from the same runtime inputs; format each consumer from it |
| Rediscover button `isEnabled()` read back | Compact PC renderer derives availability from status; poll mirrors it into diagnostic/compact buttons | Derive the same eligibility from the typed phase, retaining dispatch checks |
| PC status/layout words parsed by DiagnosticsView | Casefold `Monitoring`/`Resolved`; recovery inferred from substrings | Use explicit readiness/recovering fields; wording stays presentation only |
| `_walk_command_state` interpreted by DiagnosticsView | Runtime walk/ack handling assigns UNKNOWN, READY, waiting, unavailable, stale and timeout tokens; view infers readiness/problem from words | Normalize exact existing tokens once at the observation boundary; unknown/unsupported remain distinct |
| Party freshness and validity in DiagnosticsView | Data-source `party_payload_fresh`, raw/display party and warning/error fields | Observe raw validity and display validity separately; heartbeat alone cannot establish them |
| Connection/age/domain/session | Snapshot connected flag and timestamped payload/heartbeat; data source owns expiry; Lua/controller fields own session resets | Immutable observation from existing fields and explicit observation time; no new freshness policy |
| Recovery text and discovery state | Runtime lifecycle/progress/session results plus `pc_discovery_failure_message`; controls/manual retry render those facts | Keep recovery decisions in MainWindow; health readiness never inferred from the message text |
| Label `text()` equality checks | Party/PC editor/status update guards | Retain: these suppress redundant presentation writes, not safety decisions |
| User editable widget values | Anchor/nickname/species selections, scan offsets and paths | Retain as explicit user input, distinct from runtime health |
| Hero tooltip reads its own labels | DiagnosticsView combines already-formatted update/domain strings | Replace with the same formatted values directly |

The audit found no PC label-text dependency in acquisition validation, baseline
arming, rediscovery dispatch, command acknowledgement or walk safety decisions.
Those use snapshots, provider capabilities and runtime flags/identities. PC
button gating is a UI eligibility decision, not authority to bypass the existing
request checks. The health projection will not be consumed as a new safety policy.
Existing `DiagnosticsView.refresh(snapshot, mapping)` compatibility can remain as
an isolated presentation adapter; production will pass typed observations directly.

## Phase C3: typed diagnostic health

Implemented C3 only. The preceding table was written before changing the selected
health paths. No controller/transport extraction, game integration, protocol,
persistence, backup or UI design change is included. Earlier working-tree work
from C1/C2 was preserved.

### Models, ownership and dependency direction

- `core/diagnostic_health.py` adds frozen `ConnectionHealth`, `PartyReadHealth`,
  `CommandChannelHealth`, `PCMonitoringHealth` and `DiagnosticHealthObservation`.
  `HealthState` explicitly distinguishes healthy, unhealthy, unknown, stale and
  unsupported. `PCPhase` distinguishes connecting, monitoring, layout validation,
  baseline establishment, discovery, paused and rediscovery stages. These are
  observations, not a replacement controller or new safety policy.
- `observe_diagnostic_health` reads the existing data-source snapshot with an
  explicit observation time. It does not read RAM, decode records or infer
  freshness from a heartbeat: the data source's freshness flag remains authoritative.
  It records raw validity separately from stabilized display validity, captures
  existing stream identity fields and normalizes missing/malformed booleans,
  metadata and non-finite ages to unknown/unavailable observations. Records are
  referenced through their existing tuple, not redecoded or copied into a second
  authoritative party store.
- `observe_command_channel` normalizes exact existing runtime command-state tokens.
  READY/ACKNOWLEDGED/AVAILABLE are healthy claims; explicit stale/timeouts/unavailable
  are distinct from unknown and awaiting acknowledgements. Unsupported capability
  overrides the reported token. A recent heartbeat supplies no command readiness.
  The runtime `_walk_command_state` and all acknowledgement/time/safety handling
  remain in MainWindow; health normalization does not dispatch or permit movement.
- `_refresh_pc_storage_compact_status` now returns an ephemeral PC observation
  derived from the same connected/readiness flag, resolver lifecycle, storage data,
  discovery outcome and current session tags. MainWindow still decides recovery
  conditions, uses the existing failure-message helper, calculates phase/control
  eligibility and renders advanced context. Discovery completion is distinct from
  monitoring readiness; a completed scan does not establish a baseline by itself.
- `ui/diagnostic_health_presentation.py` provides pure field/hero and PC text
  projections. MainWindow renders PC diagnostics and uses the same observation
  for Nuzlocke summary, shell status and compact rediscovery eligibility.
  `DiagnosticsView.refresh_health` renders the complete typed observation. No new
  mutable health cache or shared authoritative state is retained between polls.
- `ui/diagnostic_health_compatibility.py` isolates the existing
  `DiagnosticsView.refresh(snapshot, mapping)` API for older callers/previews.
  That adapter still accepts claimed presentation strings to preserve its contract;
  **production polling does not use it**, and it cannot affect any acquisition,
  baseline, transport or walking guard. Legacy mapping field wording/events/progress
  behavior remains characterized separately from authoritative runtime observation.

### Dependencies removed and preserved

Removed all six PC label text readbacks and rediscover-button enabled-state
readbacks from the health/mirror pipeline. Removed production substring inference
of PC recovery/monitor/layout readiness in DiagnosticsView. Hero tooltips now use
the pure formatted update/domain values instead of reading their labels. Runtime
observation now flows into health and then into presentation; the health model
is never used to replace current input/acquisition/recovery safety decisions.

Remaining widget-derived values are intentional user inputs (species/nickname,
anchors, coordinates and filesystem selections), equality/style guards that avoid
redundant widget updates, and view event/scroll state. They are not health authority.
Controller lifecycle and command states still use their existing string tokens;
normalizing those into controller enums is future work, not silently done in this
slice. Legacy presentation-mapping parsing remains solely in the compatibility
adapter, with no application safety consumer.

Normal wording is preserved. Two inconsistent/malformed input cases no longer
claim healthy facts: a valid display fallback cannot certify invalid raw RAM when
the warning is absent, and missing session tags cannot label a layout resolved
merely because two absent IDs compare equal. Non-finite ages and malformed flags
do not invent freshness/readiness. These are diagnostic correctness adjustments;
the RAM/PC validation and acquisition/command/walking policies remain unchanged.

Source-span counts (AST spans including blank lines): MainWindow **4,726 → 4,735**
lines, reflecting explicit observation preparation; DiagnosticsView **432 → 347**
lines. Its old `refresh` mixed observation, inference and widget updates in 125
lines; the compatibility entry point is now five lines, with a 34-line typed
renderer. The objective is ownership and reliable facts, not fewer total lines.

### Characterization and verification

Before extraction, 11 dashboard wording/role fixtures were captured and passed for
valid/invalid/stale/disconnected RAM, unknown/pending/stale command state, absent PC,
discovery/baseline recovery and failure. They still pass through the preserved
legacy entry point. New typed tests cover raw/display validity separation, independent
heartbeat/command/PC readiness, enum states, immutable observations, unsupported
capabilities, missing/malformed fields and overflow/non-finite ages, stream restart,
PC pending/failed/validation/baseline/success/stale stages, recovery transitions,
session mismatch and missing tags, and consistent model/widget/control presentation.

An integration test makes PC label `.text()` and rediscover `.isEnabled()` access
raise during a real full UI poll. The typed dashboard/system mirrors and existing
acquisition observer still run. Another test puts misleading healthy words in a
recovery message and verifies that they do not change readiness or recovery state.
Phase B snapshot and C1 recommendation regressions remain included in verification.

Verification: all **867 tests passed in 159.55s**; the focused health, diagnostics,
PC acquisition, snapshot reuse, orchestration, provider and walking selection
passed **142 tests in 37.09s**. The new health module independently passed its
**42 characterization/regression cases in 6.40s**. Ruff and `git diff --check`
passed. `python -m build --no-isolation` successfully built the sdist and wheel.
An earlier full run encountered a Windows `WinError 5` replacing a temporary
preferences file during an unrelated compact-layout test's setup (866 passed,
one failed). The final isolated full rerun passed without production or test changes.
No application latency/FPS benchmark was performed in C3, and no performance
improvement is claimed. The new projection is built per poll; it does not change
the existing polling frequency, decoding cache lifetime or recommendation reuse.
Test durations are verification measurements, not responsiveness evidence.

Changed C3 responsibilities/files: `core/diagnostic_health.py`; pure health
presentation and isolated compatibility adapter; MainWindow observation/delegation;
DiagnosticsView typed rendering; new characterization/regression tests and wording
fixture; architecture/roadmap documentation. Existing C1/C2 changes were retained.
No commit or push was requested or performed.

Remaining concerns and next bounded slice: controller command/lifecycle string
tokens remain authoritative, PC orchestration and asynchronous discovery ownership
remain concentrated in MainWindow, and legacy callers can still supply claimed
presentation strings for preview. A small, separately characterized controller
state-token normalization and lifecycle transition audit is preferable before any
larger controller or transport extraction. Do not start that next slice automatically.

Live BizHawk smoke checks remain necessary for acknowledgement timing during
walk start/stop and stale reads, TCP/file reconnect, emulator/ROM restart, real PC
discovery/cache-confirmation failures, baseline recovery and current-session labels.
This pass used synthetic snapshots and isolated Qt fixtures, not a running emulator
or real user save data.

## Phase C4 boundary selection (before extraction)

Reviewed polling, C3 health projections, acquisition/reconciliation, notifications,
PC discovery/recovery, walking acknowledgement handling and Qt scheduling. PC and
walking orchestration have broad dispatch/session dependencies; extracting either
whole subsystem now would expand the behavioral risk. EV history already has a
domain observer and mostly presentation work remains.

Selected the **raw-party lifecycle observation workflow**: preparing validated HP
samples and session/frame context, sequencing death/wipe/no-run-prompt observers,
and resetting/suppressing their observation state after an accepted run end.
These three observers already consume the same authoritative raw inputs. Their
baselines and prompt suppression can have one non-Qt owner without owning the
store, widgets, transport, PC gates or walking state.

The coordinator receives the provider and clock explicitly, and each process call
receives current raw party/payload, connection, run identity, freshness threshold,
prompt eligibility and three narrowly scoped event handlers. It executes handlers
in the existing order: deaths, wipe, then prompt. A wipe handler may synchronously
end the run and call the coordinator's reset/suppression operation before prompt
evaluation. Persistence/dialog decisions remain with existing UI owners; callback
exceptions retain their current propagation. No replacement diagnostic freshness
policy is introduced, and C3 display health does not authorize HP observation.

Before production changes, seven full-poll characterization cases passed for
normal death/wipe ordering, invalid/stale/disconnected samples, restart/rollback,
recovery rebaselining and session-scoped no-run prompting.

## Phase C4: raw-party lifecycle coordination

Implemented this one boundary only. `PartyLifecycleCoordinator` owns the three
existing domain observers, provider nickname conversion and an explicitly injected
clock. Its public interface is `process` plus `run_ended`; it receives neither
MainWindow nor Qt widgets, transport/server, store or settings objects. Each poll
passes scalar policy inputs and original raw records/payload. Handlers perform
existing UI/persistence actions synchronously; they are not retained as shared
application state or routed through extra signals. The coordinator does the
validation, sample preparation and observer sequencing itself rather than asking
MainWindow to perform those steps.

Accepted run end now uses one reset/suppress operation. Unaccepted/ignored/pending
wipes leave observation behavior unchanged. Death delivery still precedes wipe
observation, and wipe handling still precedes prompt observation, including nested
dialog/run-end behavior. Callback errors propagate as before, skipping subsequent
workflow steps. The captured run identity remains fixed for the current poll,
matching prior behavior even when a handler changes the active run.

The existing raw validation and frame conversion functions moved unchanged in
meaning to `core/nuzlocke/party_snapshot.py`. Acquisition and walking callers reuse
those functions; no new protocol parsing, RAM decoder or validation policy was
introduced. The clock is evaluated only after the same party/payload/received-time
checks as before. Raw death eligibility still includes timestamp age, party count,
checksum/warning/sample-stale flags, current stats and HP bounds. C3 health is a
diagnostic projection, not a substitute for this workflow's stricter validation;
healthy heartbeat or stabilized display fallback cannot authorize death detection.

MainWindow no longer owns three independent lifecycle observers or prepares HP
samples/session/frame context in its poll. It retains composition, polling,
dialogs, accepted-wipe persistence and pending-wipe review state. AST source-span
counts: MainWindow **4,735 → 4,691**; its full poll **483 → 443**. Three module-level
validation/frame helpers also leave the UI module. These are scope measurements,
not claims that moving lines alone improved architecture. Ownership now has a
single independently exercisable boundary without duplicated baselines.

### Tests and measurements

Seven full-poll characterization cases were written and passed before extraction
(5.78s), and passed after it. Twenty-six independent coordinator cases cover normal
and repeated transitions, partial faint/reorder, missing party/payload, stale RAM,
disconnect, checksum/count/error/warning failures, stale samples, invalid HP/stats,
empty party, stream/run/frame changes, recovery, prompt progress/eligibility,
unsupported provider nickname conversion, accepted-wipe suppression, handler
failure ordering and lazy clock evaluation. They do not construct QApplication,
MainWindow or a live emulator. Existing cache/poll-frequency and lifecycle tests
were adjusted only to instrument observer state at its new owner.

The same `benchmarks/party_lifecycle.py` fixture was run before and after extraction:
six decoded members, an active run, fresh raw payload, fixed advancing-independent
frame, 30 unchanged polls per round, five rounds, no live transport or user saves.
Results are saved in `benchmarks/results/party-lifecycle-before.json` and
`party-lifecycle-after.json`. Full-poll median was **7.7391 → 8.3355 ms**;
round ranges **7.6252–8.1531 → 7.9099–8.9521 ms** overlap. This is a measured
median increase of 0.5964ms, not a performance improvement or a precise estimate
of coordinator overhead. Each existing observer still runs exactly **30 times
over 30 polls** before and after. No extra decoding, snapshot cache, timer, signal
or second party sample projection was added. No latency/FPS improvement is claimed.

Verification: focused regression selection **188 passed in 41.42s**; complete
suite **900 passed in 187.11s**. Ruff and `git diff --check` passed. The package
build produced both sdist and wheel successfully with `python -m build --no-isolation`.
The new coverage totals 33 cases (seven characterization and 26 independent tests).
No further controller extraction or Phase D work was started.

### Remaining orchestration and next bounded extraction

MainWindow still coordinates acquisition/reconciliation and PC baselines/discovery,
connection/UI scheduling, coordinate analysis, notifications, EV history and
Friendship Walk transport acknowledgements. These workflows have not been moved
or redesigned in C4. C3 controller lifecycle/command string tokens and the legacy
display adapter remain as documented. A next bounded extraction should first
characterize PC acquisition baseline ownership/reset transitions separately from
discovery dispatch, rather than attempting the entire PC controller at once.
Do not proceed automatically into that extraction or Phase D.

Live emulator acknowledgement/reconnect/PC-discovery and real-save smoke checks
remain manual validation work; synthetic regression coverage and builds do not
replace them. No user save data was modified, and no commit or push was requested.
