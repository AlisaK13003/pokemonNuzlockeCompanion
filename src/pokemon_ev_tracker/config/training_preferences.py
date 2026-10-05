"""PID-owned training preferences, separate from legacy numeric EV targets."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pokemon_ev_tracker.config.paths import app_data_directory
from pokemon_ev_tracker.core.ev_training import EV_STAT_KEYS, EVTrainingPreference

DEFAULT_EV_TRAINING_PREFERENCES_PATH = app_data_directory() / "ev_training_preferences.json"


class EVTrainingPreferenceStore:
    """Persist allowed stats by personality value through reorder and evolution.

    Only this store's file is read or written. Numeric EV targets are neither
    migrated nor interpreted as training preferences.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_EV_TRAINING_PREFERENCES_PATH
        self._preferences: dict[int, EVTrainingPreference] = {}
        self._load()

    def get(self, pid: int) -> EVTrainingPreference | None:
        return self._preferences.get(_normalize_pid(pid))

    def set(self, pid: int, preference: EVTrainingPreference) -> None:
        normalized_pid = _normalize_pid(pid)
        if not isinstance(preference, EVTrainingPreference):
            raise TypeError("Preference must be an EVTrainingPreference instance.")
        previous = self._preferences.get(normalized_pid)
        self._preferences[normalized_pid] = preference
        try:
            self._save()
        except Exception:
            if previous is None:
                del self._preferences[normalized_pid]
            else:
                self._preferences[normalized_pid] = previous
            raise

    def clear(self, pid: int) -> bool:
        normalized_pid = _normalize_pid(pid)
        if normalized_pid not in self._preferences:
            return False
        previous = self._preferences.pop(normalized_pid)
        try:
            self._save()
        except Exception:
            self._preferences[normalized_pid] = previous
            raise
        return True

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        for pid_key, values in payload.items():
            if not re.fullmatch(r"[0-9a-fA-F]{1,8}", pid_key):
                continue
            if not isinstance(values, dict) or not isinstance(values.get("allowed_stats"), list):
                continue
            try:
                preference = EVTrainingPreference(values["allowed_stats"])
            except ValueError:
                continue
            self._preferences[int(pid_key, 16)] = preference

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            f"{pid:08X}": {"allowed_stats": [stat for stat in EV_STAT_KEYS
                                          if stat in preference.allowed_stats]}
            for pid, preference in sorted(self._preferences.items())
        }
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary_path.replace(self.path)
        finally:
            # A failed replace must leave the old file and in-memory value intact.
            # Remove only this store's temporary file, without masking a save error.
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _normalize_pid(pid: int) -> int:
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 <= pid <= 0xFFFFFFFF:
        raise ValueError("PID must be an unsigned 32-bit integer.")
    return pid
