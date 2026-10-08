"""Figma header breakpoints preserve navigation and accessible labels."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pokemon_ev_tracker.ui.app_shell import AppShell
from pokemon_ev_tracker.ui.backend_status import BackendStatusWidget
from pokemon_ev_tracker.ui.theme import apply_theme


def test_navigation_breakpoints_and_resize_round_trip():
    app = QApplication.instance() or QApplication([])
    status = BackendStatusWidget()
    status.details_widget.hide()
    shell = AppShell("Pokémon Platinum", status)
    apply_theme(shell)
    shell.resize(1400, 800)
    shell.show()
    app.processEvents()
    routes = []
    shell.route_requested.connect(routes.append)
    for width, icons, compact_visible in (
        (1400, False, True),
        (1100, True, True),
        (980, True, True),
        (760, True, False),
        (520, True, False),
        (480, True, False),
        (1400, False, True),
    ):
        shell.resize(width, 800)
        app.processEvents()
        assert shell.width() == width
        assert shell.primary_nav.isVisible()
        assert shell.primary_nav.parentWidget() is shell.header
        assert shell.compact_button.isVisible() == compact_visible
        assert shell.settings_button.isVisible()
        assert shell.header_actions.geometry().right() == shell.header.width() - shell.header_layout.contentsMargins().right() - 1
        assert status.geometry().left() - shell.settings_button.geometry().right() == 13
        assert shell.brand.isVisible()
        for button, label in ((shell.tracker_button, "Tracker"),
                              (shell.nav_buttons["nuzlocke"], "Nuzlocke"),
                              (shell.nav_buttons["diagnostics"], "Diagnostics")):
            assert button.text() == ("" if icons else label)
            assert button.accessibleName() == label
            assert button.toolTip() == label
        assert status.compact_label.property("iconOnly") == (width <= 520)
        assert status.compact_label.width() == (31 if width <= 520 else 110 if width <= 760 else 232)
        shell.nav_buttons["nuzlocke"].click()
        assert routes[-1] == "nuzlocke"
    shell.set_compact(True)
    app.processEvents()
    assert not shell.primary_nav.isVisible()
    shell.set_compact(False)
    app.processEvents()
    assert shell.primary_nav.isVisible()
    shell.close()
