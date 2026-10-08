"""Independent, copy-only BizHawk save history. No emulator commands are issued."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from pokemon_ev_tracker.config.paths import app_data_directory

LOGGER = logging.getLogger(__name__)
INTERVAL_MS = 300_000


class SaveBackupService(QObject):
    status_changed = Signal(object)

    def __init__(self, settings, game_name=lambda: None, *, root=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.game_name = game_name
        self.root = Path(root) if root is not None else app_data_directory() / "backups"
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(INTERVAL_MS)
        self.timer.timeout.connect(self.request_backup)
        self._lock = threading.Lock()
        self._worker = None
        self._stopped = threading.Event()

    def start(self):
        self._stopped.clear()
        self.timer.start()
        self.request_backup()

    def stop(self):
        self.timer.stop()
        self._stopped.set()
        if self._worker is not None:
            self._worker.join()

    def request_backup(self):
        if self._stopped.is_set() or (self._worker is not None and self._worker.is_alive()):
            return
        self._worker = threading.Thread(target=self.backup_now, name="save-backup", daemon=True)
        self._worker.start()

    def resolve_source_files(self):
        directory = self.settings.bizhawk_save_ram_directory
        if not directory:
            return None, []
        save_dir = Path(directory).expanduser().resolve()
        state_dir = (Path(self.settings.bizhawk_state_directory).expanduser().resolve()
                     if self.settings.bizhawk_state_directory else save_dir.parent / "State")
        root = self.root.resolve()
        for source in (save_dir, state_dir):
            if root == source or root.is_relative_to(source) or source.is_relative_to(root):
                raise ValueError("Backup and source directories must be separate")
        saves = sorted(p for p in save_dir.iterdir()
                       if p.is_file() and p.name.lower().endswith(".saveram"))
        name = self.settings.bizhawk_save_name or self.game_name()
        if name:
            name = Path(str(name).replace("\\", "/")).name
            if name.lower().endswith((".nds", ".zip", ".saveram")):
                name = name.rsplit(".", 1)[0]
            saves = [p for p in saves if p.name[:-8].casefold() == name.casefold()]
        if len(saves) != 1:
            return None, []  # Never guess among multiple games.
        primary = saves[0]
        name = primary.name[:-8]
        files = [(primary, Path("SaveRAM") / primary.name)]
        bak = primary.with_name(primary.name + ".bak")
        if bak.is_file():
            files.append((bak, Path("SaveRAM") / bak.name))
        if state_dir.is_dir():
            files.extend((p, Path("States") / p.name) for p in sorted(state_dir.iterdir())
                         if p.is_file() and p.name.casefold().startswith(name.casefold() + ".")
                         and p.name.lower().endswith((".state", ".state.bak")))
        # Windows device names and traversal cannot become game directories.
        folder = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ")
        if not folder or folder.upper().split(".")[0] in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }:
            folder = "game_" + hashlib.sha256(name.encode()).hexdigest()[:16]
        return folder, files

    @staticmethod
    def _copy(source, destination=None):
        """Stream with shared read/write/delete access on Windows; never lock the save."""
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
            create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            create.restype = wintypes.HANDLE
            handle = create(str(source), 0x80000000, 7, None, 3, 0x80, None)
            if handle == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            stream = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb")
        else:
            stream = source.open("rb")
        digest = hashlib.sha256()
        with stream:
            output = destination.open("xb") if destination is not None else None
            try:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    if output is not None:
                        output.write(chunk)
            finally:
                if output is not None:
                    try:
                        output.flush()
                        os.fsync(output.fileno())
                    finally:
                        output.close()
        return digest.hexdigest()

    def _snapshots(self, game_dir):
        if not game_dir.is_dir() or game_dir.is_symlink() or game_dir.is_junction():
            return []
        return sorted((p for p in game_dir.iterdir() if p.is_dir() and not p.is_symlink()
                      and not p.is_junction()
                      and re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_[0-9a-f]+)?", p.name)
                      and (p / "manifest.json").is_file()), key=self._snapshot_time)

    @staticmethod
    def _snapshot_time(snapshot):
        try:
            return datetime.fromisoformat(json.loads(
                (snapshot / "manifest.json").read_text(encoding="utf-8"))["created_at"]).timestamp()
        except (OSError, ValueError, KeyError, TypeError):
            return datetime.strptime(snapshot.name[:19], "%Y-%m-%d_%H-%M-%S").astimezone().timestamp()

    def cleanup_old_backups(self, game_dir):
        root = self.root.resolve()
        if game_dir.is_symlink() or not game_dir.resolve().is_relative_to(root):
            raise ValueError("Retention target is outside the backup directory")
        snapshots = self._snapshots(game_dir)
        cutoff = (datetime.now(UTC) - timedelta(days=7)).timestamp()
        for snapshot in snapshots[:-100]:
            if self._snapshot_time(snapshot) < cutoff:
                if not snapshot.resolve().is_relative_to(root):
                    raise ValueError("Snapshot is outside the backup directory")
                # Reject linked descendants so cleanup can never traverse outside our history.
                if any(p.is_symlink() or p.is_junction() for p in snapshot.rglob("*")):
                    LOGGER.warning("Skipping retention cleanup of linked snapshot: %s", snapshot)
                    continue
                shutil.rmtree(snapshot)

    def backup_now(self):
        if not self._lock.acquire(blocking=False):
            return None
        staging = None
        try:
            game, files = self.resolve_source_files()
            if not files:
                LOGGER.warning("SaveRAM file unavailable or ambiguous; backup skipped.")
                self._publish_status("SaveRAM unavailable; configure source directories and save name.")
                return None
            game_dir = self.root / game
            if self.root.is_symlink() or self.root.is_junction():
                raise ValueError("Backup root must not be a link")
            game_dir.mkdir(parents=True, exist_ok=True)
            if game_dir.is_symlink() or game_dir.is_junction():
                raise ValueError("Backup game directory must not be a link")
            staging = game_dir / (".pending-" + uuid4().hex)
            staging.mkdir()
            LOGGER.info("Creating automatic save backup...")
            hashes = {}
            for source, relative in files:
                target = staging / relative
                target.parent.mkdir(exist_ok=True)
                for attempt in range(3):
                    try:
                        before = source.stat()
                        copied = self._copy(source, target)
                        verified = self._copy(source)
                        after = source.stat()
                        if copied != verified or before.st_size != after.st_size or (
                            before.st_mtime_ns != after.st_mtime_ns
                        ) or after.st_size == 0:
                            raise OSError(f"Source changed or was empty during backup: {source}")
                        hashes[relative.as_posix()] = copied
                        break
                    except OSError:
                        target.unlink(missing_ok=True)
                        if attempt == 2 or self._stopped.wait(0.1):
                            raise
            # A later state copy may take long enough for an earlier save to
            # change. Validate the entire source set before publishing history.
            if self.resolve_source_files() != (game, files):
                raise OSError("Save backup source files changed during backup")
            for source, relative in files:
                if self._copy(source) != hashes[relative.as_posix()]:
                    raise OSError(f"Source changed during backup: {source}")
            snapshots = self._snapshots(game_dir)
            previous = None
            if snapshots:
                try:
                    previous = json.loads((snapshots[-1] / "manifest.json").read_text())
                except (OSError, ValueError):
                    pass
            if previous is not None and previous.get("hashes") == hashes:
                LOGGER.info("Save unchanged; automatic backup skipped.")
                self.cleanup_old_backups(game_dir)
                self._publish_status("Save unchanged; backup skipped.")
                return None
            now = datetime.now(UTC).astimezone()
            (staging / "manifest.json").write_text(json.dumps({
                "created_at": now.isoformat(), "hashes": hashes,
            }, indent=2), encoding="utf-8")
            snapshot = game_dir / now.strftime("%Y-%m-%d_%H-%M-%S")
            if snapshot.exists():
                snapshot = game_dir / (snapshot.name + "_" + uuid4().hex)
            self._publish_snapshot(staging, snapshot)
            staging = None
            LOGGER.info("Save backup created: %s", snapshot)
            try:
                self.cleanup_old_backups(game_dir)
            except OSError:
                # An old snapshot held open by Explorer must not turn a
                # successful new recovery point into a reported failure.
                LOGGER.warning("Save backup created, but retention cleanup failed", exc_info=True)
            self._publish_status("Save backup created.")
            return snapshot
        except Exception as error:
            LOGGER.exception("Automatic backup failed")
            self._publish_status(f"Automatic backup failed: {error}")
            return None
        finally:
            if staging is not None:
                try:
                    if staging.resolve().is_relative_to(self.root.resolve()):
                        shutil.rmtree(staging)
                except OSError:
                    LOGGER.warning("Could not remove incomplete backup: %s", staging)
            self._lock.release()

    def _publish_snapshot(self, staging, snapshot):
        """Publish a complete snapshot, retrying transient Windows access errors."""
        for attempt in range(3):
            try:
                staging.rename(snapshot)
                return
            except PermissionError:
                if attempt == 2 or self._stopped.wait(0.1):
                    raise

    def _publish_status(self, message):
        try:
            snapshots = [p for game in self.root.iterdir() for p in self._snapshots(game)] if self.root.is_dir() else []
            latest = (max(snapshots, key=self._snapshot_time).name[:19] if snapshots else "Never")
            self.status_changed.emit({"directory": str(self.root), "last_backup": latest,
                                      "count": len(snapshots), "message": message})
        except Exception:
            LOGGER.warning("Could not refresh backup status", exc_info=True)
