"""'New version available' dialog: release notes, download with progress, install and restart."""
from __future__ import annotations

import copy

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QMessageBox, QProgressBar, QPushButton, QTextBrowser,
                               QVBoxLayout)

from dogsapka_clipper import __version__
from dogsapka_clipper.core import updater
from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.widgets.common import label


class _Progress(QObject):
    value = Signal(int)


class UpdateDialog(QDialog):
    def __init__(self, win, info: updater.UpdateInfo) -> None:
        super().__init__(win)
        self.win = win
        self.info = info
        self.setWindowTitle(tr("upd.title"))
        self.setMinimumSize(560, 460)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(12)
        v.addWidget(label(tr("upd.title"), "H1"))
        v.addWidget(label(tr("upd.versions").format(new=info.version, cur=__version__), "Muted"))
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes or tr("upd.no_notes"))
        v.addWidget(notes, 1)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        v.addWidget(self.bar)
        self.status = label("", "Muted", wrap=True)
        v.addWidget(self.status)
        row = QHBoxLayout()
        skip = QPushButton(tr("upd.skip"))
        skip.clicked.connect(self.skip)
        row.addWidget(skip)
        row.addStretch()
        later = QPushButton(tr("upd.later"))
        later.clicked.connect(self.reject)
        row.addWidget(later)
        self.go = QPushButton("  " + (tr("upd.install") if updater.can_self_install(info) else tr("upd.open_page")))
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self.install)
        row.addWidget(self.go)
        v.addLayout(row)
        self._prog = _Progress()
        self._prog.value.connect(self.bar.setValue)

    def skip(self) -> None:
        s = copy.deepcopy(self.win.backend.settings)
        s.interface.skipped_update_version = self.info.version
        self.win.backend.update_settings(s)
        self.reject()

    def install(self) -> None:
        if not updater.can_self_install(self.info):
            # only when this computer has no installer in the release (e.g. an Intel Mac)
            QDesktopServices.openUrl(QUrl(self.info.page_url))
            self.accept()
            return
        if self.win.backend.sessions and QMessageBox.question(
                self, tr("upd.title"), tr("upd.live_running")) != QMessageBox.Yes:
            return
        self.go.setEnabled(False)
        self.bar.setVisible(True)
        self.status.setText(tr("upd.downloading"))

        def done(path):
            self.status.setText(tr("upd.installing"))
            try:
                mode = updater.start_install(path)
            except Exception as e:
                self._failed(e)
                return
            if mode == "manual":
                QMessageBox.information(self, tr("upd.title"), tr("upd.manual_mac"))
            self.accept()
            self.win.quit_app(force=True)      # release files so the installer can replace them

        if updater.source_checkout():
            # running from the source code: pull the new version from GitHub and restart
            def pulled(_):
                self.status.setText(tr("upd.installing"))
                self.accept()
                self.win.restart_app(force=True)
            run_async(lambda: updater.update_source_checkout(lambda f: self._prog.value.emit(int(f * 100))),
                      pulled, self._failed)
            return
        run_async(lambda: updater.download(self.info, lambda f: self._prog.value.emit(int(f * 100))),
                  done, self._failed)

    def _failed(self, e) -> None:
        self.go.setEnabled(True)
        self.bar.setVisible(False)
        self.status.setText(f"{tr('error')}: {e}")


def check_for_updates(win, manual: bool = False) -> None:
    """Background check; shows the dialog when a newer release exists. ``manual`` also reports 'up to date'."""
    st = win.backend.settings.interface
    skip = "" if manual else st.skipped_update_version

    def found(info):
        if info:
            UpdateDialog(win, info).exec()
        elif manual:
            QMessageBox.information(win, tr("upd.title_none"), tr("upd.up_to_date").format(cur=__version__))

    def failed(e):
        if manual:
            QMessageBox.warning(win, tr("upd.title_none"), str(e))

    run_async(lambda: updater.check_for_update(skip_version=skip), found, failed)
