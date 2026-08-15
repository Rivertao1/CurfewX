from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from string import Formatter
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcdreforged.api.rtext import RColor, RText, RTextBase, RTextList

from curfew_x.schedule import ScheduleError, WeeklySchedule

DEFAULT_CONFIG: dict[str, Any] = {
    "timezone": "Asia/Shanghai",
    "schedule": {"every_day": ["00:00-08:00"]},
    "reminders": {
        "minutes": [60, 30, 10, 5],
        "seconds": [30, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    },
    "shutdown": {
        "warning_after_seconds": 120,
        "warning_interval_seconds": 300,
        "force_kill_after_seconds": 3600,
    },
    "messages": {
        "prefix": "[CurfewX]",
        "minutes_remaining": "距离服务器宵禁还有 {value} 分钟",
        "seconds_remaining": "距离服务器宵禁还有 {value} 秒",
        "shutdown_now": "宵禁开始，服务器正在关闭",
        "colors": {
            "prefix": "green",
            "countdown_text": "yellow",
            "countdown_value": "red",
            "shutdown_text": "red",
        },
    },
}

_FORMATTER = Formatter()


class ConfigError(ValueError):
    """Raised when the user configuration is invalid."""


@dataclass(frozen=True)
class MessageColors:
    prefix: RColor
    countdown_text: RColor
    countdown_value: RColor
    shutdown_text: RColor


@dataclass(frozen=True)
class MessageConfig:
    prefix: str
    minutes_remaining: str
    seconds_remaining: str
    shutdown_now: str
    colors: MessageColors

    def render_countdown(self, template: str, *, value: int) -> RTextBase:
        components = self._prefix_components()
        found_value = False
        try:
            for literal, field_name, format_spec, conversion in _FORMATTER.parse(template):
                if literal:
                    components.append(RText(literal, self.colors.countdown_text))
                if field_name is None:
                    continue
                if field_name != "value":
                    raise KeyError(field_name)
                found_value = True
                rendered: object = value
                if conversion is not None:
                    rendered = _FORMATTER.convert_field(rendered, conversion)
                components.append(
                    RText(
                        _FORMATTER.format_field(rendered, format_spec),
                        self.colors.countdown_value,
                    )
                )
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"消息模板格式错误: {exc}") from exc
        if not found_value:
            raise ConfigError("倒计时消息模板必须包含 {value}")
        return RTextList(*components)

    def render_shutdown(self) -> RTextBase:
        try:
            content = self.shutdown_now.format()
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"消息模板格式错误: {exc}") from exc
        return RTextList(
            *self._prefix_components(),
            RText(content, self.colors.shutdown_text),
        )

    def _prefix_components(self) -> list[object]:
        if not self.prefix:
            return []
        return [RText(self.prefix, self.colors.prefix), " "]


@dataclass(frozen=True)
class CurfewConfig:
    timezone_name: str
    timezone: ZoneInfo
    schedule: WeeklySchedule
    reminder_seconds: tuple[int, ...]
    shutdown_warning_after: int
    shutdown_warning_interval: int
    force_kill_after: int
    messages: MessageConfig

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> CurfewConfig:
        timezone_name = _string(raw, "timezone")
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"未知时区: {timezone_name}") from exc

        schedule_raw = raw.get("schedule")
        if not isinstance(schedule_raw, Mapping):
            raise ConfigError("schedule 必须是映射")
        try:
            schedule = WeeklySchedule.from_mapping(schedule_raw, timezone)
        except ScheduleError as exc:
            raise ConfigError(str(exc)) from exc

        reminders = _mapping(raw, "reminders")
        reminder_values: set[int] = set()
        for value in _integer_list(reminders, "minutes"):
            if value <= 0:
                raise ConfigError("提醒分钟数必须大于 0")
            reminder_values.add(value * 60)
        for value in _integer_list(reminders, "seconds"):
            if value <= 0:
                raise ConfigError("提醒秒数必须大于 0")
            reminder_values.add(value)

        shutdown = _mapping(raw, "shutdown")
        warning_after = _positive_integer(shutdown, "warning_after_seconds")
        warning_interval = _positive_integer(shutdown, "warning_interval_seconds")
        force_kill_after = _positive_integer(shutdown, "force_kill_after_seconds")
        if force_kill_after <= warning_after:
            raise ConfigError("force_kill_after_seconds 必须大于 warning_after_seconds")

        messages = _mapping(raw, "messages")
        colors = _mapping(messages, "colors")
        message_config = MessageConfig(
            prefix=_string(messages, "prefix"),
            minutes_remaining=_string(messages, "minutes_remaining"),
            seconds_remaining=_string(messages, "seconds_remaining"),
            shutdown_now=_string(messages, "shutdown_now"),
            colors=MessageColors(
                prefix=_color(colors, "prefix"),
                countdown_text=_color(colors, "countdown_text"),
                countdown_value=_color(colors, "countdown_value"),
                shutdown_text=_color(colors, "shutdown_text"),
            ),
        )
        # Validate placeholders while loading rather than during the final countdown.
        message_config.render_countdown(message_config.minutes_remaining, value=1)
        message_config.render_countdown(message_config.seconds_remaining, value=1)
        message_config.render_shutdown()

        return cls(
            timezone_name=timezone_name,
            timezone=timezone,
            schedule=schedule,
            reminder_seconds=tuple(sorted(reminder_values, reverse=True)),
            shutdown_warning_after=warning_after,
            shutdown_warning_interval=warning_interval,
            force_kill_after=force_kill_after,
            messages=message_config,
        )


def _mapping(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ConfigError(f"{key} 必须是映射")
    return value


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ConfigError(f"{key} 必须是字符串")
    return value


def _positive_integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{key} 必须是大于 0 的整数")
    return value


def _color(raw: Mapping[str, Any], key: str) -> RColor:
    value = _string(raw, key)
    try:
        return RColor.from_mc_value(value)
    except ValueError as exc:
        raise ConfigError(f"{key} 不是有效的 Minecraft 颜色: {value}") from exc


def _integer_list(raw: Mapping[str, Any], key: str) -> Sequence[int]:
    value = raw.get(key)
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise ConfigError(f"{key} 必须是整数列表")
    return value
