from __future__ import annotations

from pathlib import Path
from collections import OrderedDict
from types import SimpleNamespace
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QWidget)
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload, selectinload
from shiboken6 import isValid

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.db.models import AppSetting, Clip, Highlight, Streamer
from dogsapka_clipper.detection.preferences import PreferenceModel, feedback_key
from dogsapka_clipper.detection.scorer import category
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.widgets.common import Card, FlowLayout, Pill, label

STATUS_COLORS = {"done": theme.GREEN, "processing": theme.CYAN, "rendering": theme.CYAN, "pending": theme.MUTED,
                 "none": theme.MUTED, "error": theme.RED}


def open_path(p: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


def reveal_in_folder(p: str) -> None:
    """Open the file's folder with the file selected (Explorer / Finder); falls back to opening the folder."""
    import subprocess
    import sys
    path = Path(p)
    try:
        if sys.platform == "win32" and path.exists():
            subprocess.Popen(["explorer", "/select,", str(path)])
            return
        if sys.platform == "darwin" and path.exists():
            subprocess.Popen(["open", "-R", str(path)])
            return
    except OSError:
        pass
    open_path(str(path.parent if path.suffix else path))


def _load_library(db, filters: tuple, image_cache: OrderedDict) -> tuple[list, list]:
    """Worker-only database/file reads and QImage decoding. No QWidget/QPixmap work."""
    text, streamer_id, min_score, hide_duplicates, sort = filters
    q = select(Clip).join(Streamer).options(joinedload(Clip.streamer), selectinload(Clip.exports),
                                          joinedload(Clip.highlight).joinedload(Highlight.session))
    if text:
        like = f"%{text}%"
        q = q.where(or_(Clip.title.ilike(like), Clip.reason.ilike(like), Streamer.login.ilike(like),
                        Streamer.display_name.ilike(like)))
    if streamer_id is not None:
        q = q.where(Clip.streamer_id == streamer_id)
    if min_score:
        q = q.where(Clip.detection_score >= min_score)
    if hide_duplicates:
        q = q.where(Clip.duplicate_of.is_(None))
    order = {"score": Clip.detection_score.desc(), "quality": Clip.quality_score.desc(),
             "new": Clip.created_at.desc(), "duration": Clip.duration.desc()}[sort]
    with db.session() as s:
        streamers = [(r.id, r.display_name or r.login) for r in s.execute(
            select(Streamer.id, Streamer.display_name, Streamer.login).order_by(Streamer.login))]
        clips = s.scalars(q.order_by(order, Clip.id.desc()).limit(300)).all()
        ratings = {value["clip_id"]: value["rating"] for value in s.scalars(
            select(AppSetting.value).where(AppSetting.key.in_([feedback_key(c.id) for c in clips])))}
        items = []
        for i, c in enumerate(clips):
            # Snapshot only the fields needed by a card, so no lazy ORM reads escape to the GUI.
            fields = ("id", "title", "reason", "thumbnail_path", "duration", "created_at", "detection_score",
                      "quality_score", "export_status", "edit_status", "duplicate_of", "source_path")
            view = SimpleNamespace(**{field: getattr(c, field) for field in fields},
                                   streamer=SimpleNamespace(display_name=c.streamer.display_name))
            eligible = bool(c.highlight and not c.highlight.session.is_simulation and c.duplicate_of is None and
                            PreferenceModel.from_feedback([(c.highlight.signals or {}, 1)]).sample_count)
            exports = tuple(e.path for e in c.exports if e.path and Path(e.path).exists())
            rank = i + 1 if sort in ("quality", "score") else None
            image, image_key = QImage(), None
            if c.thumbnail_path:
                try:
                    stamp = Path(c.thumbnail_path).stat()
                    image_key = (c.thumbnail_path, stamp.st_size, stamp.st_mtime_ns)
                    image = image_cache.get(image_key)
                    if image is None:
                        image = QImage(c.thumbnail_path)
                        if not image.isNull():
                            image = image.scaled(276, 155, Qt.KeepAspectRatioByExpanding,
                                                 Qt.SmoothTransformation).copy(0, 0, 276, 155)
                        image_cache[image_key] = image
                        while len(image_cache) > 300:
                            image_cache.popitem(last=False)
                    else:
                        image_cache.move_to_end(image_key)
                except OSError:
                    image = QImage()
            rating = ratings.get(c.id, 0)
            signature = tuple(getattr(c, field) for field in fields) + (
                c.streamer.display_name, eligible, exports, rank, rating, image_key)
            items.append((view, rank, rating, image, exports, eligible, signature))
    return items, streamers


class ClipCard(Card):
    def __init__(self, page, c: Clip, rank: int | None, feedback_rating: int = 0,
                 *, thumbnail: QImage | None = None, available_exports: tuple[str, ...] | None = None,
                 feedback_eligible: bool | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=12)
        self.setFixedSize(300, 418)
        thumb = QLabel()
        thumb.setFixedSize(276, 155)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setStyleSheet(f"background: {theme.BG2}; border-radius: 10px;")
        pm = (QPixmap.fromImage(thumbnail) if thumbnail is not None else
              QPixmap(c.thumbnail_path) if c.thumbnail_path and Path(c.thumbnail_path).exists() else QPixmap())
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
        feedback = QHBoxLayout()
        rating = feedback_rating
        eligible = feedback_eligible if feedback_eligible is not None else bool(
            c.highlight and not c.highlight.session.is_simulation and c.duplicate_of is None and
            PreferenceModel.from_feedback([(c.highlight.signals or {}, 1)]).sample_count)
        for value, key in ((1, "lib.good_clip"), (-1, "lib.bad_clip")):
            btn = QPushButton(tr(key))
            btn.setCheckable(True)
            btn.setChecked(rating == value)
            btn.setEnabled(eligible)
            btn.setToolTip(tr("lib.feedback_tip" if eligible else "lib.feedback_unavailable"))
            btn.clicked.connect(lambda checked, value=value: page.rate(c.id, value if checked else 0))
            feedback.addWidget(btn)
        self.lay.addLayout(feedback)
        self.lay.addStretch()
        btns = QHBoxLayout()
        ed = QPushButton("  " + tr("lib.open_editor"))
        ed.setObjectName("Primary")
        ed.setIcon(icon("edit"))
        ed.clicked.connect(lambda: page.win.open_editor(c.id))
        btns.addWidget(ed)
        exports = available_exports if available_exports is not None else [e.path for e in c.exports if Path(e.path).exists()]
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
        self.learning = label("", "Muted", wrap=True)
        self.body.addWidget(self.learning)
        holder = QWidget()
        self._holder = holder
        self.flow = FlowLayout(holder)
        self.body.addWidget(holder)
        self.body.addStretch()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.refresh)
        self._dirty = True
        self._revision = 0
        self._generation = 0
        self._applied_filters = None
        self._streamer_items = None
        self._pending_filters = None
        self._query_active = False
        self._cards: dict[int, ClipCard] = {}
        self._card_keys: dict[int, tuple] = {}
        self._image_cache = OrderedDict()
        self._batch = None
        self._batch_new = {}
        self._batch_keys = {}
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._append_batch)
        self._empty = Card(holder)
        self._empty.setFixedSize(520, 100)
        self._empty.lay.addWidget(label(tr("lib.empty"), "Muted", wrap=True))
        self._empty.hide()
        win.bridge.event.connect(self.on_event)

    def on_event(self, ev) -> None:
        if ev.kind in ("clip.saved", "video.exported", "video.qc_failed", "clip.deleted", "clip.feedback", "job.done", "job.failed",
                       "streamer.added", "streamer.removed", "streamer.updated"):
            self._dirty = True
            self._revision += 1
            if self.isVisible():
                self._debounce.start(400)

    def on_show(self) -> None:
        filters = self._filters()
        if self._query_active and self._pending_filters == filters:
            return
        if self._batch is not None and self._batch[3] == filters and self._batch[2] == self._revision:
            return
        if self._dirty or filters != self._applied_filters:
            self.refresh()

    def _filters(self) -> tuple:
        return (self.search.text().strip(), self.streamer.currentData(), self.min_score.value(),
                self.hide_dup.isChecked(), self.sort.currentData())

    def refresh(self) -> None:
        self._debounce.stop()
        self._dirty = True
        self._generation += 1
        self._pending_filters = self._filters()
        self._discard_batch()
        if not self._query_active:
            self._start_load()

    def _start_load(self) -> None:
        if self._pending_filters is None:
            return
        self._debounce.stop()
        generation, revision, filters = self._generation, self._revision, self._pending_filters
        self._query_active = True
        self.count.setText("Loading clips…")
        db, images = self.backend.db, self._image_cache

        def done(data):
            if not isValid(self):
                return
            self._query_active = False
            if generation != self._generation or revision != self._revision:
                self._start_load()
                return
            items, streamers = data
            self._update_streamers(streamers)
            if filters != self._filters():
                self.refresh()
                return
            self._pending_filters = None
            self._begin_batch(items, generation, revision, filters)

        def failed(error):
            if not isValid(self):
                return
            self._query_active = False
            if generation != self._generation or revision != self._revision:
                self._start_load()
                return
            self._pending_filters = None
            self.count.setText(f"Could not load clips: {error}")

        run_async(lambda: _load_library(db, filters, images), done, failed)

    def _update_streamers(self, streamers: list) -> None:
        if streamers == self._streamer_items:
            return
        self._streamer_items = streamers
        current = self.streamer.currentData()
        self.streamer.blockSignals(True)
        self.streamer.clear()
        self.streamer.addItem("All streamers", None)
        for sid, name in streamers:
            self.streamer.addItem(name, sid)
        self.streamer.setCurrentIndex(max(0, self.streamer.findData(current)))
        self.streamer.blockSignals(False)

    def _discard_batch(self) -> None:
        if self._batch is None:
            return
        self._batch_timer.stop()
        while self.flow.count():
            self.flow.takeAt(0)
        for cid, card in self._batch_new.items():
            if self._cards.get(cid) is not card:
                card.hide()
                card.deleteLater()
        for card in self._cards.values():
            self.flow.addWidget(card)
            card.show()
        self.flow.invalidate()
        self._batch = None
        self._batch_new, self._batch_keys = {}, {}

    def _begin_batch(self, items: list, generation: int, revision: int, filters: tuple) -> None:
        if self._applied_filters is not None and [item[0].id for item in items] == list(self._cards):
            changed = [item for item in items if self._card_keys.get(item[0].id) != item[-1]]
            if not changed:
                self._mark_loaded(len(items), revision, filters)
                return
            if len(changed) <= 6:
                # Feedback/metadata updates replace only their cards. Keep the other widgets
                # visible throughout the same GUI turn, preserving scroll and avoiding flicker.
                replacements = {}
                for c, rank, rating, image, exports, eligible, signature in changed:
                    card = ClipCard(self, c, rank, rating, thumbnail=image, available_exports=exports,
                                    feedback_eligible=eligible, parent=self._holder)
                    replacements[c.id] = (card, signature)
                while self.flow.count():
                    self.flow.takeAt(0)
                for cid, old in list(self._cards.items()):
                    if cid in replacements:
                        card, signature = replacements[cid]
                        self._cards[cid], self._card_keys[cid] = card, signature
                        old.hide()
                        old.deleteLater()
                    self.flow.addWidget(self._cards[cid])
                    if cid in replacements:
                        self._cards[cid].show()
                self.flow.invalidate()
                self._mark_loaded(len(items), revision, filters)
                return
        while self.flow.count():
            self.flow.takeAt(0)
        for card in self._cards.values():
            card.hide()
        self._empty.hide()
        self._batch = (items, generation, revision, filters)
        self._batch_pos = 0
        self._batch_new, self._batch_keys = {}, {}
        self._append_batch()

    def _append_batch(self) -> None:
        if self._batch is None:
            return
        items, generation, revision, filters = self._batch
        if generation != self._generation:
            self._discard_batch()
            return
        deadline = time.perf_counter() + .008
        added = 0
        while self._batch_pos < len(items) and (added == 0 or (added < 6 and time.perf_counter() < deadline)):
            c, rank, rating, image, exports, eligible, signature = items[self._batch_pos]
            card = self._cards.get(c.id)
            if card is None or self._card_keys.get(c.id) != signature:
                card = ClipCard(self, c, rank, rating, thumbnail=image, available_exports=exports,
                                feedback_eligible=eligible, parent=self._holder)
            self._batch_new[c.id], self._batch_keys[c.id] = card, signature
            self.flow.addWidget(card)
            card.show()
            self._batch_pos += 1
            added += 1
        self.flow.invalidate()
        if self._batch_pos < len(items):
            self.count.setText(f"Loading clips… {self._batch_pos}/{len(items)}")
            self._batch_timer.start(8)
            return
        for cid, card in self._cards.items():
            if self._batch_new.get(cid) is not card:
                card.hide()
                card.deleteLater()
        self._cards, self._card_keys = self._batch_new, self._batch_keys
        self._batch_new, self._batch_keys = {}, {}
        self._batch = None
        if not items:
            self.flow.addWidget(self._empty)
            self._empty.show()
        self._mark_loaded(len(items), revision, filters)

    def _mark_loaded(self, count: int, revision: int, filters: tuple) -> None:
        self.count.setText(f"{count} clips")
        self.learning.setText(tr("lib.learning").format(n=self.backend.feedback_count))
        self._applied_filters = filters
        self._dirty = revision != self._revision
        if self._dirty and self.isVisible():
            self.refresh()

    def rate(self, clip_id: int, rating: int) -> None:
        try:
            self.backend.set_clip_feedback(clip_id, rating)
        except ValueError as e:
            QMessageBox.warning(self, tr("error"), str(e))
        self.refresh()

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
