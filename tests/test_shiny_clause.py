from dataclasses import replace

import pytest
from test_companion_redesign import window as _window_fixture
from test_gen4_party_ram import _party_record
from test_nuzlocke_acquisition import _candidate, _location_id

from pokemon_ev_tracker.core.nuzlocke.acquisition import (
    PartyAcquisitionObserver,
    reconcile_party_encounters,
    reconcile_shiny_encounters,
)
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.games.platinum.locations import classify_platinum_acquisition
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.games.registry import default_game_provider
from pokemon_ev_tracker.pokemon.gen4.crypto import (
    calculate_checksum,
    decrypt_box_data,
    encrypt_box_data,
)
from pokemon_ev_tracker.pokemon.gen4.structure import decode_box_pokemon, decode_party_pokemon
from pokemon_ev_tracker.ui.shiny_clause import AddShinyDialog
from pokemon_ev_tracker.ui.sprite_loader import get_static_sprite

window = _window_fixture


def shiny_record(value=0, pid=0x12345678):
    record = bytearray(_party_record(pid, 77, (0, 0, 0, 0, 0, 0), nickname="Ember"))
    old_checksum = int.from_bytes(record[6:8], "little")
    plain = bytearray(decrypt_box_data(record[8:136], pid, old_checksum))
    tid = 0x7B42
    sid = tid ^ (pid & 0xFFFF) ^ (pid >> 16) ^ value
    plain[4:6] = tid.to_bytes(2, "little")
    plain[6:8] = sid.to_bytes(2, "little")
    checksum = calculate_checksum(plain)
    record[6:8] = checksum.to_bytes(2, "little")
    record[8:136] = encrypt_box_data(plain, pid, checksum)
    return bytes(record)


@pytest.mark.parametrize("value,expected", ((0, True), (7, True), (8, False), (15, False)))
@pytest.mark.parametrize("pid", (0x12345678, 0x793A7933))
def test_shiny_threshold_party_and_box(value, expected, pid):
    raw = shiny_record(value, pid)
    party = decode_party_pokemon(raw)
    box = decode_box_pokemon(raw[:136])
    assert party.diagnostics.checksum_valid and box.checksum_valid
    assert party.is_shiny is expected and box.is_shiny is expected


def _reconcile(store, candidates, **changes):
    options = {"connected": True, "valid_snapshot": True, "run": store.active_run,
               "store": store, "classify": classify_platinum_acquisition}
    options.update(changes)
    return reconcile_shiny_encounters(candidates, **options)


def test_shiny_baseline_persistence_party_pc_dedup_and_ledger_preserved(tmp_path):
    path = tmp_path / "runs.json"
    store = NuzlockeStore(path)
    run = store.create_run("Shinies", PLATINUM_NUZLOCKE_PROFILE)
    candidate = _candidate("shiny-id", 77, "Ponyta", is_shiny=True, nickname="Ember")
    route = _location_id("Route 201")
    before = run.encounters.copy()
    assert len(_reconcile(store, (candidate,))) == 1
    assert run.encounters == before
    assert run.encounters[route].status == "NOT_ENCOUNTERED"
    store.mark_pokemon_observed(run.run_id, [candidate.stable_id])
    assert _reconcile(store, (replace(candidate, source_location="BOX"),)) == ()
    loaded = NuzlockeStore(path)
    assert _reconcile(loaded, (candidate,)) == ()
    assert loaded.active_run.shiny_encounters == run.shiny_encounters
    assert reconcile_party_encounters((candidate,), connected=True, valid_snapshot=True,
        run=run, store=store, classify=classify_platinum_acquisition) == ()
    observer = PartyAcquisitionObserver()
    for candidates in ((), (candidate,), (replace(candidate, source_location="BOX"),)):
        assert observer.observe(candidates, connected=True, valid_snapshot=True, run=run,
            store=store, classify=classify_platinum_acquisition) == ()
    assert run.acquisition_events == []


def test_shiny_preserves_caught_route_and_other_catches_reconcile(tmp_path):
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Bonus catches", PLATINUM_NUZLOCKE_PROFILE)
    normal = _candidate("normal-id", nickname="Original")
    shiny = _candidate("shiny-id", 77, "Ponyta", is_shiny=True)
    reconcile_party_encounters((normal, shiny), connected=True, valid_snapshot=True,
        run=run, store=store, classify=classify_platinum_acquisition)
    before = run.encounters.copy()
    assert len(_reconcile(store, (shiny,))) == 1
    assert run.encounters == before
    assert any(r.nickname == "Original" for r in run.encounters.values())


@pytest.mark.parametrize("changes", ({"connected": False}, {"valid_snapshot": False}, {"run": None}))
def test_shiny_rejects_invalid_snapshot_and_missing_run(tmp_path, changes):
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Safe", PLATINUM_NUZLOCKE_PROFILE)
    assert _reconcile(store, (_candidate("shiny-id", is_shiny=True),), **changes) == ()
    assert run.shiny_encounters == []


def test_shiny_skips_eggs_normal_pokemon_and_rolls_back_failed_save(tmp_path, monkeypatch):
    store = NuzlockeStore(tmp_path / "runs.json")
    run = store.create_run("Atomic", PLATINUM_NUZLOCKE_PROFILE)
    assert _reconcile(store, (_candidate("egg", is_shiny=True, is_egg=True), _candidate("normal"))) == ()
    def fail():
        raise OSError("Disk unavailable")
    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(OSError):
        _reconcile(store, (_candidate("shiny-id", is_shiny=True),))
    assert run.shiny_encounters == [] and run.resolved_acquisition_ids == []


def test_live_party_shiny_recorded_on_first_read_and_rendered(window):
    widget, source, _, store, app = window
    run = store.create_run("Shiny party", PLATINUM_NUZLOCKE_PROFILE)
    pokemon = source.party.pokemon[0]
    # Exercise the actual decoder/provider instead of a UI-only shiny flag.
    decoded = decode_party_pokemon(shiny_record())
    pokemon = replace(pokemon, species_id=77, species="Ponyta", nickname="Ember",
                      stable_id=decoded.stable_id, decoded=decoded)
    source.party = replace(source.party, pokemon=(pokemon,), party_count=1)
    widget._refresh_ram_backend_debug()
    app.processEvents()
    assert len(run.shiny_encounters) == 1
    assert run.shiny_encounters[0].nickname == "Ember"
    assert default_game_provider().party_acquisition_candidate(pokemon).is_shiny
    panel = widget.nuzlocke_view.shiny_clause
    assert panel.count.text() == "1"
    assert not get_static_sprite(77, 56, shiny=True).isNull()
    first_row = panel.entries.itemAt(0).widget()
    widget._refresh_ram_backend_debug()
    assert panel.entries.itemAt(0).widget() is first_row
    assert len(run.shiny_encounters) == 1


def test_manual_shiny_dialog_and_view_save_separate_from_route(window, monkeypatch):
    widget, _, _, store, _ = window
    run = store.create_run("Manual shiny", PLATINUM_NUZLOCKE_PROFILE)
    original = run.encounters.copy()
    def accept(dialog):
        dialog.species.setCurrentIndex(dialog.species.findData(77))
        dialog.nickname.setText("Ember")
        dialog.level.setValue(19)
        dialog.location.setCurrentIndex(1)
        return dialog.DialogCode.Accepted
    monkeypatch.setattr(AddShinyDialog, "exec", accept)
    widget.nuzlocke_view._add_shiny_manually()
    assert len(run.shiny_encounters) == 1
    assert run.shiny_encounters[0].source_location == "MANUAL"
    assert run.shiny_encounters[0].species_name == "Ponyta"
    assert run.encounters == original
    assert widget.nuzlocke_view.shiny_clause.count.text() == "1"


def test_pc_shiny_baseline_works_without_party_and_stale_pc_is_rejected(window):
    from test_pc_storage_acquisition_ui import _box_mon, _snapshot, _SnapshotSource

    widget, _, _, store, app = window
    run = store.create_run("Boxed shiny", PLATINUM_NUZLOCKE_PROFILE)
    decoded = decode_box_pokemon(shiny_record()[:136])
    boxed = _box_mon(species_id=77, species_name="Ponyta", nickname="Ember")
    boxed.decoded = decoded
    boxed.stable_id = decoded.stable_id
    source = _SnapshotSource(_snapshot((boxed,)))
    widget.ram_data_source = source
    widget._refresh_ram_backend_debug()
    app.processEvents()
    assert len(run.shiny_encounters) == 1
    assert run.shiny_encounters[0].source_location == "BOX"
    assert run.acquisition_events == []
    source.current_snapshot = _snapshot((boxed,), frame=90)
    widget._refresh_ram_backend_debug()
    assert len(run.shiny_encounters) == 1
    second = _box_mon(species_id=77, species_name="Ponyta", nickname="Other")
    second.decoded.is_shiny = True
    stale = _snapshot((boxed, second), frame=100)
    stale.details["pc_storage_payload_fresh"] = False
    source.current_snapshot = stale
    widget._refresh_ram_backend_debug()
    assert len(run.shiny_encounters) == 1
