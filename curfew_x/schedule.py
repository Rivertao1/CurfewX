from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
SECONDS_PER_DAY = 24 * 60 * 60


class ScheduleError(ValueError):
    """Raised when a weekly schedule cannot be parsed."""


@dataclass(frozen=True, order=True)
class DailyInterval:
    start_second: int
    end_second: int

    @classmethod
    def parse(cls, value: str) -> DailyInterval:
        if not isinstance(value, str) or value.count("-") != 1:
            raise ScheduleError(f"无效时间段: {value!r}")
        start_text, end_text = (part.strip() for part in value.split("-", 1))
        start = _parse_clock(start_text, allow_24=False)
        end = _parse_clock(end_text, allow_24=True)
        if start == end:
            raise ScheduleError(f"时间段起止不能相同: {value!r}")
        return cls(start, end)

    @property
    def crosses_midnight(self) -> bool:
        return self.end_second <= self.start_second


@dataclass(frozen=True)
class WeeklySchedule:
    timezone: ZoneInfo
    intervals: tuple[tuple[DailyInterval, ...], ...]

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any], timezone: ZoneInfo
    ) -> WeeklySchedule:
        unknown = set(raw) - {"every_day", *WEEKDAYS}
        if unknown:
            raise ScheduleError(f"未知的星期键: {', '.join(sorted(unknown))}")

        every_day = _parse_interval_list(raw.get("every_day", []), "every_day")
        days: list[tuple[DailyInterval, ...]] = []
        for weekday in WEEKDAYS:
            specific = _parse_interval_list(raw.get(weekday, []), weekday)
            days.append(tuple(sorted({*every_day, *specific})))
        if not any(days):
            raise ScheduleError("schedule 至少需要一个时间段")
        return cls(timezone=timezone, intervals=tuple(days))

    def is_active(self, moment: datetime) -> bool:
        local = self._local(moment)
        second = _second_of_day(local.time())
        today = local.weekday()

        for interval in self.intervals[today]:
            if interval.crosses_midnight:
                if second >= interval.start_second:
                    return True
            elif interval.start_second <= second < interval.end_second:
                return True

        previous = (today - 1) % 7
        return any(
            interval.crosses_midnight and second < interval.end_second
            for interval in self.intervals[previous]
        )

    def next_start(self, moment: datetime) -> datetime:
        """Return the next inactive-to-active boundary strictly after ``moment``."""
        local = self._local(moment)
        candidates: list[datetime] = []
        for offset in range(0, 9):
            candidate_date = local.date() + timedelta(days=offset)
            intervals = self.intervals[candidate_date.weekday()]
            candidates.extend(
                self._at_second(candidate_date, interval.start_second)
                for interval in intervals
            )
        for candidate in sorted(set(candidates)):
            if candidate <= local:
                continue
            if not self.is_active(candidate - timedelta(seconds=1)) and self.is_active(candidate):
                return candidate
        raise ScheduleError("无法计算下一次宵禁开始时间")

    def next_end(self, moment: datetime) -> datetime:
        """Return the next active-to-inactive boundary strictly after ``moment``."""
        local = self._local(moment)
        candidates: list[datetime] = []
        for offset in range(-1, 9):
            start_date = local.date() + timedelta(days=offset)
            for interval in self.intervals[start_date.weekday()]:
                end_date = start_date
                if interval.crosses_midnight or interval.end_second == SECONDS_PER_DAY:
                    end_date += timedelta(days=1)
                end_second = interval.end_second % SECONDS_PER_DAY
                candidates.append(self._at_second(end_date, end_second))
        for candidate in sorted(set(candidates)):
            if candidate <= local:
                continue
            if self.is_active(candidate - timedelta(seconds=1)) and not self.is_active(candidate):
                return candidate
        raise ScheduleError("无法计算下一次宵禁结束时间")

    def _local(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            raise ValueError("datetime 必须包含时区")
        return moment.astimezone(self.timezone)

    def _at_second(self, target_date: date, second: int) -> datetime:
        if second == SECONDS_PER_DAY:
            target_date += timedelta(days=1)
            second = 0
        hour, remainder = divmod(second, 3600)
        minute, sec = divmod(remainder, 60)
        return datetime.combine(target_date, time(hour, minute, sec), self.timezone)


def _parse_interval_list(value: Any, key: str) -> tuple[DailyInterval, ...]:
    if not isinstance(value, list):
        raise ScheduleError(f"schedule.{key} 必须是时间段列表")
    return tuple(DailyInterval.parse(item) for item in value)


def _parse_clock(value: str, *, allow_24: bool) -> int:
    parts = value.split(":")
    if len(parts) != 2 or any(len(part) != 2 or not part.isdigit() for part in parts):
        raise ScheduleError(f"无效时间: {value!r}，应使用 HH:MM")
    hour, minute = (int(part) for part in parts)
    if allow_24 and hour == 24 and minute == 0:
        return SECONDS_PER_DAY
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduleError(f"无效时间: {value!r}")
    return hour * 3600 + minute * 60


def _second_of_day(value: time) -> float:
    return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000

