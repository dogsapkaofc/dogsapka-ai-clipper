from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from dogsapka_clipper.publishing.schedule import (DEFAULT_TIMES, DEFAULT_TIMEZONE, next_publish_time,
                                                validate_schedule)

UTC = timezone.utc
BUDAPEST = ZoneInfo(DEFAULT_TIMEZONE)


def local(year, month, day, hour, minute=0, *, fold=0):
    return datetime(year, month, day, hour, minute, tzinfo=BUDAPEST, fold=fold)


def test_next_slot_is_future_and_returned_in_utc():
    result = next_publish_time(local(2026, 11, 18, 10), DEFAULT_TIMES, DEFAULT_TIMEZONE)
    assert result == datetime(2026, 11, 18, 14, tzinfo=UTC)
    assert result.tzinfo is UTC


def test_upload_headroom_and_strict_future_boundary():
    assert next_publish_time(local(2026, 11, 18, 8, 45), DEFAULT_TIMES, DEFAULT_TIMEZONE) == local(2026, 11, 18, 9)
    assert next_publish_time(local(2026, 11, 18, 8, 46), DEFAULT_TIMES, DEFAULT_TIMEZONE) == local(2026, 11, 18, 15)
    assert next_publish_time(local(2026, 11, 18, 9), DEFAULT_TIMES, DEFAULT_TIMEZONE,
                             lead=timedelta(0)) == local(2026, 11, 18, 15)


def test_three_daily_slots_then_roll_to_next_day_without_backfilling():
    now = local(2026, 11, 18, 7)
    occupied = []
    for _ in range(5):
        occupied.append(next_publish_time(now, DEFAULT_TIMES, DEFAULT_TIMEZONE, occupied))
    assert [instant.astimezone(BUDAPEST) for instant in occupied] == [
        local(2026, 11, 18, 9), local(2026, 11, 18, 15), local(2026, 11, 18, 21),
        local(2026, 11, 19, 9), local(2026, 11, 19, 15)]
    assert next_publish_time(local(2026, 11, 18, 22), DEFAULT_TIMES, DEFAULT_TIMEZONE) == local(2026, 11, 19, 9)


def test_schedule_changes_cannot_exceed_three_on_already_full_local_day():
    occupied = [local(2026, 11, 18, h) for h in (7, 8, 9)]
    result = next_publish_time(local(2026, 11, 18, 10), ["11:00", "16:00", "22:00"], DEFAULT_TIMEZONE, occupied)
    assert result == local(2026, 11, 19, 11)


def test_different_offset_reservation_and_same_minute_are_not_reused():
    occupied = [datetime(2026, 11, 18, 8, 0, 30, tzinfo=UTC)]
    result = next_publish_time(local(2026, 11, 18, 7), DEFAULT_TIMES, DEFAULT_TIMEZONE, occupied)
    assert result == local(2026, 11, 18, 15)


def test_spring_forward_skips_nonexistent_wall_clock_time():
    times = ["02:30", "09:00", "21:00"]
    result = next_publish_time(local(2026, 3, 29, 0), times, DEFAULT_TIMEZONE)
    assert result == datetime(2026, 3, 29, 7, tzinfo=UTC)  # 09:00 CEST


def test_fall_back_uses_first_occurrence_only():
    times = ["02:30", "09:00", "21:00"]
    first = next_publish_time(local(2026, 10, 25, 0), times, DEFAULT_TIMEZONE)
    assert first == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    # Between repeated 02:30 occurrences: the second is not a fourth daily slot.
    result = next_publish_time(datetime(2026, 10, 25, 0, 45, tzinfo=UTC), times, DEFAULT_TIMEZONE)
    assert result == local(2026, 10, 25, 9)
    # An existing second-fold reservation also consumes this local slot.
    result = next_publish_time(local(2026, 10, 25, 0), times, DEFAULT_TIMEZONE,
                               [local(2026, 10, 25, 2, 30, fold=1)])
    assert result == local(2026, 10, 25, 9)


def test_long_backlog_has_no_arbitrary_search_horizon():
    now = local(2026, 11, 18, 7)
    occupied = [local(2026, 11, 18, h) + timedelta(days=day) for day in range(400) for h in (9, 15, 21)]
    result = next_publish_time(now, DEFAULT_TIMES, DEFAULT_TIMEZONE, occupied)
    assert result == local(2026, 11, 18, 9) + timedelta(days=400)


@pytest.mark.parametrize("times", [[], ["09:00"], ["09:00"] * 3, ["9:00", "15:00", "21:00"],
                                   ["24:00", "15:00", "21:00"], ["09:60", "15:00", "21:00"],
                                   ["09:00 ", "15:00", "21:00"], [None, "15:00", "21:00"],
                                   "09:00,15:00,21:00"])
def test_invalid_times_are_rejected(times):
    with pytest.raises(ValueError):
        validate_schedule(times, DEFAULT_TIMEZONE)


@pytest.mark.parametrize("zone", ["", "Not/AZone", None, "../Europe/Budapest"])
def test_invalid_zones_are_rejected(zone):
    with pytest.raises(ValueError):
        validate_schedule(DEFAULT_TIMES, zone)


def test_validator_returns_sorted_clock_times():
    assert validate_schedule(["21:00", "09:00", "15:00"], DEFAULT_TIMEZONE) == DEFAULT_TIMES


@pytest.mark.parametrize("kwargs", [{"now": datetime(2026, 11, 18)},
                                    {"occupied": [datetime(2026, 11, 18, 9)]},
                                    {"lead": timedelta(minutes=-1)}, {"lead": 0}])
def test_naive_dates_and_invalid_lead_are_rejected(kwargs):
    args = dict(now=local(2026, 11, 18, 7), times=DEFAULT_TIMES, timezone=DEFAULT_TIMEZONE)
    args.update(kwargs)
    with pytest.raises(ValueError):
        next_publish_time(**args)
