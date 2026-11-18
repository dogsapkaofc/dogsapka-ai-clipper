"""Highlight fusion prioritizes sustained/corroborated human reactions."""
import math

import pytest

from dogsapka_clipper.core.config import DetectionSettings
from dogsapka_clipper.detection.base import SignalSample
from dogsapka_clipper.detection.scorer import HighlightScorer


def scorer(**kwargs):
    return HighlightScorer(DetectionSettings(use_visual=True, auto_threshold=False), 10,
                           lambda ev: None, chat_delay_s=0, eval_lag_s=0, **kwargs)


def all_sources(sc, ts, **values):
    for name in ("chat_activity", "chat_reactions", "audio", "visual"):
        sc.push(name, ts, values.get(name, SignalSample(0)))


def test_scene_cut_alone_never_gets_normalized_to_a_perfect_highlight():
    sc = scorer()
    sc.push("visual", 100, SignalSample(1, "Scene change"))
    score, values, _ = sc.score_at(100)
    assert score < sc.threshold
    assert values["visual"] == 1


def test_loud_game_noise_is_not_a_funny_highlight_even_with_visual_action():
    sc = scorer()
    all_sources(sc, 100, audio=SignalSample(1, "Sudden loud audio", {"voice": .05}),
                visual=SignalSample(1, "Sudden visual action"))
    assert sc.score_at(100)[0] < sc.threshold


def test_sustained_laughter_is_kept_when_small_chat_is_quiet():
    sc = scorer()
    laughing = SignalSample(.85, "Laughter / excited voice", {"voice": .9, "mod": .55})
    all_sources(sc, 100, audio=laughing)
    assert sc.score_at(100)[0] < sc.threshold  # a single bucket is not sustained
    all_sources(sc, 101, audio=laughing)
    score, vals, reasons = sc.score_at(101)
    assert score >= sc.threshold
    assert vals["laughter"] > .7 and vals["voice_reaction"] > .7
    assert vals["sustained_reaction"] > .7
    assert any("Sustained" in r for r in reasons)


def test_chat_reactions_can_reveal_quiet_funny_moment_without_loudness():
    sc = scorer()
    for ts in (100, 101):
        all_sources(sc, ts, chat_reactions=SignalSample(.8, "Chat reactions: HAHAHA"))
    assert sc.score_at(101)[0] >= sc.threshold


def test_weak_visual_signal_does_not_dilute_a_sustained_voice_reaction():
    sc = scorer()
    for ts in (100, 101):
        all_sources(sc, ts, audio=SignalSample(.72, "Loud reaction", {"voice": .8}),
                    visual=SignalSample(.05))
    assert sc.score_at(101)[0] >= sc.threshold


@pytest.mark.parametrize("visual_value", [0.0, 0.05])
def test_supporting_video_does_not_dilute_moderate_sustained_laughter(visual_value):
    scores = []
    for use_visual in (False, True):
        sc = HighlightScorer(DetectionSettings(use_visual=use_visual, auto_threshold=False), 10,
                             lambda ev: None, chat_delay_s=0, eval_lag_s=0)
        for ts in (99, 100, 101):
            sc.push("audio", ts, SignalSample(.6, "Laughter / excited voice", {"voice": .8, "mod": .5}))
            if use_visual:
                sc.push("visual", ts, SignalSample(visual_value))
        scores.append(sc.score_at(100)[0])
    assert scores[0] >= 60
    assert scores[1] == pytest.approx(scores[0])


def test_moderate_sustained_laughter_is_kept_with_quiet_available_chat():
    sc = scorer()
    laughing = SignalSample(.6, "Laughter / excited voice", {"voice": .8, "mod": .5})
    all_sources(sc, 100, audio=laughing)
    assert sc.score_at(100)[0] < sc.threshold
    all_sources(sc, 101, audio=laughing)
    score, vals, reasons = sc.score_at(101)
    assert vals["laughter"] == pytest.approx(.48)
    assert score >= sc.threshold
    assert "Sustained laughter / audience reaction" in reasons


@pytest.mark.parametrize("name,voice,modulation", [("Sudden loud audio", .05, .8), ("Music", .2, .7)])
def test_sustained_noise_or_music_does_not_gain_laughter_floor(name, voice, modulation):
    sc = scorer()
    for ts in (100, 101):
        all_sources(sc, ts, audio=SignalSample(.6, name, {"voice": voice, "mod": modulation}))
    score, vals, reasons = sc.score_at(101)
    assert score < sc.threshold
    assert vals["laughter"] < .45
    assert "Sustained laughter / audience reaction" not in reasons


def test_both_chat_signals_are_not_two_independent_sources():
    sc = scorer()
    all_sources(sc, 100, chat_activity=SignalSample(1, "Chat spike"),
                chat_reactions=SignalSample(1, "Chat reactions"))
    assert sc.score_at(100)[0] < sc.threshold
    all_sources(sc, 101, chat_activity=SignalSample(1), chat_reactions=SignalSample(1))
    assert sc.score_at(101)[0] <= 82


def test_voice_reaction_matching_visual_action_does_not_need_chat():
    sc = scorer()
    all_sources(sc, 100, audio=SignalSample(.9, "Loud reaction", {"voice": .9}),
                visual=SignalSample(.8, "Sustained visual action"))
    score, _, reasons = sc.score_at(100)
    assert score >= sc.threshold
    assert any("matches" in r for r in reasons)


def test_strong_visual_action_and_chat_can_corroborate_without_voice():
    sc = scorer()
    all_sources(sc, 100, chat_activity=SignalSample(.9, "Chat spike"),
                visual=SignalSample(.9, "Sustained visual action"))
    assert sc.score_at(100)[0] >= sc.threshold


def test_preference_adjustment_uses_features_and_stays_bounded():
    seen = []
    base = scorer()
    learned = scorer(score_adjuster=lambda vals: seen.append(vals) or 1000)
    for sc in (base, learned):
        all_sources(sc, 100, chat_activity=SignalSample(.5),
                    audio=SignalSample(.5, "Loud reaction", {"voice": .8}))
    before, vals, _ = base.score_at(100)
    after, _, _ = learned.score_at(100)
    assert after-before <= 12 + 1e-9
    assert set(seen[0]) == {"chat_activity", "chat_reactions", "audio", "visual", "laughter", "voice_reaction", "sustained_reaction"}


@pytest.mark.parametrize("adjustment", [math.nan, math.inf, -math.inf])
def test_nonfinite_preferences_do_not_corrupt_score(adjustment):
    sc = scorer(score_adjuster=lambda vals: adjustment)
    sc.push("visual", 100, SignalSample(1))
    assert math.isfinite(sc.score_at(100)[0])


def test_learned_preferences_cannot_override_noise_guard():
    sc = scorer(score_adjuster=lambda vals: 100)
    all_sources(sc, 100, audio=SignalSample(1, "Sudden loud audio", {"voice": .0}),
                visual=SignalSample(1))
    assert sc.score_at(100)[0] < sc.threshold


def test_events_retain_learning_features_at_peak():
    events = []
    sc = HighlightScorer(DetectionSettings(auto_threshold=False, merge_window_s=2), 10,
                         events.append, chat_delay_s=0, eval_lag_s=0)
    for ts in range(10):
        hot = ts in (2, 3)
        all_sources(sc, ts, audio=SignalSample(.9 if hot else 0, "Loud reaction", {"voice": .9}),
                    chat_reactions=SignalSample(.9 if hot else 0))
        sc.tick(ts)
    sc.flush()
    assert len(events) == 1
    assert "voice_reaction" in events[0].signals
