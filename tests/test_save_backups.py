import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.core.save_backups import INTERVAL_MS, SaveBackupService


@pytest.fixture
def service(tmp_path):
    saves = tmp_path / "BizHawk" / "SaveRAM"
    states = saves.parent / "State"
    saves.mkdir(parents=True)
    states.mkdir()
    (saves / "koi26.SaveRAM").write_bytes(b"current save")
    (saves / "koi26.SaveRAM.bak").write_bytes(b"previous save")
    (states / "koi26.MelonDS.QuickSave4.State").write_bytes(b"state")
    (states / "other.QuickSave4.State").write_bytes(b"unrelated")
    settings = AppSettings(bizhawk_save_ram_directory=str(saves))
    return SaveBackupService(settings, root=tmp_path / "backups")


def test_snapshot_creation_and_originals_untouched(service):
    source = service.resolve_source_files()[1][0][0]
    before = source.stat()
    snapshot = service.backup_now()
    datetime.strptime(snapshot.name[:19], "%Y-%m-%d_%H-%M-%S").astimezone()
    assert (snapshot / "SaveRAM/koi26.SaveRAM").read_bytes() == b"current save"
    assert (snapshot / "SaveRAM/koi26.SaveRAM.bak").read_bytes() == b"previous save"
    assert (snapshot / "States/koi26.MelonDS.QuickSave4.State").read_bytes() == b"state"
    assert not (snapshot / "States/other.QuickSave4.State").exists()
    assert source.stat().st_mtime_ns == before.st_mtime_ns
    assert not list(snapshot.parent.glob(".pending-*"))


def test_unchanged_across_restart(service):
    snapshot = service.backup_now()
    restarted = SaveBackupService(service.settings, root=service.root)
    assert restarted.backup_now() is None
    assert restarted._snapshots(snapshot.parent) == [snapshot]


def test_changed_save_and_same_second_never_overwrite(service, monkeypatch):
    import pokemon_ev_tracker.core.save_backups as module

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 8, 12, tzinfo=UTC)

    monkeypatch.setattr(module, "datetime", FixedDatetime)
    first = service.backup_now()
    source = service.resolve_source_files()[1][0][0]
    source.write_bytes(b"rolled back by emulator")
    second = service.backup_now()
    assert second != first
    assert second.name.startswith(first.name + "_")
    assert (first / "SaveRAM/koi26.SaveRAM").read_bytes() == b"current save"
    assert (second / "SaveRAM/koi26.SaveRAM").read_bytes() == b"rolled back by emulator"
    assert service.backup_now() is None


@pytest.mark.parametrize("relative", ["SaveRAM/koi26.SaveRAM.bak", "State/koi26.MelonDS.QuickSave4.State"], ids=["bak", "state"])
def test_bak_or_state_changes_trigger_snapshot(service, relative):
    first = service.backup_now()
    source = Path(service.settings.bizhawk_save_ram_directory).parent / relative
    source.write_bytes(b"changed")
    second = service.backup_now()
    assert second and second != first


def test_retention_keeps_recent_and_at_least_100(service):
    game = service.root / "koi26"
    now = datetime.now(UTC)
    for i in range(105):
        created = now - timedelta(days=9, minutes=105-i)
        path = game / created.strftime("%Y-%m-%d_%H-%M-%S")
        path.mkdir(parents=True)
        (path / "manifest.json").write_text(json.dumps({"created_at": created.isoformat()}))
    recent = game / now.strftime("%Y-%m-%d_%H-%M-%S")
    recent.mkdir()
    (recent / "manifest.json").write_text(json.dumps({"created_at": now.isoformat()}))
    foreign = game / "user-files"
    foreign.mkdir()
    service.cleanup_old_backups(game)
    assert len(service._snapshots(game)) == 100
    assert recent.exists() and foreign.exists()
    with pytest.raises(ValueError):
        service.cleanup_old_backups(game.parent.parent)


def test_retention_preserves_only_snapshot(service):
    snapshot = service.backup_now()
    (snapshot / "manifest.json").write_text(json.dumps({"created_at": "2000-01-01T00:00:00+00:00"}))
    service.cleanup_old_backups(snapshot.parent)
    assert snapshot.exists()


def test_missing_save_does_not_crash(service, caplog):
    service.resolve_source_files()[1][0][0].unlink()
    assert service.backup_now() is None
    assert "unavailable" in caplog.text
    assert not service.root.exists()


def test_failed_copy_is_retried_and_does_not_publish(service, monkeypatch, caplog):
    original = service.backup_now()
    attempts = []

    def fail(*args):
        attempts.append(args)
        raise PermissionError("Busy")

    monkeypatch.setattr(service, "_copy", fail)
    assert service.backup_now() is None
    assert len(attempts) == 3
    assert service._snapshots(original.parent) == [original]
    assert not list(original.parent.glob(".pending-*"))
    assert "Automatic backup failed" in caplog.text


def test_changing_during_copy_does_not_publish(service, monkeypatch):
    copy = service._copy

    def race(source, target=None):
        digest = copy(source, target)
        if target is not None:
            source.write_bytes(source.read_bytes() + b"changed")
        return digest

    monkeypatch.setattr(service, "_copy", race)
    assert service.backup_now() is None
    assert not service._snapshots(service.root / "koi26")


def test_save_changing_while_later_state_is_copied_does_not_publish(service, monkeypatch):
    copy = service._copy
    source_save = service.resolve_source_files()[1][0][0]

    def race(source, target=None):
        digest = copy(source, target)
        if target is not None and source.suffix == ".State":
            source_save.write_bytes(b"new save while copying state")
        return digest

    monkeypatch.setattr(service, "_copy", race)
    assert service.backup_now() is None
    assert not service._snapshots(service.root / "koi26")
    assert not list((service.root / "koi26").glob(".pending-*"))


def test_snapshot_publish_retries_temporary_access_error(service, monkeypatch):
    rename = Path.rename
    calls = []

    def busy_once(path, target):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("Temporary sharing violation")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", busy_once)
    snapshot = service.backup_now()
    assert snapshot is not None
    assert len(calls) == 2
    assert (snapshot / "SaveRAM/koi26.SaveRAM").read_bytes() == b"current save"


def test_retention_failure_does_not_hide_successful_snapshot(service, monkeypatch, caplog):
    def fail_cleanup(game_dir):
        raise PermissionError("Old snapshot is busy")

    monkeypatch.setattr(service, "cleanup_old_backups", fail_cleanup)
    snapshot = service.backup_now()
    assert snapshot is not None and snapshot.is_dir()
    assert "retention cleanup failed" in caplog.text


def test_game_resolution_refuses_ambiguity(service):
    from pathlib import Path
    (Path(service.settings.bizhawk_save_ram_directory) / "other.SaveRAM").write_bytes(b"other")
    assert service.resolve_source_files() == (None, [])
    service.game_name = lambda: "koi26"
    assert service.resolve_source_files()[0] == "koi26"
    service.settings.bizhawk_save_name = "other"
    assert service.resolve_source_files()[0] == "other"


def test_backup_root_must_be_outside_sources(service):
    from pathlib import Path
    service.root = Path(service.settings.bizhawk_save_ram_directory) / "backups"
    assert service.backup_now() is None
    assert not service.root.exists()


def test_five_minute_timer_initial_backup_and_shutdown(service, monkeypatch):
    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(service, "request_backup", lambda: calls.append(True))
    service.start()
    assert service.timer.isActive()
    assert service.timer.interval() == INTERVAL_MS == 300_000
    assert service.timer.timerType() == Qt.TimerType.PreciseTimer
    assert calls == [True]
    service.stop()
    assert not service.timer.isActive()
    assert app is not None


def test_backup_path_settings_round_trip(tmp_path):
    settings = AppSettings(bizhawk_save_ram_directory="saves", bizhawk_state_directory="states",
                           bizhawk_save_name="koi26")
    path = tmp_path / "settings.json"
    settings.save(path)
    assert AppSettings.load(path) == settings


def test_timer_and_manual_request_share_backup_worker(service, monkeypatch):
    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(service, "backup_now", lambda: calls.append(True))
    service.request_backup()
    service._worker.join()
    service.timer.timeout.emit()
    service._worker.join()
    assert calls == [True, True]
    service.stop()
    service.request_backup()
    assert calls == [True, True]
    assert app is not None


def test_preferences_backup_section_and_manual_action(service):
    from pokemon_ev_tracker.ui.preferences_view import PreferencesView

    app = QApplication.instance() or QApplication([])
    view = PreferencesView(service.settings, SimpleNamespace(store=SimpleNamespace(active_run=None)),
                           SimpleNamespace(display_name="Platinum", capabilities=SimpleNamespace(pc_storage=True)))
    calls = []
    view.backup_requested.connect(lambda: calls.append(True))
    service.status_changed.connect(view.update_backup_status)
    service._publish_status("Ready")
    assert "backups" in view.sections
    assert str(service.root) in view.backup_status.text()
    view.backup_button.click()
    assert calls == [True]
    view.close()
    assert app is not None
