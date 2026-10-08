from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.ui.manage_locations import ManageLocations
from pokemon_ev_tracker.ui.nuzlocke_view import NuzlockeView
from pokemon_ev_tracker.ui.theme import apply_theme


@pytest.fixture
def manager(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Locations", PLATINUM_NUZLOCKE_PROFILE)
    widget = ManageLocations(store)
    apply_theme(widget)
    widget.resize(1400, 900)
    widget.open_run(run, PLATINUM_NUZLOCKE_PROFILE)
    yield widget, store, run, app
    widget.close()


def test_draft_search_save_and_linked_death(manager):
    widget, store, run, _ = manager
    first = widget.cards[0]
    original = run.encounters[first.original.location_id]
    first.nickname.setText("Sapphire")
    first.species.setText("Marill")
    first.notes.setText("Caught at level 4")
    first.set_status("DEAD")
    assert run.encounters[first.original.location_id] == original
    widget.search.setText("sapphire")
    assert widget.match_count.text() == "1 matching locations"
    saved = []
    widget.saved.connect(lambda: saved.append(True))
    widget.save()
    assert saved == [True]
    record = store.get_run(run.run_id).encounters[first.original.location_id]
    assert record.nickname == "Sapphire"
    assert record.status == "DEAD"
    assert run.deaths[0].encounter_location_id == record.location_id
    loaded = NuzlockeStore(store.path).active_run
    assert loaded.encounters[record.location_id] == record


def test_reset_cancel_preserves_identity_until_saved(manager):
    widget, store, run, _ = manager
    first = widget.cards[0]
    key = first.original.location_id
    record = replace(first.original, status="CAUGHT", stable_id="owned-pokemon", level=4,
                     met_location_id=12, species="Zubat", nickname="Amethyst")
    store.update_encounter(run.run_id, record)
    widget.open_run(run, PLATINUM_NUZLOCKE_PROFILE)
    first = widget.cards[0]
    first.notes.setText("Notes only")
    widget.save()
    assert run.encounters[key].stable_id == "owned-pokemon"
    assert run.encounters[key].level == 4
    widget.open_run(run, PLATINUM_NUZLOCKE_PROFILE)
    widget.cards[0].reset()
    widget.back.click()
    assert run.encounters[key].status == "CAUGHT"
    widget.open_run(run, PLATINUM_NUZLOCKE_PROFILE)
    assert widget.cards[0].nickname.text() == "Amethyst"
    widget.cards[0].reset()
    widget.save()
    assert run.encounters[key].status == "NOT_ENCOUNTERED"
    assert run.encounters[key].stable_id is None


def test_live_conflict_and_changed_run_do_not_overwrite(manager):
    widget, store, run, _ = manager
    card = widget.cards[0]
    card.nickname.setText("Draft")
    live = replace(card.original, status="CAUGHT", nickname="Live catch")
    store.update_encounter(run.run_id, live)
    widget.save()
    assert "live tracking" in widget.error.text()
    assert run.encounters[live.location_id] == live
    other = store.create_run("Other", PLATINUM_NUZLOCKE_PROFILE)
    widget.save()
    assert "active run changed" in widget.error.text()
    assert all(not r.nickname for r in other.encounters.values())


def test_atomic_save_failure_rolls_back_all_records_and_deaths(manager, monkeypatch):
    widget, store, run, _ = manager
    before = run.encounters.copy()
    widget.cards[0].set_status("DEAD")
    widget.cards[1].nickname.setText("Edited")
    def fail():
        raise OSError("Disk unavailable")
    monkeypatch.setattr(store, "save", fail)
    widget.save()
    assert "Disk unavailable" in widget.error.text()
    assert run.encounters == before
    assert not run.deaths


def test_footer_navigation_and_responsive_management(manager):
    widget, store, _, app = manager
    view = NuzlockeView(store, (PLATINUM_NUZLOCKE_PROFILE,))
    view.ledger.manage_button.click()
    assert view.sections.currentWidget() is view.manage_locations
    assert view.run_header.isHidden()
    view.manage_locations.back.click()
    assert view.sections.tabText(view.sections.currentIndex()) == "Dashboard"
    assert not view.run_header.isHidden()
    widget.show()
    widget.resize(480, 900)
    app.processEvents()
    assert widget._columns == 1
    assert widget.minimumSizeHint().width() <= 480
    view.close()
