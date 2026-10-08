import pytest
from PySide6.QtWidgets import QApplication, QLabel

from pokemon_ev_tracker.ui.disconnected_view import DisconnectedView
from pokemon_ev_tracker.ui.theme import apply_theme


@pytest.fixture
def waiting():
    app = QApplication.instance() or QApplication([])
    view = DisconnectedView()
    apply_theme(view)
    yield view, app
    view.close()


def test_retry_signal_and_actual_lua_hint(waiting):
    view, _ = waiting
    retried = []
    view.retry_requested.connect(lambda: retried.append(True))
    view.retry.click()
    assert retried == [True]
    assert "bizhawk / ev_tracker.lua" in view.script_path.text()
    assert "pokemon_companion.lua" not in view.script_path.text()
    assert len(view.steps) == 4
    assert any("Listening for Lua heartbeat" in item.text() for item in view.findChildren(QLabel))


@pytest.mark.parametrize(("width", "columns"), ((1100, 4), (720, 2), (480, 1)))
def test_setup_cards_reflow_without_horizontal_overflow(waiting, width, columns):
    view, app = waiting
    view.resize(width, 1500)
    view.show()
    app.processEvents()
    assert view.width() == width
    assert view._columns == columns
    assert view.content.width() <= min(width, 1280)
    assert all(card.geometry().right() < view.steps_layout.geometry().right()+1 for card in view.steps)
    assert view.minimumSizeHint().width() <= width


def test_animation_stays_in_place_and_stops_when_hidden(waiting):
    view, app = waiting
    view.resize(1100, 900)
    view.show()
    app.processEvents()
    assert view.timer.isActive()
    geometry = (view.pulse.geometry(), view.guide.geometry(), view.sizeHint())
    for _ in range(6):
        view.pulse.advance()
        app.processEvents()
        assert (view.pulse.geometry(), view.guide.geometry(), view.sizeHint()) == geometry
    view.hide()
    assert not view.timer.isActive()
