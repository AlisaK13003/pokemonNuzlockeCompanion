"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.config.settings import AppSettings
from pokemon_ev_tracker.ui.main_window import MainWindow


def configure_logging() -> None:
    configured_level = getattr(
        logging,
        os.environ.get("POKEMON_EV_TRACKER_LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    )
    log_level = configured_level if isinstance(configured_level, int) else logging.INFO
    logging.basicConfig(
        level=log_level,
        format=("%(asctime)s %(levelname)s [%(name)s] %(message)s"
                if log_level <= logging.DEBUG else "%(asctime)s %(levelname)s: %(message)s"),
        datefmt="%H:%M:%S",
    )


def main() -> int:
    configure_logging()
    settings = AppSettings.load_default()

    app = QApplication(sys.argv)
    window = MainWindow(settings=settings)
    app.aboutToQuit.connect(window.save_backup_service.stop)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
