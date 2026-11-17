from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from dogsapka_clipper.ui.widgets.common import label


class SmoothScrollArea(QScrollArea):
    """Mouse-wheel scrolling glides to the target instead of jumping (touchpads keep pixel scrolling)."""

    def __init__(self) -> None:
        super().__init__()
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._target: int | None = None

    def wheelEvent(self, e):
        if not e.pixelDelta().isNull() or e.modifiers() & Qt.ControlModifier or e.angleDelta().y() == 0:
            return super().wheelEvent(e)       # precise touchpad / zoom / horizontal: default handling
        bar = self.verticalScrollBar()
        running = self._anim.state() == QPropertyAnimation.Running
        start = self._target if running and self._target is not None else bar.value()
        step = int(-e.angleDelta().y() / 120 * 110)
        self._target = max(bar.minimum(), min(bar.maximum(), start + step))
        self._anim.stop()
        self._anim.setStartValue(bar.value())
        self._anim.setEndValue(self._target)
        self._anim.start()
        e.accept()


class Page(QWidget):
    def __init__(self, win, title: str, subtitle: str = "", scroll: bool = True) -> None:
        super().__init__()
        self.win = win
        self.backend = win.backend
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 8, 30, 20)
        outer.setSpacing(18)
        head = QHBoxLayout()
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(label(title, "H1"))
        if subtitle:
            tv.addWidget(label(subtitle, "Muted"))
        head.addLayout(tv)
        head.addStretch()
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head.addLayout(self.actions)
        outer.addLayout(head)
        if scroll:
            sa = SmoothScrollArea()
            sa.setWidgetResizable(True)
            inner = QWidget()
            self.body = QVBoxLayout(inner)
            self.body.setContentsMargins(0, 0, 8, 0)
            self.body.setSpacing(16)
            sa.setWidget(inner)
            outer.addWidget(sa, 1)
        else:
            self.body = QVBoxLayout()
            self.body.setSpacing(16)
            outer.addLayout(self.body, 1)
