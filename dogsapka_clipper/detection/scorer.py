"""Detection method E: combined highlight score, event merging, cooldown, auto-threshold."""
from __future__ import annotations

import logging
import math
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
                 chat_delay_s: float = 5.0, eval_lag_s: float = 10.0,
                 score_adjuster: Callable[[dict[str, float]], float] | None = None) -> None:
        self.s = settings
        self.post_event_s = post_event_s
        self.on_highlight = on_highlight
        self.chat_delay_s = chat_delay_s
        self.eval_lag_s = eval_lag_s
        self.score_adjuster = score_adjuster
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
        samples: dict[str, SignalSample | None] = {}
        reasons: list[str] = []
        for k in weights:
            smp: SignalSample | None = self.series[k].window_max(sec - 1, sec + 1)
            v = self._unit(smp.value) if smp else 0.0
            vals[k] = v
            samples[k] = smp
            if smp and smp.reason and v >= 0.5:
                reasons.append(smp.reason)
        linear = sum(weights[k] * vals[k] for k in weights)
        # Vision supplies supporting evidence. Enabling it must not lower the score
        # of a genuine human reaction simply because the picture stays quiet.
        human_weight = sum(w for k, w in weights.items() if k != "visual")
        if human_weight:
            human_linear = sum(w * vals[k] for k, w in weights.items() if k != "visual") / human_weight
            linear = max(linear, human_linear)
        # Message count and reacting messages come from the same chat, so they do not
        # earn an independent-source agreement bonus for reinforcing each other.
        chat = max(vals.get("chat_activity", 0.0), vals.get("chat_reactions", 0.0))
        audio, visual = vals.get("audio", 0.0), vals.get("visual", 0.0)
        reactions = vals.get("chat_reactions", 0.0)
        strong = sum(v >= 0.5 for v in (chat, audio, visual))
        peak = max(vals.values())
        combined = linear + (0.12 * (strong - 1) if strong >= 2 else 0.0) + 0.25 * peak * (1 - linear)
        score = 100.0 * min(1.0, combined)

        sound = samples.get("audio")
        voice, laugh = self._voice_evidence(sound)
        voice_reaction = audio * voice
        laughter = audio * laugh
        audio_support = self._support("audio", sec)
        voice_support = self._support("audio", sec, voiced=True)
        reaction_support = self._support("chat_reactions", sec)
        vals.update(laughter=laughter, voice_reaction=voice_reaction,
                    sustained_reaction=voice_reaction * min(1.0, voice_support / 2))

        # A laughing streamer or a sustained reaction can be interesting even when
        # a small chat cannot provide enough message volume for a weighted average.
        if laughter >= 0.45 and (voice_support >= 2 or reactions >= 0.65):
            score = max(score, 64 + 15 * laughter)
            reasons.append("Sustained laughter / audience reaction")
        if voice_support >= 2 and audio >= 0.7 and voice_reaction >= 0.5:
            score = max(score, 60 + 15 * voice_reaction)
            reasons.append("Sustained streamer reaction")
        if reactions >= 0.65 and reaction_support >= 2:
            score = max(score, 64 + 10 * reactions)
            reasons.append("Sustained audience reactions")
        if voice_reaction >= 0.6 and (chat >= 0.45 or visual >= 0.45):
            score = max(score, 68 + 12 * voice_reaction)
            reasons.append("Streamer reaction matches chat / visual action")

        if self.score_adjuster is not None:
            try:
                adjustment = float(self.score_adjuster(dict(vals)))
                if math.isfinite(adjustment):
                    adjustment = max(-12.0, min(12.0, adjustment))
                    score += adjustment
                    if abs(adjustment) >= 0.5:
                        reasons.append(f"Your clip ratings ({adjustment:+.1f} points)")
            except Exception:
                # A preference model must never stop recording or highlight selection.
                log.exception("Preference score adjustment failed")

        # Learning cannot turn a loading screen, a cut, or one generic explosion
        # into a sure highlight. Human reactions remain the strongest evidence.
        chat_on, audio_on, visual_on = chat >= 0.5, audio >= 0.5, visual >= 0.5
        ceiling = 100.0
        if visual_on and not chat_on and not audio_on:
            ceiling = 54.0
        if chat_on and not audio_on and not visual_on:
            ceiling = min(ceiling, 82.0 if reactions >= 0.65 and reaction_support >= 2 else 58.0 if reactions >= 0.5 else 54.0)
        if audio_on and not chat_on and not visual_on:
            if voice >= 0.4:
                ceiling = min(ceiling, 82.0 if voice_support >= 2 else 58.0)
            else:
                ceiling = min(ceiling, 65.0 if audio_support >= 2 else 55.0)
        known_noise = audio_on and sound is not None and ("voice" in sound.detail or "sudden loud audio" in sound.reason.lower()) and voice < 0.35
        if known_noise and reactions < 0.5:
            ceiling = min(ceiling, 58.0)
        return max(0.0, min(ceiling, score)), vals, list(dict.fromkeys(reasons))

    @staticmethod
    def _unit(value: float) -> float:
        return max(0.0, min(1.0, value)) if math.isfinite(value) else 0.0

    @classmethod
    def _voice_evidence(cls, sample: SignalSample | None) -> tuple[float, float]:
        if sample is None:
            return 0.0, 0.0
        reason = sample.reason.lower()
        voice = cls._unit(float(sample.detail.get("voice", 0.0)))
        if "loud reaction" in reason or "excited voice" in reason or "laughter" in reason:
            voice = max(voice, 0.8)
        mod = cls._unit(float(sample.detail.get("mod", 0.0)))
        laugh = voice * max(0.0, min(1.0, (mod - 0.25) / 0.25))
        if "laughter" in reason:
            laugh = max(laugh, voice)
        return voice, laugh

    def _support(self, signal: str, sec: int, voiced: bool = False) -> int:
        count = 0
        # Separate second buckets are required; a single maximum spread through
        # the +/-1s alignment window does not count as sustained evidence.
        for t in range(sec - 1, sec + 2):
            sample = self.series[signal].window_max(t, t)
            if sample and sample.value >= 0.5 and (not voiced or self._voice_evidence(sample)[0] >= 0.4):
                count += 1
        return count

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
