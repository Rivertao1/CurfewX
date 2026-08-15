from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcdreforged.api.command import Integer, Literal, Requirements
from mcdreforged.api.types import CommandSource, PluginServerInterface

from curfew_x.config import DEFAULT_CONFIG, ConfigError, CurfewConfig
from curfew_x.state import PersistentState, parse_optional_datetime

ADMIN_PERMISSION_LEVEL = 3
TICK_SECONDS = 0.2
ENABLE_GRACE_SECONDS = 10
START_RETRY_SECONDS = 30


class CurfewRuntime:
    def __init__(
        self,
        server: PluginServerInterface,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.server = server
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._config: CurfewConfig | None = None
        self._state_path = Path(server.get_data_folder()) / "state.json"
        self._state = PersistentState()
        self._countdown_target: datetime | None = None
        self._announced: set[int] = set()
        self._last_watchdog_warning_slot = -1
        self._next_start_attempt_at: datetime | None = None

    @property
    def config(self) -> CurfewConfig:
        if self._config is None:
            raise RuntimeError("CurfewX 尚未加载配置")
        return self._config

    def load(self) -> None:
        with self._lock:
            self._config = self._load_config()
            self._state = PersistentState.load(self._state_path)
            self._normalize_state(self._now())
            self._register_commands()
            self.server.register_help_message(
                "!!cfx",
                {
                    "zh_cn": "管理服务器宵禁时间与临时解除（完整别名：!!curfew）",
                    "en_us": "Manage server curfews and pardons (full alias: !!curfew)",
                },
                permission=ADMIN_PERMISSION_LEVEL,
            )
            self._thread = threading.Thread(
                target=self._scheduler_loop,
                name="CurfewX-Scheduler",
                daemon=True,
            )
            self._thread.start()
        self.server.logger.info("CurfewX 调度器已启动")

    def unload(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
            if thread.is_alive():
                self.server.logger.warning("CurfewX 调度线程未能在 2 秒内退出")

    def on_server_start(self) -> None:
        with self._lock:
            now = self._now()
            if self._enforcement_due(now):
                self.server.logger.warning("检测到服务端在宵禁期间启动，将立即软关闭")
                self._initiate_shutdown(now, announce=False)

    def on_server_startup(self) -> None:
        with self._lock:
            now = self._now()
            if self._enforcement_due(now):
                if self._state.shutdown_requested_at is not None:
                    self.server.logger.warning("服务端在宵禁期间完成启动，再次请求软关闭")
                    self._retry_soft_shutdown(announce=True)
                else:
                    self._initiate_shutdown(now, announce=True)

    def on_server_stop(self, return_code: int) -> None:
        with self._lock:
            if self._state.shutdown_requested_at is not None:
                self.server.logger.info("CurfewX 已确认服务端停止，返回码: %s", return_code)
                self._state.shutdown_requested_at = None
                self._state.force_kill_at = None
                self._state.force_kill_issued = False
                self._last_watchdog_warning_slot = -1
                self._save_state()

    def tick(self, now: datetime | None = None) -> None:
        with self._lock:
            current = now or self._now()
            self._normalize_state(current)
            self._check_shutdown_watchdog(current)

            if not self.server.is_server_running():
                self._finish_stopped_shutdown_if_needed()
                self._start_managed_server_if_allowed(current)

            target = self._effective_shutdown_at(current)
            self._update_countdown(target, current)

            if target is not None and target <= current and self.server.is_server_running():
                self._initiate_shutdown(current, announce=True)

    def _scheduler_loop(self) -> None:
        while not self._stop_event.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:
                self.server.logger.exception("CurfewX 调度循环发生异常")
                self._stop_event.wait(5)

    def _load_config(self) -> CurfewConfig:
        raw = self.server.load_config_simple(
            file_name="config.yml",
            default_config=copy.deepcopy(DEFAULT_CONFIG),
            in_data_folder=True,
            failure_policy="raise",
            data_processor=_fill_missing_defaults,
        )
        if not isinstance(raw, Mapping):
            raise ConfigError("配置文件根节点必须是映射")
        merged = _deep_merge(DEFAULT_CONFIG, raw)
        return CurfewConfig.from_mapping(merged)

    def _register_commands(self) -> None:
        def failure() -> str:
            return "权限不足：需要 MCDR admin（等级 3）或更高权限"

        for prefix in ("!!cfx", "!!curfew"):
            root = (
                Literal(prefix)
                .requires(Requirements.has_permission(ADMIN_PERMISSION_LEVEL), failure)
                .runs(self._command_status)
            )
            root.then(Literal("help").runs(self._command_help))
            root.then(Literal("status").runs(self._command_status))
            root.then(Literal("enable").runs(self._command_enable))
            root.then(Literal("disable").runs(self._command_disable))
            root.then(Literal("reload").runs(self._command_reload))
            root.then(
                Literal("pardon")
                .then(Integer("minutes").at_min(1).runs(self._command_pardon))
                .then(Literal("cancel").runs(self._command_pardon_cancel))
            )
            self.server.register_command(root)

    def _command_help(
        self, source: CommandSource, _context: dict[str, Any] | None = None
    ) -> None:
        source.reply(
            "\n".join(
                (
                    "§6[CurfewX] 命令帮助",
                    "§e!!cfx help§7 - 显示本帮助",
                    "§e!!cfx status§7 - 查看宵禁、服务器和临时解除状态",
                    "§e!!cfx pardon <分钟>§7 - 临时解除宵禁并按需启动服务器",
                    "§e!!cfx pardon cancel§7 - 取消临时解除",
                    "§e!!cfx enable§7 - 启用宵禁调度",
                    "§e!!cfx disable§7 - 禁用调度并恢复插件关闭的服务器",
                    "§e!!cfx reload§7 - 验证并重载配置文件",
                    "§7完整命令别名：§e!!curfew",
                    "§7所有命令需要 MCDR admin（等级 3）或更高权限。",
                )
            )
        )

    def _command_status(
        self, source: CommandSource, _context: dict[str, Any] | None = None
    ) -> None:
        with self._lock:
            now = self._now()
            config = self.config
            local_now = now.astimezone(config.timezone)
            target = self._effective_shutdown_at(now)
            pardon = self._pardon_until()
            lines = [
                "§6[CurfewX] 状态",
                f"§7插件调度: {'§a已启用' if self._state.enabled else '§c已禁用'}",
                f"§7服务器: {'§a运行中' if self.server.is_server_running() else '§c已停止'}",
                f"§7当前时间: §f{local_now:%Y-%m-%d %H:%M:%S} ({config.timezone_name})",
                f"§7当前时段: {'§c宵禁' if config.schedule.is_active(now) else '§a开放'}",
                f"§7CurfewX 管理的停服: {'是' if self._state.managed_shutdown else '否'}",
            ]
            if pardon is not None and pardon > now:
                lines.append(
                    f"§7临时解除至: §f{pardon.astimezone(config.timezone):%Y-%m-%d %H:%M:%S}"
                )
            if target is not None:
                lines.append(
                    f"§7下次有效关服: §f{target.astimezone(config.timezone):%Y-%m-%d %H:%M:%S}"
                )
            source.reply("\n".join(lines))

    def _command_enable(self, source: CommandSource, _context: dict[str, Any]) -> None:
        with self._lock:
            if self._state.enabled:
                source.reply("§e[CurfewX] 调度已经启用")
                return
            now = self._now()
            self._state.enabled = True
            if (
                self.config.schedule.is_active(now)
                and not self._pardon_active(now)
                and self.server.is_server_running()
            ):
                self._state.enable_grace_until = _iso(now + timedelta(seconds=ENABLE_GRACE_SECONDS))
            self._save_state()
            self._reset_countdown()
            source.reply("§a[CurfewX] 已启用宵禁调度")

    def _command_disable(self, source: CommandSource, _context: dict[str, Any]) -> None:
        with self._lock:
            if not self._state.enabled:
                source.reply("§e[CurfewX] 调度已经禁用")
                return
            self._state.enabled = False
            self._state.pardon_until = None
            self._state.enable_grace_until = None
            self._save_state()
            self._reset_countdown()
            source.reply("§a[CurfewX] 已禁用宵禁调度")
            if self._state.managed_shutdown:
                if self.server.is_server_running():
                    source.reply("§e[CurfewX] 正在等待当前关服流程完成，随后将重新启动")
                else:
                    self._start_managed_server_if_allowed(self._now())

    def _command_reload(self, source: CommandSource, _context: dict[str, Any]) -> None:
        with self._lock:
            try:
                replacement = self._load_config()
            except Exception as exc:
                self.server.logger.exception("CurfewX 配置重载失败")
                source.reply(f"§c[CurfewX] 配置重载失败: {exc}")
                return
            self._config = replacement
            now = self._now()
            if (
                self._state.enabled
                and replacement.schedule.is_active(now)
                and not self._pardon_active(now)
                and self.server.is_server_running()
            ):
                self._state.enable_grace_until = _iso(now + timedelta(seconds=ENABLE_GRACE_SECONDS))
                self._save_state()
            self._reset_countdown()
            source.reply("§a[CurfewX] 配置已重载")

    def _command_pardon(self, source: CommandSource, context: dict[str, Any]) -> None:
        with self._lock:
            if not self._state.enabled:
                source.reply("§e[CurfewX] 调度已禁用，无需临时解除")
                return
            minutes = int(context["minutes"])
            now = self._now()
            pardon_until = now + timedelta(minutes=minutes)
            self._state.pardon_until = _iso(pardon_until)
            self._state.enable_grace_until = None
            self._save_state()
            self._reset_countdown()
            local_until = pardon_until.astimezone(self.config.timezone)
            source.reply(
                f"§a[CurfewX] 已临时解除 {minutes} 分钟，截止 {local_until:%Y-%m-%d %H:%M:%S}"
            )
            if self._state.managed_shutdown:
                if self.server.is_server_running():
                    source.reply("§e[CurfewX] 当前关服流程无法撤销，停止完成后会自动启动")
                else:
                    self._start_managed_server_if_allowed(now)

    def _command_pardon_cancel(self, source: CommandSource, _context: dict[str, Any]) -> None:
        with self._lock:
            now = self._now()
            if not self._pardon_active(now):
                self._state.pardon_until = None
                self._save_state()
                source.reply("§e[CurfewX] 当前没有生效中的临时解除")
                return
            self._state.pardon_until = None
            if self.config.schedule.is_active(now) and self.server.is_server_running():
                self._state.enable_grace_until = _iso(now + timedelta(seconds=ENABLE_GRACE_SECONDS))
            self._save_state()
            self._reset_countdown()
            source.reply("§a[CurfewX] 已取消临时解除")

    def _effective_shutdown_at(self, now: datetime) -> datetime | None:
        if not self._state.enabled:
            return None
        exemption_until = max(
            (
                value
                for value in (self._pardon_until(), self._enable_grace_until())
                if value is not None and value > now
            ),
            default=None,
        )
        if exemption_until is not None:
            if self.config.schedule.is_active(exemption_until):
                return exemption_until
            return self.config.schedule.next_start(exemption_until)
        if self.config.schedule.is_active(now):
            return now
        return self.config.schedule.next_start(now)

    def _enforcement_due(self, now: datetime) -> bool:
        target = self._effective_shutdown_at(now)
        return target is not None and target <= now

    def _update_countdown(self, target: datetime | None, now: datetime) -> None:
        if target != self._countdown_target:
            self._countdown_target = target
            self._announced.clear()
            if target is not None:
                remaining = (target - now).total_seconds()
                self._announced.update(
                    threshold
                    for threshold in self.config.reminder_seconds
                    if threshold > remaining + 0.5
                )
        if target is None or self._state.shutdown_requested_at is not None:
            return

        remaining = (target - now).total_seconds()
        for threshold in self.config.reminder_seconds:
            if threshold in self._announced or remaining > threshold:
                continue
            self._announced.add(threshold)
            if not self.server.is_server_startup():
                continue
            if threshold >= 60 and threshold % 60 == 0:
                message = self.config.messages.render_countdown(
                    self.config.messages.minutes_remaining,
                    value=threshold // 60,
                )
            else:
                message = self.config.messages.render_countdown(
                    self.config.messages.seconds_remaining,
                    value=threshold,
                )
            self.server.say(message)

    def _initiate_shutdown(self, now: datetime, *, announce: bool) -> None:
        if not self.server.is_server_running():
            return
        if self._state.shutdown_requested_at is not None:
            return
        self._state.managed_shutdown = True
        self._state.shutdown_requested_at = _iso(now)
        self._state.force_kill_at = _iso(now + timedelta(seconds=self.config.force_kill_after))
        self._state.force_kill_issued = False
        self._last_watchdog_warning_slot = -1
        self._save_state()
        self._retry_soft_shutdown(announce=announce)

    def _retry_soft_shutdown(self, *, announce: bool) -> None:
        if announce and self.server.is_server_startup():
            self.server.say(self.config.messages.render_shutdown())
        self.server.set_exit_after_stop_flag(False)
        if self.server.stop():
            self.server.logger.info("CurfewX 已发送软关闭命令")
        else:
            self.server.logger.warning("CurfewX 软关闭请求未被接受，将继续监控服务端状态")

    def _check_shutdown_watchdog(self, now: datetime) -> None:
        requested = parse_optional_datetime(self._state.shutdown_requested_at)
        if requested is None:
            return
        if not self.server.is_server_running():
            return
        if self._state.force_kill_issued:
            retry_at = parse_optional_datetime(self._state.force_kill_at)
            if retry_at is None or now < retry_at:
                return
            self._state.force_kill_issued = False
        elapsed = max(0, int((now - requested).total_seconds()))
        if elapsed < self.config.shutdown_warning_after:
            return

        force_at = parse_optional_datetime(self._state.force_kill_at)
        if force_at is None:
            force_at = requested + timedelta(seconds=self.config.force_kill_after)
            self._state.force_kill_at = _iso(force_at)
            self._save_state()
        if now >= force_at:
            self.server.logger.critical(
                "CurfewX: 服务端软关闭已卡住 %s 秒，即将强制终止进程组",
                elapsed,
            )
            self.server.set_exit_after_stop_flag(False)
            killed = self.server.kill()
            self._state.force_kill_issued = killed
            self._state.force_kill_at = _iso(now + timedelta(seconds=60))
            self._save_state()
            if not killed:
                self.server.logger.critical("CurfewX: 强制终止请求失败，将在 60 秒后重试")
            return

        slot = (
            elapsed - self.config.shutdown_warning_after
        ) // self.config.shutdown_warning_interval
        if slot > self._last_watchdog_warning_slot:
            self._last_watchdog_warning_slot = slot
            remaining = max(0, int((force_at - now).total_seconds()))
            self.server.logger.error(
                "CurfewX: 服务端软关闭已等待 %s 秒，若仍未退出将在 %s 秒后强制终止",
                elapsed,
                remaining,
            )

    def _finish_stopped_shutdown_if_needed(self) -> None:
        if self._state.shutdown_requested_at is None:
            return
        self._state.shutdown_requested_at = None
        self._state.force_kill_at = None
        self._state.force_kill_issued = False
        self._last_watchdog_warning_slot = -1
        self._save_state()

    def _start_managed_server_if_allowed(self, now: datetime) -> None:
        if not self._state.managed_shutdown or self.server.is_server_running():
            return
        if self._state.enabled and self._enforcement_due(now):
            return
        if self._next_start_attempt_at is not None and now < self._next_start_attempt_at:
            return
        self._next_start_attempt_at = now + timedelta(seconds=START_RETRY_SECONDS)
        if self.server.start():
            self._next_start_attempt_at = None
            self._state.managed_shutdown = False
            self._save_state()
            self.server.logger.info("CurfewX 已启动由插件管理关闭的服务端")
        else:
            self.server.logger.error(
                "CurfewX 无法启动由插件管理关闭的服务端，将在 %s 秒后重试",
                START_RETRY_SECONDS,
            )

    def _normalize_state(self, now: datetime) -> None:
        changed = False
        pardon = self._pardon_until()
        if pardon is not None and pardon <= now:
            self._state.pardon_until = None
            changed = True
        grace = self._enable_grace_until()
        if grace is not None and grace <= now:
            self._state.enable_grace_until = None
            changed = True
        if changed:
            self._save_state()

    def _pardon_until(self) -> datetime | None:
        return parse_optional_datetime(self._state.pardon_until)

    def _enable_grace_until(self) -> datetime | None:
        return parse_optional_datetime(self._state.enable_grace_until)

    def _pardon_active(self, now: datetime) -> bool:
        pardon = self._pardon_until()
        return pardon is not None and pardon > now

    def _save_state(self) -> None:
        self._state.save(self._state_path)

    def _reset_countdown(self) -> None:
        self._countdown_target = None
        self._announced.clear()


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime 必须包含时区")
    return value.astimezone(timezone.utc).isoformat()


def _deep_merge(defaults: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = copy.deepcopy(dict(defaults))
    for key, value in values.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _fill_missing_defaults(values: Any) -> bool:
    """Add newly introduced nested settings without replacing user values."""
    if not isinstance(values, dict):
        return False

    changed = False

    def fill(target: dict[str, Any], defaults: Mapping[str, Any]) -> None:
        nonlocal changed
        for key, default in defaults.items():
            if key not in target:
                target[key] = copy.deepcopy(default)
                changed = True
            elif isinstance(target[key], dict) and isinstance(default, Mapping):
                fill(target[key], default)

    fill(values, DEFAULT_CONFIG)
    return changed
