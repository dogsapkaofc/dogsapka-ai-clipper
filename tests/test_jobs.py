import threading
import time

from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.db.models import ProcessingJob
from dogsapka_clipper.processing.jobs import JobQueue


def wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def status(db, jid):
    with db.session() as s:
        return s.get(ProcessingJob, jid).status


def test_publish_all_again_only_retries_failed_unpublished_pairs(db):
    from dogsapka_clipper.db.models import ExportedVideo
    q = JobQueue(db, EventBus())
    with db.session() as s:
        a = ExportedVideo(path="a.mp4", published={"account:2": {"id": "already-published"}})
        s.add(a)
        s.flush()
        for kind, state, account in [("upload", "failed", 1), ("upload", "failed", 1),
                                     ("upload", "failed", 2), ("upload", "done", 3),
                                     ("upload", "failed", 3), ("upload", "running", 4),
                                     ("upload", "failed", 4), ("process", "failed", 5),
                                     ("upload", "cancelled", 6)]:
            s.add(ProcessingJob(kind=kind, status=state, error="old error", progress=.5,
                                params={"account_id": account, "export_id": a.id}))
    assert q.retry_failed_uploads() == 1
    assert q.retry_failed_uploads() == 0
    with db.session() as s:
        queued = s.query(ProcessingJob).filter_by(status="queued").all()
        assert len(queued) == 1 and queued[0].params["account_id"] == 1
        assert queued[0].progress == 0 and queued[0].error == ""
        assert s.query(ProcessingJob).filter_by(status="done").count() == 1


def test_queue_runs_fails_and_retries(db):
    q = JobQueue(db, EventBus(), workers=1)
    attempts = {"n": 0}

    def flaky(ctx):
        attempts["n"] += 1
        ctx.progress(0.5, "half")
        if attempts["n"] == 1:
            raise RuntimeError("ffmpeg exploded")
    q.register("x", flaky)
    q.start()
    try:
        jid = q.submit("x")
        assert wait(lambda: status(db, jid) == "failed")
        with db.session() as s:
            assert "exploded" in s.get(ProcessingJob, jid).error
        q.retry(jid)
        assert wait(lambda: status(db, jid) == "done")
    finally:
        q.stop()


def test_cancel_and_pause(db):
    q = JobQueue(db, EventBus(), workers=1)
    started = threading.Event()
    progressed = []

    def slow(ctx):
        started.set()
        for i in range(200):
            ctx.progress(i / 200)
            progressed.append(i)
            time.sleep(0.02)
    q.register("slow", slow)
    q.start()
    try:
        jid = q.submit("slow")
        assert started.wait(5)
        q.pause(jid)
        time.sleep(0.3)
        n = len(progressed)
        time.sleep(0.5)
        assert len(progressed) <= n + 1        # paused: no progress
        q.resume(jid)
        assert wait(lambda: len(progressed) > n + 3)
        q.cancel(jid)
        assert wait(lambda: status(db, jid) == "cancelled")
    finally:
        q.stop()


def test_interrupted_jobs_recovered(db):
    with db.session() as s:
        s.add(ProcessingJob(kind="x", status="running"))
    q = JobQueue(db, EventBus(), workers=1)
    done = threading.Event()
    q.register("x", lambda ctx: done.set())
    q.start()
    try:
        assert done.wait(5)
    finally:
        q.stop()
