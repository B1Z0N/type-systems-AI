"""Time helpers: clocks, calendar arithmetic and billing periods."""

import calendar
from datetime import datetime, timezone

from .models import SECONDS_PER_DAY, EpochMillis, EpochSeconds, Interval


class FixedClock:
    """A manually advanced clock, used by tests and simulations."""

    def __init__(self, start: EpochSeconds) -> None:
        self._now = start

    def now(self) -> EpochSeconds:
        return self._now

    def advance(self, seconds: int) -> EpochSeconds:
        self._now = EpochSeconds(self._now + seconds)
        return self._now

    def advance_days(self, days: int) -> EpochSeconds:
        return self.advance(days * SECONDS_PER_DAY)

    def set(self, value: EpochSeconds) -> None:
        if value < self._now:
            raise ValueError("clock cannot move backwards")
        self._now = value


class SystemClock:
    def now(self) -> EpochSeconds:
        return EpochSeconds(int(datetime.now(tz=timezone.utc).timestamp()))


def to_millis(value: EpochSeconds) -> EpochMillis:
    return EpochMillis(value * 1000)


def to_seconds(value: EpochMillis) -> EpochSeconds:
    return EpochSeconds(value // 1000)


def epoch(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> EpochSeconds:
    moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    return EpochSeconds(int(moment.timestamp()))


def civil(value: EpochSeconds) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def add_months(value: EpochSeconds, months: int, anchor_day: int | None = None) -> EpochSeconds:
    moment = civil(value)
    index = moment.year * 12 + (moment.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    day = anchor_day if anchor_day is not None else moment.day
    day = min(day, days_in_month(year, month))
    shifted = moment.replace(year=year, month=month, day=day)
    return EpochSeconds(int(shifted.timestamp()))


def add_interval(value: EpochSeconds, interval: Interval, count: int, anchor_day: int | None = None) -> EpochSeconds:
    if count <= 0:
        raise ValueError("interval count must be positive")
    if interval == "day":
        return EpochSeconds(value + count * SECONDS_PER_DAY)
    if interval == "week":
        return EpochSeconds(value + count * 7 * SECONDS_PER_DAY)
    if interval == "month":
        return add_months(value, count, anchor_day)
    return add_months(value, 12 * count, anchor_day)


def start_of_day(value: EpochSeconds) -> EpochSeconds:
    return EpochSeconds(value - value % SECONDS_PER_DAY)


def whole_days_between(start: EpochSeconds, end: EpochSeconds) -> int:
    return (start_of_day(end) - start_of_day(start)) // SECONDS_PER_DAY


def format_date(value: EpochSeconds) -> str:
    return civil(value).strftime("%Y-%m-%d")


def overlaps(a_start: EpochSeconds, a_end: EpochSeconds, b_start: EpochSeconds, b_end: EpochSeconds) -> bool:
    return a_start < b_end and b_start < a_end
