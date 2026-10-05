from __future__ import annotations

import json
from pathlib import Path

import pytest

from pokemon_ev_tracker.config import training_preferences
from pokemon_ev_tracker.config.training_preferences import EVTrainingPreferenceStore
from pokemon_ev_tracker.core.ev_training import EV_STAT_KEYS, EVTrainingPreference


def test_preferences_round_trip_in_canonical_order(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    store = EVTrainingPreferenceStore(path)
    preference = EVTrainingPreference({"speed", "hp", "attack"})
    store.set(0x1234ABCD, preference)
    assert EVTrainingPreferenceStore(path).get(0x1234ABCD) == preference
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "1234ABCD": {"allowed_stats": ["hp", "attack", "speed"]},
    }
    assert not path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("stats", [frozenset(), frozenset(EV_STAT_KEYS)])
def test_empty_and_all_stats_can_be_saved(tmp_path, stats) -> None:
    path = tmp_path / "preferences.json"
    preference = EVTrainingPreference(stats)
    EVTrainingPreferenceStore(path).set(0xFFFFFFFF, preference)
    assert EVTrainingPreferenceStore(path).get(0xFFFFFFFF) == preference


def test_clear_removes_only_the_requested_pid_and_persists(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    store = EVTrainingPreferenceStore(path)
    store.set(0, EVTrainingPreference({"hp"}))
    store.set(1, EVTrainingPreference({"attack"}))
    assert store.clear(0)
    assert not store.clear(0)
    restored = EVTrainingPreferenceStore(path)
    assert restored.get(0) is None
    assert restored.get(1) == EVTrainingPreference({"attack"})


def test_reorder_and_evolution_do_not_change_pid_owned_preferences(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    store = EVTrainingPreferenceStore(path)
    store.set(10, EVTrainingPreference({"hp", "attack"}))
    store.set(20, EVTrainingPreference({"speed"}))
    party_before = ((10, "Gible"), (20, "Shinx"))
    reordered_and_evolved = ((20, "Luxio"), (10, "Gabite"))
    before = {pid: store.get(pid) for pid, _species in party_before}
    restored = EVTrainingPreferenceStore(path)
    assert [restored.get(pid) for pid, _species in reordered_and_evolved] == [before[20], before[10]]


@pytest.mark.parametrize("pid", [-1, 0x100000000, True, "12345678", 1.5, None])
def test_get_set_and_clear_reject_invalid_pid_without_writes(tmp_path, pid) -> None:
    path = tmp_path / "preferences.json"
    store = EVTrainingPreferenceStore(path)
    with pytest.raises(ValueError, match="unsigned 32-bit"):
        store.get(pid)
    with pytest.raises(ValueError, match="unsigned 32-bit"):
        store.set(pid, EVTrainingPreference({"hp"}))
    with pytest.raises(ValueError, match="unsigned 32-bit"):
        store.clear(pid)
    assert not path.exists()


def test_set_rejects_untyped_preference(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    with pytest.raises(TypeError, match="EVTrainingPreference"):
        EVTrainingPreferenceStore(path).set(1, {"attack"})
    assert not path.exists()


@pytest.mark.parametrize(
    "contents", [b"not json", b"[1, 2]", b"null", b"true", b"\xff\xfe"],
    ids=["invalid-json", "array", "null", "boolean", "invalid-utf8"],
)
def test_corrupt_or_wrong_root_file_loads_empty_without_overwriting(tmp_path, contents) -> None:
    path = tmp_path / "preferences.json"
    path.write_bytes(contents)
    assert EVTrainingPreferenceStore(path).get(1) is None
    assert path.read_bytes() == contents


def test_loader_keeps_valid_rows_and_skips_malformed_or_numeric_targets(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({
        "00000001": {"allowed_stats": ["attack"]},
        "00000002": {"allowed_stats": ["unknown"]},
        "00000003": {"allowed_stats": "attack"},
        "00000004": {"allowed_stats": [True]},
        "00000005": {"allowed_stats": [["hp"]]},
        "00000006": {"attack_target": 252},
        "00000007": ["hp"],
        "00000008": {"allowed_stats": {"hp": True}},
        "-1": {"allowed_stats": ["hp"]},
        "100000000": {"allowed_stats": ["hp"]},
        "oops": {"allowed_stats": ["hp"]},
        "aabbccdd": {"allowed_stats": []},
    }), encoding="utf-8")
    store = EVTrainingPreferenceStore(path)
    assert store.get(1) == EVTrainingPreference({"attack"})
    assert all(store.get(pid) is None for pid in range(2, 9))
    assert store.get(0xAABBCCDD) == EVTrainingPreference()


@pytest.mark.parametrize("action", ["new", "replace", "clear"])
def test_atomic_replace_error_rolls_back_memory_and_preserves_disk(tmp_path, monkeypatch, action):
    path = tmp_path / "preferences.json"
    store = EVTrainingPreferenceStore(path)
    original = EVTrainingPreference({"hp"})
    store.set(1, original)
    original_bytes = path.read_bytes()

    def fail_replace(_self, _target):
        raise OSError("replace denied")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace denied"):
        if action == "clear":
            store.clear(1)
        else:
            store.set(2 if action == "new" else 1, EVTrainingPreference({"attack"}))
    assert store.get(1) == original
    assert store.get(2) is None
    assert path.read_bytes() == original_bytes
    assert not path.with_suffix(".json.tmp").exists()


def test_write_error_rolls_back_without_creating_preference(tmp_path, monkeypatch) -> None:
    path = tmp_path / "preferences.json"
    store = EVTrainingPreferenceStore(path)

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail_write)
    with pytest.raises(OSError, match="disk full"):
        store.set(1, EVTrainingPreference({"speed"}))
    assert store.get(1) is None
    assert not path.exists()


def test_default_store_does_not_migrate_reinterpret_or_delete_old_targets(tmp_path, monkeypatch):
    old_path = tmp_path / "ev_targets.json"
    original = b'{"00000001": {"attack_target": 252, "speed_target": 252}}'
    old_path.write_bytes(original)
    preference_path = tmp_path / "ev_training_preferences.json"
    monkeypatch.setattr(training_preferences, "DEFAULT_EV_TRAINING_PREFERENCES_PATH", preference_path)
    store = EVTrainingPreferenceStore()
    assert store.path == preference_path
    assert store.get(1) is None
    assert not preference_path.exists()
    store.set(1, EVTrainingPreference({"hp"}))
    assert old_path.read_bytes() == original
    assert EVTrainingPreferenceStore().get(1) == EVTrainingPreference({"hp"})
