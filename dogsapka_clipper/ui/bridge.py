"""Delivers backend EventBus events to the Qt GUI thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from dogsapka_clipper.core.events import Event, EventBus


class QtBridge(QObject):
    event = Signal(object)

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        # Qt queues cross-thread signal emissions onto the receiver's (GUI) thread
        self._unsub = bus.subscribe(self.event.emit)

    def close(self) -> None:
        self._unsub()


class _Task(QRunnable):
    def __init__(self, fn, done, err):
        super().__init__()
        self.fn, self.sig = fn, _TaskSignals()
        if done:
            self.sig.done.connect(done)
        if err:
            self.sig.error.connect(err)

    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # report on the GUI thread
            self.sig.error.emit(e)
        else:
            self.sig.done.emit(res)


class _TaskSignals(QObject):
    done = Signal(object)
    error = Signal(object)


_keep: set = set()


def run_async(fn, done=None, error=None) -> None:
    """Run ``fn`` in a worker thread so the UI never freezes; callbacks run on the GUI thread."""
    t = _Task(fn, done, error)
    _keep.add(t.sig)
    t.sig.done.connect(lambda *_: _keep.discard(t.sig))
    t.sig.error.connect(lambda *_: _keep.discard(t.sig))
    t.setAutoDelete(True)
    QThreadPool.globalInstance().start(t)
