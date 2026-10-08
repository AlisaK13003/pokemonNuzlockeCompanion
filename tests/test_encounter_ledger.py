from dataclasses import asdict, replace

import pytest
from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.ui.run_design import EncounterLedger
from pokemon_ev_tracker.ui.theme import apply_theme


@pytest.fixture
def ledger(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = NuzlockeStore(tmp_path / "ledger.json")
    run = store.create_run("Ledger", PLATINUM_NUZLOCKE_PROFILE)
    statuses = ("NOT_ENCOUNTERED", "CAUGHT", "DEAD", "FAILED")
    for index, (key, record) in enumerate(run.encounters.items()):
        run.encounters[key] = replace(record, status=statuses[index % 4])
    widget = EncounterLedger()
    apply_theme(widget)
    widget.resize(1100, 700)
    widget.refresh(run, PLATINUM_NUZLOCKE_PROFILE)
    yield widget, store, run, app
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_ledger_defaults_to_ten_open_first_and_expand_reverse_preserve_records(ledger):
    widget, _, run, _ = ledger
    before = asdict(run)
    assert len(widget.visible_records) == 10
    assert {r.status for r in widget.visible_records} == {"NOT_ENCOUNTERED"}
    widget.expand_button.click()
    assert len(widget.visible_records) == len(run.encounters)
    rank = {"NOT_ENCOUNTERED": 0, "CAUGHT": 1, "DEAD": 2, "FAILED": 3}
    values = [rank[r.status] for r in widget.visible_records]
    assert values == sorted(values)
    by_status = {status: [r.location_id for r in widget.visible_records if r.status == status]
                 for status in rank}
    widget.reverse_button.click()
    values = [rank[r.status] for r in widget.visible_records]
    assert values == sorted(values, reverse=True)
    for status in rank:
        assert [r.location_id for r in widget.visible_records if r.status == status] == by_status[status]
    widget.expand_button.click()
    assert len(widget.visible_records) == 10
    assert sum(not row.isHidden() for row in widget._row_pool) == 10
    assert asdict(run) == before


def test_filters_reuse_ten_rows_and_edit_the_current_location(ledger):
    widget, _, run, _ = ledger
    rows = tuple(widget._row_pool)
    edited = []
    widget.edit_requested.connect(edited.append)
    for status in ("CAUGHT", "DEAD", "FAILED", "NOT_ENCOUNTERED", "CAUGHT"):
        widget.filters[status].click()
        assert len(widget.visible_records) == 10
        assert all(record.status == status for record in widget.visible_records)
        assert tuple(widget._row_pool) == rows
        widget._row_pool[0].click()
        assert edited[-1] == widget.visible_records[0].location_id
    widget.expand_button.click()
    assert len(widget.visible_records) == sum(r.status == "CAUGHT" for r in run.encounters.values())
    assert "matching locations" in widget.footer.text()


def test_switching_runs_restores_limit_and_unchanged_refresh_keeps_widgets(ledger):
    widget, store, run, _ = ledger
    rows = tuple(widget._row_pool)
    widget.refresh(run, PLATINUM_NUZLOCKE_PROFILE)
    assert tuple(widget._row_pool) == rows
    widget.filters[""].click()
    assert widget.filters[""].isChecked()
    assert tuple(widget._row_pool) == rows
    widget.expand_button.click()
    other = store.create_run("Other run", PLATINUM_NUZLOCKE_PROFILE)
    widget.refresh(other, PLATINUM_NUZLOCKE_PROFILE)
    assert not widget.expand_button.isChecked()
    assert len(widget.visible_records) == 10
    assert sum(not row.isHidden() for row in widget._row_pool) == 10


def test_legacy_statuses_remain_visible_and_empty_filter_hides_all_rows(ledger):
    widget, _, run, _ = ledger
    keys = list(run.encounters)
    run.encounters[keys[0]] = replace(run.encounters[keys[0]], status="SKIPPED")
    run.encounters[keys[1]] = replace(run.encounters[keys[1]], status="DUPES")
    widget.refresh(run, PLATINUM_NUZLOCKE_PROFILE)
    widget.expand_button.click()
    assert {r.status for r in widget.visible_records[-2:]} == {"SKIPPED", "DUPES"}
    widget.reverse_button.click()
    assert {r.status for r in widget.visible_records[-2:]} == {"SKIPPED", "DUPES"}
    for key, record in run.encounters.items():
        run.encounters[key] = replace(record, status="NOT_ENCOUNTERED")
    widget.filters["CAUGHT"].click()
    assert not widget.visible_records
    assert all(row.isHidden() for row in widget._row_pool)
    assert "Showing 0 of 0" in widget.footer.text()
