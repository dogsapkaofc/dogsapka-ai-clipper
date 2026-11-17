"""Integration: full detection engine on a synthetic stream with simulated chat."""
from dogsapka_clipper.core.config import DetectionSettings
from dogsapka_clipper.simulation.replay import replay
from dogsapka_clipper.simulation.simulator import SimulatedChat


def test_detects_planted_highlight_with_chat(sample_video):
    chat = SimulatedChat(sample_video.highlights, seed=5).messages_between(0, sample_video.duration, 1_000_000.0)
    r = replay(sample_video.path, chat, DetectionSettings())
    assert len(r.events) == 1
    e = r.events[0]
    assert abs(e.peak_ts - 22) <= 3 and e.score >= 75


def test_detects_with_audio_only(sample_video):
    r = replay(sample_video.path, None, DetectionSettings(use_chat_activity=False, use_chat_reactions=False))
    assert [round(e.peak_ts) for e in r.events] in ([22], [21], [23])


def test_no_highlight_without_signals(sample_video):
    chat = SimulatedChat([], seed=1).messages_between(0, sample_video.duration, 1_000_000.0)
    r = replay(None, chat, DetectionSettings(), duration=sample_video.duration)
    assert r.events == []
