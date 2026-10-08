import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QPushButton

from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.ui.nuzlocke_view import NuzlockeView
from pokemon_ev_tracker.ui.run_library_cards import LEGENDARIES, legendary_for_run
from pokemon_ev_tracker.ui.theme import apply_theme


def test_legendary_mascot_is_stable_and_from_run_generation():
    for game, generation in [('pokemon-platinum', 4), ('pokemon-emerald', 3), ('pokemon-firered', 3), ('pokemon-crystal', 2)]:
        run = SimpleNamespace(game=game, run_id='recorded-run')
        assert legendary_for_run(run) in LEGENDARIES[generation]
        assert legendary_for_run(run) == legendary_for_run(run)
    assert len({legendary_for_run(SimpleNamespace(game='pokemon-platinum', run_id=str(i))) for i in range(30)}) > 1


def test_library_hero_archive_actions_and_responsive_layout(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = NuzlockeStore(tmp_path / 'runs.json')
    past = store.create_run('Old run', PLATINUM_NUZLOCKE_PROFILE)
    store.set_run_status(past.run_id, 'WON')
    active = store.create_run('Active run', PLATINUM_NUZLOCKE_PROFILE)
    view = NuzlockeView(store, (PLATINUM_NUZLOCKE_PROFILE,))
    apply_theme(view)
    view.show_section('Run Library')
    view.resize(1470, 1000)
    view.show()
    app.processEvents()
    library = view.library_cards
    assert view.sections.currentWidget() is library
    assert library.name.text() == active.name
    assert library.hero.height() == 294
    assert view.run_library_table.isHidden() or not view.run_library_table.isVisible()
    assert not view.run_back_button.isVisible()
    assert view.create_button.isVisible()
    assert library.archive_count.text() == '1 runs in your archive'
    opened = []
    monkeypatch.setattr(view, '_show_historical_run', lambda run: opened.append(run.run_id))
    library.archive.findChildren(QPushButton)[0].click()
    assert opened == [past.run_id]
    library.open.click()
    assert view.sections.tabText(view.sections.currentIndex()) == 'Dashboard'
    view.show_section('Run Library')
    view.resize(480, 1200)
    app.processEvents()
    assert view.width() == 480
    assert library.hero.height() == 460
    view.close()
