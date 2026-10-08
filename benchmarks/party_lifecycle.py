"""Comparable C4 full-poll measurements; no live transport or user persistence."""

import argparse
import cProfile
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtWidgets import QApplication
from runtime_fixtures import measure
from test_training_integration import _party, _Source

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.config.training_preferences import EVTrainingPreferenceStore
from pokemon_ev_tracker.core.nuzlocke.storage import NuzlockeStore
from pokemon_ev_tracker.core.save_backups import SaveBackupService
from pokemon_ev_tracker.games.platinum.nuzlocke import PLATINUM_NUZLOCKE_PROFILE
from pokemon_ev_tracker.ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    root = ROOT / ".test-scratch" / f"c4-benchmark-{uuid4().hex}"
    root.mkdir(parents=True)
    source = _Source()
    source.party = _party(*tuple((index + 1, species) for index, species in enumerate((391, 388, 397, 404, 418, 93))))
    original_snapshot = source.snapshot

    def snapshot():
        result = original_snapshot()
        result.details["party_payload"] = SimpleNamespace(
            received_at=time.monotonic(), source="fixture",
            payload={"run_id": "benchmark", "frame": 10})
        return result

    source.snapshot = snapshot
    with patch.object(AppSettings, "save_default"), patch.object(SaveBackupService, "start"):
        window = MainWindow(
            AppSettings(prompt_for_nuzlocke_run=False, auto_pc_rediscovery=False,
                        notify_encounters=False), data_source=source,
            nuzlocke_store=NuzlockeStore(root / "runs.json"),
            training_preference_store=EVTrainingPreferenceStore(root / "training.json"))
        window.refresh_timer.stop()
        window.nuzlocke_view.store.create_run("benchmark", PLATINUM_NUZLOCKE_PROFILE).starter_observation_complete = True
        try:
            result = {"unchanged_full_poll": measure(window._refresh_ram_backend_debug)}
            profile = cProfile.Profile()
            profile.enable()
            for _ in range(30):
                window._refresh_ram_backend_debug()
            profile.disable()
            result["observer_calls_30_polls"] = {
                name: sum(entry.callcount for entry in profile.getstats()
                          if hasattr(entry.code, "co_name") and entry.code.co_name == "observe"
                          and Path(entry.code.co_filename).stem == name)
                for name in ("death_detection", "wipe_detection", "run_prompt")}
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
