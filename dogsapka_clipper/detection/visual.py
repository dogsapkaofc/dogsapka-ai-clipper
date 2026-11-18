"""Low-resolution visual action evidence for the multimodal highlight scorer.

Camera pans, brightness changes, loading screens and isolated cuts are weak evidence.
Sustained, local movement relative to the stream's learned baseline is stronger evidence.
This analyzes pixels; it does not infer what a game character or a joke means.
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


def _motion_residual(previous: np.ndarray, current: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Remove global brightness and confident camera translation before scoring movement.

    Phase correlation uses only tiny FFTs and NumPy, so no optional model or GPU is needed.
    Overlapping pixels are compared without introducing wrapped camera-pan borders.
    """
    a, b = previous.astype(np.float32), current.astype(np.float32)
    raw_diff = float(np.abs(b - a).mean()) / 255.0
    centered_a, centered_b = a - a.mean(), b - b.mean()
    cross = np.fft.rfft2(centered_b) * np.conj(np.fft.rfft2(centered_a))
    magnitude = np.abs(cross)
    phase = np.fft.irfft2(cross / np.maximum(magnitude, 1e-6), s=a.shape)
    iy, ix = np.unravel_index(np.argmax(phase), phase.shape)
    h, w = a.shape
    dy, dx = int(iy if iy <= h // 2 else iy - h), int(ix if ix <= w // 2 else ix - w)
    confidence = float(phase[iy, ix])
    pan = 0.0
    if confidence >= 0.2 and abs(dx) <= w // 4 and abs(dy) <= h // 4 and (dx or dy):
        old = a[max(0, -dy):min(h, h - dy), max(0, -dx):min(w, w - dx)]
        new = b[max(0, dy):min(h, h + dy), max(0, dx):min(w, w + dx)]
        aligned = new - old
        # Accept alignment only if it actually explains the observed change.
        if float(np.abs(aligned).mean()) < raw_diff * 255 * 0.8:
            a, b = old, new
            pan = float(np.hypot(dx, dy))
    delta = b - a
    residual = np.abs(delta - np.median(delta)) / 255.0
    return residual, raw_diff, pan


def _local_motion(residual: np.ndarray) -> tuple[float, float]:
    mean = float(residual.mean())
    tiles = [float(tile.mean()) for row in np.array_split(residual, 3, axis=0)
             for tile in np.array_split(row, 4, axis=1) if tile.size]
    peak = max(tiles, default=mean)
    locality = ramp(peak / (mean + 0.002), 1.2, 3.5)
    return 0.65 * mean + 0.35 * peak, locality


class VisualChangeDetector:
    def __init__(self, fps: float = 2.0, sensitivity: float = 0.5) -> None:
        self.fps = fps
        self.sensitivity = sensitivity
        self.diff_stats = EWStats(span=240)   # ~2 min
        self._prev: np.ndarray | None = None
        self._last_ts: float | None = None
        self._action: deque[tuple[float, float]] = deque(maxlen=max(4, int(fps * 3)))

    def process(self, frames: np.ndarray, start_ts: float) -> list[tuple[float, SignalSample]]:
        out = []
        for i, fr in enumerate(frames):
            ts = start_ts + i / self.fps
            if (self._prev is None or self._prev.shape != fr.shape or
                    (self._last_ts is not None and (ts <= self._last_ts or ts - self._last_ts > max(2, 3 / self.fps)))):
                self._prev = fr.copy()
                self._last_ts = ts
                self._action.clear()
                continue
            residual, raw_diff, pan = _motion_residual(self._prev, fr)
            diff, locality = _local_motion(residual)
            hd = hist_distance(fr, self._prev)
            blank = float(fr.std()) < 2.0 or float(self._prev.std()) < 2.0
            self._prev = fr.copy()
            self._last_ts = ts
            warm = self.diff_stats.n >= 20
            mean, std = self.diff_stats.mean, max(self.diff_stats.std, 0.01)
            z = (diff - mean) / std if warm else 0.0
            cut = hd > 0.35 and raw_diff > 0.08
            action = ramp(z, 2.5 - self.sensitivity, 7.0) * ramp(diff, 0.01, 0.06)
            action *= 0.25 + 0.75 * locality
            if blank:
                action = 0.0
            if cut:
                action = min(action, 0.25)  # cuts/loading transitions alone are not exciting content
            while self._action and self._action[0][0] < ts - 2.0:
                self._action.popleft()
            self._action.append((ts, action))
            sustained = sum(v >= 0.4 for _, v in self._action)
            value = action * (0.35 + 0.65 * ramp(sustained, 1, 3))
            self.diff_stats.update(min(diff, mean + 3 * std) if warm else diff)
            reason = ""
            if value >= 0.5:
                reason = "Sustained local visual action"
            out.append((ts, SignalSample(value if warm else 0.0, reason,
                       {"diff": diff, "raw_diff": raw_diff, "hist": hd, "z": z,
                        "locality": locality, "pan": pan, "cut": cut, "blank": blank,
                        "sustained": sustained})))
        return out

    def state(self) -> dict:
        return {"version": 2, "diff": self.diff_stats.state()}

    def load_state(self, st: dict | None) -> None:
        # Legacy statistics measured raw frame difference, not compensated motion.
        # Accept old state without an error, but relearn the changed feature's baseline.
        if st and st.get("version") == 2:
            self.diff_stats.load(st.get("diff"))
            self.diff_stats.n = min(self.diff_stats.n, 20)
