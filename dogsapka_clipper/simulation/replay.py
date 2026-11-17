"""Offline (faster than real time) replay of the detection engine over a local recording.

Used by the automated tests and by the "Analyze local recording" tool: it runs exactly the same
detectors and scorer as a live session, using simulated wall-clock time.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dogsapka_clipper.core.config import DetectionSettings
from dogsapka_clipper.detection.audio import AudioExcitementDetector
from dogsapka_clipper.detection.chat import ChatActivityDetector, ChatReactionDetector
from dogsapka_clipper.detection.scorer import HighlightEvent, HighlightScorer
from dogsapka_clipper.detection.visual import VisualChangeDetector
from dogsapka_clipper.processing.ffmpeg_utils import decode_audio_pcm, decode_gray_frames, probe
from dogsapka_clipper.twitch.chat import ChatMessage


@dataclass
class ReplayResult:
    events: list[HighlightEvent]
    scores: list[tuple[float, float]]
    duration: float


def replay(video: Path | None, chat: list[ChatMessage] | None, settings: DetectionSettings,
           duration: float | None = None, wall0: float = 1_000_000.0, segment: float = 4.0,
           post_event_s: float = 10.0) -> ReplayResult:
    events: list[HighlightEvent] = []
    scorer = HighlightScorer(settings, post_event_s, events.append, eval_lag_s=segment * 2 + 3)
    scores: list[tuple[float, float]] = []
    act = ChatActivityDetector(sensitivity=settings.sensitivity)
    react = ChatReactionDetector(sensitivity=settings.sensitivity)
    audio = AudioExcitementDetector(sensitivity=settings.sensitivity)
    visual = VisualChangeDetector(sensitivity=settings.sensitivity) if settings.use_visual else None
    pcm = frames = None
    if video is not None:
        duration = duration or probe(video).duration
        if settings.use_audio:
            pcm = decode_audio_pcm(video)
        if visual:
            frames = decode_gray_frames(video, fps=visual.fps)
    duration = duration or 0.0
    msgs = sorted(chat or [], key=lambda m: m.ts)
    mi = 0
    seg_done = 0.0
    t = 0.0
    while t <= duration + scorer.eval_lag_s + settings.merge_window_s + 2:
        now = wall0 + t
        # deliver completed segments (audio/visual are available one segment late, like live)
        while seg_done + segment <= min(t, duration + segment) and seg_done < duration:
            a, b = seg_done, min(seg_done + segment, duration)
            if pcm is not None:
                for ts, smp in audio.process(pcm[int(a * 16000):int(b * 16000)], wall0 + a):
                    scorer.push("audio", ts, smp)
            if frames is not None and visual:
                fps = visual.fps
                for ts, smp in visual.process(frames[int(a * fps):int(b * fps)], wall0 + a):
                    scorer.push("visual", ts, smp)
            seg_done += segment
        while mi < len(msgs) and msgs[mi].ts <= now:
            act.add(msgs[mi].ts, msgs[mi].user_hash)
            react.add(msgs[mi].ts, msgs[mi].text, msgs[mi].emotes)
            mi += 1
        if chat is not None and t <= duration:
            scorer.push("chat_activity", now, act.evaluate(now))
            scorer.push("chat_reactions", now, react.evaluate(now))
        scorer.tick(now)
        t += 1.0
    scorer.flush()
    for ts, sc in scorer.scores:
        scores.append((ts - wall0, sc))
    for e in events:
        e.peak_ts -= wall0
        e.start_ts -= wall0
        e.end_ts -= wall0
    return ReplayResult(events, scores, duration)
