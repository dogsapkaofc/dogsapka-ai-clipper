"""DB-backed processing queue with bounded workers, pause/resume, cancel and retry."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select

from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.db.database import Database
from dogsapka_clipper.db.models import ExportedVideo, ProcessingJob, utcnow

log = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


@dataclass
class JobContext:
    job_id: int
    clip_id: int | None
    kind: str
    params: dict
    cancel: threading.Event
    pause: threading.Event
    _report: Callable[[float, str], None] = field(repr=False, default=lambda p, s: None)

    def progress(self, frac: float, stage: str | None = None) -> None:
        self.check()
        self._report(max(0.0, min(1.0, frac)), stage or "")

    def check(self) -> None:
        while self.pause.is_set() and not self.cancel.is_set():
            time.sleep(0.2)
        if self.cancel.is_set():
            raise JobCancelled()


Handler = Callable[[JobContext], None]


class JobQueue:
    def __init__(self, db: Database, bus: EventBus, workers: int = 1) -> None:
        self.db = db
        self.bus = bus
        self.handlers: dict[str, Handler] = {}
        self._workers: list[threading.Thread] = []
        self._n = max(1, workers)
        self._stop = threading.Event()
        self._wake = threading.Condition()
        self._running: dict[int, JobContext] = {}
        self._paused_global = threading.Event()
        self._lock = threading.Lock()

    def register(self, kind: str, handler: Handler) -> None:
        self.handlers[kind] = handler

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        with self.db.session() as s:  # crash recovery: jobs interrupted mid-run are re-queued
            for j in s.scalars(select(ProcessingJob).where(ProcessingJob.status == "running")):
                j.status, j.stage = "queued", "Recovered after restart"
        self._stop.clear()
        for i in range(self._n):
            t = threading.Thread(target=self._worker, name=f"job-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        for ctx in list(self._running.values()):
            ctx.cancel.set()
        with self._wake:
            self._wake.notify_all()
        for t in self._workers:
            t.join(timeout)
        self._workers.clear()
        # cancelled-by-shutdown jobs should resume next start
        with self.db.session() as s:
            for j in s.scalars(select(ProcessingJob).where(ProcessingJob.status == "running")):
                j.status = "queued"

    def set_workers(self, n: int) -> None:
        n = max(1, n)
        if n > self._n and self._workers:
            for i in range(self._n, n):
                t = threading.Thread(target=self._worker, name=f"job-worker-{i}", daemon=True)
                t.start()
                self._workers.append(t)
        self._n = n  # reductions take effect as workers finish (see _worker)

    # -- API --------------------------------------------------------------
    def submit(self, kind: str, clip_id: int | None = None, params: dict | None = None) -> int:
        with self.db.session() as s:
            j = ProcessingJob(kind=kind, clip_id=clip_id, params=params or {}, status="queued", stage="Queued")
            s.add(j)
            s.flush()
            jid = j.id
        self.bus.emit("job.queued", "", job_id=jid, kind=kind, clip_id=clip_id)
        with self._wake:
            self._wake.notify()
        return jid

    def cancel(self, job_id: int) -> None:
        ctx = self._running.get(job_id)
        if ctx:
            ctx.cancel.set()
        self._set(job_id, status="cancelled", stage="Cancelled", only_if=("queued", "paused"))
        self.bus.emit("job.updated", "", job_id=job_id)

    def pause(self, job_id: int) -> None:
        ctx = self._running.get(job_id)
        if ctx:
            ctx.pause.set()
        self._set(job_id, status="paused", only_if=("queued", "running"))
        self.bus.emit("job.updated", "", job_id=job_id)

    def resume(self, job_id: int) -> None:
        ctx = self._running.get(job_id)
        if ctx:
            ctx.pause.clear()
            self._set(job_id, status="running")
        else:
            self._set(job_id, status="queued", only_if=("paused",))
        self.bus.emit("job.updated", "", job_id=job_id)
        with self._wake:
            self._wake.notify()

    def retry(self, job_id: int) -> None:
        self._set(job_id, status="queued", stage="Queued (retry)", error="", progress=0.0,
                  only_if=("failed", "cancelled"))
        self.bus.emit("job.updated", "", job_id=job_id)
        with self._wake:
            self._wake.notify()

    def pause_all(self, paused: bool) -> None:
        if paused:
            self._paused_global.set()
        else:
            self._paused_global.clear()
            with self._wake:
                self._wake.notify_all()
        for ctx in self._running.values():
            ctx.pause.set() if paused else ctx.pause.clear()
        self.bus.emit("queue.paused" if paused else "queue.resumed", "")

    def retry_failed_uploads(self) -> int:
        """Retry failed account/export pairs once, without touching successful uploads."""
        retried = []
        with self._lock, self.db.session() as s:
            jobs = s.scalars(select(ProcessingJob).where(ProcessingJob.kind == "upload")
                             .order_by(ProcessingJob.id.desc())).all()
            def key(j):
                return (str((j.params or {}).get("account_id")), str((j.params or {}).get("export_id")))
            active = {key(j) for j in jobs if j.status in ("queued", "running", "paused", "done")}
            for j in jobs:
                if j.status != "failed" or key(j) in active:
                    continue
                params = j.params or {}
                if not params.get("account_id") or not params.get("export_id"):
                    continue
                e = s.get(ExportedVideo, int(params["export_id"]))
                result = (e.published or {}).get(f"account:{params['account_id']}") if e else None
                if e is None or (result and result.get("state") != "schedule_pending"):
                    continue
                j.status, j.stage, j.error, j.progress = "queued", "Queued (publish again)", "", 0.0
                active.add(key(j))
                retried.append(j.id)
        for jid in retried:
            self.bus.emit("job.updated", "", job_id=jid)
        with self._wake:
            self._wake.notify_all()
        return len(retried)

    @property
    def globally_paused(self) -> bool:
        return self._paused_global.is_set()

    def active_count(self) -> int:
        return len(self._running)

    def pending_count(self) -> int:
        return self.db.count(ProcessingJob, ProcessingJob.status.in_(("queued", "running", "paused")))

    # -- internals ----------------------------------------------------------
    def _set(self, job_id: int, only_if: tuple[str, ...] | None = None, **fields) -> bool:
        with self.db.session() as s:
            j = s.get(ProcessingJob, job_id)
            if j is None or (only_if and j.status not in only_if):
                return False
            for k, v in fields.items():
                setattr(j, k, v)
            j.updated_at = utcnow()
            return True

    def _claim(self) -> ProcessingJob | None:
        with self._lock, self.db.session() as s:
            j = s.scalars(select(ProcessingJob).where(ProcessingJob.status == "queued")
                          .order_by(ProcessingJob.id).limit(1)).first()
            if j is None:
                return None
            j.status, j.attempts, j.stage = "running", j.attempts + 1, "Starting"
            s.flush()
            s.expunge(j)
            return j

    def _worker(self) -> None:
        idx = int(threading.current_thread().name.rsplit("-", 1)[-1])
        while not self._stop.is_set():
            if idx >= self._n or self._paused_global.is_set():
                with self._wake:
                    self._wake.wait(1.0)
                continue
            job = self._claim()
            if job is None:
                with self._wake:
                    self._wake.wait(2.0)
                continue
            try:
                self._execute(job)
            except Exception:  # never let a worker thread die
                log.exception("Job worker error")
                self._set(job.id, status="failed", stage="Failed", error="Internal worker error")

    def _execute(self, job: ProcessingJob) -> None:
        handler = self.handlers.get(job.kind)
        ctx = JobContext(job.id, job.clip_id, job.kind, dict(job.params or {}), threading.Event(), threading.Event())
        last = [0.0]

        def report(p: float, stage: str) -> None:
            now = time.monotonic()
            if now - last[0] < 0.4 and p < 1.0:
                return
            last[0] = now
            f = {"progress": p}
            if stage:
                f["stage"] = stage
            self._set(job.id, **f)
            self.bus.emit("job.progress", "", job_id=job.id, progress=p, stage=stage, clip_id=job.clip_id)
        ctx._report = report
        self._running[job.id] = ctx
        if self._paused_global.is_set():
            ctx.pause.set()
        self.bus.emit("job.started", "", job_id=job.id, kind=job.kind, clip_id=job.clip_id)
        try:
            if handler is None:
                raise RuntimeError(f"No handler for job type '{job.kind}'")
            handler(ctx)
            self._set(job.id, status="done", progress=1.0, stage="Completed")
            self.bus.emit("job.done", "", job_id=job.id, kind=job.kind, clip_id=job.clip_id)
        except JobCancelled:
            self._set(job.id, status="cancelled", stage="Cancelled")
            self.bus.emit("job.updated", "", job_id=job.id)
        except Exception as e:
            if ctx.cancel.is_set() or str(e) == "Cancelled":
                self._set(job.id, status="cancelled", stage="Cancelled")
                self.bus.emit("job.updated", "", job_id=job.id)
            else:
                log.error("Job %s (%s) failed: %s\n%s", job.id, job.kind, e, traceback.format_exc())
                self._set(job.id, status="failed", error=str(e)[:2000], stage="Failed")
                self.bus.emit("job.failed", f"Processing failed: {e}", "error", job_id=job.id, clip_id=job.clip_id)
        finally:
            self._running.pop(job.id, None)
