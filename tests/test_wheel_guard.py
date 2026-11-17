"""Mouse wheel over settings: scrolls the page, never changes a field unless it was clicked."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget  # noqa: E402

from dogsapka_clipper.ui.widgets.common import WheelGuard  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    guard = WheelGuard()
    a.installEventFilter(guard)
    yield a
    a.removeEventFilter(guard)


def _wheel(w, dy):
    ev = QWheelEvent(QPointF(5, 5), QPointF(w.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, dy),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(w, ev)


def _click(w):
    for t in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        QApplication.sendEvent(w, QMouseEvent(t, QPointF(5, 5), QPointF(w.mapToGlobal(QPoint(5, 5))),
                                              Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


@pytest.fixture
def page(app):
    sa = QScrollArea()
    inner = QWidget()
    lay = QVBoxLayout(inner)
    spin, combo = QSpinBox(), QComboBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    combo.addItems(["a", "b", "c"])
    lay.addWidget(spin)
    lay.addWidget(combo)
    for _ in range(60):
        lay.addWidget(QSpinBox())
    sa.setWidget(inner)
    sa.resize(300, 300)
    sa.show()
    app.processEvents()
    yield sa, spin, combo
    sa.close()


@pytest.mark.parametrize("via_line_edit", [False, True])
def test_hover_wheel_scrolls_page_not_field(page, via_line_edit):
    sa, spin, combo = page
    assert spin.focusPolicy() == Qt.StrongFocus          # the wheel itself can no longer focus the field
    _wheel(spin.lineEdit() if via_line_edit else spin, -120)
    _wheel(combo, -120)
    assert spin.value() == 50 and combo.currentIndex() == 0


def test_clicked_field_takes_the_wheel_until_left(page):
    sa, spin, combo = page
    _click(spin.lineEdit())
    spin.setFocus(Qt.MouseFocusReason)
    _wheel(spin.lineEdit(), 120)
    assert spin.value() == 51
    combo.setFocus(Qt.MouseFocusReason)                  # clicked elsewhere
    _wheel(spin, 120)
    assert spin.value() == 51
