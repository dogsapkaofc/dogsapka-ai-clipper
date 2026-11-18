"""Pure local-calendar allocation for YouTube's server-side ``publishAt``.

The caller owns durable reservations and concurrency. Each occupied datetime is
one existing publication/reservation on the target channel; include completed
publications too so changing today's clock times cannot exceed three per day.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = datetime_timezone.utc
DEFAULT_TIMES = ("09:00", "15:00", "21:00")
DEFAULT_TIMEZONE = "Europe/Budapest"
DAILY_LIMIT = 3
_CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z", re.ASCII)


def validate_schedule(times: Sequence[str], timezone: str) -> tuple[str, str, str]:
    """Validate exactly three distinct 24-hour times and an IANA timezone.

    Returns canonical chronological clock times. Existing invalid settings must
    be corrected rather than silently scheduling a public video at a guess.
    """
    if not isinstance(times, (list, tuple)) or len(times) != DAILY_LIMIT:
        raise ValueError("YouTube scheduling requires exactly three daily times")
    if any(not isinstance(value, str) or _CLOCK.fullmatch(value) is None for value in times):
        raise ValueError("Publication times must use HH:MM in 24-hour format")
    if len(set(times)) != DAILY_LIMIT:
        raise ValueError("The three daily publication times must be different")
    if not isinstance(timezone, str) or not timezone:
        raise ValueError("Choose a valid IANA timezone, for example Europe/Budapest")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Timezone is unavailable: {timezone}. Install timezone data or choose another zone") from exc
    return tuple(sorted(times))


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _slot(day: date, clock: str, zone: ZoneInfo) -> datetime | None:
    """Map one wall-clock slot to UTC, skipping DST gaps and choosing the first fold."""
    hour, minute = map(int, clock.split(":"))
    wall = datetime.combine(day, time(hour, minute))
    local = wall.replace(tzinfo=zone, fold=0)
    instant = local.astimezone(UTC)
    # Merely attaching ZoneInfo accepts imaginary spring-forward wall times.
    # A UTC round trip is required to prove the local time actually exists.
    if instant.astimezone(zone).replace(tzinfo=None) != wall:
        return None
    return instant


def next_publish_time(now: datetime, times: Sequence[str], timezone: str,
                      occupied: Iterable[datetime] = (), *,
                      lead: timedelta = timedelta(minutes=15)) -> datetime:
    """Return the first free future slot, with at most three publications per local day.

    Slots already passed or less than ``lead`` away are not backfilled. Excess
    videos roll over to later days. DST gaps are skipped; a repeated clock time
    uses only its first occurrence (fold 0), never two slots on the same day.
    The 15-minute default leaves upload/processing headroom, but the upload layer
    must check again if uploading actually takes longer than that.
    """
    clocks = validate_schedule(times, timezone)
    zone = ZoneInfo(timezone)
    now = _utc(now, "now")
    if not isinstance(lead, timedelta) or lead < timedelta(0):
        raise ValueError("Publication lead time must be a non-negative timedelta")
    try:
        earliest = now + lead
    except OverflowError as exc:
        raise ValueError("Publication lead time exceeds the supported date range") from exc

    instants = set()
    occupied_minutes = set()
    daily_counts: Counter[date] = Counter()
    for value in occupied:
        instant = _utc(value, "occupied publication time")
        local = instant.astimezone(zone)
        instants.add(instant)
        occupied_minutes.add((local.date(), local.hour, local.minute))
        daily_counts[local.date()] += 1

    day = now.astimezone(zone).date()
    while True:
        if daily_counts[day] < DAILY_LIMIT:
            for clock in clocks:
                candidate = _slot(day, clock, zone)
                if candidate is None or candidate <= now or candidate < earliest or candidate in instants:
                    continue
                hour, minute = map(int, clock.split(":"))
                if (day, hour, minute) not in occupied_minutes:
                    return candidate
        if day == date.max:
            raise ValueError("No future publication slot is available within the supported date range")
        day += timedelta(days=1)
