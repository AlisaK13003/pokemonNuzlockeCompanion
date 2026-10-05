from __future__ import annotations

import json

import pytest

from pokemon_ev_tracker.config.settings import AppSettings


def test_settings_round_trip(tmp_path) -> None:
    path = tmp_path / "settings.json"
    original = AppSettings(
        compact_mode=True,
        tracker_view="stats",
        workspace_view="nuzlocke",
        friendship_walk_axis="vertical",
        friendship_goals={"pid:1234": 220},
        window_geometry=(-1200, 55, 744, 620),
        normal_window_geometry=(20, 30, 1260, 850),
    )

    original.save(path)
    loaded = AppSettings.load(path)

    assert loaded == original
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "compact_mode",
        "tracker_view",
        "workspace_view",
        "friendship_walk_axis",
        "friendship_goals",
        "nuzlocke_wipe_action",
        "prompt_for_nuzlocke_run",
        "pc_box1_species_id",
        "pc_box1_nickname",
        "window_geometry",
        "normal_window_geometry",
    }


def test_settings_ignore_unknown_preferences(tmp_path) -> None:
    path = tmp_path / "old-settings.json"
    path.write_text(
        json.dumps(
            {
                "obsolete_option": "ignored",
                "another_unknown_option": {"value": 0.1},
                "compact_mode": True,
                "tracker_view": "stats",
                "window_geometry": [10, 20, 800, 600],
            }
        ),
        encoding="utf-8",
    )

    loaded = AppSettings.load(path)

    assert loaded.compact_mode
    assert loaded.tracker_view == "stats"
    assert loaded.window_geometry == (10, 20, 800, 600)
    assert not hasattr(loaded, "obsolete_option")


def test_load_default_migrates_only_tracker_preferences(monkeypatch, tmp_path) -> None:
    legacy = tmp_path / "legacy-config.json"
    legacy.write_text(
        '{"compact_mode": true, "window_geometry": [1, 2, 600, 400], "obsolete_option": true}',
        encoding="utf-8",
    )
    destination = tmp_path / "local-settings.json"
    monkeypatch.setattr("pokemon_ev_tracker.config.settings.DEFAULT_SETTINGS_PATH", destination)
    monkeypatch.setattr("pokemon_ev_tracker.config.settings.LEGACY_SETTINGS_PATH", legacy)

    settings = AppSettings.load_default()

    assert settings.compact_mode
    assert settings.window_geometry == (1, 2, 600, 400)
    assert set(json.loads(destination.read_text(encoding="utf-8"))) == {
        "compact_mode",
        "tracker_view",
        "workspace_view",
        "friendship_walk_axis",
        "friendship_goals",
        "nuzlocke_wipe_action",
        "prompt_for_nuzlocke_run",
        "pc_box1_species_id",
        "pc_box1_nickname",
        "window_geometry",
        "normal_window_geometry",
    }
    assert legacy.exists()


def test_unknown_tracker_view_defaults_to_training(tmp_path) -> None:
    path = tmp_path / "invalid-settings.json"
    path.write_text('{"tracker_view": "unrecognized"}', encoding="utf-8")

    assert AppSettings.load(path).tracker_view == "training"


def test_invalid_friendship_walk_axis_defaults_to_horizontal(tmp_path) -> None:
    path = tmp_path / "invalid-axis.json"
    path.write_text('{"friendship_walk_axis": ["vertical"]}', encoding="utf-8")

    assert AppSettings.load(path).friendship_walk_axis == "horizontal"


def test_friendship_goals_filter_invalid_values_and_round_trip(tmp_path) -> None:
    path = tmp_path / "goals.json"
    path.write_text(json.dumps({"friendship_goals": {
        "pid:one": 220, "pid:two": 0, "too-high": 256, "boolean": True,
        "negative": -1,
    }}), encoding="utf-8")
    loaded = AppSettings.load(path)
    assert loaded.friendship_goals == {"pid:one": 220, "pid:two": 0}
    loaded.save(path)
    assert AppSettings.load(path).friendship_goals == loaded.friendship_goals


@pytest.mark.parametrize("value", [None, [], {}, 42, "missing"])
def test_invalid_workspace_preference_keeps_legacy_tracker_view(tmp_path, value) -> None:
    path = tmp_path / "invalid-workspace.json"
    path.write_text(json.dumps({"workspace_view": value, "tracker_view": "stats"}), encoding="utf-8")

    loaded = AppSettings.load(path)

    assert loaded.workspace_view is None
    assert loaded.tracker_view == "stats"
