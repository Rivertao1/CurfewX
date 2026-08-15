import copy

import pytest

from curfew_x.config import DEFAULT_CONFIG, ConfigError, CurfewConfig
from curfew_x.runtime import _deep_merge, _fill_missing_defaults


def test_default_config() -> None:
    config = CurfewConfig.from_mapping(copy.deepcopy(DEFAULT_CONFIG))

    assert config.timezone_name == "Asia/Shanghai"
    assert config.reminder_seconds == (3600, 1800, 600, 300, 30, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1)
    assert config.shutdown_warning_after == 120
    assert config.shutdown_warning_interval == 300
    assert config.force_kill_after == 3600
    assert config.messages.colors.prefix.name == "green"
    assert config.messages.colors.countdown_text.name == "yellow"
    assert config.messages.colors.countdown_value.name == "red"


def test_countdown_uses_structured_colors() -> None:
    config = CurfewConfig.from_mapping(copy.deepcopy(DEFAULT_CONFIG))

    message = config.messages.render_countdown(config.messages.seconds_remaining, value=10)

    assert message.to_plain_text() == "[CurfewX] 距离服务器宵禁还有 10 秒"
    assert message.to_json_object() == [
        "",
        {"text": "[CurfewX]", "color": "green"},
        {"text": " "},
        {"text": "距离服务器宵禁还有 ", "color": "yellow"},
        {"text": "10", "color": "red"},
        {"text": " 秒", "color": "yellow"},
    ]


def test_force_kill_must_be_after_warning() -> None:
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["shutdown"]["force_kill_after_seconds"] = 60

    with pytest.raises(ConfigError, match="force_kill_after_seconds"):
        CurfewConfig.from_mapping(raw)


def test_message_template_is_validated() -> None:
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["messages"]["seconds_remaining"] = "{missing}"

    with pytest.raises(ConfigError, match="消息模板"):
        CurfewConfig.from_mapping(raw)


def test_countdown_template_requires_value() -> None:
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["messages"]["seconds_remaining"] = "即将宵禁"

    with pytest.raises(ConfigError, match=r"必须包含 \{value\}"):
        CurfewConfig.from_mapping(raw)


def test_message_color_is_validated() -> None:
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["messages"]["colors"]["countdown_value"] = "not-a-color"

    with pytest.raises(ConfigError, match="Minecraft 颜色"):
        CurfewConfig.from_mapping(raw)


def test_partial_config_is_merged_with_defaults() -> None:
    merged = _deep_merge(
        DEFAULT_CONFIG,
        {"timezone": "UTC", "shutdown": {"warning_after_seconds": 5}},
    )

    assert merged["timezone"] == "UTC"
    assert merged["shutdown"]["warning_after_seconds"] == 5
    assert merged["shutdown"]["force_kill_after_seconds"] == 3600


def test_missing_nested_config_is_added_without_replacing_values() -> None:
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["messages"].pop("colors")
    raw["messages"]["prefix"] = "[Custom]"

    assert _fill_missing_defaults(raw)
    assert raw["messages"]["prefix"] == "[Custom]"
    assert raw["messages"]["colors"] == DEFAULT_CONFIG["messages"]["colors"]
    assert not _fill_missing_defaults(raw)
