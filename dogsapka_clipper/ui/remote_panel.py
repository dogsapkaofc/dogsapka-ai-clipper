"""Settings → Remote control (iPhone): enable the API, pair a phone with a QR code, revoke devices."""
from __future__ import annotations

import time
import urllib.parse
from datetime import datetime

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.services.remote import PAIR_TTL_S, local_addresses
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.widgets.common import clear_layout, label


def pairing_url(hosts: list[str], port: int, code: str) -> str:
    """Content of the pairing QR code, read by the iPhone app."""
    return "dogsapka://pair?" + urllib.parse.urlencode({"hosts": ",".join(hosts), "port": port, "code": code})


class QrWidget(QWidget):
    """Draws a QR code (qrcode package matrix) with Qt - no image library needed."""

    def __init__(self, data: str, size: int = 260) -> None:
        super().__init__()
        import qrcode
        q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(data)
        q.make(fit=True)
        self.matrix = q.get_matrix()
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("white"))
        n = len(self.matrix)
        cell = self.width() / n
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("black"))
        for y, row in enumerate(self.matrix):
            for x, on in enumerate(row):
                if on:
                    p.drawRect(QRectF(x * cell, y * cell, cell + 0.5, cell + 0.5))


class PairDialog(QDialog):
    def __init__(self, win) -> None:
        super().__init__(win)
        self.win = win
        b = win.backend
        self.setWindowTitle(tr("rc.pair_title"))
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 24)
        v.setSpacing(12)
        v.addWidget(label(tr("rc.pair_title"), "H2"))
        v.addWidget(label(tr("rc.pair_steps"), "Muted", wrap=True))
        code = b.remote.open_pairing()
        hosts = local_addresses()
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(QrWidget(pairing_url(hosts, b.remote.port, code)))
        row.addStretch()
        v.addLayout(row)
        big = label(f"{code[:3]} {code[3:]}")
        big.setAlignment(Qt.AlignCenter)
        big.setStyleSheet(f"font-size: 28pt; font-weight: 800; letter-spacing: 6px; color: {theme.CYAN};")
        v.addWidget(big)
        v.addWidget(label(tr("rc.manual").format(addr=", ".join(f"{h}:{b.remote.port}" for h in hosts)),
                          "Muted", wrap=True))
        self.left = label("", "Muted")
        self.left.setAlignment(Qt.AlignCenter)
        v.addWidget(self.left)
        close = QPushButton(tr("cancel"))
        close.clicked.connect(self.reject)
        v.addWidget(close)
        self._end = time.time() + PAIR_TTL_S
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(1000)
        self._tick()
        win.bridge.event.connect(self._on_event)

    def _tick(self) -> None:
        left = int(self._end - time.time())
        if left <= 0 or not self.win.backend.remote.pairing_open():
            self.left.setText(tr("rc.expired"))
            self._t.stop()
            return
        self.left.setText(tr("rc.valid").format(m=left // 60, s=left % 60))

    def _on_event(self, ev) -> None:
        if ev.kind == "remote.paired":
            self.win.toast(ev.message)
            self.accept()

    def done(self, r):
        self.win.backend.remote.close_pairing()
        try:
            self.win.bridge.event.disconnect(self._on_event)
        except (RuntimeError, TypeError):
            pass
        super().done(r)


class RemotePanel(QWidget):
    def __init__(self, page) -> None:
        super().__init__()
        self.page = page
        self.b = page.backend
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.status = label("", "Muted", wrap=True)
        v.addWidget(self.status)
        btns = QHBoxLayout()
        self.pair_btn = QPushButton("  " + tr("rc.pair"))
        self.pair_btn.setObjectName("Primary")
        self.pair_btn.setIcon(icon("plus"))
        self.pair_btn.clicked.connect(self.pair)
        btns.addWidget(self.pair_btn)
        btns.addStretch()
        v.addLayout(btns)
        v.addWidget(label(tr("rc.devices"), "H2"))
        self.rows = QVBoxLayout()
        v.addLayout(self.rows)
        v.addWidget(label(tr("rc.note"), "Muted", wrap=True))
        page.win.bridge.event.connect(lambda ev: self.refresh() if ev.kind in ("remote.paired", "settings.changed",
                                                                                  "remote.error") else None)
        self.refresh()

    def refresh(self) -> None:
        r = self.b.remote
        if r.running:
            self.status.setText(tr("rc.on").format(addr=", ".join(f"{h}:{r.port}" for h in local_addresses())))
        else:
            self.status.setText(tr("rc.off"))
        self.pair_btn.setEnabled(r.running)
        clear_layout(self.rows)
        devices = r.devices()
        if not devices:
            self.rows.addWidget(label(tr("rc.none"), "Muted"))
        for d in devices:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            seen = datetime.fromtimestamp(d.get("last_seen", d["created"])).strftime("%Y-%m-%d %H:%M")
            h.addWidget(label(f"📱  {d['name']}", None), 1)
            h.addWidget(label(tr("rc.last_seen").format(t=seen), "Muted"))
            rm = QPushButton(tr("rc.revoke"))
            rm.setObjectName("Danger")
            rm.clicked.connect(lambda _=False, dev=d: self.revoke(dev))
            h.addWidget(rm)
            self.rows.addWidget(w)

    def pair(self) -> None:
        PairDialog(self.page.win).exec()
        self.refresh()

    def revoke(self, dev: dict) -> None:
        if QMessageBox.question(self, tr("rc.revoke"), tr("rc.revoke_q").format(name=dev["name"])) == QMessageBox.Yes:
            self.b.remote.revoke(dev["id"])
            self.refresh()
