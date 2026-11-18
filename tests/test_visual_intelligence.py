"""Visual evidence distinguishes local sustained action from camera/editing artifacts."""
import numpy as np

from dogsapka_clipper.detection.visual import VisualChangeDetector


def texture(seed=0):
    return np.random.default_rng(seed).integers(40, 120, (90, 160), dtype=np.uint8)


def warmed():
    base = texture()
    detector = VisualChangeDetector()
    detector.process(np.repeat(base[None], 40, axis=0), 0)
    return detector, base


def test_sustained_local_movement_is_strong_after_learning_normal_scene():
    detector, base = warmed()
    frames = []
    for i in range(8):
        frame = base.copy()
        frame[25:65, 55:105] = 220 if i % 2 == 0 else 20
        frames.append(frame)
    samples = detector.process(np.array(frames), 20)
    assert samples[0][1].value < .4  # a single instantaneous change is insufficient
    assert max(sample.value for _, sample in samples) > .7
    assert any("local visual action" in sample.reason for _, sample in samples)
    assert max(sample.detail["locality"] for _, sample in samples) > .8


def test_camera_pan_is_compensated_instead_of_becoming_a_highlight():
    detector, base = warmed()
    frames = [np.roll(base, i * 3, axis=1) for i in range(1, 15)]
    samples = detector.process(np.array(frames), 20)
    assert max(sample.value for _, sample in samples) < .1
    assert all(sample.detail["pan"] > 0 for _, sample in samples)
    assert max(sample.detail["raw_diff"] for _, sample in samples) > .05


def test_brightness_flashes_black_frames_and_loading_screens_stay_low():
    detector, base = warmed()
    frames = [base + 100, base, np.zeros_like(base), base, np.full_like(base, 255), base,
              np.full_like(base, 80), np.full_like(base, 80), base]
    samples = detector.process(np.array(frames), 20)
    assert max(sample.value for _, sample in samples) < .15
    assert any(sample.detail["blank"] for _, sample in samples)


def test_repeated_full_frame_cuts_do_not_masquerade_as_local_action():
    detector, _ = warmed()
    frames = [np.random.default_rng(i).integers(10, 245, (90, 160), dtype=np.uint8) for i in range(20)]
    samples = detector.process(np.array(frames), 20)
    assert max(sample.value for _, sample in samples) < .4


def test_one_local_transition_to_a_static_menu_is_not_sustained_action():
    detector, base = warmed()
    menu = base.copy()
    menu[20:70, 40:120] = 210
    samples = detector.process(np.repeat(menu[None], 10, axis=0), 20)
    assert max(sample.value for _, sample in samples) < .4


def test_segments_share_continuous_visual_context_but_gaps_do_not():
    detector, base = warmed()
    frames = np.array([np.roll(base, i * 2, axis=1) for i in range(1, 11)])
    full = detector.process(frames, 20)
    split_detector, _ = warmed()
    split = split_detector.process(frames[:5], 20) + split_detector.process(frames[5:], 22.5)
    assert [s.value for _, s in full] == [s.value for _, s in split]
    after_gap = detector.process(np.array([texture(9)]), 100)
    assert after_gap == []


def test_visual_baseline_survives_restart_and_legacy_state_is_accepted():
    detector, _ = warmed()
    resumed = VisualChangeDetector()
    resumed.load_state(detector.state())
    assert resumed.diff_stats.mean == detector.diff_stats.mean
    assert resumed.diff_stats.n == 20
    legacy = VisualChangeDetector()
    legacy.load_state({"diff": {"mean": .5, "var": .2, "n": 100}})
    assert legacy.diff_stats.n == 0  # feature changed: learn a new motion baseline
