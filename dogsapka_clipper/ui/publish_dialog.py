"""Publishing: manual export (always available) + optional official YouTube / TikTok upload."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication
from shiboken6 import isValid
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)

from dogsapka_clipper.db.models import ExportedVideo
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.pages.library import open_path
from dogsapka_clipper.ui.widgets.common import label


class _Prog(QObject):
    value = Signal(int)


class PublishDialog(QDialog):
    def __init__(self, backend, export_id: int, parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.export_id = export_id
        self._uploading = False
        self.setWindowTitle("Publish")
        self.setMinimumWidth(620)
        with backend.db.session() as s:
            e = s.get(ExportedVideo, export_id)
            self.path = Path(e.path)
            c = e.clip
            title, desc, tags = (c.title, c.description, c.hashtags) if c else (self.path.stem, "", [])
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 22, 22, 22)
        v.addWidget(label("Publish video", "H2"))
        v.addWidget(label(str(self.path), "Muted", wrap=True))
        f = QFormLayout()
        self.title = QLineEdit(title)
        f.addRow("Title", self.title)
        self.desc = QPlainTextEdit(desc)
        self.desc.setMaximumHeight(120)
        f.addRow("Description", self.desc)
        self.tags = QLineEdit(" ".join(tags or []))
        f.addRow("Hashtags", self.tags)
        v.addLayout(f)

        v.addWidget(label("Manual export (no account needed)", "H2"))
        mh = QHBoxLayout()
        cp = QPushButton("Copy caption")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.caption()))
        mh.addWidget(cp)
        sv = QPushButton("Save caption .txt")
        sv.clicked.connect(self.save_caption)
        mh.addWidget(sv)
        op = QPushButton("Show video file")
        op.clicked.connect(lambda: open_path(str(self.path.parent)))
        mh.addWidget(op)
        v.addLayout(mh)

        v.addWidget(label("Official platform upload", "H2"))
        from dogsapka_clipper.publishing.accounts import TT_MODES, YT_PRIVACY, published_key
        from dogsapka_clipper.ui.accounts_panel import TT_LABELS
        with backend.db.session() as s:
            done = set((s.get(ExportedVideo, export_id).published or {}).keys())
        self.targets: list[tuple[int, QCheckBox, QComboBox]] = []
        for acc in backend.accounts.list():
            yt = acc.platform == "youtube"
            row = QHBoxLayout()
            cb = QCheckBox(f"{'YouTube' if yt else 'TikTok'}: {acc.name}"
                           + ("   ✔ already uploaded" if published_key(acc.id) in done else ""))
            row.addWidget(cb, 1)
            mode = QComboBox()
            for m in (YT_PRIVACY if yt else TT_MODES):
                mode.addItem(m if yt else TT_LABELS[m], m)
            mode.setCurrentIndex(max(0, mode.findData(acc.privacy)))
            row.addWidget(mode)
            v.addLayout(row)
            self.targets.append((acc.id, cb, mode))
        if not self.targets:
            v.addWidget(label("No accounts connected. Add YouTube channels / TikTok accounts in Settings → Publishing "
                              "(requires your own Google / TikTok developer credentials).", "Muted", wrap=True))
        v.addWidget(label("Being uploaded vertically does not guarantee a video is shown as a Short or recommended.",
                          "Muted", wrap=True))
        self.confirm = QCheckBox("I confirm I have the rights and the streamer's permission to publish this content")
        v.addWidget(self.confirm)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        v.addWidget(self.bar)
        self.status = label("", "Muted", wrap=True)
        v.addWidget(self.status)
        bh = QHBoxLayout()
        bh.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bh.addWidget(close)
        self.go = QPushButton("Upload")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self.upload)
        bh.addWidget(self.go)
        v.addLayout(bh)
        self.prog = _Prog()
        self.prog.value.connect(self.bar.setValue)

    def caption(self) -> str:
        return f"{self.title.text()}\n\n{self.desc.toPlainText()}".strip()

    def save_caption(self) -> None:
        p = self.path.with_suffix(".caption.txt")
        p.write_text(self.caption(), encoding="utf-8")
        self.status.setText(f"Saved {p.name}")

    def upload(self) -> None:
        if self._uploading:
            return
        chosen = [(acc_id, mode.currentData()) for acc_id, cb, mode in self.targets if cb.isChecked()]
        if not chosen:
            QMessageBox.information(self, "Publish", "Select at least one account, or use the manual export.")
            return
        if not self.confirm.isChecked():
            QMessageBox.warning(self, "Publish", "Please confirm that you have the rights to publish this content.")
            return
        if QMessageBox.question(self, "Publish", f"Upload this video to {len(chosen)} account(s) now?") != QMessageBox.Yes:
            return
        b = self.backend
        title, desc = self.title.text(), self.desc.toPlainText()
        tags = self.tags.text().split()
        self._uploading = True
        self.go.setEnabled(False)
        self.bar.setVisible(True)
        self.status.setText("Uploading…")

        def job():
            results, errors = [], []
            for i, (acc_id, mode) in enumerate(chosen):
                def prog(f, i=i):
                    self.prog.value.emit(int((i + f) / len(chosen) * 100))
                try:
                    results.append(b.accounts.upload_now(acc_id, self.export_id, title, desc, tags, prog, privacy=mode))
                except Exception as e:      # one failing account must not stop the others
                    acc = b.accounts.get(acc_id)
                    errors.append(f"{acc.name if acc else acc_id}: {e}")
            return results, errors

        def done(res):
            results, errors = res
            msg = []
            for r in results:
                if r["platform"] == "youtube":
                    msg.append(f"YouTube ({r['account']}): uploaded (video id {r['id']}, {r['privacy']})")
                else:
                    msg.append(f"TikTok ({r['account']}): " + ("sent to the inbox - finish posting in the TikTok app"
                                                              if r.get("mode") == "inbox" else "post submitted"))
            msg += [f"Failed - {e}" for e in errors]
            if results:
                b.bus.emit("video.published", "; ".join(msg), "success" if not errors else "warning")
            if not isValid(self):
                return
            self._uploading = False
            self.go.setEnabled(True)
            self.status.setText("\n".join(msg))
            self.bar.setVisible(False)

        def err(e):
            if not isValid(self):
                return
            self._uploading = False
            self.go.setEnabled(True)
            self.status.setText(f"Upload failed: {e}")
            self.bar.setVisible(False)
        run_async(job, done, err)
