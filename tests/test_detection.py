import numpy as np

from dogsapka_clipper.core.config import DetectionSettings
from dogsapka_clipper.detection.audio import SR, AudioExcitementDetector
from dogsapka_clipper.detection.base import SignalSample
from dogsapka_clipper.detection.chat import ChatActivityDetector, ChatReactionDetector, reaction_strength
from dogsapka_clipper.detection.scorer import HighlightScorer, category


def run_chat(base_rate, spike_rate, spike_at=200, spike_len=8, total=260, seed=0):
    rng = np.random.default_rng(seed)
    d = ChatActivityDetector()
    vals = {}
    for t in range(total):
        rate = spike_rate if spike_at <= t < spike_at + spike_len else base_rate
        for _ in range(rng.poisson(rate)):
            d.add(t + rng.random(), f"u{rng.integers(0, 5000)}")
        vals[t] = d.evaluate(t + 1.0).value
    return vals


def test_chat_spike_large_streamer():
    v = run_chat(8, 65)
    assert max(v[t] for t in range(200, 212)) > 0.8
    assert max(v[t] for t in range(60, 195)) < 0.5


def test_chat_spike_small_streamer_adaptive():
    v = run_chat(0.3, 3.0)
    assert max(v[t] for t in range(200, 212)) > 0.5
    assert max(v[t] for t in range(60, 195)) < 0.5


def test_large_streamer_normal_fluctuation_is_not_spike():
    v = run_chat(40, 48)   # +20 % is normal variation for a big channel
    assert max(v.values()) < 0.5


def test_reaction_keywords_en_hu():
    assert reaction_strength("LMAO")[0] >= 1.0
    assert reaction_strength("ne már")[1] == "NE MÁR"
    assert reaction_strength("MI VOLT EZ????")[0] > 1.0
    assert reaction_strength("úristen")[0] > 0.9
    assert reaction_strength("hahahahaha")[0] > 0.8
    assert reaction_strength("KEKW", ["KEKW"])[0] >= 1.0
    assert reaction_strength("what time is the next stream")[0] <= 0.5
    assert reaction_strength("I love this map")[0] == 0.0
    assert reaction_strength("clipboard")[0] == 0.0   # no partial word matches


def test_reaction_detector():
    d = ChatReactionDetector()
    t = 0.0
    for i in range(120):
        d.add(t, "hello how are you")
        t += 0.5
        d.evaluate(t)
    for i in range(30):
        d.add(t, "LMAO NO WAY")
        t += 0.2
    s = d.evaluate(t)
    assert s.value > 0.5 and "LMAO" in s.reason


def tone(sec, f=220.0, amp=0.5, mod=5.0):
    t = np.arange(int(sec * SR)) / SR
    env = 0.55 + 0.45 * np.sin(2 * np.pi * mod * t)
    sig = sum(a * np.sin(2 * np.pi * f * k * t) for k, a in ((1, 1.0), (2, 0.6), (3, 0.4), (6, 0.25)))
    return (amp * env * sig / 2.25).astype(np.float32)


def noise(sec, amp=0.03, seed=0):
    return (amp * np.random.default_rng(seed).uniform(-1, 1, int(sec * SR))).astype(np.float32)


def feed(det, pcm):
    out = []
    for i in range(0, pcm.size, SR * 4):
        out += det.process(pcm[i:i + SR * 4], i / SR)
    return {round(t, 1): s for t, s in out}


def test_audio_voice_reaction_detected():
    pcm = np.concatenate([noise(40), tone(3) + noise(3, seed=1), noise(10, seed=2)])
    r = feed(AudioExcitementDetector(), pcm)
    assert max(r[t].value for t in r if 40 <= t < 43) > 0.7
    assert max(r[t].value for t in r if t < 39) < 0.2


def test_audio_short_explosion_scores_low():
    burst = np.random.default_rng(3).uniform(-0.9, 0.9, int(0.4 * SR)).astype(np.float32)
    pcm = np.concatenate([noise(40), burst, noise(10, seed=2)])
    r = feed(AudioExcitementDetector(), pcm)
    assert max(s.value for s in r.values()) < 0.5


def test_audio_continuous_loud_music_adapts():
    # constant loud tonal "music": loud but continuous => baseline adapts, no highlight
    t = np.arange(int(120 * SR)) / SR
    music = (0.4 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 660 * t)).astype(np.float32)
    r = feed(AudioExcitementDetector(), music)
    assert max(s.value for s in r.values()) < 0.3


def make_scorer(events, **kw):
    s = DetectionSettings(**kw)
    return HighlightScorer(s, 10, events.append, chat_delay_s=0, eval_lag_s=0)


def test_scorer_weighting_and_categories():
    ev = []
    sc = make_scorer(ev)
    sc.push("audio", 100, SignalSample(1.0, "Loud reaction"))
    sc.push("chat_activity", 100, SignalSample(1.0, "Chat spike"))
    sc.available |= {"chat_reactions", "visual"}
    score, vals, reasons = sc.score_at(100)
    assert score >= 75 and "Loud reaction" in reasons
    sc.s.use_visual = True
    assert abs(sum(sc.enabled_weights().values()) - 1.0) < 1e-9
    assert category(95) == "Exceptional highlight" and category(80) == "Very good highlight"
    assert category(65) == "Potential highlight" and category(10) == "Ignored"


def test_scorer_merges_nearby_detections_into_one():
    ev = []
    sc = make_scorer(ev, merge_window_s=20, cooldown_s=15, auto_threshold=False)
    for t in range(0, 200):
        v = 1.0 if (50 <= t < 55 or 62 <= t < 66) else 0.0
        sc.push("audio", t, SignalSample(v, "Loud reaction" if v else ""))
        sc.push("chat_activity", t, SignalSample(v))
        sc.tick(t)
    sc.flush()
    assert len(ev) == 1
    assert ev[0].merged == 2 and ev[0].start_ts <= 50 and ev[0].end_ts >= 65


def test_scorer_separate_events_and_cooldown():
    ev = []
    sc = make_scorer(ev, merge_window_s=10, cooldown_s=15, auto_threshold=False)
    for t in range(0, 300):
        v = 1.0 if (50 <= t < 53 or 70 <= t < 72 or 150 <= t < 153) else 0.0
        sc.push("audio", t, SignalSample(v))
        sc.push("chat_activity", t, SignalSample(v))
        sc.tick(t)
    sc.flush()
    # 70 is inside cooldown after the first event's finalization? first ends 52, merge window 10 -> 62,
    # finalized at ~63; 70 >= 52+15 so it's allowed -> 3 events
    assert [round(e.peak_ts) for e in ev] == [50, 70, 150]


def test_auto_threshold_rises_when_too_many_highlights():
    ev = []
    sc = make_scorer(ev, merge_window_s=2, cooldown_s=0, auto_threshold=True)
    base = sc.threshold
    for t in range(0, 1200):
        v = 1.0 if t % 30 < 2 else 0.0
        sc.push("audio", t, SignalSample(v))
        sc.tick(t)
    assert sc.threshold > base
