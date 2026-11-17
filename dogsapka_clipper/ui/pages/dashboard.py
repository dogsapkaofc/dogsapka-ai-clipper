from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.processing.system import gpu_info
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.widgets.common import ActivityFeed, Card, LiveGraph, StatCard, label

QUIET = {"live.stats", "job.progress", "job.queued", "job.started", "job.updated", "streamer.updated",
         "settings.changed", "render.started"}


class DashboardPage(Page):
    def __init__(self, win) -> None:
        super().__init__(win, tr("nav.dashboard"), "Real-time overview of monitoring, detection and processing")
        sim = QPushButton("  " + tr("dash.simulate"))
        sim.setObjectName("Accent")
        sim.setIcon(icon("zap", theme.CYAN))
        sim.setToolTip("Runs a local simulated livestream with chat so you can see the whole pipeline work "
                       "without a real Twitch stream.")
        sim.clicked.connect(self.run_simulation)
        self.sim_btn = sim
        self.actions.addWidget(sim)
        add = QPushButton("  " + tr("str.add"))
        add.setObjectName("Primary")
        add.setIcon(icon("plus"))
        add.clicked.connect(lambda: (win.navigate("streamers"), win.pages["streamers"].add_dialog()))
        self.actions.addWidget(add)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.cards = {
            "monitored": StatCard(tr("dash.monitored"), "users", theme.PURPLE2),
            "live": StatCard(tr("dash.live"), "radio", theme.RED),
            "recording": StatCard(tr("dash.recording"), "activity", theme.CYAN),
            "clips": StatCard(tr("dash.clips_total"), "film", theme.PURPLE2),
            "today": StatCard(tr("dash.clips_today"), "sparkles", theme.GREEN),
            "ai": StatCard(tr("dash.ai_status"), "cpu", theme.CYAN),
            "disk": StatCard(tr("dash.disk"), "disk", theme.AMBER),
            "gpu": StatCard(tr("dash.gpu"), "zap", theme.GREEN),
        }
        for i, c in enumerate(self.cards.values()):
            grid.addWidget(c, i // 4, i % 4)
        self.body.addLayout(grid)

        row = QHBoxLayout()
        row.setSpacing(16)
        feed_card = Card()
        feed_card.lay.addWidget(label(tr("dash.activity"), "H2"))
        self.feed = ActivityFeed()
        feed_card.lay.addWidget(self.feed)
        feed_card.lay.addStretch()
        row.addWidget(feed_card, 3)
        right = QVBoxLayout()
        right.setSpacing(16)
        qcard = Card()
        qh = QHBoxLayout()
        qh.addWidget(label(tr("dash.queue"), "H2"))
        qh.addStretch()
        self.queue_count = label("", "Muted")
        qh.addWidget(self.queue_count)
        qcard.lay.addLayout(qh)
        self.queue_graph = LiveGraph(theme.PURPLE2, 60, height=90)
        qcard.lay.addWidget(self.queue_graph)
        open_q = QPushButton("Open processing queue")
        open_q.clicked.connect(lambda: win.navigate("queue"))
        qcard.lay.addWidget(open_q)
        right.addWidget(qcard)
        scard = Card()
        scard.lay.addWidget(label("Highlight score (all live sessions)", "H2"))
        self.score_graph = LiveGraph(theme.CYAN, 120, y_max=100, height=120)
        scard.lay.addWidget(self.score_graph)
        right.addWidget(scard)
        right.addStretch()
        row.addLayout(right, 2)
        self.body.addLayout(row)

        for ev in list(self.backend.bus.history)[-30:]:
            if ev.message and ev.kind not in QUIET:
                self.feed.add(ev.message, ev.level, ev.ts)
        win.bridge.event.connect(self.on_event)
        self._gpu = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2500)
        self.gpu_timer = QTimer(self)
        self.gpu_timer.timeout.connect(lambda: run_async(gpu_info, self._set_gpu))
        self.gpu_timer.start(5000)
        run_async(gpu_info, self._set_gpu)
        self.refresh()

    def _set_gpu(self, g) -> None:
        self._gpu = g
        if g is None:
            self.cards["gpu"].set("CPU", "No NVIDIA GPU detected - CPU encoding")
        else:
            self.cards["gpu"].set(f"{g.utilization:.0f}%", f"{g.name} · {g.memory_used_mb:.0f}/{g.memory_total_mb:.0f} MB")

    def on_event(self, ev) -> None:
        if ev.message and ev.kind not in QUIET:
            self.feed.add(ev.message, ev.level, ev.ts)
        if ev.kind == "live.stats":
            pass
        if ev.kind in ("clip.saved", "streamer.live", "streamer.offline", "video.exported", "session.ended"):
            self.refresh()

    def refresh(self) -> None:
        if not self.isVisible():
            return
        s = self.backend.dashboard_stats()
        c = self.cards
        c["monitored"].set(s["monitored"])
        c["live"].set(s["live"])
        c["recording"].set(s["recording"], "rolling buffers active")
        c["clips"].set(s["clips"], f"{s['exports']} exported videos")
        c["today"].set(s["clips_today"])
        busy = s["processing"] > 0
        c["ai"].set(tr("dash.busy") if busy else tr("dash.idle"),
                    f"{s['processing']} running · {s['queue']} queued" + (" · paused" if self.backend.jobs.globally_paused else ""))
        c["disk"].set(f"{s['disk_free_gb']:.0f} GB", f"of {s['disk_total_gb']:.0f} GB")
        self.queue_count.setText(f"{s['queue']} pending")
        self.queue_graph.push(s["queue"])
        best = max((x.stats.score for x in self.backend.sessions.values()), default=0.0)
        self.score_graph.push(best)
        if self.backend.sessions:
            self.score_graph.threshold = min(x.stats.threshold for x in self.backend.sessions.values())

    def on_show(self) -> None:
        self.refresh()

    def run_simulation(self) -> None:
        self.sim_btn.setEnabled(False)
        self.feed.add("Preparing simulation stream (generated locally with FFmpeg)…", "info")

        def done(sid):
            self.sim_btn.setEnabled(True)
            self.win.open_live(sid)

        def err(e):
            self.sim_btn.setEnabled(True)
            QMessageBox.warning(self, tr("error"), f"Could not start simulation:\n{e}")
        run_async(lambda: self.backend.start_simulation(), done, err)
