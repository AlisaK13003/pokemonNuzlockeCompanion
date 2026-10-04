"""User-local application preferences for the RAM tracker."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from pokemon_ev_tracker.config.paths import app_data_directory

DEFAULT_SETTINGS_PATH = app_data_directory() / "settings.json"
LEGACY_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "config.json"


def _normalize_window_geometry(value) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    try:
        x, y, width, height = (int(part) for part in value)
    except (TypeError, ValueError):
        return None
    if width < 320 or height < 240:
        return None
    return x, y, width, height


@dataclass
class AppSettings:
    compact_mode: bool = False
    tracker_view: str = "training"
    friendship_walk_axis: str = "horizontal"
    pc_box1_species_id: int = 415
    pc_box1_nickname: str = "mitsu"
    window_geometry: tuple[int, int, int, int] | None = None
    normal_window_geometry: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        self.compact_mode = bool(self.compact_mode)
        if not isinstance(self.tracker_view, str) or self.tracker_view not in {
            "training",
            "stats",
        }:
            self.tracker_view = "training"
        if not isinstance(self.friendship_walk_axis, str) or self.friendship_walk_axis not in {
            "horizontal",
            "vertical",
        }:
            self.friendship_walk_axis = "horizontal"
        if type(self.pc_box1_species_id) is not int or not 1 <= self.pc_box1_species_id <= 493:
            self.pc_box1_species_id = 415
        if not isinstance(self.pc_box1_nickname, str) or (
            len(self.pc_box1_nickname) > 10
            or any(ord(character) < 0x20 or ord(character) > 0x7E
                   for character in self.pc_box1_nickname)
        ):
            self.pc_box1_nickname = ""
        self.window_geometry = _normalize_window_geometry(self.window_geometry)
        self.normal_window_geometry = _normalize_window_geometry(self.normal_window_geometry)

    @classmethod
    def load_default(cls) -> AppSettings:
        path = DEFAULT_SETTINGS_PATH
        if path.is_file():
            return cls.load(path)

        settings = cls()
        if LEGACY_SETTINGS_PATH.is_file():
            try:
                legacy = json.loads(LEGACY_SETTINGS_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                legacy = {}
            if isinstance(legacy, dict):
                settings = cls(
                    compact_mode=legacy.get("compact_mode", False),
                    tracker_view=legacy.get("tracker_view", "training"),
                    friendship_walk_axis=legacy.get("friendship_walk_axis", "horizontal"),
                    window_geometry=legacy.get("window_geometry"),
                    normal_window_geometry=legacy.get("normal_window_geometry"),
                )
        settings.save(path)
        return settings

    @classmethod
    def load(cls, path: Path) -> AppSettings:
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(
            compact_mode=data.get("compact_mode", False),
            tracker_view=data.get("tracker_view", "training"),
            friendship_walk_axis=data.get("friendship_walk_axis", "horizontal"),
            pc_box1_species_id=data.get("pc_box1_species_id", 415),
            pc_box1_nickname=data.get("pc_box1_nickname", "mitsu"),
            window_geometry=data.get("window_geometry"),
            normal_window_geometry=data.get("normal_window_geometry"),
        )

    def save_default(self) -> None:
        self.save(DEFAULT_SETTINGS_PATH)

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
