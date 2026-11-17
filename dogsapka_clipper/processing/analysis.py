"""Clip-level analysis helpers: silence detection, perceptual hashing, duplicates."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from dogsapka_clipper.processing.ffmpeg_utils import decode_audio_pcm, decode_gray_frames


def speech_activity(pcm: np.ndarray, sr: int = 16000, frame: float = 0.1) -> np.ndarray:
    """Boolean activity per frame using an adaptive energy threshold."""
    n = int(sr * frame)
    if pcm.size < n:
        return np.zeros(0, dtype=bool)
    k = pcm.size // n
    rms = np.sqrt(np.mean(pcm[: k * n].reshape(k, n) ** 2, axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    floor = np.percentile(db, 10)
    thr = max(floor + 10, -50.0)
    return db > thr


def keep_ranges_without_silence(pcm: np.ndarray, duration: float, min_silence: float = 1.2,
                                pad: float = 0.25, sr: int = 16000, frame: float = 0.1,
                                protect: tuple[float, float] | None = None) -> list[tuple[float, float]] | None:
    """Ranges to keep when shortening long silences.

    Only silences longer than ``min_silence`` are shortened (to ``2*pad``), and the
    ``protect`` span (the highlight itself) is never cut. Returns None when nothing is cut.
    """
    act = speech_activity(pcm, sr, frame)
    if act.size == 0:
        return None
    silences = []
    start = None
    for i, a in enumerate(list(act) + [True]):
        if not a and start is None:
            start = i
        elif a and start is not None:
            s, e = start * frame, i * frame
            if e - s >= min_silence:
                silences.append((s + pad, e - pad))
            start = None
    if protect:
        silences = [(s, e) for s, e in silences if e <= protect[0] or s >= protect[1]]
    if not silences:
        return None
    keep, cur = [], 0.0
    for s, e in silences:
        if s > cur:
            keep.append((round(cur, 3), round(s, 3)))
        cur = e
    if cur < duration:
        keep.append((round(cur, 3), round(duration, 3)))
    removed = duration - sum(b - a for a, b in keep)
    return keep if removed >= 1.0 else None


def silence_keep_ranges(path: Path, start: float, duration: float, protect: tuple[float, float] | None = None):
    pcm = decode_audio_pcm(path, start=start, duration=duration)
    return keep_ranges_without_silence(pcm, duration, protect=protect)


def dhash(gray: np.ndarray, size: int = 8) -> int:
    """Difference hash of a grayscale image."""
    h, w = gray.shape
    ys = (np.arange(size) * h / size).astype(int)
    xs = (np.arange(size + 1) * w / (size + 1)).astype(int)
    small = gray[np.ix_(ys, xs)].astype(np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def video_signature(path: Path, n: int = 4) -> list[int]:
    frames = decode_gray_frames(path, fps=0.5, size=(64, 36))
    if len(frames) == 0:
        return []
    idx = np.linspace(0, len(frames) - 1, min(n, len(frames))).round().astype(int)
    return [dhash(frames[i]) for i in idx]


def signature_distance(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 64.0
    return float(np.mean([min(hamming(x, y) for y in b) for x in a]))


def time_overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Intersection over the shorter interval (1.0 = one contains the other)."""
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    short = min(a[1] - a[0], b[1] - b[0])
    return inter / short if short > 0 else 0.0


def is_duplicate(range_a: tuple[float, float] | None, range_b: tuple[float, float] | None,
                 sig_a: list[int] | None = None, sig_b: list[int] | None = None) -> bool:
    if range_a and range_b and time_overlap(range_a, range_b) >= 0.6:
        return True
    if sig_a and sig_b and signature_distance(sig_a, sig_b) <= 5:
        return True
    return False
