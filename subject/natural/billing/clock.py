"""Time helpers: clocks, calendar arithmetic and billing periods.

Unless a name says otherwise, timestamps are int epoch seconds in UTC. Millisecond
timestamps (used for events and usage records) are converted with `to_millis` and
`to_seconds`.
"""

import calendar
from datetime import datetime, timezone

from .models import SECONDS_PER_DAY


class FixedClock:
    """A manually advanced clock, used by tests and simulations."""

    def __init__(self, start):
        self._now = start

    def now(self):
        """Current time in epoch seconds."""
        return self._now

    def advance(self, seconds):
        """Move the clock forward by `seconds` and return the new time."""
        self._now = self._now + seconds
        return self._now

    def advance_days(self, days):
        """Move the clock forward by whole days and return the new time."""
        return self.advance(days * SECONDS_PER_DAY)

    def set(self, value):
        """Jump to epoch second `value`; raises ValueError if that is in the past."""
        if value < self._now:
            raise ValueError("clock cannot move backwards")
        self._now = value


class SystemClock:
    """Wall-clock time, truncated to whole epoch seconds."""

    def now(self):
        return int(datetime.now(tz=timezone.utc).timestamp())


def to_millis(value):
    """Epoch seconds to epoch milliseconds."""
    return value * 1000


def to_seconds(value):
    """Epoch milliseconds to epoch seconds, truncating toward negative infinity."""
    return value // 1000


def epoch(year, month, day, hour=0, minute=0, second=0):
    """Epoch seconds for a UTC calendar date and time."""
    moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    return int(moment.timestamp())


def civil(value):
    """Epoch seconds to an aware UTC datetime."""
    return datetime.fromtimestamp(value, tz=timezone.utc)


def days_in_month(year, month):
    return calendar.monthrange(year, month)[1]


def add_months(value, months, anchor_day=None):
    """Shift epoch seconds `value` by whole calendar months, keeping the time of day.

    The result lands on `anchor_day` (defaults to the current day of month), clamped to
    the last day of a short month, so a subscription anchored on the 31st bills on
    Feb 28/29 and goes back to the 31st in March.
    """
    moment = civil(value)
    month_index = moment.year * 12 + (moment.month - 1) + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = anchor_day if anchor_day is not None else moment.day
    day = min(day, days_in_month(year, month))
    shifted = moment.replace(year=year, month=month, day=day)
    return int(shifted.timestamp())


def add_interval(value, interval, count, anchor_day=None):
    """Advance epoch seconds `value` by `count` billing intervals.

    `interval` is "day", "week", "month" or "year"; any other value is treated as
    "year". Raises ValueError if `count` is not positive.
    """
    if count <= 0:
        raise ValueError("interval count must be positive")
    if interval == "day":
        return value + count * SECONDS_PER_DAY
    if interval == "week":
        return value + count * 7 * SECONDS_PER_DAY
    if interval == "month":
        return add_months(value, count, anchor_day)
    return add_months(value, 12 * count, anchor_day)


def start_of_day(value):
    """Midnight UTC of the day containing epoch second `value`."""
    return value - value % SECONDS_PER_DAY


def whole_days_between(start, end):
    """Number of UTC midnights crossed going from `start` to `end` (negative if end is earlier)."""
    return (start_of_day(end) - start_of_day(start)) // SECONDS_PER_DAY


def format_date(value):
    """Epoch seconds as an ISO "YYYY-MM-DD" date in UTC."""
    return civil(value).strftime("%Y-%m-%d")


def overlaps(a_start, a_end, b_start, b_end):
    """Whether half-open intervals [a_start, a_end) and [b_start, b_end) intersect."""
    return a_start < b_end and b_start < a_end
