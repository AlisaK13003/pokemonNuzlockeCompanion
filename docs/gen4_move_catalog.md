# Generation IV move catalog

`src/pokemon_ev_tracker/pokemon/gen4/data/moves.json` is bundled with the app.
Runtime lookup never accesses the network. It covers every move ID 1–467,
including Gen IV's `???` type for Curse.

The catalog was built from PokeAPI's source tables in
[`data/v2/csv`](https://github.com/PokeAPI/pokeapi/tree/master/data/v2/csv):
`moves.csv`, `move_changelog.csv`, `types.csv`, `move_damage_classes.csv`, and
`version_groups.csv`. The build script takes current rows and restores each
field's earliest post-Platinum historical value from `move_changelog.csv`.
Platinum is version group 9 (order 13). Display names come from the existing
bundled Platinum names table.

PokeAPI's changelog omits four older priority changes. The Gen IV values for
Protect (182), Detect (197), Extreme Speed (245), and Fake Out (252) come from
[Pokémon Showdown's Gen IV move overrides](https://github.com/smogon/pokemon-showdown/blob/master/data/mods/gen4/moves.ts).
The build script explicitly applies these four overrides. We checked its other
static Gen IV overrides against the generated catalog.

The source CSV SHA-256 digests used for this catalog were:

| File | SHA-256 |
| --- | --- |
| `moves.csv` | `8AAFD37BF78F19471495C05B201545180F50F0A08A2A2A844D69F9837DD39AC9` |
| `move_changelog.csv` | `86C1511F6A0E29DCC5A885C5B8F125FFD502AFBEC9F69C7F2B85A3E0E8B001F8` |
| `types.csv` | `37F039C8D722F47D51BA1C5C5ECF9B7007235B1A9A1AF2827645C777B70307C8` |
| `move_damage_classes.csv` | `B7101CECA4DFF152537A2FB5C439CA4030B05F86442C643812C0B7B6CCF16F1B` |
| `version_groups.csv` | `28DA8D89D8EB4966941F81A9E62B3990510ED4D76DD774158246551A8E7707A7` |

Rebuild with `python scripts/build_gen4_move_catalog.py PATH_TO_CSV_DIRECTORY`.
The four live PP bytes and four PP Up bytes follow the four move IDs in
[Platinum's Pokémon data block B](https://github.com/pret/pokeplatinum/blob/main/include/struct_defs/pokemon.h).
Maximum PP follows
[Platinum's `MoveTable_CalcMaxPP`](https://github.com/pret/pokeplatinum/blob/main/src/move_table.c).
