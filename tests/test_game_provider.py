"""Provider resolution and UI behavior with a non-Platinum test integration."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.core.nuzlocke.models import (
    EncounterLocation,
    LevelCap,
    NuzlockeGameProfile,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.data_sources.base import DataSourceSnapshot
from pokemon_ev_tracker.games.provider import GameCapabilities, UnsupportedGameProvider
from pokemon_ev_tracker.games.registry import get_game_provider
from pokemon_ev_tracker.ui.main_window import MainWindow


class FakeSource:
    def __init__(self):
        self.started = False
        self.profile = None

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def snapshot(self):
        return DataSourceSnapshot("Fake RAM", False, {})


class FakeProvider(UnsupportedGameProvider):
    def __init__(self, capabilities: GameCapabilities):
        super().__init__("test-game")
        self.display_name = "Test Game"
        self.profile = self.profile.__class__(
            "test-game", "Test Game", (), (), None, lambda *_args: None,
            move_definition=lambda _move_id: None)
        self.capabilities = capabilities
        self.nuzlocke_profile = NuzlockeGameProfile(
            "test-game", "Test Game", (EncounterLocation("start", "Start", 0),),
            (LevelCap("boss", "Boss", "Major", 0, 10),)) if capabilities.nuzlocke else None
        self.source = FakeSource()

    def make_data_source(self):
        return self.source


def test_platinum_provider_exposes_existing_services():
    provider = get_game_provider("pokemon-platinum")
    assert provider.game_id == provider.profile.game_id == "pokemon-platinum"
    assert provider.generation == "Generation IV"
    assert provider.capabilities.live_party
    assert provider.capabilities.pc_storage
    assert provider.capabilities.friendship_walk
    assert provider.nuzlocke_profile.level_caps
    assert provider.profile.move_definition(33) is not None
    assert provider.profile.friendship_walk_rules is not None
    assert provider.pc_storage.box_count == 18


def test_unknown_game_is_unsupported_and_has_no_ram_reader():
    provider = get_game_provider("unknown-game")
    assert provider.display_name == "Unsupported game"
    assert not provider.capabilities.live_party
    assert not provider.capabilities.pc_storage
    assert not provider.make_data_source().snapshot().connected


@pytest.mark.parametrize("capabilities", [
    GameCapabilities(live_party=True),
    GameCapabilities(live_party=True, move_metadata=True),
    GameCapabilities(live_party=True, live_opponents=True, ev_yields=True),
    GameCapabilities(live_party=True, friendship=True),
    GameCapabilities(live_party=True, nuzlocke=True),
], ids=["party", "party-moves", "battle-ev", "friendship-no-walker", "nuzlocke-no-pc"])
def test_fake_provider_keeps_unrelated_workspaces_usable(
    tmp_path, monkeypatch, capabilities
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda self: None)
    provider = FakeProvider(capabilities)
    window = MainWindow(
        AppSettings(), provider=provider,
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"))
    window.refresh_timer.stop()
    assert window.provider is provider
    assert window.game_session.provider is provider
    assert window.training_view is not None
    assert window.party_stats_view is not None
    assert window.pc_storage_section.isHidden()
    assert window.compact_nuzlocke_view.pc_panel.isHidden()
    assert window.friendship_walk_group.isHidden()
    if capabilities.nuzlocke:
        run = window.nuzlocke_view.store.create_run("Fake", provider.nuzlocke_profile)
        window.nuzlocke_view._refresh_all()
        assert window.nuzlocke_view.fight_timeline().next_fight.name == "Boss"
        assert run.game == provider.game_id
    if not capabilities.live_opponents:
        assert window.current_opponent_panel.empty_label.text() == (
            "Live battle analysis unavailable for this game")
    window.close()
    assert app is not None


def test_unsupported_window_never_starts_data_source(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppSettings, "save_default", lambda self: None)
    provider = FakeProvider(GameCapabilities())
    window = MainWindow(
        AppSettings(), provider=provider,
        nuzlocke_store=NuzlockeStore(tmp_path / "runs.json"))
    assert not provider.source.started
    assert not window.refresh_timer.isActive()
    assert "Unsupported game" in window.tracker_status_message._full_text
    window.close()
    assert app is not None
