"""Detection method E: combined highlight score, event merging, cooldown, auto-threshold."""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from dogsapka_clipper.core.config import DetectionSettings
from dogsapka_clipper.detection.base import SecondSeries, SignalSample

log = logging.getLogger(__name__)

SIGNALS = ("chat_activity", "chat_reactions", "audio", "visual")


def category(score: float) -> str:
    if score >= 90:
        return "Exceptional highlight"
    if score >= 75:
        return "Very good highlight"
    if score >= 60:
        return "Potential highlight"
    return "Ignored"


@dataclass
class HighlightEvent:
    peak_ts: float
    start_ts: float          # first second above threshold
    end_ts: float            # last second above threshold
    score: float
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)
    merged: int = 1

    @property
    def category(self) -> str:
        return category(self.score)


class HighlightScorer:
    """Combines per-second detector signals into a 0..100 score and emits merged events.

    Chat reacts *after* the moment (stream latency + typing time), so chat signals are shifted
    back by ``chat_delay_s`` before being aligned with audio/visual signals.
    """

    def __init__(self, settings: DetectionSettings, post_event_s: float,
                 on_highlight: Callable[[HighlightEvent], None],
                 chat_delay_s: float = 5.0, eval_lag_s: float = 10.0) -> None:
        self.s = settings
        self.post_event_s = post_event_s
        self.on_highlight = on_highlight
        self.chat_delay_s = chat_delay_s
        self.eval_lag_s = eval_lag_s
        self.series = {k: SecondSeries() for k in SIGNALS}
        self.available: set[str] = set()
        self._last_eval: int | None = None
        self._open: HighlightEvent | None = None
        self._pending: HighlightEvent | None = None
        self._last_final_end = -1e18
        self.threshold_offset = 0.0
        self._recent_events: deque[float] = deque()
        self.scores: deque[tuple[float, float]] = deque(maxlen=900)   # (ts, score) for UI
        self.closed_scores: deque[float] = deque(maxlen=3600)

    # ------------------------------------------------------------------
    def push(self, signal: str, ts: float, sample: SignalSample) -> None:
        if signal in ("chat_activity", "chat_reactions"):
            ts -= self.chat_delay_s
        self.series[signal].put(ts, sample)
        self.available.add(signal)

    def enabled_weights(self) -> dict[str, float]:
        s = self.s
        cfg = {"chat_activity": (s.use_chat_activity, s.weight_chat_activity),
               "chat_reactions": (s.use_chat_reactions, s.weight_chat_reactions),
               "audio": (s.use_audio, s.weight_audio),
               "visual": (s.use_visual, s.weight_visual)}
        w = {k: wt for k, (on, wt) in cfg.items() if on and wt > 0 and k in self.available}
        tot = sum(w.values())
        return {k: v / tot for k, v in w.items()} if tot else {}

    @property
    def threshold(self) -> float:
        base = self.s.threshold - (self.s.sensitivity - 0.5) * 20.0
        return max(30.0, min(97.0, base + self.threshold_offset))

    def score_at(self, sec: int) -> tuple[float, dict[str, float], list[str]]:
        weights = self.enabled_weights()
        if not weights:
            return 0.0, {}, []
        vals: dict[str, float] = {}
        reasons: list[str] = []
        for k, w in weights.items():
            smp: SignalSample | None = self.series[k].window_max(sec - 1, sec + 1)
            v = smp.value if smp else 0.0
            vals[k] = v
            if smp and smp.reason and v >= 0.5:
                reasons.append(smp.reason)
        linear = sum(weights[k] * vals[k] for k in weights)
        # agreement bonus: several independent signals firing together is more reliable than one
        strong = sum(1 for v in vals.values() if v >= 0.5)
        peak = max(vals.values())
        combined = linear + (0.12 * (strong - 1) if strong >= 2 else 0.0) + 0.25 * peak * (1 - linear)
        return max(0.0, min(100.0, 100.0 * min(1.0, combined))), vals, reasons

    def tick(self, now: float) -> None:
        """Evaluate all whole seconds up to ``now - eval_lag``; call ~1/s."""
        target = int(now - self.eval_lag_s)
        start = target if self._last_eval is None else self._last_eval + 1
        for sec in range(max(start, target - 120), target + 1):
            self._eval_second(sec)
        self._last_eval = target
        self._maybe_finalize(now - self.eval_lag_s)
        self._adapt_threshold(now)

    def _eval_second(self, sec: int) -> None:
        score, vals, reasons = self.score_at(sec)
        self.scores.append((float(sec), score))
        thr = self.threshold
        if self._open is None:
            # a pending (not yet finalized) event may still absorb new activity; otherwise the
            # cooldown after the last finalized highlight applies
            if score >= thr and (self._pending is not None or sec >= self._last_final_end + self.s.cooldown_s):
                self._open = HighlightEvent(sec, sec, sec, score, list(dict.fromkeys(reasons)), vals)
            return
        ev = self._open
        if score >= thr * 0.75:
            ev.end_ts = sec
            if score > ev.score:
                ev.score, ev.peak_ts, ev.signals = score, sec, vals
            for r in reasons:
                if r not in ev.reasons:
                    ev.reasons.append(r)
        if score < thr * 0.75 or sec - ev.start_ts > 60:
            self._close(ev)
            self._open = None

    def _close(self, ev: HighlightEvent) -> None:
        self.closed_scores.append(ev.score)
        p = self._pending
        if p is not None and ev.start_ts - p.end_ts <= self.s.merge_window_s:
            p.end_ts = max(p.end_ts, ev.end_ts)
            p.merged += 1
            if ev.score > p.score:
                p.score, p.peak_ts, p.signals = ev.score, ev.peak_ts, ev.signals
            p.reasons += [r for r in ev.reasons if r not in p.reasons]
            return
        if p is not None:
            self._finalize()
        self._pending = ev

    def _maybe_finalize(self, eval_now: float) -> None:
        p = self._pending
        if p is not None and self._open is None and eval_now - p.end_ts > self.s.merge_window_s:
            self._finalize()

    def flush(self) -> None:
        """Finalize everything (stream ended)."""
        if self._open is not None:
            self._close(self._open)
            self._open = None
        if self._pending is not None:
            self._finalize()

    def _finalize(self) -> None:
        ev, self._pending = self._pending, None
        if ev is None:
            return
        self._last_final_end = ev.end_ts
        self._recent_events.append(ev.end_ts)
        if not ev.reasons:
            ev.reasons = ["Combined signal increase"]
        log.info("Highlight finalized: score=%.1f reasons=%s", ev.score, ev.reasons)
        try:
            self.on_highlight(ev)
        except Exception:
            log.exception("highlight callback failed")

    def _adapt_threshold(self, now: float) -> None:
        if not self.s.auto_threshold:
            self.threshold_offset = 0.0
            return
        while self._recent_events and self._recent_events[0] < now - 3600:
            self._recent_events.popleft()
        target_per_hour = 4 + 8 * self.s.sensitivity           # 8/h at default sensitivity
        rate = len(self._recent_events)
        if rate > target_per_hour:
            self.threshold_offset = min(25.0, self.threshold_offset + 0.05)
        elif self.threshold_offset > 0:
            self.threshold_offset = max(0.0, self.threshold_offset - 0.01)

    def current_score(self) -> float:
        return self.scores[-1][1] if self.scores else 0.0
