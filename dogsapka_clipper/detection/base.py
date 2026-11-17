"""Shared primitives for detectors."""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class SignalSample:
    value: float                 # normalized 0..1
    reason: str = ""
    detail: dict = field(default_factory=dict)


class SecondSeries:
    """Per-second (wall clock) store of the strongest sample, bounded in size."""

    def __init__(self, keep_seconds: int = 900) -> None:
        self.keep = keep_seconds
        self._data: "OrderedDict[int, SignalSample]" = OrderedDict()
        self._lock = threading.Lock()

    def put(self, ts: float, sample: SignalSample) -> None:
        sec = int(ts)
        with self._lock:
            cur = self._data.get(sec)
            if cur is None or sample.value > cur.value:
                self._data[sec] = sample
                self._data.move_to_end(sec)  # keeps approx order; trimming below is by key
            if len(self._data) > self.keep * 1.2:
                cutoff = max(self._data) - self.keep
                for k in [k for k in self._data if k < cutoff]:
                    del self._data[k]

    def window_max(self, start: float, end: float) -> SignalSample | None:
        best = None
        with self._lock:
            for sec in range(int(start), int(end) + 1):
                s = self._data.get(sec)
                if s and (best is None or s.value > best.value):
                    best = s
        return best

    def latest_second(self) -> int | None:
        with self._lock:
            return max(self._data) if self._data else None


class EWStats:
    """Exponentially weighted mean/variance with a time-constant in samples."""

    def __init__(self, span: float, mean: float = 0.0, var: float = 0.0, n: int = 0) -> None:
        self.alpha = 2.0 / (span + 1.0)
        self.mean = mean
        self.var = var
        self.n = n

    def update(self, x: float) -> None:
        if self.n == 0:
            self.mean, self.var = x, 0.0
        else:
            d = x - self.mean
            # bias correction: behave like a cumulative average until enough samples were seen,
            # so early baselines are not dominated by the first (often quiet) seconds
            alpha = max(self.alpha, 1.0 / (self.n + 1))
            incr = alpha * d
            self.mean += incr
            self.var = (1 - alpha) * (self.var + d * incr)
        self.n += 1

    @property
    def std(self) -> float:
        return math.sqrt(max(self.var, 0.0))

    def state(self) -> dict:
        return {"mean": self.mean, "var": self.var, "n": self.n}

    def load(self, st: dict | None) -> None:
        if st:
            self.mean = float(st.get("mean", self.mean))
            self.var = float(st.get("var", self.var))
            self.n = int(st.get("n", self.n))


def ramp(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))
