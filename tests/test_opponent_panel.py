from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QBoxLayout

from pokemon_ev_tracker.ui.opponent_panel import CurrentOpponentPanel
from pokemon_ev_tracker.ui.sprite_loader import SpriteAsset


def test_opponent_panel_shows_species_level_and_base_ev_yield(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "pokemon_ev_tracker.ui.opponent_panel.resolve_sprite_asset",
        lambda _species_id, *, companion=False: SpriteAsset("missing", None),
    )
    panel = CurrentOpponentPanel()

    panel.set_opponent(
        SimpleNamespace(
            species_id=179,
            species="Mareep",
            nickname="sheepy",
            level=14,
        )
    )

    assert panel.title() == "Currently Battling"
    assert panel.name.text() == "Mareep • Lv. 14"
    assert panel.ev_yield.text() == "+1 Sp. Atk EV"
    assert not hasattr(panel, "hp")
    panel.close()
    assert app is not None


def test_opponent_panel_shows_both_active_slots_in_a_double_battle(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "pokemon_ev_tracker.ui.opponent_panel.resolve_sprite_asset",
        lambda _species_id, *, companion=False: SpriteAsset("missing", None),
    )
    panel = CurrentOpponentPanel()
    opponents = (
        SimpleNamespace(species_id=291, species="Ninjask", nickname="Ninja", level=16),
        SimpleNamespace(species_id=302, species="Sableye", nickname="Sable", level=15),
    )

    panel.set_opponents(opponents)

    assert panel.cards[0].name.text() == "Ninjask • Lv. 16"
    assert panel.cards[1].name.text() == "Sableye • Lv. 15"
    assert panel.cards[0].ev_yield.text() == "+2 Speed EV"
    assert panel.cards[1].ev_yield.text() == "+1 Attack · +1 Defense"
    assert panel.empty_label.isHidden()

    panel.set_opponents(())
    assert not panel.empty_label.isHidden()
    assert panel.cards[0].isHidden()
    assert panel.empty_label.text() == "Not currently battling"
    panel.close()
    assert app is not None


def test_double_battle_entries_stack_only_at_narrow_width(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "pokemon_ev_tracker.ui.opponent_panel.resolve_sprite_asset",
        lambda _species_id, *, companion=False: SpriteAsset("missing", None),
    )
    panel = CurrentOpponentPanel()
    panel.set_opponents(
        (
            SimpleNamespace(species_id=66, species="Machop", level=14),
            SimpleNamespace(species_id=41, species="Zubat", level=13),
        )
    )
    panel.show()
    app.processEvents()

    panel.resize(560, 400)
    app.processEvents()
    assert panel.opponents_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert panel.maximumHeight() >= 200

    panel.resize(900, 400)
    app.processEvents()
    assert panel.opponents_layout.direction() == QBoxLayout.Direction.LeftToRight
    assert panel.maximumHeight() < 400
    panel.close()


def test_delibird_replacement_updates_sprite_identity_level_and_yield(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    resolved_species = []

    def resolve(species_id, *, companion=False):
        resolved_species.append(species_id)
        return SpriteAsset("missing", None)

    monkeypatch.setattr(
        "pokemon_ev_tracker.ui.opponent_panel.resolve_sprite_asset",
        resolve,
    )
    panel = CurrentOpponentPanel()

    panel.set_opponent(
        SimpleNamespace(
            species_id=225,
            species="Delibird",
            level=14,
        )
    )

    assert panel.name.text() == "Delibird • Lv. 14"
    assert panel.ev_yield.text() == "+1 Speed EV"
    assert panel.cards[0]._species_id == 225

    panel.set_opponent(SimpleNamespace(species_id=56, species="Mankey", level=16))

    assert panel.name.text() == "Mankey • Lv. 16"
    assert panel.ev_yield.text() == "+1 Attack EV"
    assert panel.cards[0]._species_id == 56
    assert resolved_species == [225, 56]
    panel.close()
    assert app is not None


def test_opponent_panel_loads_and_retains_animated_sprite() -> None:
    app = QApplication.instance() or QApplication([])
    panel = CurrentOpponentPanel()

    panel.set_opponent(SimpleNamespace(species_id=66, species="Machop", level=14))

    movie = panel.cards[0]._movie
    assert movie is not None
    assert movie.isValid()
    assert movie.frameCount() > 1
    assert panel.cards[0].sprite.movie() is movie
    assert panel.cards[0].name.text() == "Machop • Lv. 14"
    assert panel.cards[0].ev_yield.text() == "+1 Attack EV"
    panel.close()
    app.processEvents()
