"""Clip quality ranking.

Scores are *estimates to help prioritise review* - they do not predict virality.
"""
from __future__ import annotations


def quality_score(detection_score: float, signals: dict | None, transcript: dict | None, duration: float,
                  width: int, height: int, duplicate: bool = False) -> tuple[float, dict]:
    signals = signals or {}
    parts: dict[str, float] = {}
    parts["detection"] = 0.45 * detection_score
    parts["reactions"] = 10.0 * max(signals.get("audio", 0.0), signals.get("chat_reactions", 0.0))
    parts["chat"] = 5.0 * signals.get("chat_activity", 0.0)
    # subtitle/transcript quality: speech present and confidently recognised
    segs = (transcript or {}).get("segments", [])
    if segs:
        conf = sum(s.get("confidence", 0.0) for s in segs) / len(segs)
        speech = sum(s["end"] - s["start"] for s in segs) / max(duration, 1.0)
        parts["subtitles"] = 15.0 * (0.6 * conf + 0.4 * min(1.0, speech * 1.5))
    else:
        parts["subtitles"] = 5.0
    # clarity: source resolution
    px = min(width, height) if width and height else 0
    parts["clarity"] = 10.0 * (1.0 if px >= 1080 else 0.8 if px >= 720 else 0.5 if px >= 480 else 0.2)
    # context: 20-60 s is the sweet spot for short-form
    if 20 <= duration <= 60:
        parts["context"] = 15.0
    elif 12 <= duration <= 90:
        parts["context"] = 10.0
    else:
        parts["context"] = 5.0
    total = sum(parts.values())
    if duplicate:
        parts["redundancy"] = -30.0
        total -= 30.0
    return round(max(0.0, min(100.0, total)), 1), parts
