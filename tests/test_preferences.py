"""Preference learning uses explicit labels and stays local, bounded and repeatable."""
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from dogsapka_clipper.detection.preferences import MAX_SAMPLES, PreferenceModel


FUNNY = {"chat_activity": .6, "chat_reactions": .9, "audio": .8, "visual": .5,
         "laughter": .9, "voice_reaction": .8, "sustained_reaction": .8}
NOISY = {"chat_activity": .1, "chat_reactions": .05, "audio": .9, "visual": .1,
         "laughter": 0, "voice_reaction": .1, "sustained_reaction": .1}


def test_requires_real_feedback_before_adjustment():
    assert PreferenceModel.from_feedback([]).adjustment(FUNNY) == 0
    assert PreferenceModel.from_feedback([(FUNNY, 1)] * 3).adjustment(FUNNY) == 0
    invalid = PreferenceModel.from_feedback([({}, 1), (FUNNY, 0), ({"audio": float("nan")}, 1)])
    assert invalid.sample_count == 0


def test_liked_and_disliked_patterns_get_opposite_corrections():
    model = PreferenceModel.from_feedback([(FUNNY, 1)] * 4 + [(NOISY, -1)] * 4)
    assert model.sample_count == 8
    assert 0 < model.adjustment(FUNNY) <= 10
    assert -10 <= model.adjustment(NOISY) < 0
    similar = {**FUNNY, "chat_reactions": .85, "visual": .45}
    assert 0 < model.adjustment(similar) < model.adjustment(FUNNY)


def test_unknown_patterns_and_missing_evidence_are_not_boosted():
    model = PreferenceModel.from_feedback([(FUNNY, 1)] * 20)
    assert model.adjustment({}) == 0
    assert model.adjustment({"visual": 1}) == 0
    assert model.adjustment(NOISY) == 0


def test_laughter_and_game_noise_with_same_raw_scores_are_distinguishable():
    raw = {"chat_activity": .5, "chat_reactions": .6, "audio": .9, "visual": .6}
    laughter = {**raw, "laughter": 1, "voice_reaction": 1, "sustained_reaction": 1}
    noise = {**raw, "laughter": 0, "voice_reaction": 0, "sustained_reaction": 0}
    model = PreferenceModel.from_feedback([(laughter, 1)] * 4 + [(noise, -1)] * 4)
    assert model.adjustment(laughter) > 0 > model.adjustment(noise)


def test_legacy_raw_ratings_support_new_patterns_without_inventing_missing_features():
    legacy = {key: FUNNY[key] for key in ("chat_activity", "chat_reactions", "audio", "visual")}
    legacy_model = PreferenceModel.from_feedback([(legacy, 1)] * 4)
    full_model = PreferenceModel.from_feedback([(FUNNY, 1)] * 4)
    assert 0 < legacy_model.adjustment(FUNNY) < full_model.adjustment(FUNNY)
    assert 0 < full_model.adjustment(legacy) < full_model.adjustment(FUNNY)
    assert legacy_model.adjustment({"visual": legacy["visual"]}) == 0
    # A measured zero remains known evidence, rather than becoming a wildcard.
    measured_no_laughter = {**legacy, "laughter": 0, "voice_reaction": 0, "sustained_reaction": 0}
    assert full_model.adjustment(measured_no_laughter) == 0


def test_single_visual_feature_is_not_enough_for_preference_boost():
    model = PreferenceModel.from_feedback([({"visual": 1}, 1)] * 20)
    assert model.adjustment({"visual": 1}) == 0
    assert model.adjustment(FUNNY) == 0


def test_contradictory_ratings_shrink_towards_neutral():
    model = PreferenceModel.from_feedback([(FUNNY, 1), (FUNNY, -1)] * 4)
    assert model.adjustment(FUNNY) == 0


def test_rebuild_with_revised_ratings_replaces_prior_learning():
    ratings = [(FUNNY, 1)] * 4
    liked = PreferenceModel.from_feedback(ratings)
    rebuilt = PreferenceModel.from_feedback(list(ratings))
    revised = PreferenceModel.from_feedback([(FUNNY, -1)] * 4)
    assert rebuilt.sample_count == liked.sample_count == 4
    assert rebuilt.adjustment(FUNNY) == liked.adjustment(FUNNY)
    assert revised.adjustment(FUNNY) == -liked.adjustment(FUNNY)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), None, "invalid"])
def test_nonfinite_or_invalid_features_cannot_poison_model(value):
    model = PreferenceModel.from_feedback([(FUNNY, 1)] * 4 + [({"audio": value}, -1)])
    assert model.sample_count == 4
    assert model.adjustment({"audio": value}) == 0
    assert np.isfinite(model.adjustment(FUNNY))


def test_retained_history_and_corrections_are_bounded():
    model = PreferenceModel.from_feedback([(FUNNY, -1)] * (MAX_SAMPLES + 20) + [(FUNNY, 1)] * MAX_SAMPLES)
    assert model.sample_count == MAX_SAMPLES
    assert 0 < model.adjustment(FUNNY) <= 10
    assert abs(model.adjustment({"audio": 100, "visual": -100})) <= 10


def test_concurrent_readers_get_identical_results():
    model = PreferenceModel.from_feedback([(FUNNY, 1)] * 8 + [(NOISY, -1)] * 8)
    queries = [FUNNY, NOISY] * 100
    expected = [model.adjustment(q) for q in queries]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(model.adjustment, queries)) == expected
