from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from pokemon_ev_tracker.ui import sprite_loader
from pokemon_ev_tracker.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def reset_sprite_cache():
    sprite_loader.clear_sprite_cache()
    yield
    sprite_loader.clear_sprite_cache()


def test_sprite_path_resolves_under_gen4_assets():
    expected = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "pokemon_ev_tracker"
        / "assets"
        / "sprites"
        / "gen4"
        / "443.png"
    )

    assert sprite_loader.sprite_path(443) == expected


def test_packaged_sprite_resolution_is_independent_of_working_directory(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1] / "tests")

    assert sprite_loader.static_sprite_path(443).is_file()
    assert not sprite_loader.get_static_sprite(443, 64).isNull()


def test_animated_sprite_path_resolves_by_species_id(monkeypatch):
    animated_directory = Path("test-assets") / "animated"
    expected = animated_directory / "443.gif"
    monkeypatch.setattr(sprite_loader, "ANIMATED_SPRITE_DIRECTORY", animated_directory)
    monkeypatch.setattr(Path, "is_file", lambda path: path == expected)

    assert sprite_loader.get_animated_sprite_path(443) == expected


def test_companion_asset_checks_disk_once_until_cache_clear(monkeypatch):
    calls = []
    expected = sprite_loader.ASSET_SPRITE_DIRECTORY / "companion" / "443.gif"

    def is_file(path):
        calls.append(path)
        return path == expected

    monkeypatch.setattr(Path, "is_file", is_file)
    first = sprite_loader.resolve_sprite_asset(443, companion=True)
    assert first.kind == "animated" and first.path == expected
    for _ in range(20):
        assert sprite_loader.resolve_sprite_asset(443, companion=True) == first
    assert calls == [expected]
    sprite_loader.clear_sprite_cache()
    sprite_loader.resolve_sprite_asset(443, companion=True)
    assert calls == [expected, expected]


def test_animated_asset_is_preferred_to_available_static_sprite(qt_app):
    asset = sprite_loader.resolve_sprite_asset(443)

    assert asset.kind == "animated"
    assert asset.path == sprite_loader.ANIMATED_SPRITE_DIRECTORY / "443.gif"
    assert sprite_loader.static_sprite_path(443).is_file()


@pytest.mark.parametrize("species_id", (1, 21, 179, 443))
def test_known_species_sprite_exists_and_loads(qt_app, species_id):
    path = sprite_loader.static_sprite_path(species_id)

    assert path.is_file()
    assert not sprite_loader.get_static_sprite(species_id, 64).isNull()


def test_missing_animation_falls_back_to_static_platinum_sprite(qt_app, monkeypatch):
    monkeypatch.setattr(
        sprite_loader,
        "ANIMATED_SPRITE_DIRECTORY",
        Path("missing") / "animated",
    )
    sprite_loader.clear_sprite_cache()

    asset = sprite_loader.resolve_sprite_asset(179)

    assert asset.kind == "static"
    assert asset.path == sprite_loader.static_sprite_path(179)
    assert not sprite_loader.get_static_sprite(179, 64).isNull()


def test_missing_animated_and_static_sprite_is_handled(qt_app, monkeypatch):
    monkeypatch.setattr(sprite_loader, "ANIMATED_SPRITE_DIRECTORY", Path("missing/animated"))
    monkeypatch.setattr(sprite_loader, "STATIC_SPRITE_DIRECTORY", Path("missing/static"))
    monkeypatch.setattr(Path, "is_file", lambda _path: False)

    asset = sprite_loader.resolve_sprite_asset(99999)
    pixmap = sprite_loader.get_static_sprite(99999, 64)

    assert asset.kind == "missing"
    assert asset.path is None
    assert pixmap.isNull()


def test_sprite_resolution_is_cached_without_rechecking_files(monkeypatch):
    animated_directory = Path("test-assets") / "animated"
    expected = animated_directory / "443.gif"
    checks = []
    monkeypatch.setattr(sprite_loader, "ANIMATED_SPRITE_DIRECTORY", animated_directory)
    monkeypatch.setattr(Path, "is_file", lambda path: checks.append(path) or path == expected)
    sprite_loader.clear_sprite_cache()

    first = sprite_loader.resolve_sprite_asset(443)
    second = sprite_loader.resolve_sprite_asset(443)

    assert first == second
    assert first.kind == "animated"
    assert checks == [expected]


@pytest.mark.parametrize("species_id", (21, 179, 443))
def test_current_party_species_load_valid_multiframe_movies(qt_app, species_id):
    sprite = QLabel()
    card = _sprite_card(sprite)

    assert MainWindow._refresh_tracker_card_sprite(card, species_id) is True
    movie = card["sprite_movie"]
    assert movie is not None
    assert movie.isValid()
    assert movie.frameCount() > 1
    assert movie.jumpToFrame(1)
    assert movie.currentFrameNumber() == 1
    assert card["sprite_asset"].kind == "animated"

    MainWindow._clear_tracker_card_sprite(card)
    qt_app.processEvents()


def test_sprite_change_replaces_movie_but_normal_refresh_retains_it(qt_app):
    card = _sprite_card(QLabel())

    assert MainWindow._refresh_tracker_card_sprite(card, 21) is True
    original_movie = card["sprite_movie"]
    assert MainWindow._refresh_tracker_card_sprite(card, 21) is False
    assert card["sprite_movie"] is original_movie

    assert MainWindow._refresh_tracker_card_sprite(card, 179) is True
    assert card["sprite_movie"] is not original_movie
    assert card["sprite_species_id"] == 179

    MainWindow._clear_tracker_card_sprite(card)
    qt_app.processEvents()


def test_sprite_pixmap_is_reused_from_cache(qt_app):
    first = sprite_loader.get_static_sprite(443, 64)
    second = sprite_loader.get_static_sprite(443, 64)

    assert first is second


def _sprite_card(sprite):
    return {
        "sprite": sprite,
        "sprite_size": 64,
        "sprite_species_id": None,
        "sprite_loaded_size": None,
        "sprite_asset": None,
        "sprite_movie": None,
    }
