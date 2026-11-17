"""Detection method D: visual changes (optional; disabled by default for low-end PCs).

Works on tiny grayscale frames (160x90 @ 2 fps), so it is cheap. A frame-difference
baseline adapts to the game: constant fast motion raises the baseline instead of
triggering highlights; hard cuts and bursts of change relative to that baseline score high.
"""
from __future__ import annotations

from collections import deque

import numpy as np

from dogsapka_clipper.detection.base import EWStats, SignalSample, ramp


def hist_distance(a: np.ndarray, b: np.ndarray, bins: int = 32) -> float:
    ha = np.histogram(a, bins=bins, range=(0, 255))[0].astype(np.float64)
    hb = np.histogram(b, bins=bins, range=(0, 255))[0].astype(np.float64)
    ha /= ha.sum() + 1e-9
    hb /= hb.sum() + 1e-9
    return float(0.5 * np.abs(ha - hb).sum())   # total variation distance, 0..1


class VisualChangeDetector:
    def __init__(self, fps: float = 2.0, sensitivity: float = 0.5) -> None:
        self.fps = fps
        self.sensitivity = sensitivity
        self.diff_stats = EWStats(span=240)   # ~2 min
        self._prev: np.ndarray | None = None
        self._cuts: deque[float] = deque()

    def process(self, frames: np.ndarray, start_ts: float) -> list[tuple[float, SignalSample]]:
        out = []
        for i, fr in enumerate(frames):
            ts = start_ts + i / self.fps
            if self._prev is None:
                self._prev = fr
                continue
            diff = float(np.mean(np.abs(fr.astype(np.int16) - self._prev.astype(np.int16)))) / 255.0
            hd = hist_distance(fr, self._prev)
            self._prev = fr
            warm = self.diff_stats.n >= 20
            mean, std = self.diff_stats.mean, max(self.diff_stats.std, 0.01)
            z = (diff - mean) / std if warm else 0.0
            cut = hd > 0.35 and diff > 0.08
            if cut:
                self._cuts.append(ts)
            while self._cuts and self._cuts[0] < ts - 10:
                self._cuts.popleft()
            burst = ramp(len(self._cuts), 2, 6)          # rapid camera/scene changes
            value = max(ramp(z, 2.5 - self.sensitivity, 7.0), 0.6 * ramp(hd, 0.35, 0.8) if cut else 0.0, burst * 0.8)
            self.diff_stats.update(min(diff, mean + 3 * std) if warm else diff)
            reason = ""
            if value >= 0.5:
                reason = "Rapid scene changes" if burst >= 0.5 else "Scene change" if cut else "Sudden visual action"
            out.append((ts, SignalSample(value if warm else 0.0, reason, {"diff": diff, "hist": hd, "z": z})))
        return out

    def state(self) -> dict:
        return {"diff": self.diff_stats.state()}

    def load_state(self, st: dict | None) -> None:
        if st:
            self.diff_stats.load(st.get("diff"))
            self.diff_stats.n = min(self.diff_stats.n, 20)
