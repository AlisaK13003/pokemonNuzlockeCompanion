# Third-Party Notices

## Project Source Code

Original source code created for this project is licensed under the MIT License in [LICENSE](LICENSE). That license applies to the project's original source code only. It does not license, relicense, or grant rights to the third-party sprite artwork, item icons, Pokémon-related artwork, trademarks, or other third-party content bundled with the application.

## Bundled Pokémon and Item Assets

The project bundles Pokémon Platinum front sprites (including the `shiny/` palette variants for IDs 1-493), animated Pokémon sprites, and item icons obtained from the [PokeAPI sprites repository](https://github.com/PokeAPI/sprites). The upstream source locations are `sprites/pokemon/versions/generation-iv/platinum/`, `sprites/pokemon/versions/generation-v/black-white/animated/`, and `sprites/items/`.

PokeAPI's [README](https://github.com/PokeAPI/sprites/blob/master/README.md) and [LICENCE.txt](https://github.com/PokeAPI/sprites/blob/master/LICENCE.txt) are the upstream attribution and license references. Preserve those upstream notices when working with these assets. The upstream `LICENCE.txt` says the repository is distributed under CC0 1.0 Universal, while also stating that image contents are Copyright The Pokémon Company and disclaiming responsibility for clearing rights belonging to other persons. Those repository-level statements do not establish that PokeAPI held rights to relicense the underlying Pokémon artwork. This project does not treat that CC0 statement, or its own MIT License, as a license for the bundled Pokémon or item artwork.

The upstream README says the Black/White set includes official game sprites and custom community sprites for Pokémon IDs greater than 650, crediting the Smogon community and the contributors listed in that README. This project's bundled Pokémon sprite files cover IDs 1-493, so those greater-than-650 custom sprites are not included. The party roster also bundles IDs 1-493 from `sprites/pokemon/other/showdown/` to match the Make reference; the upstream README identifies these Showdown sprites as Smogon-community work. The README also credits DevMike123, JoseBaGra, and Pokétwo for custom shiny official-artwork sprites; that shiny asset set is not bundled here. Refer to the upstream README for its complete, current contributor credits.

Pokémon and related artwork and trademarks remain with their respective rights holders, including Nintendo, The Pokémon Company, and Game Freak where applicable. This project does not claim ownership of that content or affiliation with Nintendo, The Pokémon Company, Game Freak, BizHawk, PokeAPI, PKHeX, or pret. The available upstream notice does not confirm that this project has permission to redistribute the underlying artwork; that rights question remains unresolved and separate from the MIT license for this project's original source code.

## Offline Ability Lookup Data

The bundled Platinum ability-slot lookup is based on PKHeX's [Generation IV personal data](https://github.com/kwsch/PKHeX/tree/master/PKHeX.Core/Resources/byte/personal), specifically its `personal_pt` table. English ability labels are from [PokeAPI's ability names CSV](https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/ability_names.csv). These references are used to identify the game's stored ability ID and matching species slot; they do not change ownership or licensing of Pokémon-related content.

## Offline EV-Yield Lookup Data

The bundled species EV-yield values are derived from PokeAPI's [`pokemon.csv`](https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/pokemon.csv) and [`pokemon_stats.csv`](https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/pokemon_stats.csv), filtered to default species IDs 1-493. They are stored locally; the application makes no runtime API requests. The displayed values are base species yields and may be modified in-game by effects such as held items or Pokérus.

## Offline Move-Name Lookup Data

The bundled move-name lookup is derived from PokeAPI's [`moves.csv`](https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/moves.csv), filtered to Generation IV move IDs 1-467 and normalized for display, with Generation IV spellings retained where they differ. The names are stored locally; the application makes no runtime API requests.

## Offline Generation IV Move Metadata

The bundled Generation IV move definitions use PokeAPI's [`moves.csv`](https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/moves.csv), [`move_changelog.csv`](https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/move_changelog.csv), and related type, category, and version-group tables. Four historical priority values omitted from that changelog are based on [Pokémon Showdown's Generation IV move overrides](https://github.com/smogon/pokemon-showdown/blob/master/data/mods/gen4/moves.ts). The data is packaged offline; details of the reconstruction are in [docs/gen4_move_catalog.md](docs/gen4_move_catalog.md).

## Companion Interface Fonts

Pixelify Sans, Manrope, and DM Mono are distributed under the SIL Open Font License 1.1.
The original font files and license notices are packaged under `assets/fonts/`.
They were obtained from the [Google Fonts repository](https://github.com/google/fonts):
[Pixelify Sans](https://github.com/google/fonts/tree/main/ofl/pixelifysans) and
[Manrope](https://github.com/google/fonts/tree/main/ofl/manrope), and
[DM Mono](https://github.com/google/fonts/tree/main/ofl/dmmono). The application
loads them locally and does not request fonts from the network at runtime.
