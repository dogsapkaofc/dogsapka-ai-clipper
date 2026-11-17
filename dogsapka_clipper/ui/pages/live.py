from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout
from sqlalchemy import select

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.db.models import Highlight, MonitoringSession, Streamer
from dogsapka_clipper.processing.ffmpeg_utils import extract_frame
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.widgets.common import ActivityFeed, Card, LiveGraph, Meter, Pill, TimelineBar, label


class LivePage(Page):
    def __init__(self, win) -> None:
        super().__init__(win, tr("nav.live"), "Signals, scores and detections in real time")
        self.combo = QComboBox()
        self.combo.setMinimumWidth(240)
        self.combo.currentIndexChanged.connect(self._changed)
        self.actions.addWidget(self.combo)
        self.sid: int | None = None

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

        win.bridge.event.connect(self.on_event)
        self._preview_busy = False
        self._last_preview = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_preview)
        self.timer.start(5000)
        self.reload_streamers()

    def reload_streamers(self) -> None:
        with self.backend.db.session() as s:
            rows = s.scalars(select(Streamer).order_by(Streamer.is_live.desc(), Streamer.login)).all()
            items = [(r.id, r.display_name or r.login, r.is_live) for r in rows]
        cur = self.sid
        self.combo.blockSignals(True)
        self.combo.clear()
        for sid, name, live in items:
            self.combo.addItem(("● " if live else "○ ") + name, sid)
        self.combo.blockSignals(False)
        if cur is not None:
            idx = self.combo.findData(cur)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        if self.combo.count() and self.sid is None:
            self._changed(0)

    def select_streamer(self, sid: int) -> None:
        self.reload_streamers()
        idx = self.combo.findData(sid)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
            self._changed(idx)

    def on_show(self) -> None:
        self.reload_streamers()

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
        if ev.kind in ("streamer.live", "streamer.offline", "streamer.added", "streamer.removed", "session.started"):
            if self.isVisible():
                self.reload_streamers()
                self._load_static()
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
        sess = self.backend.sessions.get(self.sid) if self.sid is not None else None
        if not sess or self._preview_busy:
            if not sess:
                self.preview.setText("Stream preview appears here while a session is recording.")
                self.preview.setPixmap(QPixmap())
            return
        segs = sess.buffer.segments
        if not segs:
            return
        seg = segs[-1]
        out = self.backend.paths.temp / f"preview_{self.sid}.jpg"
        self._preview_busy = True

        def job():
            return extract_frame(seg.path, max(0.0, seg.duration - 0.5), out, width=960)

        def done(p):
            self._preview_busy = False
            pm = QPixmap(str(p))
            if not pm.isNull():
                self.preview.setPixmap(pm.scaled(self.preview.width(), self.preview.height(), Qt.KeepAspectRatio,
                                                 Qt.SmoothTransformation))

        def err(e):
            self._preview_busy = False
            import logging
            logging.getLogger(__name__).debug("preview failed: %s", e)
        run_async(job, done, err)
