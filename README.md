# Pokémon Nuzlocke Companion

A Windows desktop companion for **Pokémon Platinum** running in BizHawk/EmuHawk. It reads live Nintendo DS memory to show your party, help plan EV training, and maintain a Nuzlocke run. The app runs locally; it does not need a cloud service or a runtime API.

Pokémon Platinum is the only supported game today. The application has a [game provider and capability boundary](docs/game_provider_architecture.md) for future integrations, but it does not interpret an unknown game's RAM as Platinum data.

## What it does

- **Training:** Shows decoded EVs for the selected party Pokémon, saves allowed EV stats by Pokémon identity, and rates current opponents as good, mixed, or avoid based on their **base species EV yields**. These are recommendations, not a measurement of EVs awarded after held-item, Pokérus, or participation effects. EV change history remains available.
- **Party Stats:** Shows live HP, battle stats, IVs, nature, ability, friendship, held item, and Generation IV move details, including current and maximum PP where the party data is valid.
- **Friendship Walk:** Provides guarded walking controls, a selected Pokémon and friendship goal, session progress, and an approximate active-walking ETA. Use it only in a safe area you have checked yourself. The app pauses or releases input when its safety conditions require it; coordinate changes are only a proxy for game steps.
- **Nuzlocke:** Tracks runs, encounters, deaths, PC-box observations, major fights and level caps, and run history. It can suggest party or box acquisitions and prompt for death or full-party-wipe review when reliable live data is available. You review consequential run decisions; PC detection requires a validated monitor and is not guaranteed for every capture or emulator state.
- **Diagnostics:** Shows connection and RAM health, decoded party records, and advanced Platinum PC and coordinate tools. PC monitoring and discovery are presented only when the active game provider supports them.
- **Compact mode:** Keeps dedicated Training, Party Stats, and Nuzlocke workspaces available in a smaller, always-on-top window.

The displayed EV values come from decoded party records. Battle recommendations and Nuzlocke detections are separate aids and do not rewrite those values.

## Supported setup

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
