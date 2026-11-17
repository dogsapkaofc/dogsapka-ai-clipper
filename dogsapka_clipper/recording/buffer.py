"""Circular recording buffer built from short, timestamped MPEG-TS segments.

The buffer keeps segments covering at least the last ``buffer_seconds`` of wall-clock time.
Segments overlapping a *protected* range (a pending clip still waiting for post-event
footage) are never deleted until the protection is released.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class Segment:
    path: Path
    start: float     # wall-clock epoch seconds
    duration: float
    index: int = 0

    @property
    def end(self) -> float:
        return self.start + self.duration


class SegmentRingBuffer:
    def __init__(self, buffer_seconds: float, on_delete: Callable[[Segment], None] | None = None) -> None:
        self.buffer_seconds = buffer_seconds
        self._segments: list[Segment] = []
        self._protected: dict[int, tuple[float, float]] = {}
        self._next_token = 1
        self._lock = threading.RLock()
        self._on_delete = on_delete

    def __len__(self) -> int:
        return len(self._segments)

    @property
    def segments(self) -> list[Segment]:
        with self._lock:
            return list(self._segments)

    def add(self, seg: Segment) -> None:
        with self._lock:
            # keep sorted, ignore exact duplicates
            if any(s.path == seg.path for s in self._segments):
                return
            self._segments.append(seg)
            self._segments.sort(key=lambda s: s.start)
            self._evict()

    def covered_seconds(self) -> float:
        with self._lock:
            return sum(s.duration for s in self._segments)

    def latest_end(self) -> float:
        with self._lock:
            return self._segments[-1].end if self._segments else 0.0

    def earliest_start(self) -> float:
        with self._lock:
            return self._segments[0].start if self._segments else 0.0

    def protect(self, start: float, end: float) -> int:
        with self._lock:
            tok = self._next_token
            self._next_token += 1
            self._protected[tok] = (start, end)
            return tok

    def release(self, token: int) -> None:
        with self._lock:
            self._protected.pop(token, None)
            self._evict()

    def range(self, start: float, end: float) -> list[Segment]:
        """Segments overlapping [start, end), in order."""
        with self._lock:
            return [s for s in self._segments if s.end > start and s.start < end]

    def _is_protected(self, seg: Segment) -> bool:
        return any(seg.end > a and seg.start < b for a, b in self._protected.values())

    def _evict(self) -> None:
        if not self._segments:
            return
        horizon = self._segments[-1].end - self.buffer_seconds
        keep: list[Segment] = []
        for s in self._segments:
            if s.end < horizon and not self._is_protected(s):
                self._delete(s)
            else:
                keep.append(s)
        self._segments = keep

    def _delete(self, seg: Segment) -> None:
        try:
            seg.path.unlink(missing_ok=True)
        except OSError as e:  # file may be locked briefly on Windows; it will be cleaned later
            log.debug("Could not delete segment %s: %s", seg.path, e)
        if self._on_delete:
            self._on_delete(seg)

    def clear(self, delete_files: bool = True) -> None:
        with self._lock:
            if delete_files:
                for s in self._segments:
                    if not self._is_protected(s):
                        self._delete(s)
            self._segments = [s for s in self._segments if self._is_protected(s)] if delete_files else []


class WallClockMapper:
    """Maps stream timestamps (segment muxer times) to wall-clock time.

    A segment is reported only after it has been fully written, so
    ``report_wall - end_pts`` is always >= the true offset. The minimum observed value is
    therefore the best estimate, and robust against processing delays.
    """

    def __init__(self) -> None:
        self.offset: float | None = None

    def observe(self, end_pts: float, report_wall: float) -> None:
        cand = report_wall - end_pts
        if self.offset is None or cand < self.offset:
            self.offset = cand

    def reset(self) -> None:
        self.offset = None

    def to_wall(self, pts: float) -> float:
        return (self.offset or 0.0) + pts
