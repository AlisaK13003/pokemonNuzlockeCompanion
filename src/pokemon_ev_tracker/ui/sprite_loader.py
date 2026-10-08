"""Resolve and cache animated and static Pokémon sprites for tracker cards."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImageReader, QPixmap

ASSET_SPRITE_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "sprites"
STATIC_SPRITE_DIRECTORY = ASSET_SPRITE_DIRECTORY / "gen4"
ANIMATED_SPRITE_DIRECTORY = ASSET_SPRITE_DIRECTORY / "animated"
_STATIC_SPRITE_CACHE: dict[tuple[int, int, bool], QPixmap] = {}
_ANIMATED_PATH_CACHE: dict[int, Path | None] = {}
_SPRITE_ASSET_CACHE: dict[int, SpriteAsset] = {}
_COMPANION_ASSET_CACHE: dict[int, SpriteAsset] = {}
_ANIMATED_SIZE_CACHE: dict[tuple[Path, int], QSize] = {}


@dataclass(frozen=True)
class SpriteAsset:
    kind: Literal["animated", "static", "missing"]
    path: Path | None


def sprite_path(species_id: int) -> Path:
    """Return the Platinum front-sprite path for a National Pokédex ID."""
    return static_sprite_path(species_id)


def static_sprite_path(species_id: int) -> Path:
    """Return the static Platinum PNG path for a National Pokédex ID."""
    return STATIC_SPRITE_DIRECTORY / f"{int(species_id)}.png"


def get_animated_sprite_path(species_id: int) -> Path | None:
    """Find a cached animated front sprite, if one is installed for this species."""
    try:
        normalized_id = int(species_id)
    except (TypeError, ValueError):
        return None

    if normalized_id not in _ANIMATED_PATH_CACHE:
        _ANIMATED_PATH_CACHE[normalized_id] = next(
            (
                path
                for suffix in (".gif", ".webp")
                if (path := ANIMATED_SPRITE_DIRECTORY / f"{normalized_id}{suffix}").is_file()
            ),
            None,
        )
    return _ANIMATED_PATH_CACHE[normalized_id]


def resolve_sprite_asset(species_id: int, *, companion: bool = False) -> SpriteAsset:
    """Resolve one cached animated-first asset, falling back to Platinum art."""
    try:
        normalized_id = int(species_id)
    except (TypeError, ValueError):
        return SpriteAsset("missing", None)

    if companion:
        if normalized_id not in _COMPANION_ASSET_CACHE:
            path = ASSET_SPRITE_DIRECTORY / "companion" / f"{normalized_id}.gif"
            _COMPANION_ASSET_CACHE[normalized_id] = (
                SpriteAsset("animated", path) if path.is_file()
                else resolve_sprite_asset(normalized_id)
            )
        return _COMPANION_ASSET_CACHE[normalized_id]

    if normalized_id not in _SPRITE_ASSET_CACHE:
        animated_path = get_animated_sprite_path(normalized_id)
        if animated_path is not None:
            asset = SpriteAsset("animated", animated_path)
        else:
            platinum_path = static_sprite_path(normalized_id)
            asset = (
                SpriteAsset("static", platinum_path)
                if platinum_path.is_file()
                else SpriteAsset("missing", None)
            )
        _SPRITE_ASSET_CACHE[normalized_id] = asset
    return _SPRITE_ASSET_CACHE[normalized_id]


def get_animated_sprite_size(path: Path, size: int) -> QSize:
    """Return a cached square-bounded size that preserves the GIF canvas ratio."""
    normalized_size = int(size)
    key = (path, normalized_size)
    if key not in _ANIMATED_SIZE_CACHE:
        source_size = QImageReader(str(path)).size()
        if normalized_size <= 0:
            result = QSize()
        elif source_size.isValid():
            result = source_size.scaled(
                normalized_size,
                normalized_size,
                Qt.AspectRatioMode.KeepAspectRatio,
            )
        else:
            result = QSize(normalized_size, normalized_size)
        _ANIMATED_SIZE_CACHE[key] = result
    return QSize(_ANIMATED_SIZE_CACHE[key])


def get_static_sprite(species_id: int, size: int = 72, *, shiny: bool = False) -> QPixmap:
    """Return a cached, nearest-neighbor-scaled sprite or a null pixmap."""
    try:
        normalized_id = int(species_id)
        normalized_size = int(size)
    except (TypeError, ValueError):
        return QPixmap()

    key = (normalized_id, normalized_size, shiny)
    if key in _STATIC_SPRITE_CACHE:
        return _STATIC_SPRITE_CACHE[key]

    path = (STATIC_SPRITE_DIRECTORY / "shiny" / f"{normalized_id}.png"
            if shiny else static_sprite_path(normalized_id))
    pixmap = QPixmap(str(path)) if normalized_size > 0 and path.is_file() else QPixmap()
    if not pixmap.isNull() and normalized_size > 0:
        pixmap = pixmap.scaled(
            normalized_size,
            normalized_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

    _STATIC_SPRITE_CACHE[key] = pixmap
    return pixmap


def get_sprite(species_id: int, size: int = 72) -> QPixmap:
    """Backward-compatible alias for the static sprite loader."""
    return get_static_sprite(species_id, size)


def clear_sprite_cache() -> None:
    """Clear cached pixmaps, primarily for tests and asset reloads."""
    _STATIC_SPRITE_CACHE.clear()
    _ANIMATED_PATH_CACHE.clear()
    _SPRITE_ASSET_CACHE.clear()
    _COMPANION_ASSET_CACHE.clear()
    _ANIMATED_SIZE_CACHE.clear()
