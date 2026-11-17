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
