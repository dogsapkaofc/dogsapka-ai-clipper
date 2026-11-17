from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QWidget)
from sqlalchemy import or_, select

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.db.models import Clip, Streamer
from dogsapka_clipper.detection.scorer import category
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.widgets.common import Card, FlowLayout, Pill, clear_layout, label

STATUS_COLORS = {"done": theme.GREEN, "processing": theme.CYAN, "rendering": theme.CYAN, "pending": theme.MUTED,
                 "none": theme.MUTED, "error": theme.RED}


def open_path(p: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


class ClipCard(Card):
    def __init__(self, page, c: Clip, rank: int | None) -> None:
        super().__init__(padding=12)
        self.setFixedSize(300, 372)
        thumb = QLabel()
        thumb.setFixedSize(276, 155)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setStyleSheet(f"background: {theme.BG2}; border-radius: 10px;")
        pm = QPixmap(c.thumbnail_path) if c.thumbnail_path and Path(c.thumbnail_path).exists() else QPixmap()
        if not pm.isNull():
            thumb.setPixmap(pm.scaled(276, 155, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                            .copy(0, 0, 276, 155))
        else:
            thumb.setText("No thumbnail")
        dur = QLabel(f"{int(c.duration // 60)}:{int(c.duration % 60):02d}", thumb)
        dur.setStyleSheet("background: rgba(0,0,0,0.7); border-radius: 6px; padding: 2px 6px; font-size: 8pt;")
        dur.move(228, 125)
        if rank:
            rk = QLabel(f"#{rank}", thumb)
            rk.setStyleSheet(f"background: {theme.PURPLE}; border-radius: 6px; padding: 2px 7px; font-weight: 700;")
            rk.move(8, 8)
        self.lay.addWidget(thumb)
        title = label(c.title or c.reason or "Highlight", wrap=True)
        title.setStyleSheet("font-weight: 600;")
        title.setMaximumHeight(40)
        self.lay.addWidget(title)
        self.lay.addWidget(label(f"{c.streamer.display_name} · {c.created_at:%Y-%m-%d %H:%M}", "Muted"))
        reason = c.reason or "—"
        why = label(reason if len(reason) <= 90 else reason[:88].rsplit(" ", 1)[0] + "…", "Muted", wrap=True)
        why.setToolTip(reason)
        why.setStyleSheet("font-size: 8pt;")
        why.setFixedHeight(32)
        self.lay.addWidget(why)
        pills = QHBoxLayout()
        sc = c.detection_score
        pills.addWidget(Pill(f"AI {sc:.0f}", theme.GREEN if sc >= 90 else theme.CYAN if sc >= 75 else theme.AMBER))
        pills.addWidget(Pill(f"Q {c.quality_score:.0f}", theme.PURPLE2))
        pills.addWidget(Pill(c.export_status if c.export_status != "none" else c.edit_status,
                             STATUS_COLORS.get(c.export_status if c.export_status != "none" else c.edit_status, theme.MUTED)))
        if c.duplicate_of:
            pills.addWidget(Pill("duplicate", theme.AMBER))
        pills.addStretch()
        self.lay.addLayout(pills)
        self.setToolTip(f"{category(sc)}\nDetection score {sc:.1f} · estimated quality {c.quality_score:.1f}\n"
                        "Scores are estimates to help prioritise review, not virality predictions.")
        self.lay.addStretch()
        btns = QHBoxLayout()
        ed = QPushButton("  " + tr("lib.open_editor"))
        ed.setObjectName("Primary")
        ed.setIcon(icon("edit"))
        ed.clicked.connect(lambda: page.win.open_editor(c.id))
        btns.addWidget(ed)
        exports = [e.path for e in c.exports if Path(e.path).exists()]
        play = QPushButton()
        play.setIcon(icon("play", theme.CYAN))
        play.setToolTip(tr("lib.play") + (" (latest export)" if exports else " (source clip)"))
        play.clicked.connect(lambda: open_path(exports[-1] if exports else c.source_path))
        btns.addWidget(play)
        fol = QPushButton()
        fol.setIcon(icon("folder"))
        fol.setToolTip(tr("lib.folder"))
        fol.clicked.connect(lambda: open_path(str(Path(exports[-1] if exports else c.source_path).parent)))
        btns.addWidget(fol)
        rm = QPushButton()
        rm.setObjectName("Danger")
        rm.setIcon(icon("trash", theme.RED))
        rm.clicked.connect(lambda: page.delete(c.id))
        btns.addWidget(rm)
        self.lay.addLayout(btns)


class LibraryPage(Page):
    def __init__(self, win) -> None:
        super().__init__(win, tr("nav.library"), "Ranked highlights — scores are review aids, not virality predictions")
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("lib.search"))
        self.search.addAction(icon("search", theme.MUTED), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(lambda: self._debounce.start(250))
        bar.addWidget(self.search, 2)
        self.streamer = QComboBox()
        self.streamer.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.streamer, 1)
        self.sort = QComboBox()
        for k, t in (("score", tr("lib.sort_score")), ("quality", "Best quality (ranked)"), ("new", tr("lib.sort_new")),
                     ("duration", "Longest")):
            self.sort.addItem(t, k)
        self.sort.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.sort, 1)
        bar.addWidget(label(tr("lib.min_score"), "Muted"))
        self.min_score = QSpinBox()
        self.min_score.setRange(0, 100)
        self.min_score.valueChanged.connect(lambda: self._debounce.start(250))
        bar.addWidget(self.min_score)
        self.hide_dup = QCheckBox("Hide duplicates")
        self.hide_dup.setChecked(True)
        self.hide_dup.toggled.connect(self.refresh)
        bar.addWidget(self.hide_dup)
        self.body.addLayout(bar)
        self.count = label("", "Muted")
        self.body.addWidget(self.count)
        holder = QWidget()
        self.flow = FlowLayout(holder)
        self.body.addWidget(holder)
        self.body.addStretch()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.refresh)
        self._dirty = True
        win.bridge.event.connect(self.on_event)

    def on_event(self, ev) -> None:
        if ev.kind in ("clip.saved", "video.exported", "video.qc_failed", "clip.deleted", "job.done", "job.failed"):
            self._dirty = True
            if self.isVisible():
                self._debounce.start(400)

    def on_show(self) -> None:
        cur = self.streamer.currentData()
        self.streamer.blockSignals(True)
        self.streamer.clear()
        self.streamer.addItem("All streamers", None)
        with self.backend.db.session() as s:
            for st in s.scalars(select(Streamer).order_by(Streamer.login)):
                self.streamer.addItem(st.display_name or st.login, st.id)
        i = self.streamer.findData(cur)
        self.streamer.setCurrentIndex(max(0, i))
        self.streamer.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.flow)
        q = select(Clip).join(Streamer)
        text = self.search.text().strip()
        if text:
            like = f"%{text}%"
            q = q.where(or_(Clip.title.ilike(like), Clip.reason.ilike(like), Streamer.login.ilike(like),
                            Streamer.display_name.ilike(like)))
        if self.streamer.currentData():
            q = q.where(Clip.streamer_id == self.streamer.currentData())
        if self.min_score.value():
            q = q.where(Clip.detection_score >= self.min_score.value())
        if self.hide_dup.isChecked():
            q = q.where(Clip.duplicate_of.is_(None))
        key = self.sort.currentData()
        order = {"score": Clip.detection_score.desc(), "quality": Clip.quality_score.desc(),
                 "new": Clip.created_at.desc(), "duration": Clip.duration.desc()}[key]
        with self.backend.db.session() as s:
            clips = s.scalars(q.order_by(order).limit(300)).all()
            for i, c in enumerate(clips):
                self.flow.addWidget(ClipCard(self, c, i + 1 if key in ("quality", "score") else None))
        self.count.setText(f"{len(clips)} clips")
        if not clips:
            e = Card()
            e.setFixedSize(520, 100)
            e.lay.addWidget(label(tr("lib.empty"), "Muted", wrap=True))
            self.flow.addWidget(e)
        self._dirty = False

    def delete(self, clip_id: int) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(tr("lib.delete"))
        box.setText(tr("lib.confirm_delete"))
        cb = QCheckBox("Also delete exported videos of this clip")
        box.setCheckBox(cb)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if box.exec() == QMessageBox.Yes:
            self.backend.delete_clip(clip_id, delete_exports=cb.isChecked())
            self.refresh()
