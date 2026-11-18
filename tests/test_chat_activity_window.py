"""Chat windows stay small without losing late or delayed YouTube messages."""
import pytest

from dogsapka_clipper.detection.chat import ChatActivityDetector


def test_window_boundaries_and_out_of_order_messages():
    detector = ChatActivityDetector(window_s=5)
    for timestamp, user in [(100, "a"), (103, "future"), (95, "b"), (94.9, "expired"), (99, "a")]:
        detector.add(timestamp, user)
    sample = detector.evaluate(100)
    assert sample.detail["rate"] == 3 / 5
    assert sample.detail["unique"] == 2 / 5
    assert list(detector._msgs) == [(100, "a"), (103, "future"), (95, "b"), (99, "a")]


def test_delayed_evaluation_keeps_future_messages_and_accepts_late_batches():
    detector = ChatActivityDetector(window_s=5)
    detector.add(100, "a")
    detector.add(110, "future")
    assert detector.evaluate(100).detail["rate"] == 1 / 5
    detector.add(103, "late")  # arrives behind an already buffered future message
    detector.add(95, "expired")
    assert detector.evaluate(105).detail["rate"] == 2 / 5
    assert detector.evaluate(110).detail["rate"] == 1 / 5
    assert list(detector._msgs) == [(110, "future")]


@pytest.mark.parametrize("window", [2, 5, 12])
def test_dense_chat_retains_only_active_window_and_future_batch(window):
    detector = ChatActivityDetector(window_s=window)
    for second in range(120):
        # A one-second future batch emulates polling ahead of the evaluation clock.
        for i in range(100):
            detector.add(second + i / 100, f"user-{i % 25}")
        now = second - 1
        result = detector.evaluate(now)
        assert len(detector._msgs) <= (window + 2) * 100
        if second > window + 1:
            assert result.detail["rate"] == pytest.approx((window * 100 + 1) / window)
            assert result.detail["unique"] == 25 / window
