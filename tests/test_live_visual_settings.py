from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dogsapka_clipper.core.config import AppSettings, DetectionSettings
from dogsapka_clipper.services import session


def test_visual_is_default_but_existing_explicit_opt_out_is_retained():
    assert DetectionSettings().use_visual
    assert not AppSettings.from_dict({"detection": {"use_visual": False}}).detection.use_visual


def test_visual_can_be_enabled_and_disabled_without_restarting_recording(monkeypatch):
    live = session.LiveSession.__new__(session.LiveSession)
    live.visual = None
    live._visual_baseline = {}
    live.chat_act = SimpleNamespace(sensitivity=.5)
    live.chat_react = SimpleNamespace(sensitivity=.5)
    live.audio = SimpleNamespace(sensitivity=.5)
    samples = []
    live.scorer = SimpleNamespace(s=None, push=lambda *args: samples.append(args))
    calls = []
    def decode(*args, **kwargs):
        calls.append(args)
        return np.zeros((8, 90, 160), dtype=np.uint8)
    monkeypatch.setattr(session, "decode_gray_frames", decode)
    settings = DetectionSettings(use_visual=True, use_audio=False, sensitivity=.7)
    live.apply_detection_settings(settings)
    segment = SimpleNamespace(path=Path("recording.ts"), start=10)
    live._analyze_segment(segment)
    assert live.visual is not None and samples and len(calls) == 1
    assert live.scorer.s is settings and live.visual.sensitivity == .7
    live.apply_detection_settings(DetectionSettings(use_visual=False, use_audio=False))
    live._analyze_segment(segment)
    assert len(calls) == 1
