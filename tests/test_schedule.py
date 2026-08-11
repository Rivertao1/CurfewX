from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from curfew_x.schedule import ScheduleError, WeeklySchedule

SHANGHAI = ZoneInfo("Asia/Shanghai")


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SHANGHAI)


def test_every_day_schedule_boundaries() -> None:
    schedule = WeeklySchedule.from_mapping({"every_day": ["00:00-08:00"]}, SHANGHAI)

    assert schedule.is_active(at(2026, 8, 11, 0, 0))
    assert schedule.is_active(at(2026, 8, 11, 7, 59))
    assert not schedule.is_active(at(2026, 8, 11, 8, 0))
    assert not schedule.is_active(at(2026, 8, 11, 23, 59))
    assert schedule.next_start(at(2026, 8, 11, 23, 0)) == at(2026, 8, 12, 0, 0)
    assert schedule.next_end(at(2026, 8, 11, 2, 0)) == at(2026, 8, 11, 8, 0)


def test_cross_midnight_weekday_schedule() -> None:
    schedule = WeeklySchedule.from_mapping({"friday": ["23:00-02:00"]}, SHANGHAI)

    assert schedule.is_active(at(2026, 8, 14, 23, 30))  # Friday
    assert schedule.is_active(at(2026, 8, 15, 1, 59))
    assert not schedule.is_active(at(2026, 8, 15, 2, 0))
    assert schedule.next_end(at(2026, 8, 14, 23, 30)) == at(2026, 8, 15, 2, 0)


def test_overlapping_intervals_are_one_continuous_curfew() -> None:
    schedule = WeeklySchedule.from_mapping(
        {"every_day": ["00:00-08:00", "07:00-09:00"]}, SHANGHAI
    )

    assert schedule.next_end(at(2026, 8, 11, 1, 0)) == at(2026, 8, 11, 9, 0)
    assert schedule.next_start(at(2026, 8, 11, 6, 0)) == at(2026, 8, 12, 0, 0)


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"every_day": ["08:00-08:00"]},
        {"every_day": ["25:00-08:00"]},
        {"holiday": ["00:00-08:00"]},
    ],
)
def test_invalid_schedules_are_rejected(raw: dict[str, list[str]]) -> None:
    with pytest.raises(ScheduleError):
        WeeklySchedule.from_mapping(raw, SHANGHAI)

