"""The focus editor collects selected stats without numeric target semantics."""

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QSpinBox

from pokemon_ev_tracker.core.ev_training import EV_STAT_KEYS, EVTrainingPreference
from pokemon_ev_tracker.ui.training_focus_dialog import TrainingFocusDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_focus_dialog_saves_exact_selected_stats(app):
    dialog = TrainingFocusDialog("Shizuku")
    assert set(dialog.stat_buttons) == set(EV_STAT_KEYS)
    assert not dialog.findChildren(QSpinBox)
    dialog.stat_buttons["hp"].click()
    dialog.stat_buttons["attack"].click()
    dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.preference().allowed_stats == frozenset({"hp", "attack"})


def test_focus_dialog_cancel_leaves_initial_preference_unchanged(app):
    initial = EVTrainingPreference(frozenset({"speed"}))
    dialog = TrainingFocusDialog("Shizuku", initial)
    assert dialog.stat_buttons["speed"].isChecked()
    dialog.stat_buttons["speed"].click()
    dialog.stat_buttons["defense"].click()
    dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert initial.allowed_stats == frozenset({"speed"})


@pytest.mark.parametrize("selected", (frozenset(), frozenset(EV_STAT_KEYS)))
def test_focus_dialog_allows_empty_and_all_six_stats(app, selected):
    dialog = TrainingFocusDialog("Shizuku", EVTrainingPreference(selected))
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save.isEnabled()
    save.click()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.preference().allowed_stats == selected
