from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout, QWidget
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload, load_only

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.db.models import Clip, ExportedVideo, ProcessingJob
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.widgets.common import Card, Pill, label

COL = {"queued": theme.MUTED, "running": theme.CYAN, "paused": theme.AMBER, "done": theme.GREEN,
       "failed": theme.RED, "cancelled": theme.MUTED}


def job_video(s, j: ProcessingJob) -> str | None:
    """The exported video a finished job produced (or uploaded), if it still exists on disk."""
    from pathlib import Path
    e = None
    if (j.params or {}).get("export_id"):
        e = s.get(ExportedVideo, int(j.params["export_id"]))
    elif j.clip_id:
        rows = s.scalars(select(ExportedVideo).where(ExportedVideo.clip_id == j.clip_id)
                         .order_by(ExportedVideo.created_at.desc())).all()
        # the export made by this job: the first one created after the job started, else the newest
        after = [r for r in rows if j.created_at and r.created_at and r.created_at >= j.created_at]
        e = after[-1] if after else (rows[0] if rows else None)
    return e.path if e is not None and e.path and Path(e.path).exists() else None


def job_videos(s, jobs: list[ProcessingJob]) -> dict[int, str | None]:
    """Resolve the displayed jobs' video files with one query, instead of one per row."""
    from pathlib import Path
    done = [j for j in jobs if j.status == "done"]
    export_ids = {int(j.params["export_id"]) for j in done if (j.params or {}).get("export_id")}
    clip_ids = {j.clip_id for j in done if j.clip_id and not (j.params or {}).get("export_id")}
    if not export_ids and not clip_ids:
        return {}
    exports = s.scalars(select(ExportedVideo).options(load_only(ExportedVideo.id, ExportedVideo.path,
                        ExportedVideo.clip_id, ExportedVideo.created_at)).where(or_(ExportedVideo.id.in_(export_ids),
                        ExportedVideo.clip_id.in_(clip_ids))).order_by(ExportedVideo.created_at.desc())).all()
    by_id = {e.id: e for e in exports}
    by_clip = {}
    for e in exports:
        by_clip.setdefault(e.clip_id, []).append(e)
    result = {}
    exists = {}
    for j in done:
        if (j.params or {}).get("export_id"):
            e = by_id.get(int(j.params["export_id"]))
        else:
            rows = by_clip.get(j.clip_id, [])
            after = [r for r in rows if j.created_at and r.created_at and r.created_at >= j.created_at]
            e = after[-1] if after else (rows[0] if rows else None)
        if e is None or not e.path:
            result[j.id] = None
            continue
        if e.path not in exists:
            exists[e.path] = Path(e.path).exists()
        result[j.id] = e.path if exists[e.path] else None
    return result


class JobRow(Card):
    def __init__(self, page, j: ProcessingJob, video: str | None = None) -> None:
        super().__init__(padding=12, shadow=False)
        self.video = video
        if video:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(tr("q.open_tip"))
        h = QHBoxLayout()
        self.lay.addLayout(h)
        v = QVBoxLayout()
        name = {"process": "Automatic edit & export", "render": "Render (editor)", "transcribe": "AI transcription"}.get(j.kind, j.kind)
        clip = j.clip.title or j.clip.reason if j.clip else ""
        v.addWidget(label(f"#{j.id} · {name}", "H2"))
        v.addWidget(label((clip or "")[:90], "Muted"))
        h.addLayout(v, 2)
        mid = QVBoxLayout()
        self.bar = QProgressBar()
        self.bar.setValue(int(j.progress * 100))
        self.bar.setTextVisible(False)
        mid.addWidget(self.bar)
        self.stage = label(j.error[:160] if j.status == "failed" else j.stage, "Muted")
        self.stage.setWordWrap(True)
        mid.addWidget(self.stage)
        h.addLayout(mid, 3)
        h.addWidget(Pill(j.status, COL.get(j.status, theme.MUTED)))
        q = page.backend.jobs
        btns = []
        if j.status in ("running", "queued"):
            btns.append(("pause", tr("q.pause"), lambda: q.pause(j.id)))
        if j.status == "paused":
            btns.append(("play", tr("q.resume"), lambda: q.resume(j.id)))
        if j.status in ("running", "queued", "paused"):
            btns.append(("x", tr("q.cancel"), lambda: q.cancel(j.id)))
        if j.status in ("failed", "cancelled"):
            btns.append(("refresh", tr("q.retry"), lambda: q.retry(j.id)))
        if video:
            from dogsapka_clipper.ui.pages.library import open_path, reveal_in_folder
            btns.append(("play", tr("q.open_video"), lambda: open_path(video)))
            btns.append(("folder", tr("q.open_folder"), lambda: reveal_in_folder(video)))
        for ic, tip, fn in btns:
            b = QPushButton()
            b.setIcon(icon(ic))
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f=fn: (f(), page.refresh()))
            h.addWidget(b)

    def mouseReleaseEvent(self, e):
        # click anywhere on a finished job (not on its buttons) opens the video
        if self.video and e.button() == Qt.LeftButton:
            from dogsapka_clipper.ui.pages.library import open_path
            open_path(self.video)
        super().mouseReleaseEvent(e)


class QueuePage(Page):
    def __init__(self, win) -> None:
        super().__init__(win, tr("nav.queue"), "Background jobs — the interface never waits for them")
        self.pause_all = QPushButton()
        self.pause_all.clicked.connect(self._toggle_all)
        self.actions.addWidget(self.pause_all)
        self.publish_again = QPushButton("Publish all again")
        self.publish_again.setToolTip("Retry failed uploads; completed uploads are skipped")
        self.publish_again.clicked.connect(self._publish_again)
        self.actions.addWidget(self.publish_again)
        clr = QPushButton("Clear finished")
        clr.clicked.connect(self.clear_finished)
        self.actions.addWidget(clr)
        holder = QWidget()
        self.list = QVBoxLayout(holder)
        self.list.setSpacing(10)
        self.body.addWidget(holder)
        self.body.addStretch()
        self.rows: dict[int, JobRow] = {}
        self._row_keys: dict[int, tuple] = {}
        self._empty = label(tr("q.empty"), "Muted")
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.timeout.connect(self.refresh)
        win.bridge.event.connect(self.on_event)

    def _toggle_all(self):
        self.backend.jobs.pause_all(not self.backend.jobs.globally_paused)
        self.refresh()

    def _publish_again(self):
        self.backend.jobs.retry_failed_uploads()
        self.refresh()

    def on_show(self):
        self.refresh()

    def on_event(self, ev) -> None:
        if not self.isVisible() or not ev.kind.startswith("job."):
            return
        if ev.kind == "job.progress" and ev.data.get("job_id") in self.rows:
            r = self.rows[ev.data["job_id"]]
            r.bar.setValue(int(ev.data["progress"] * 100))
            if ev.data.get("stage"):
                r.stage.setText(ev.data["stage"])
        elif not self._t.isActive():
            self._t.start(200)

    def refresh(self) -> None:
        paused = self.backend.jobs.globally_paused
        self.pause_all.setText("  Resume all" if paused else "  Pause all")
        self.pause_all.setIcon(icon("play" if paused else "pause"))
        with self.backend.db.session() as s:
            failed_upload = s.scalars(select(ProcessingJob.id).where(
                ProcessingJob.kind == "upload", ProcessingJob.status == "failed").limit(1)).first()
            self.publish_again.setVisible(failed_upload is not None)
            jobs = s.scalars(select(ProcessingJob).options(joinedload(ProcessingJob.clip).load_only(Clip.title, Clip.reason))
                             .order_by(ProcessingJob.id.desc()).limit(100)).all()
            videos = job_videos(s, jobs)
            current = {}
            keys = {}
            for j in jobs:
                key = (j.kind, j.status, j.error, j.clip.title if j.clip else "",
                       j.clip.reason if j.clip else "", videos.get(j.id))
                row = self.rows.get(j.id)
                if row is None or self._row_keys.get(j.id) != key:
                    row = JobRow(self, j, videos.get(j.id))
                else:
                    row.bar.setValue(int(j.progress * 100))
                    row.stage.setText(j.error[:160] if j.status == "failed" else j.stage)
                current[j.id], keys[j.id] = row, key
        # Reorder existing widgets without deleting them; only changed or removed rows are replaced.
        while self.list.count():
            self.list.takeAt(0)
        for jid, row in self.rows.items():
            if current.get(jid) is not row:
                row.setParent(None)
                row.deleteLater()
        self.rows, self._row_keys = current, keys
        for row in current.values():
            self.list.addWidget(row)
        self._empty.setVisible(not jobs)
        if not jobs:
            self.list.addWidget(self._empty)

    def clear_finished(self) -> None:
        with self.backend.db.session() as s:
            for j in s.scalars(select(ProcessingJob).where(ProcessingJob.status.in_(("done", "cancelled")))):
                s.delete(j)
        self.refresh()
