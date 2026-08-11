import copy
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from curfew_x.config import DEFAULT_CONFIG, CurfewConfig
from curfew_x.runtime import CurfewRuntime

SHANGHAI = ZoneInfo("Asia/Shanghai")


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, tuple[object, ...]]] = []

    def _add(self, level: str, message: str, *args: object) -> None:
        self.records.append((level, message, args))

    def info(self, message: str, *args: object) -> None:
        self._add("info", message, *args)

    def warning(self, message: str, *args: object) -> None:
        self._add("warning", message, *args)

    def error(self, message: str, *args: object) -> None:
        self._add("error", message, *args)

    def critical(self, message: str, *args: object) -> None:
        self._add("critical", message, *args)

    def exception(self, message: str, *args: object) -> None:
        self._add("exception", message, *args)


class FakeServer:
    def __init__(self, data_folder: Path) -> None:
        self.data_folder = data_folder
        self.logger = FakeLogger()
        self.running = True
        self.startup = True
        self.stop_calls = 0
        self.start_calls = 0
        self.kill_calls = 0
        self.exit_flags: list[bool] = []
        self.messages: list[str] = []

    def get_data_folder(self) -> str:
        return str(self.data_folder)

    def is_server_running(self) -> bool:
        return self.running

    def is_server_startup(self) -> bool:
        return self.startup

    def say(self, message: str) -> None:
        self.messages.append(message)

    def set_exit_after_stop_flag(self, value: bool) -> None:
        self.exit_flags.append(value)

    def stop(self) -> bool:
        self.stop_calls += 1
        return self.running

    def start(self) -> bool:
        self.start_calls += 1
        if self.running:
            return False
        self.running = True
        self.startup = False
        return True

    def kill(self) -> bool:
        self.kill_calls += 1
        return self.running


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 8, 11, hour, minute, second, tzinfo=SHANGHAI)


def make_runtime(tmp_path: Path) -> tuple[CurfewRuntime, FakeServer]:
    server = FakeServer(tmp_path)
    runtime = CurfewRuntime(server)  # type: ignore[arg-type]
    runtime._config = CurfewConfig.from_mapping(copy.deepcopy(DEFAULT_CONFIG))
    return runtime, server


def test_reminders_and_shutdown_are_emitted_once(tmp_path: Path) -> None:
    runtime, server = make_runtime(tmp_path)

    runtime.tick(at(23, 0))
    runtime.tick(at(23, 30))
    runtime.tick(at(23, 59, 30))
    for second in range(50, 60):
        runtime.tick(at(23, 59, second))
    runtime.tick(at(0, 0) + timedelta(days=1))
    runtime.tick(at(0, 0, 1) + timedelta(days=1))

    assert "[CurfewX] 距离服务器宵禁还有 60 分钟" in server.messages
    assert "[CurfewX] 距离服务器宵禁还有 30 分钟" in server.messages
    assert "[CurfewX] 距离服务器宵禁还有 30 秒" in server.messages
    assert "[CurfewX] 距离服务器宵禁还有 1 秒" in server.messages
    assert server.messages[-1] == "[CurfewX] 宵禁开始，服务器正在关闭"
    assert server.stop_calls == 1
    assert server.exit_flags == [False]
    assert runtime._state.managed_shutdown


def test_shutdown_watchdog_warns_then_kills_once(tmp_path: Path) -> None:
    runtime, server = make_runtime(tmp_path)
    shutdown_at = at(0, 0)

    runtime.tick(shutdown_at)
    runtime.tick(shutdown_at + timedelta(seconds=119))
    runtime.tick(shutdown_at + timedelta(seconds=120))
    runtime.tick(shutdown_at + timedelta(minutes=7))
    runtime.tick(shutdown_at + timedelta(hours=1))
    runtime.tick(shutdown_at + timedelta(hours=1, seconds=1))

    errors = [record for record in server.logger.records if record[0] == "error"]
    assert len(errors) == 2
    assert server.kill_calls == 1
    assert runtime._state.force_kill_issued


def test_managed_server_restarts_at_curfew_end(tmp_path: Path) -> None:
    runtime, server = make_runtime(tmp_path)
    runtime.tick(at(0, 0))
    server.running = False
    server.startup = False
    runtime.on_server_stop(0)

    runtime.tick(at(7, 59))
    assert server.start_calls == 0

    runtime.tick(at(8, 0))
    assert server.start_calls == 1
    assert not runtime._state.managed_shutdown


def test_manually_stopped_server_is_not_started(tmp_path: Path) -> None:
    runtime, server = make_runtime(tmp_path)
    server.running = False
    server.startup = False

    runtime.tick(at(8, 0))

    assert server.start_calls == 0


def test_pardon_delays_shutdown_until_expiry(tmp_path: Path) -> None:
    runtime, server = make_runtime(tmp_path)
    now = at(2, 0)
    runtime._state.pardon_until = (now + timedelta(minutes=60)).isoformat()

    runtime.tick(now)
    runtime.tick(now + timedelta(minutes=59, seconds=50))
    assert server.stop_calls == 0
    assert "[CurfewX] 距离服务器宵禁还有 10 秒" in server.messages

    runtime.tick(now + timedelta(minutes=60))
    assert server.stop_calls == 1


def test_disable_allows_managed_server_to_start(tmp_path: Path) -> None:
    runtime, server = make_runtime(tmp_path)
    server.running = False
    server.startup = False
    runtime._state.enabled = False
    runtime._state.managed_shutdown = True

    runtime.tick(at(2, 0))

    assert server.start_calls == 1
    assert not runtime._state.managed_shutdown
