"""First-run wizard: verifies FFmpeg, Streamlink, GPU, disk, AI models and Twitch."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.processing.system import run_system_checks
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.icons import app_icon, pixmap
from dogsapka_clipper.ui.widgets.common import clear_layout, label


class SetupWizard(QDialog):
    def __init__(self, backend, parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle("DOGSAPKA AI CLIPPER – Setup")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(720, 640)
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 28, 28, 22)
        v.setSpacing(12)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(56, 56))
        head.addWidget(logo)
        tv = QVBoxLayout()
        tv.addWidget(label(tr("wiz.title"), "H1"))
        tv.addWidget(label("Automatic highlight detection and short-form editing for authorized Twitch streams.",
                           "Muted", wrap=True))
        head.addLayout(tv, 1)
        v.addLayout(head)
        lr = QHBoxLayout()
        lr.addWidget(label("Language / Nyelv", "Muted"))
        self.lang = QComboBox()
        self.lang.addItem("English", "en")
        self.lang.addItem("Magyar", "hu")
        self.lang.setCurrentIndex(self.lang.findData(backend.settings.interface.language))
        lr.addWidget(self.lang)
        lr.addStretch()
        v.addLayout(lr)
        v.addWidget(label(tr("wiz.check"), "H2"))
        holder = QWidget()
        self.list = QVBoxLayout(holder)
        self.list.setSpacing(8)
        v.addWidget(holder, 1)
        self.summary = label("", wrap=True)
        v.addWidget(self.summary)
        v.addWidget(label("Only monitor, record and republish channels you own or have explicit permission for. "
                          "DOGSAPKA uses official Twitch, YouTube and TikTok interfaces only.", "Muted", wrap=True))
        bh = QHBoxLayout()
        again = QPushButton(tr("wiz.recheck"))
        again.clicked.connect(self.check)
        bh.addWidget(again)
        bh.addStretch()
        fin = QPushButton(tr("wiz.finish"))
        fin.setObjectName("Primary")
        fin.clicked.connect(self.finish)
        bh.addWidget(fin)
        v.addLayout(bh)
        self.check()

    def check(self) -> None:
        clear_layout(self.list)
        self.list.addWidget(label("Checking…", "Muted"))
        run_async(lambda: run_system_checks(self.backend.paths.root, self.backend.twitch_status()), self.show_report,
                  lambda e: self.summary.setText(f"System check failed: {e}"))

    def show_report(self, rep) -> None:
        clear_layout(self.list)
        for c in rep.checks:
            row = QFrame()
            row.setStyleSheet(f"background: {theme.SURFACE}; border-radius: 10px;")
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 9, 12, 9)
            ic = QLabel()
            color = theme.GREEN if c.ok else (theme.RED if c.required else theme.AMBER)
            ic.setPixmap(pixmap("check" if c.ok else "alert", color, 18))
            h.addWidget(ic, 0, Qt.AlignTop)
            tv = QVBoxLayout()
            tv.setSpacing(2)
            tv.addWidget(label(c.name + ("" if c.required else "  (optional)")))
            d = label(c.detail, "Muted", wrap=True)
            tv.addWidget(d)
            if not c.ok and c.fix:
                fx = label("→ " + c.fix, wrap=True)
                fx.setStyleSheet(f"color: {color}; font-size: 9pt;")
                tv.addWidget(fx)
            h.addLayout(tv, 1)
            self.list.addWidget(row)
        self.summary.setText("✔ Everything required is available." if rep.ok else
                             "Some required components are missing - see the instructions above. "
                             "You can still explore the app and the simulation mode needs only FFmpeg.")

    def finish(self) -> None:
        s = self.backend.settings
        s.interface.first_run_complete = True
        s.interface.language = self.lang.currentData()
        self.backend.update_settings(s)
        self.accept()
