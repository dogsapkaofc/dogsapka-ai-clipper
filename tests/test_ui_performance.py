"""UI refresh work stays bounded as the processing history grows."""
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from sqlalchemy import event
from shiboken6 import isValid

from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.db.models import Clip, ExportedVideo, ProcessingJob, Streamer, utcnow
from dogsapka_clipper.processing.jobs import JobQueue
from dogsapka_clipper.ui.bridge import QtBridge
from dogsapka_clipper.ui.pages.queue import QueuePage, job_video, job_videos
from test_live_previews import previews


@pytest.fixture
def queue_page(db):
    app = QApplication.instance() or QApplication([])
    bus = EventBus()
    bridge = QtBridge(bus)
    page = QueuePage(SimpleNamespace(backend=SimpleNamespace(db=db, jobs=JobQueue(db, bus)), bridge=bridge))
    yield page
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    bridge.close()


def test_queue_refresh_uses_three_queries_for_100_completed_clips(db, queue_page, tmp_path):
    video = tmp_path / "video.mp4"
    video.touch()
    with db.session() as s:
        streamer = Streamer(login="channel")
        s.add(streamer)
        s.flush()
        for i in range(100):
            clip = Clip(streamer_id=streamer.id, source_path="source.mp4", title=f"Clip {i}")
            s.add(clip)
            s.flush()
            s.add(ExportedVideo(clip_id=clip.id, path=str(video)))
            s.add(ProcessingJob(clip_id=clip.id, kind="process", status="done"))
    queries = []
    def count(conn, cursor, statement, params, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)
    event.listen(db.engine, "before_cursor_execute", count)
    try:
        queue_page.refresh()
    finally:
        event.remove(db.engine, "before_cursor_execute", count)
    assert len(queries) == 3
    assert len(queue_page.rows) == 100
    assert all(row.video == str(video) for row in queue_page.rows.values())


def test_queue_reuses_rows_for_progress_but_replaces_status_controls(db, queue_page):
    with db.session() as s:
        job = ProcessingJob(kind="process", status="running", stage="Rendering", progress=.1)
        s.add(job)
        s.flush()
        jid = job.id
    queue_page.refresh()
    row = queue_page.rows[jid]
    with db.session() as s:
        job = s.get(ProcessingJob, jid)
        job.progress, job.stage = .8, "Quality check"
    queue_page.refresh()
    assert queue_page.rows[jid] is row
    assert row.bar.value() == 80 and row.stage.text() == "Quality check"
    with db.session() as s:
        s.get(ProcessingJob, jid).status = "failed"
    queue_page.refresh()
    assert queue_page.rows[jid] is not row
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(row)


def test_batched_video_lookup_preserves_job_export_selection(db, tmp_path):
    now = utcnow()
    with db.session() as s:
        streamer = Streamer(login="channel")
        s.add(streamer)
        s.flush()
        clip = Clip(streamer_id=streamer.id, source_path="source.mp4")
        s.add(clip)
        s.flush()
        exports = []
        for offset in (-3, 2, 5):
            path = tmp_path / f"export-{offset}.mp4"
            path.touch()
            export = ExportedVideo(clip_id=clip.id, path=str(path), created_at=now+timedelta(seconds=offset))
            s.add(export)
            exports.append(export)
        s.flush()
        jobs = [ProcessingJob(clip_id=clip.id, kind="process", status="done", created_at=now),
                ProcessingJob(clip_id=clip.id, kind="process", status="done", created_at=now+timedelta(seconds=20)),
                ProcessingJob(kind="upload", status="done", params={"export_id": exports[0].id})]
        s.add_all(jobs)
        s.flush()
        assert job_videos(s, jobs) == {j.id: job_video(s, j) for j in jobs}
        assert job_video(s, jobs[0]) == exports[1].path


def test_live_preview_extracts_only_new_recording_segments(previews):
    page, pending, _, frame = previews
    pending[0][0](frame)
    for _ in range(5):
        page._refresh_tile_previews()
    assert len(pending) == 1
    segments = page.backend.sessions[1].buffer.segments
    segments.append(SimpleNamespace(path=frame.with_suffix(".ts"), duration=5))
    page._refresh_tile_previews()
    assert len(pending) == 2
    pending[1][1](RuntimeError("try again"))
    page._refresh_tile_previews()
    assert len(pending) == 3


def test_detail_preview_drops_stale_selection_and_reuses_same_frame(previews, monkeypatch):
    page, pending, _, frame = previews
    page.mode, page.sid = "single", 1
    applied = []
    monkeypatch.setattr(page.preview, "setPixmap", lambda pm: applied.append(pm))
    page._refresh_preview(force=True)
    assert len(pending) == 2
    page.sid = None
    pending[1][0](frame)
    assert all(pm.isNull() for pm in applied)
    applied.clear()
    page.sid = 1
    page._refresh_preview(force=True)
    pending[-1][0](frame)
    assert len(applied) == 1
    count = len(pending)
    page._refresh_preview(force=True)
    assert len(pending) == count
