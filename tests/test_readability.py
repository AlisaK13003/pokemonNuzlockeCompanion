from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from pokemon_ev_tracker.ui.theme import apply_theme, readable_stylesheet


def test_full_size_type_is_larger_and_compact_round_trip_restores_it():
    app = QApplication.instance() or QApplication([])
    root = QWidget()
    root.compact_mode = False
    layout = QVBoxLayout(root)
    labels = []
    for role in ("trainingMicro", "inspectionMeta", "offlineStepTitle"):
        item = QLabel("Readable text")
        item.setProperty("uiRole", role)
        layout.addWidget(item)
        labels.append(item)
    apply_theme(root)
    root.show()
    app.processEvents()
    full = [item.font().pixelSize() for item in labels]
    root.compact_mode = True
    apply_theme(root)
    app.processEvents()
    compact = [item.font().pixelSize() for item in labels]
    assert all(normal > dense for normal, dense in zip(full, compact))
    assert min(full) >= 10
    root.compact_mode = False
    apply_theme(root)
    app.processEvents()
    assert [item.font().pixelSize() for item in labels] == full
    root.close()


def test_local_panel_type_scale_keeps_nontext_geometry():
    stylesheet = "QLabel { font-size: 9px; border: 1px solid red; padding: 4px; }"
    scaled = readable_stylesheet(stylesheet)
    assert "font-size: 12px" in scaled
    assert "border: 1px solid red" in scaled
    assert "padding: 4px" in scaled
