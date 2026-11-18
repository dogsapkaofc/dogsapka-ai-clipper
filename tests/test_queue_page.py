import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.db.models import ExportedVideo, ProcessingJob
from dogsapka_clipper.processing.jobs import JobQueue
from dogsapka_clipper.ui.bridge import QtBridge
from dogsapka_clipper.ui.pages.queue import QueuePage


def test_publish_again_button_requeues_failed_upload(db):
    app = QApplication.instance() or QApplication([])
    bus = EventBus()
    queue = JobQueue(db, bus)
    bridge = QtBridge(bus)
    page = QueuePage(SimpleNamespace(backend=SimpleNamespace(db=db, jobs=queue), bridge=bridge))
    try:
        page.refresh()
        assert page.publish_again.isHidden()
        with db.session() as s:
            export = ExportedVideo(path="test.mp4")
            s.add(export)
            s.flush()
            job = ProcessingJob(kind="upload", status="failed", params={"account_id": 1, "export_id": export.id})
            s.add(job)
            s.flush()
            jid = job.id
        page.refresh()
        assert not page.publish_again.isHidden()
        page.publish_again.click()
        with db.session() as s:
            assert s.get(ProcessingJob, jid).status == "queued"
        assert page.publish_again.isHidden()
    finally:
        page.close()
        bridge.close()
