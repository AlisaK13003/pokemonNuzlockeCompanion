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
    bizhawk_save_ram_directory: str = ""
    bizhawk_state_directory: str = ""
    bizhawk_save_name: str = ""
    always_on_top: bool = False
    remember_geometry: bool = True
    launch_compact: bool = False
    advanced_diagnostics: bool = False
    auto_pc_rediscovery: bool = True
    notify_connection: bool = True
    notify_encounters: bool = True
    notify_faints: bool = True
    notify_friendship: bool = True
    encounter_action: str = "ASK ME"
    compact_mode: bool = False
    tracker_view: str = "training"
    workspace_view: str | None = None
    friendship_walk_axis: str = "horizontal"
    friendship_goals: dict[str, int] | None = None
    nuzlocke_wipe_action: str = "ASK ME"
    prompt_for_nuzlocke_run: bool = True
    pc_box1_species_id: int = 415
    pc_box1_nickname: str = "mitsu"
    window_geometry: tuple[int, int, int, int] | None = None
    normal_window_geometry: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        for key in ("bizhawk_save_ram_directory", "bizhawk_state_directory", "bizhawk_save_name"):
            if not isinstance(getattr(self, key), str):
                setattr(self, key, "")
        for key in ("always_on_top", "remember_geometry", "launch_compact", "advanced_diagnostics",
                    "auto_pc_rediscovery", "notify_connection", "notify_encounters",
                    "notify_faints", "notify_friendship"):
            value = getattr(self, key)
            if type(value) is not bool:
                setattr(self, key, key in {"remember_geometry", "auto_pc_rediscovery", "notify_connection",
                                         "notify_encounters", "notify_faints", "notify_friendship"})
        if self.encounter_action not in {"ASK ME", "AUTOMATIC", "IGNORE"}:
            self.encounter_action = "ASK ME"
        self.compact_mode = bool(self.compact_mode)
        if self.nuzlocke_wipe_action not in {"ASK ME", "END RUN AS WIPED", "IGNORE"}:
            self.nuzlocke_wipe_action = "ASK ME"
        self.prompt_for_nuzlocke_run = bool(self.prompt_for_nuzlocke_run)
        if not isinstance(self.workspace_view, str) or self.workspace_view not in {
            "training", "stats", "box", "nuzlocke", "diagnostics", "settings",
        }:
            self.workspace_view = None
        if not isinstance(self.tracker_view, str) or self.tracker_view not in {
            "training",
            "stats",
            "box",
        }:
            self.tracker_view = "training"
        if not isinstance(self.friendship_walk_axis, str) or self.friendship_walk_axis not in {
            "horizontal",
            "vertical",
        }:
            self.friendship_walk_axis = "horizontal"
        if not isinstance(self.friendship_goals, dict):
            self.friendship_goals = {}
        else:
            self.friendship_goals = {
                key: value for key, value in self.friendship_goals.items()
                if isinstance(key, str) and key and type(value) is int and 0 <= value <= 255
            }
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
            bizhawk_save_ram_directory=data.get("bizhawk_save_ram_directory", ""),
            bizhawk_state_directory=data.get("bizhawk_state_directory", ""),
            bizhawk_save_name=data.get("bizhawk_save_name", ""),
            **{key: data.get(key, default) for key, default in {
                "always_on_top": False, "remember_geometry": True, "launch_compact": False,
                "advanced_diagnostics": False, "auto_pc_rediscovery": True,
                "notify_connection": True, "notify_encounters": True,
                "notify_faints": True, "notify_friendship": True, "encounter_action": "ASK ME",
            }.items()},
            compact_mode=data.get("compact_mode", False),
            tracker_view=data.get("tracker_view", "training"),
            workspace_view=data.get("workspace_view"),
            friendship_walk_axis=data.get("friendship_walk_axis", "horizontal"),
            friendship_goals=data.get("friendship_goals"),
            nuzlocke_wipe_action=data.get("nuzlocke_wipe_action", "ASK ME"),
            prompt_for_nuzlocke_run=data.get("prompt_for_nuzlocke_run", True),
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
