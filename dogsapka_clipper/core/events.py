"""Thread-safe publish/subscribe event bus used between backend workers and the UI."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Event:
    kind: str                 # e.g. "streamer.live", "highlight.detected", "clip.saved"
    message: str = ""
    level: str = "info"       # info | success | warning | error
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


Listener = Callable[[Event], None]


class EventBus:
    def __init__(self, history: int = 500) -> None:
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self.history: deque[Event] = deque(maxlen=history)

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)
        return unsubscribe

    def emit(self, kind: str, message: str = "", level: str = "info", /, **data: Any) -> Event:
        ev = Event(kind=kind, message=message, level=level, data=data)
        with self._lock:
            self.history.append(ev)
            listeners = list(self._listeners)
        if message:
            getattr(log, "error" if level == "error" else "warning" if level == "warning" else "info")(
                "[%s] %s", kind, message)
        for fn in listeners:
            try:
                fn(ev)
            except Exception:  # a broken listener must never break the producer
                log.exception("Event listener failed for %s", kind)
        return ev
