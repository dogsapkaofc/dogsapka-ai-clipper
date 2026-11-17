"""Reusable UI widgets."""
from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, Signal,
                            Property)
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLayout, QSizePolicy,
                               QVBoxLayout, QWidget, QWidgetItem)

from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.icons import pixmap


class Card(QFrame):
    """Glass card. On hover the border lights up and the shadow turns into a soft purple glow (animated)."""

    _REST = (QColor(0, 0, 0, 110), 28.0, 8.0)
    _HOVER = (QColor(139, 92, 246, 95), 42.0, 12.0)

    def __init__(self, parent=None, padding: int = 18, shadow: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(9)
        self._fx = None
        self._anims: list[QPropertyAnimation] = []
        if shadow:
            eff = QGraphicsDropShadowEffect(self)
            color, blur, off = self._REST
            eff.setBlurRadius(blur)
            eff.setOffset(0, off)
            eff.setColor(color)
            self.setGraphicsEffect(eff)
            self._fx = eff

    def _set_hover(self, on: bool) -> None:
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)
        if self._fx is None:
            return
        color, blur, off = self._HOVER if on else self._REST
        self._anims = []
        for prop, end in ((b"color", color), (b"blurRadius", blur), (b"yOffset", off)):
            a = QPropertyAnimation(self._fx, prop, self)
            a.setDuration(220)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.setEndValue(end)
            a.start()
            self._anims.append(a)

    def enterEvent(self, e):
        self._set_hover(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._set_hover(False)
        super().leaveEvent(e)


def label(text: str = "", obj: str | None = None, wrap: bool = False) -> QLabel:
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(wrap)
    return l


class IconLabel(QLabel):
    def __init__(self, name: str, color: str = theme.TEXT, size: int = 18) -> None:
        super().__init__()
        self.setPixmap(pixmap(name, color, size))
        self.setFixedSize(size + 4, size + 4)


class StatCard(Card):
    def __init__(self, title: str, icon_name: str, accent: str = theme.PURPLE) -> None:
        super().__init__(padding=16)
        top = QHBoxLayout()
        badge = QLabel()
        badge.setFixedSize(34, 34)
        badge.setAlignment(Qt.AlignCenter)
        badge.setPixmap(pixmap(icon_name, accent, 18))
        badge.setStyleSheet(f"background: {QColor(accent).darker(380).name()}; border-radius: 10px;")
        top.addWidget(badge)
        top.addStretch()
        self.lay.addLayout(top)
        self.value = label("—", "StatValue")
        self.lay.addWidget(self.value)
        self.title = label(title, "Muted")
        self.lay.addWidget(self.title)
        self.sub = label("", "Muted")
        self.sub.setStyleSheet("font-size: 8pt;")
        self.lay.addWidget(self.sub)
        self.setMinimumWidth(170)

    def set(self, value, sub: str = "") -> None:
        """Integers count smoothly from the previous value; other values are shown directly."""
        self.sub.setText(sub)
        old = getattr(self, "_num", None)
        if isinstance(value, int) and not isinstance(value, bool) and old is not None and old != value:
            from PySide6.QtCore import QVariantAnimation
            a = QVariantAnimation(self)
            a.setStartValue(int(old))
            a.setEndValue(int(value))
            a.setDuration(min(700, 250 + 40 * abs(value - old)))
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.valueChanged.connect(lambda v: self.value.setText(str(int(v))))
            self._count_anim = a
            a.start()
        else:
            self.value.setText(str(value))
        self._num = value if isinstance(value, int) and not isinstance(value, bool) else None


class Pill(QLabel):
    def __init__(self, text: str = "", color: str = theme.MUTED) -> None:
        super().__init__(text)
        self.set(text, color)

    def set(self, text: str, color: str) -> None:
        self.setText(text)
        c = QColor(color)
        self.setStyleSheet(f"color: {color}; background: rgba({c.red()},{c.green()},{c.blue()},0.14); "
                           f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.45); border-radius: 9px; "
                           f"padding: 2px 9px; font-size: 8pt; font-weight: 600;")


class Avatar(QLabel):
    def __init__(self, size: int = 48) -> None:
        super().__init__()
        self.sz = size
        self.setFixedSize(size, size)

    def set(self, image_path: str, name: str) -> None:
        s = self.sz
        pm = QPixmap(s * 2, s * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, s * 2, s * 2))
        p.setClipPath(path)
        src = QPixmap(image_path) if image_path and Path(image_path).exists() else QPixmap()
        if not src.isNull():
            p.drawPixmap(QRect(0, 0, s * 2, s * 2), src.scaled(s * 2, s * 2, Qt.KeepAspectRatioByExpanding,
                                                             Qt.SmoothTransformation))
        else:
            g = QLinearGradient(0, 0, s * 2, s * 2)
            g.setColorAt(0, QColor(theme.PURPLE))
            g.setColorAt(1, QColor(theme.CYAN))
            p.fillRect(QRect(0, 0, s * 2, s * 2), g)
            p.setPen(QColor("white"))
            f = QFont()
            f.setPixelSize(int(s * 0.8))
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRect(0, 0, s * 2, s * 2), Qt.AlignCenter, (name or "?")[:1].upper())
        p.end()
        pm.setDevicePixelRatio(2.0)
        self.setPixmap(pm)


class LiveGraph(QWidget):
    """Smooth rolling line graph with gradient fill and optional threshold line / markers."""

    def __init__(self, color: str = theme.CYAN, max_points: int = 120, y_max: float | None = None,
                 height: int = 110) -> None:
        super().__init__()
        self.color = QColor(color)
        self.values: deque[float] = deque(maxlen=max_points)
        self.y_max = y_max
        self.threshold: float | None = None
        self.markers: list[int] = []   # indices from the right
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def push(self, v: float) -> None:
        self.values.append(float(v))
        self.update()

    def set_values(self, vals) -> None:
        self.values.clear()
        self.values.extend(float(v) for v in vals)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(2, 6, -2, -4)
        p.setPen(QPen(QColor(theme.BORDER), 1, Qt.DotLine))
        for i in range(1, 4):
            y = r.top() + r.height() * i / 4
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        vals = list(self.values)
        if len(vals) < 2:
            return
        top = self.y_max if self.y_max else max(max(vals) * 1.2, 1e-6)
        n = self.values.maxlen or len(vals)
        step = r.width() / max(1, n - 1)
        x0 = r.right() - step * (len(vals) - 1)
        pts = [QPointF(x0 + i * step, r.bottom() - min(1.0, v / top) * r.height()) for i, v in enumerate(vals)]
        path = QPainterPath(pts[0])
        for a, b in zip(pts, pts[1:]):
            mx = (a.x() + b.x()) / 2
            path.cubicTo(QPointF(mx, a.y()), QPointF(mx, b.y()), b)
        fill = QPainterPath(path)
        fill.lineTo(pts[-1].x(), r.bottom())
        fill.lineTo(pts[0].x(), r.bottom())
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        c1 = QColor(self.color)
        c1.setAlpha(90)
        c2 = QColor(self.color)
        c2.setAlpha(0)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.fillPath(fill, g)
        p.setPen(QPen(self.color, 2.2))
        p.drawPath(path)
        if self.threshold is not None:
            y = r.bottom() - min(1.0, self.threshold / top) * r.height()
            p.setPen(QPen(QColor(theme.AMBER), 1.2, Qt.DashLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PURPLE2))
        for idx in self.markers:
            if 0 <= idx < len(pts):
                pt = pts[idx]
                p.drawEllipse(pt, 5, 5)
        p.setBrush(self.color)
        p.drawEllipse(pts[-1], 3.5, 3.5)


class Meter(QWidget):
    """Animated horizontal level meter (0..1)."""

    def __init__(self, color: str = theme.GREEN) -> None:
        super().__init__()
        self._v = 0.0
        self.color = color
        self.setFixedHeight(12)
        self._anim = QPropertyAnimation(self, b"level")
        self._anim.setDuration(350)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def get_level(self) -> float:
        return self._v

    def set_level(self, v: float) -> None:
        self._v = v
        self.update()

    level = Property(float, get_level, set_level)

    def animate_to(self, v: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._v)
        self._anim.setEndValue(max(0.0, min(1.0, v)))
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG2))
        p.drawRoundedRect(r, 6, 6)
        g = QLinearGradient(0, 0, r.width(), 0)
        g.setColorAt(0, QColor(theme.GREEN))
        g.setColorAt(0.7, QColor(theme.AMBER))
        g.setColorAt(1, QColor(theme.RED))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(0, 0, r.width() * self._v, r.height()), 6, 6)


class TimelineBar(QWidget):
    """Timeline with highlight markers (live) or a draggable trim range (editor)."""
    rangeChanged = Signal(float, float)
    seek = Signal(float)

    def __init__(self, editable: bool = False) -> None:
        super().__init__()
        self.editable = editable
        self.duration = 1.0
        self.start = 0.0
        self.end = 1.0
        self.position: float | None = None
        self.markers: list[tuple[float, float]] = []   # (t, score)
        self.waveform: list[float] = []
        self._drag: str | None = None
        self.setMinimumHeight(56 if editable else 36)
        self.setMouseTracking(True)

    def set_duration(self, d: float) -> None:
        self.duration = max(0.1, d)
        self.update()

    def set_range(self, a: float, b: float) -> None:
        self.start, self.end = max(0.0, a), min(self.duration, b)
        self.update()

    def _x(self, t: float) -> float:
        return 8 + (self.width() - 16) * t / self.duration

    def _t(self, x: float) -> float:
        return max(0.0, min(self.duration, (x - 8) / max(1, self.width() - 16) * self.duration))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        track = QRectF(8, h * 0.25, self.width() - 16, h * 0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG2))
        p.drawRoundedRect(track, 6, 6)
        if self.waveform:
            p.setPen(QPen(QColor(theme.MUTED), 1))
            n = len(self.waveform)
            for i, v in enumerate(self.waveform):
                x = track.left() + track.width() * i / n
                hh = track.height() * 0.45 * v
                p.drawLine(QPointF(x, track.center().y() - hh), QPointF(x, track.center().y() + hh))
        if self.editable:
            sel = QRectF(self._x(self.start), track.top(), self._x(self.end) - self._x(self.start), track.height())
            c = QColor(theme.PURPLE)
            c.setAlpha(80)
            p.setPen(QPen(QColor(theme.PURPLE), 1.5))
            p.setBrush(c)
            p.drawRoundedRect(sel, 5, 5)
            p.setBrush(QColor("white"))
            p.setPen(Qt.NoPen)
            for t in (self.start, self.end):
                p.drawRoundedRect(QRectF(self._x(t) - 4, track.top() - 6, 8, track.height() + 12), 3, 3)
        for t, s in self.markers:
            col = QColor(theme.GREEN if s >= 90 else theme.CYAN if s >= 75 else theme.AMBER)
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(self._x(t), track.center().y()), 5, 5)
        if self.position is not None:
            p.setPen(QPen(QColor(theme.CYAN), 2))
            x = self._x(self.position)
            p.drawLine(QPointF(x, 2), QPointF(x, h - 2))

    def mousePressEvent(self, e):
        t = self._t(e.position().x())
        if self.editable:
            ds, de = abs(self._x(self.start) - e.position().x()), abs(self._x(self.end) - e.position().x())
            if min(ds, de) < 12:
                self._drag = "s" if ds < de else "e"
                return
        self.seek.emit(t)

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        t = self._t(e.position().x())
        if self._drag == "s":
            self.start = min(t, self.end - 1.0)
        else:
            self.end = max(t, self.start + 1.0)
        self.update()
        self.rangeChanged.emit(self.start, self.end)

    def mouseReleaseEvent(self, e):
        self._drag = None


class FlowLayout(QLayout):
    """Wrapping grid layout for cards."""

    def __init__(self, parent=None, spacing: int = 16) -> None:
        super().__init__(parent)
        self._items: list = []
        self._sp = spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s + QSize(2 * self.contentsMargins().top(), 2 * self.contentsMargins().top())

    def _do(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > rect.right() and line_h > 0:
                x, y = rect.x(), y + line_h + self._sp
                nx, line_h = x + hint.width() + self._sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x, line_h = nx, max(line_h, hint.height())
        return y + line_h - rect.y()


def clear_layout(lay) -> None:
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w:
            w.setParent(None)
            w.deleteLater()


class ActivityFeed(QWidget):
    def __init__(self, max_items: int = 60) -> None:
        super().__init__()
        self.max_items = max_items
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.lay.addStretch()

    def add(self, message: str, level: str = "info", ts: float | None = None) -> None:
        row = QFrame()
        row.setStyleSheet(f"background: {theme.BG2}; border-radius: 9px;")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 7, 10, 7)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {theme.LEVEL_COLORS.get(level, theme.CYAN)}; border-radius: 4px;")
        h.addWidget(dot)
        m = QLabel(message)
        m.setWordWrap(True)
        m.setStyleSheet("background: transparent;")
        h.addWidget(m, 1)
        t = QLabel(time.strftime("%H:%M:%S", time.localtime(ts or time.time())))
        t.setObjectName("Muted")
        t.setStyleSheet("background: transparent; font-size: 8pt;")
        h.addWidget(t)
        self.lay.insertWidget(0, row)
        while self.lay.count() - 1 > self.max_items:
            it = self.lay.takeAt(self.lay.count() - 2)
            if it.widget():
                it.widget().deleteLater()
