# Pokémon Nuzlocke Companion

A Windows desktop companion for **Pokémon Platinum** running in BizHawk/EmuHawk. It reads live Nintendo DS memory to show your party, help plan EV training, and maintain a Nuzlocke run. The app runs locally; it does not need a cloud service or a runtime API.

Pokémon Platinum is the only supported game today. The application has a [game provider and capability boundary](docs/game_provider_architecture.md) for future integrations, but it does not interpret an unknown game's RAM as Platinum data.

## What it does

- **Training:** Shows decoded EVs for the selected party Pokémon, saves allowed EV stats by Pokémon identity, and rates current opponents as good, mixed, or avoid based on their **base species EV yields**. These are recommendations, not a measurement of EVs awarded after held-item, Pokérus, or participation effects. EV change history remains available.
- **Party Stats:** Expand a party member to inspect live HP, IVs, nature, ability, friendship, and held item. IV bars use a consistent 0–31 scale with semantic quality colors.
- **Box:** Browse the supported PC boxes using validated live monitoring data. Inspect boxed IVs, nature, ability, friendship, and held item. Current level and HP are unavailable from box records; the app does not substitute met level for current level.
- **Friendship Walk:** Provides guarded walking controls, a selected Pokémon and friendship goal, session progress, and an approximate active-walking ETA. Use it only in a safe area you have checked yourself. The app pauses or releases input when its safety conditions require it; coordinate changes are only a proxy for game steps.
- **Nuzlocke:** Tracks runs, encounters, deaths, PC-box observations, major fights and level caps, and run history. It can suggest party or box acquisitions and prompt for death or full-party-wipe review when reliable live data is available. You review consequential run decisions; PC detection requires a validated monitor and is not guaranteed for every capture or emulator state.
- **Diagnostics:** Shows connection and RAM health, decoded party records, and advanced Platinum PC and coordinate tools. PC monitoring and discovery are presented only when the active game provider supports them.
- **Compact mode:** Shows the current opponent and equal-width party recommendation tiles in a smaller, always-on-top window. It is Tracker-only, wraps without horizontal scrolling, and is unavailable while disconnected.
- **Settings:** Configure window behavior, notifications, run automation, PC recovery, and advanced diagnostics from the cog. Existing runs, EV preferences, and integration state continue to use their established stores.

The displayed EV values come from decoded party records. Battle recommendations and Nuzlocke detections are separate aids and do not rewrite those values.

The full-size interface uses larger text and controls across Tracker, Nuzlocke, Diagnostics,
Settings and notifications. Compact Mode retains its denser typography; returning to the
full window restores the larger text.

Navigation stays in the top bar and uses icons with tooltips at 1,100px and below.
Below 760px the Compact control hides; at 520px and below, the connection badge shows
only its colored indicator. Settings remains available through the cog.

The Run Library shows the active run and an archive of previous runs. Each archive
entry has a stable legendary Pokémon mascot chosen from its game's generation.
For a fresh run, the first validated single-Pokémon party is recorded as the Starter
encounter. This exception is saved once per run and cannot repeat after a restart
or a manual reset of the Starter row.

The encounter ledger shows the first 10 matching locations, ordered by **Not encountered →
Caught → Dead → Failed**. The double-arrow footer button reverses these status groups;
**View all** expands every matching location and **Collapse** restores the 10-row limit.
Locations within each group retain their game order. **Manage locations** opens searchable
cards for editing statuses, nicknames, species and notes. **Save changes** persists the
draft together; **Cancel** or **Discard** leaves saved records unchanged.

The Nuzlocke dashboard includes a **Shiny Clause** section beneath the encounter ledger.
Validated shiny Pokémon in the party or monitored PC are added automatically, including
Pokémon already present when a run is opened. Platinum's personality-ID/original-trainer-ID
check determines shininess; sprites do not determine eligibility. Entries are saved per run
and deduplicated by Pokémon identity across restarts and party/PC moves. Shiny catches stay
separate from normal encounter suggestions and never consume or replace a route's ledger
entry. The section shows the shiny sprite, detection source, met location/level, and the
preserved original encounter. **Add shiny manually** records a bonus catch when live data
is unavailable; avoid manually adding one that is already being detected automatically.
Uncaught opponents and unhatched eggs are not recorded as shiny catches.

Notifications appear as dismissible cards at the bottom right of the companion.
Connection changes, encounter review, shiny catches, friendship goals, and faint/wipe
review use the same presentation as the **Settings → Notifications** preview buttons.
Previews never modify saved data. Real encounter and faint actions apply to the
specific notified Pokémon and run; switching runs cannot redirect an old action.
Dismissed alerts stay quiet instead of reappearing on every RAM refresh. The existing
notification switches still control real events, while previews remain available.
Shinies stay automatically recorded; their real notification opens the Shiny Clause
section for review rather than asking for a duplicate catch.

Live party reads support Platinum's temporary decrypted RAM representation as well as encrypted records. Both require the original box-data checksum to match; HP and stats retain their range checks. Reads captured midway through a transformation remain rejected, with the last valid matching-PID sample retained for display. Acquisition warnings appear only after 10 consecutive seconds of invalid reads, then at most once every 30 seconds. Brief drops and their recoveries stay silent; a recovery message appears only after a warning was shown. Harmless output-log rotation details are available at DEBUG level.

Validated party reads also reconcile unrecorded wild encounters, including Pokémon already present when the Companion starts. A unique party identity with a mapped met location fills an unused ledger row as caught, using its met level rather than its current level. Existing encounter history, eggs, trades, starter/gift suggestions, and multiple party identities from the same location are left for review. This works independently of PC discovery.

PC discovery first looks for Platinum's live SaveData page table, verifies its connection to the active party, and validates every box slot. This automatically handles a new save, a changed Box 1, Slot 1 Pokémon, and an entirely empty PC without relying on the saved nickname. Lua independently validates the structure before caching it and checks the save's trainer identity during monitoring; a changed save invalidates the cache. If structural validation fails, the manual anchor remains a fallback. Missing an anchor or finding zero-filled RAM alone never counts as an empty PC. Reload `bizhawk/ev_tracker.lua` after updating to the v25 reader.

Startup logs show concise status messages and recovery instructions. Routine output-file rotation is informational when no scan is interrupted; raw scan progress, identities and cache details use debug logging. **Rediscover PC layout automatically** in Settings controls startup discovery and recovery, including empty PCs. For troubleshooting details, set `$env:POKEMON_EV_TRACKER_LOG_LEVEL = "DEBUG"` in PowerShell before launching the Companion.

## Supported setup

### Independent save backups

In **Settings → Save Backups**, select BizHawk's configured SaveRAM and State
directories once. The State directory defaults to `State` beside `SaveRAM`.
The existing `BIZHAWK_SAVE_RAM_DIR` environment setting is also supported.
Reload the updated Lua script to report the active ROM name. If it differs from
your save filename, enter the save name without `.SaveRAM`; with no ROM name,
the companion can use a directory containing exactly one `.SaveRAM` file.
Ambiguous selections are skipped and logged.

The companion makes an initial backup and checks every **5 minutes** while it
is running. **Back Up Now** uses the same process. Copies of `.SaveRAM`,
`.SaveRAM.bak`, and matching `.State`/`.State.bak` files are stored under
`%LOCALAPPDATA%\PokemonEVTracker\backups\<save-name>\YYYY-MM-DD_HH-mm-ss\`
in `SaveRAM` and `States` subfolders. Same-second snapshots get unique suffixes.
Content hashes skip unchanged snapshots, including across app restarts.
Each game keeps all snapshots from the last seven days and at least its newest
100 snapshots. Source files are only read; the companion never flushes SaveRAM
or changes emulator state. Failed or changing reads are retried briefly, logged,
and left for the next interval without interrupting RAM monitoring.
The full source set is checked again before a snapshot is published, so a save
changing while a larger savestate is copied does not produce a mixed snapshot.

For recovery, close BizHawk, preserve its current files, and copy the desired
snapshot's save or state back to the matching BizHawk directory. Backups reflect
what BizHawk has written to disk; they cannot capture unsaved RAM-only progress.
The Settings section shows the backup location, last success, and retained count.

| Game | Emulator | Status |
| --- | --- | --- |
| Pokémon Platinum | BizHawk / EmuHawk with a Nintendo DS core | Supported |

You need Windows 10 or later, Python 3.12 or later, BizHawk/EmuHawk, and your own legally obtained Pokémon Platinum game copy. ROMs, save files, and emulator binaries are not included.

## Install

```powershell
git clone https://github.com/AlisaK13003/pokemonNuzlockeCompanion.git
cd pokemonNuzlockeCompanion
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

## Run

1. Launch EmuHawk and load Pokémon Platinum with the Nintendo DS core.
2. Open **Tools → Lua Console** and run `bizhawk/ev_tracker.lua` from this checkout.
3. Start the desktop app with `pokemon-ev-tracker` or `python -m pokemon_ev_tracker`.
4. Wait for the BizHawk connection and a valid party snapshot before relying on live panels.

The Lua reader sends data through localhost TCP, with a system-temporary-file fallback. It enumerates memory domains at runtime. Settings, Nuzlocke runs, and training preferences are stored under `%LOCALAPPDATA%\PokemonEVTracker` on Windows. The app does not bundle sample party data.

## Development

```powershell
python -m pip install -e ".[dev]"
pytest
ruff check .
python -m build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development practices and [game provider architecture](docs/game_provider_architecture.md) for the integration boundary and new-game checklist. Feature details are in the [Training](docs/ev_training_preferences.md), [move catalog](docs/gen4_move_catalog.md), and [Friendship Walk](docs/friendship_walk_eta.md) notes.

## License and assets

Original project source code is licensed under the [MIT License](LICENSE). Bundled Pokémon sprites and item artwork have separate ownership and terms; see [third-party notices](THIRD_PARTY_NOTICES.md). This unofficial project is not affiliated with Nintendo, The Pokémon Company, or Game Freak.
