"""Conservative, local preference learning from explicitly rated clips.

This learns which detector *patterns* the user likes; it does not train a vision
model or infer whether a scene is semantically funny. Only nearby rated patterns
affect a new candidate, with a neutral prior and a small bounded score adjustment.
The caller supplies one current rating per clip when rebuilding the model, so
changing a rating replaces an example rather than training on it repeatedly.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping

import numpy as np


FEATURES = ("chat_activity", "chat_reactions", "audio", "visual", "laughter",
            "voice_reaction", "sustained_reaction")
MIN_SAMPLES = 4
MAX_SAMPLES = 512
MAX_ADJUSTMENT = 10.0
FEEDBACK_PREFIX = "highlight_feedback:"


def feedback_key(clip_id: int) -> str:
    return f"{FEEDBACK_PREFIX}{clip_id}"


_NEIGHBOR_RADIUS = 0.65
_NEUTRAL_PRIOR = 3.0


def _vector(signals: Mapping[str, float]) -> np.ndarray | None:
    if not isinstance(signals, Mapping):
        return None
    try:
        values = np.asarray([float(signals.get(k, 0.0)) for k in FEATURES], dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        return None
    if not np.isfinite(values).all():
        return None
    values = np.clip(values, 0.0, 1.0)
    return values if np.any(values) else None


class PreferenceModel:
    """An immutable snapshot, safe to share between live-session readers.

    Input feedback is chronological (oldest first). At most the latest 512 valid
    rated examples are retained. Four examples are needed before any adjustment;
    sparse or contradictory local feedback is shrunk towards zero.
    """

    def __init__(self, patterns: np.ndarray, ratings: np.ndarray, known: np.ndarray | None = None) -> None:
        self._patterns = np.array(patterns, dtype=np.float64, copy=True)
        self._ratings = np.array(ratings, dtype=np.float64, copy=True)
        self._known = np.ones_like(self._patterns, dtype=bool) if known is None else np.array(known, dtype=bool, copy=True)
        self._patterns.setflags(write=False)
        self._ratings.setflags(write=False)
        self._known.setflags(write=False)

    @classmethod
    def from_feedback(cls, feedback: Iterable[tuple[Mapping[str, float], int]]) -> "PreferenceModel":
        examples: deque[tuple[np.ndarray, np.ndarray, int]] = deque(maxlen=MAX_SAMPLES)
        for signals, rating in feedback:
            if rating not in (-1, 1):
                continue
            vector = _vector(signals)
            if vector is not None:
                examples.append((vector, np.asarray([k in signals for k in FEATURES]), int(rating)))
        patterns = np.stack([v for v, _, _ in examples]) if examples else np.empty((0, len(FEATURES)))
        known = np.stack([mask for _, mask, _ in examples]) if examples else np.empty((0, len(FEATURES)), dtype=bool)
        return cls(patterns, np.asarray([rating for _, _, rating in examples]), known)

    @property
    def sample_count(self) -> int:
        return len(self._ratings)

    def adjustment(self, signals: Mapping[str, float]) -> float:
        """Return a finite correction in [-10, 10] highlight-score points.

        A compact similarity kernel means an unseen or very different pattern
        receives exactly zero correction, even if all previous clips were liked.
        This cannot create a highlight from silence / absent detector evidence.
        """
        if self.sample_count < MIN_SAMPLES:
            return 0.0
        vector = _vector(signals)
        if vector is None:
            return 0.0
        # Legacy clips contain only the four original detector features. Unknown
        # laughter/voice values must not be mistaken for measured zeros. Compare
        # shared features and reduce confidence for incomplete overlap.
        query_known = np.asarray([k in signals for k in FEATURES])
        shared = self._known & query_known
        shared_count = shared.sum(axis=1)
        coverage = shared_count / np.maximum(1, (self._known | query_known).sum(axis=1))
        delta = (self._patterns - vector) * shared
        squared_distance = np.einsum("ij,ij->i", delta, delta) * len(FEATURES) / np.maximum(1, shared_count)
        similarity = np.maximum(0.0, 1.0 - squared_distance / _NEIGHBOR_RADIUS ** 2) ** 2 * coverage
        similarity[(coverage < 0.5) | (shared_count < 2)] = 0.0
        support = float(similarity.sum())
        if support <= 0.0:
            return 0.0
        signed_support = float(np.dot(similarity, self._ratings))
        correction = MAX_ADJUSTMENT * signed_support / (support + _NEUTRAL_PRIOR)
        return float(np.clip(correction, -MAX_ADJUSTMENT, MAX_ADJUSTMENT))
