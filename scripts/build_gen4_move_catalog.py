"""Build the bundled Platinum-era catalog from PokeAPI's source CSV tables.

Usage: python scripts/build_gen4_move_catalog.py PATH_TO_CSV_DIRECTORY
The directory must contain moves, move_changelog, types, version_groups and
move_damage_classes CSVs from PokeAPI/pokeapi/data/v2/csv.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ROOT / "src/pokemon_ev_tracker/games/platinum/data/pokemon_moves.json"
OUTPUT = ROOT / "src/pokemon_ev_tracker/pokemon/gen4/data/moves.json"
ATTRIBUTES = ("type_id", "power", "pp", "accuracy", "priority")
# PokeAPI's move_changelog omits these older priority changes. The Gen IV
# overrides in Pokemon Showdown's data/mods/gen4/moves.ts provide the values.
GEN4_PRIORITY_OVERRIDES = {182: 3, 197: 3, 245: 1, 252: 1}


def rows(source: Path, name: str) -> list[dict[str, str]]:
    with (source / f"{name}.csv").open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def main(source: Path) -> None:
    versions = {int(row["id"]): int(row["order"]) for row in rows(source, "version_groups")}
    platinum_order = versions[9]
    types = {int(row["id"]): ("???" if row["identifier"] == "unknown"
                                   else row["identifier"].title())
             for row in rows(source, "types")}
    categories = {
        int(row["id"]): row["identifier"].title()
        for row in rows(source, "move_damage_classes")
    }
    names = json.loads(NAMES.read_text(encoding="utf-8"))
    changes: dict[int, list[dict[str, str]]] = {}
    for row in rows(source, "move_changelog"):
        if versions[int(row["changed_in_version_group_id"])] > platinum_order:
            changes.setdefault(int(row["move_id"]), []).append(row)
    for history in changes.values():
        history.sort(key=lambda row: versions[int(row["changed_in_version_group_id"])])

    catalog = {}
    for row in rows(source, "moves"):
        move_id = int(row["id"])
        if not 1 <= move_id <= 467:
            continue
        values = {attribute: row[attribute] for attribute in ATTRIBUTES}
        # A changelog row records the value immediately before that release.
        # The first post-Platinum change for each field is its Platinum value.
        for attribute in ATTRIBUTES:
            for change in changes.get(move_id, ()):
                if change[attribute] != "":
                    values[attribute] = change[attribute]
                    break
        power = int(values["power"]) if values["power"] else None
        accuracy = int(values["accuracy"]) if values["accuracy"] else None
        catalog[str(move_id)] = {
            "name": names[str(move_id)],
            "type_name": types[int(values["type_id"])],
            "category": categories[int(row["damage_class_id"])],
            "power": power or None,
            "accuracy": accuracy or None,
            "base_pp": int(values["pp"]),
            "priority": GEN4_PRIORITY_OVERRIDES.get(move_id, int(values["priority"])),
        }
    assert set(catalog) == set(names) == {str(i) for i in range(1, 468)}
    unexpected = {move_id: value["name"] for move_id, value in catalog.items()
                  if value["type_name"] == "Fairy"}
    assert not unexpected, unexpected
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(catalog)} moves to {OUTPUT}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
