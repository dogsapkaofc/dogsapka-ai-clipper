from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)
from sqlalchemy import or_, select
from shiboken6 import isValid

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.db.models import Highlight, MonitoringSession, Streamer
from dogsapka_clipper.processing.ffmpeg_utils import extract_frame
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.widgets.common import (ActivityFeed, Card, FlowLayout, LiveGraph, Meter, Pill,
                                                 TimelineBar, clear_layout, label)


class LiveTile(Card):
    """One streamer in the multi-stream grid: preview, score graph and key numbers. Click = details."""

    def __init__(self, page, sid: int, name: str) -> None:
        super().__init__(padding=12)
        self.page, self.sid = page, sid
        self.setFixedWidth(352)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("live.tile_tip"))
        self.preview = QLabel(tr("live.waiting"))
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(328, 184)
        self.preview.setStyleSheet(f"background: {theme.BG2}; border-radius: 10px; color: {theme.MUTED};")
        self.lay.addWidget(self.preview)
        head = QHBoxLayout()
        self.name = label(name, "H2")
        head.addWidget(self.name, 1)
        self.pill = Pill(tr("str.live"), theme.RED)
        head.addWidget(self.pill)
        self.lay.addLayout(head)
        row = QHBoxLayout()
        row.addWidget(label(tr("live.score"), "Muted"))
        row.addStretch()
        self.score = label("0", "H2")
        row.addWidget(self.score)
        self.lay.addLayout(row)
        self.graph = LiveGraph(theme.PURPLE2, 180, y_max=100, height=64)
        self.lay.addWidget(self.graph)
        self.info = label("", "Muted", wrap=True)
        self.lay.addWidget(self.info)
        self.busy = False
        self.preview_key = None

    def update_stats(self, st) -> None:
        self.graph.set_values([v for _, v in st.scores[-180:]])
        self.graph.threshold = st.threshold
        self.score.setText(f"{st.score:.0f}")
        hot = st.score >= st.threshold
        self.score.setStyleSheet(f"color: {theme.AMBER if hot else theme.TEXT};")
        self.pill.set("\u25cf " + tr("str.recording") if st.recording else tr("str.live"), theme.RED)
        self.info.setText(f"Chat {st.chat_rate:.1f}/s  \u00b7  {tr('live.audio_short')} {st.audio_level:.2f}  \u00b7  "
                          f"Buffer {st.buffer_s:.0f}/{st.buffer_target:.0f}s  \u00b7  \u2605 {st.highlights}")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.page.show_single(self.sid)
        super().mouseReleaseEvent(e)


class LivePage(Page):
    def __init__(self, win) -> None:
        super().__init__(win, tr("nav.live"), "Signals, scores and detections in real time")
        # view switch: all live streamers side by side / one streamer in detail
        self.btn_grid = QPushButton("  " + tr("live.view_all"))
        self.btn_one = QPushButton("  " + tr("live.view_one"))
        grp = QButtonGroup(self)
        for btn in (self.btn_grid, self.btn_one):
            btn.setCheckable(True)
            grp.addButton(btn)
            self.actions.addWidget(btn)
        self.btn_grid.clicked.connect(lambda: self.set_mode("grid"))
        self.btn_one.clicked.connect(lambda: self.set_mode("single"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(240)
        self.combo.currentIndexChanged.connect(self._changed)
        self.actions.addWidget(self.combo)
        self.sid: int | None = None
        self.tiles: dict[int, LiveTile] = {}
        self._streamer_items = None
        self.mode = "grid"

        # multi-stream grid
        self.grid_view = QWidget()
        gv = QVBoxLayout(self.grid_view)
        gv.setContentsMargins(0, 0, 0, 0)
        self.grid_empty = label(tr("live.none"), "Muted", wrap=True)
        gv.addWidget(self.grid_empty)
        holder = QWidget()
        self.flow = FlowLayout(holder)
        gv.addWidget(holder)
        self.body.addWidget(self.grid_view)

        # single-streamer detail view (built into its own container)
        self.detail = QWidget()
        real_body = self.body
        self.body = QVBoxLayout(self.detail)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(16)
        prev = Card()
        self.preview = QLabel(tr("live.select"))
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(480, 270)
        self.preview.setStyleSheet(f"background: {theme.BG2}; border-radius: 10px; color: {theme.MUTED};")
        prev.lay.addWidget(self.preview)
        info = QHBoxLayout()
        self.title = label("—", "H2")
        self.title.setWordWrap(True)
        info.addWidget(self.title, 1)
        self.state_pill = Pill("OFFLINE", theme.MUTED)
        info.addWidget(self.state_pill, 0, Qt.AlignTop)
        prev.lay.addLayout(info)
        self.subtitle = label("", "Muted")
        prev.lay.addWidget(self.subtitle)
        top.addWidget(prev, 3)

        side = QVBoxLayout()
        side.setSpacing(16)
        sc = Card()
        h = QHBoxLayout()
        h.addWidget(label(tr("live.score"), "H2"))
        h.addStretch()
        self.score_val = label("0", "StatValue")
        h.addWidget(self.score_val)
        sc.lay.addLayout(h)
        self.score_graph = LiveGraph(theme.PURPLE2, 180, y_max=100, height=130)
        sc.lay.addWidget(self.score_graph)
        self.score_note = label("Dashed line = current threshold", "Muted")
        sc.lay.addWidget(self.score_note)
        side.addWidget(sc)
        bc = Card()
        bc.lay.addWidget(label(tr("live.buffer"), "H2"))
        self.buffer = QProgressBar()
        self.buffer.setTextVisible(False)
        bc.lay.addWidget(self.buffer)
        self.buffer_lbl = label("", "Muted")
        bc.lay.addWidget(self.buffer_lbl)
        side.addWidget(bc)
        top.addLayout(side, 2)
        self.body.addLayout(top)

        grid = QGridLayout()
        grid.setSpacing(16)
        cc = Card()
        hh = QHBoxLayout()
        hh.addWidget(label(tr("live.chat_rate"), "H2"))
        hh.addStretch()
        self.chat_val = label("0.0", "H2")
        hh.addWidget(self.chat_val)
        cc.lay.addLayout(hh)
        self.chat_graph = LiveGraph(theme.CYAN, 120, height=110)
        cc.lay.addWidget(self.chat_graph)
        self.chat_note = label("", "Muted")
        cc.lay.addWidget(self.chat_note)
        grid.addWidget(cc, 0, 0)
        ac = Card()
        ac.lay.addWidget(label(tr("live.audio"), "H2"))
        self.meter = Meter()
        ac.lay.addWidget(self.meter)
        self.audio_graph = LiveGraph(theme.GREEN, 120, y_max=1.0, height=92)
        ac.lay.addWidget(self.audio_graph)
        grid.addWidget(ac, 0, 1)
        self.body.addLayout(grid)

        tl = Card()
        tl.lay.addWidget(label(tr("live.timeline"), "H2"))
        self.timeline = TimelineBar()
        tl.lay.addWidget(self.timeline)
        self.body.addWidget(tl)
        ev = Card()
        ev.lay.addWidget(label(tr("live.events"), "H2"))
        self.feed = ActivityFeed(30)
        ev.lay.addWidget(self.feed)
        self.body.addWidget(ev)
        self.body = real_body
        self.body.addWidget(self.detail)
        self.body.addStretch()

        win.bridge.event.connect(self.on_event)
        self._preview_busy = False
        self._preview_key = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_preview)
        self.timer.start(5000)
        self.reload_streamers()
        self.set_mode("grid")

    # -- multi-stream grid -------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.btn_grid.setChecked(mode == "grid")
        self.btn_one.setChecked(mode == "single")
        self.combo.setVisible(mode == "single")
        self.grid_view.setVisible(mode == "grid")
        self.detail.setVisible(mode == "single")
        if mode == "grid":
            self.rebuild_tiles()
        else:
            self._refresh_preview(force=True)

    def show_single(self, sid: int) -> None:
        self.select_streamer(sid)
        self.set_mode("single")

    def _live_streamers(self) -> list[tuple[int, str]]:
        with self.backend.db.session() as s:
            rows = s.execute(select(Streamer.id, Streamer.display_name, Streamer.login)
                             .where(or_(Streamer.is_live.is_(True), Streamer.id.in_(list(self.backend.sessions))))
                             .order_by(Streamer.display_name)).all()
            return [(r.id, r.display_name or r.login) for r in rows]

    def rebuild_tiles(self) -> None:
        live = self._live_streamers()
        if [sid for sid, _ in live] == list(self.tiles):
            self.grid_empty.setVisible(not live)
            return
        clear_layout(self.flow)
        self.tiles = {}
        for sid, name in live:
            t = LiveTile(self, sid, name)
            self.tiles[sid] = t
            self.flow.addWidget(t)
        self.grid_empty.setVisible(not live)
        self._refresh_tile_previews()

    def _refresh_tile_previews(self) -> None:
        for sid, tile in self.tiles.items():
            sess = self.backend.sessions.get(sid)
            if not sess or tile.busy:
                continue
            segments = sess.buffer.segments
            if not segments:
                continue
            seg = segments[-1]
            key = (id(sess), str(seg.path), getattr(seg, "start", None), seg.duration)
            if tile.preview_key == key:
                continue
            out = self.backend.paths.temp / f"preview_tile_{sid}.jpg"
            tile.busy = True

            def done(p, tile=tile, sid=sid, key=key):
                tile.busy = False
                # A streamer-list change rebuilds the grid while frame extraction may still run.
                # Ignore results for replaced tiles, including those awaiting deleteLater().
                if self.tiles.get(sid) is not tile or not isValid(tile.preview):
                    return
                pm = QPixmap(str(p))
                if not pm.isNull():
                    tile.preview.setPixmap(pm.scaled(tile.preview.width(), tile.preview.height(),
                                                     Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    tile.preview_key = key

            def err(_e, tile=tile):
                tile.busy = False
            run_async(lambda seg=seg, out=out: extract_frame(seg.path, max(0.0, seg.duration - 0.5), out,
                                                             width=480), done, err)

    def reload_streamers(self) -> None:
        with self.backend.db.session() as s:
            rows = s.execute(select(Streamer.id, Streamer.display_name, Streamer.login, Streamer.is_live)
                             .order_by(Streamer.is_live.desc(), Streamer.login)).all()
            items = [(r.id, r.display_name or r.login, r.is_live) for r in rows]
        if items == self._streamer_items:
            return
        self._streamer_items = items
        cur = self.sid
        self.combo.blockSignals(True)
        self.combo.clear()
        for sid, name, live in items:
            self.combo.addItem(("● " if live else "○ ") + name, sid)
        if cur is not None:
            idx = self.combo.findData(cur)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)
        if self.combo.currentData() != self.sid:
            self._changed(self.combo.currentIndex())

    def select_streamer(self, sid: int) -> None:
        self.reload_streamers()
        idx = self.combo.findData(sid)
        if idx >= 0:
            if self.combo.currentIndex() == idx:
                self._changed(idx)
            else:
                self.combo.setCurrentIndex(idx)

    def on_show(self) -> None:
        self.reload_streamers()
        if self.mode == "grid":
            self.rebuild_tiles()

    def _changed(self, idx: int) -> None:
        self.sid = self.combo.itemData(idx)
        for g in (self.score_graph, self.chat_graph, self.audio_graph):
            g.values.clear()
            g.update()
        self.feed_clear()
        self._load_static()
        self._refresh_preview(force=True)

    def feed_clear(self):
        while self.feed.lay.count() > 1:
            it = self.feed.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _load_static(self) -> None:
        if self.sid is None:
            return
        with self.backend.db.session() as s:
            st = s.get(Streamer, self.sid)
            if not st:
                return
            self.title.setText(st.stream_title or st.display_name or st.login)
            self.subtitle.setText(f"{st.display_name} · {st.category or '—'} · {st.viewer_count} viewers")
            self.state_pill.set(tr("str.live") if st.is_live else tr("str.offline"), theme.RED if st.is_live else theme.MUTED)
            sess = s.scalars(select(MonitoringSession).where(MonitoringSession.streamer_id == self.sid)
                             .order_by(MonitoringSession.id.desc()).limit(1)).first()
            hls = []
            if sess:
                hls = [(h.event_ts, h.score, h.reasons) for h in sess.highlights]
                start = sess.started_at
        if not hls:
            self.timeline.markers = []
            self.timeline.update()
            return
        t0 = min(h[0] for h in hls) - 60
        t1 = max(max(h[0] for h in hls) + 30, time.time() if self.sid in self.backend.sessions else 0)
        self.timeline.set_duration(t1 - t0)
        self.timeline.markers = [(t - t0, sc) for t, sc, _ in hls]
        self.timeline.update()
        for t, sc, reasons in hls[-10:]:
            self.feed.add(f"{sc:.0f}/100 — {'; '.join(reasons[:2])}", "success", t)

    def on_event(self, ev) -> None:
        if ev.kind in ("streamer.live", "streamer.offline", "streamer.added", "streamer.removed", "session.started",
                       "session.ended"):
            if self.isVisible():
                self.reload_streamers()
                self._load_static()
                if self.mode == "grid":
                    self.rebuild_tiles()
        if ev.kind == "live.stats" and self.mode == "grid":
            if not self.isVisible():
                return
            sid = ev.data.get("streamer_id")
            tile = self.tiles.get(sid)
            if tile is None and sid in self.backend.sessions:
                self.rebuild_tiles()
                tile = self.tiles.get(sid)
            if tile is not None:
                tile.update_stats(ev.data["stats"])
            return
        if ev.data.get("streamer_id") != self.sid:
            return
        if ev.kind == "live.stats":
            st = ev.data["stats"]
            if not self.isVisible():
                return
            self.score_graph.set_values([s for _, s in st.scores[-180:]])
            self.score_graph.threshold = st.threshold
            self.score_val.setText(f"{st.score:.0f}")
            self.chat_graph.push(st.chat_rate)
            self.chat_val.setText(f"{st.chat_rate:.1f}")
            self.chat_note.setText(f"Baseline {st.chat_baseline:.1f}/s · chat {'connected' if st.chat_connected else 'not connected'}")
            self.meter.animate_to(st.audio_level)
            self.audio_graph.push(st.audio_level)
            self.buffer.setMaximum(int(st.buffer_target))
            self.buffer.setValue(int(st.buffer_s))
            self.buffer_lbl.setText(f"{st.buffer_s:.0f}s of {st.buffer_target:.0f}s buffered · "
                                    f"{'recording' if st.recording else 'not recording'} · {st.highlights} highlights")
            self.state_pill.set("● " + tr("str.recording") if st.recording else tr("str.live"), theme.RED)
        elif ev.kind in ("highlight.detected",):
            self._load_static()
        elif ev.message and ev.kind not in ("job.progress",):
            self.feed.add(ev.message, ev.level, ev.ts)

    def _refresh_preview(self, force: bool = False) -> None:
        """Preview = latest frame from our own recording buffer (no extra stream connection)."""
        if not self.isVisible() and not force:
            return
        if self.mode == "grid":
            self._refresh_tile_previews()
            return
        sess = self.backend.sessions.get(self.sid) if self.sid is not None else None
        if not sess or self._preview_busy:
            if not sess:
                self._preview_key = None
                self.preview.setText("Stream preview appears here while a session is recording.")
                self.preview.setPixmap(QPixmap())
            return
        segs = sess.buffer.segments
        if not segs:
            return
        seg = segs[-1]
        sid = self.sid
        key = (id(sess), str(seg.path), getattr(seg, "start", None), seg.duration)
        if self._preview_key == key:
            return
        out = self.backend.paths.temp / f"preview_{self.sid}.jpg"
        self._preview_busy = True

        def job():
            return extract_frame(seg.path, max(0.0, seg.duration - 0.5), out, width=960)

        def done(p):
            self._preview_busy = False
            if not isValid(self.preview):
                return
            if self.sid != sid or self.backend.sessions.get(sid) is not sess:
                self._refresh_preview(force=True)
                return
            pm = QPixmap(str(p))
            if not pm.isNull():
                self.preview.setPixmap(pm.scaled(self.preview.width(), self.preview.height(), Qt.KeepAspectRatio,
                                                 Qt.SmoothTransformation))
                self._preview_key = key

        def err(e):
            self._preview_busy = False
            import logging
            logging.getLogger(__name__).debug("preview failed: %s", e)
        run_async(job, done, err)
