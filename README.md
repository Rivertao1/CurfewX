# CurfewX

简体中文 | [English](README_en_us.md)

CurfewX 是一个适用于 MCDReforged 的关服型宵禁插件。它会在配置的时间点提醒在线玩家，随后软关闭 Minecraft 服务端；宵禁结束时，只重新启动由 CurfewX 关闭的服务端。

当前版本面向 MCDReforged 2.15.7，使用公开的服务端控制 API，不依赖 Forge Mod。已在设计上兼容 `forge_handler`，也可以用于其他能够被 MCDR 正确管理的服务端。

## 安装

1. 从 `dist/` 获取 `CurfewX-v0.1.0.mcdr`。
2. 将文件放入 MCDR 的 `plugins/` 目录。
3. 执行 `!!MCDR reload plugin`，或重新启动 MCDR。
4. 首次加载后，插件会生成 `config/curfew_x/config.yml` 和 `config/curfew_x/state.json`。

默认时区为 `Asia/Shanghai`，每天 `00:00-08:00` 宵禁。

## 配置

默认配置：

```yaml
timezone: Asia/Shanghai

schedule:
  every_day:
    - 00:00-08:00

reminders:
  minutes:
    - 60
    - 30
    - 10
    - 5
  seconds:
    - 30
    - 10
    - 9
    - 8
    - 7
    - 6
    - 5
    - 4
    - 3
    - 2
    - 1

shutdown:
  warning_after_seconds: 120
  warning_interval_seconds: 300
  force_kill_after_seconds: 3600

messages:
  prefix: "[CurfewX]"
  minutes_remaining: "距离服务器宵禁还有 {value} 分钟"
  seconds_remaining: "距离服务器宵禁还有 {value} 秒"
  shutdown_now: "宵禁开始，服务器正在关闭"
```

`schedule` 还支持按星期配置，并支持一天内多个区间和跨零点：

```yaml
schedule:
  monday:
    - 00:00-08:00
  friday:
    - 00:00-08:00
    - 23:30-24:00
  saturday:
    - 00:00-09:00
```

可用的星期键为 `monday`、`tuesday`、`wednesday`、`thursday`、`friday`、`saturday`、`sunday`。`every_day` 中的区间会应用到每天，并与具体星期下的区间合并；如果需要每天采用不同规则，请移除 `every_day`。

修改配置后执行 `!!curfew reload`。配置错误时，插件会保留当前正在使用的有效配置，并在命令回复和 MCDR 日志中报告错误。

## 命令与权限

所有命令要求 MCDR `admin`（权限等级 3）或更高权限。MCDR 控制台拥有 `owner` 权限，可以在 Minecraft 服务端关闭期间执行命令。

| 命令 | 作用 |
| --- | --- |
| `!!curfew` / `!!curfew status` | 显示调度、服务器和临时解除状态 |
| `!!curfew pardon <分钟>` | 临时解除宵禁；需要时立即启动服务器 |
| `!!curfew pardon cancel` | 取消临时解除；若正值宵禁，执行 10 秒倒计时后关服 |
| `!!curfew enable` | 启用调度；若正值宵禁，执行 10 秒倒计时后关服 |
| `!!curfew disable` | 禁用调度，并启动由 CurfewX 关闭的服务器 |
| `!!curfew reload` | 验证并重载配置 |

`pardon` 的截止时间会持久化。如果解除到期时仍处于宵禁时段，服务器会关闭；如果原定宵禁已经结束，则服务器继续运行。

## 关服与恢复策略

- 到点先调用 MCDR 的软关闭 API，让 Minecraft 正常保存并退出。
- 120 秒未退出时在 MCDR 控制台和日志报警，之后每 300 秒重复一次。
- 3600 秒仍未退出时调用 MCDR 的 `kill()` 强制终止服务端进程组。
- 强制终止可能导致未保存数据丢失或存档损坏，应只作为最终兜底。三个时间均可配置。
- CurfewX 只会自动启动由自身关闭的服务器。开放期间由管理员手动关闭或意外崩溃的服务器不会被自动启动。
- 宵禁期间通过其他 MCDR 命令启动服务端时，CurfewX 会再次请求关闭。公开 API 无法取消尚未创建的进程，因此 Forge 进程可能短暂启动。
- `state.json` 保存管理状态、解除期限和强杀截止时间，不建议手动编辑或删除。

## 开发与打包

项目使用 [uv](https://docs.astral.sh/uv/) 管理开发环境：

仓库通过 `.python-version` 固定使用 Python 3.11.15；插件源码保持兼容 MCDReforged 2.15.7 所支持的 Python 3.9 及以上版本。

```bash
uv sync
uv run pytest -q
uv run ruff check .
mkdir -p dist
uv run mcdreforged pack -o dist
```

运行 `pack` 后会按照 `mcdreforged.plugin.json` 中的版本生成 `.mcdr` 文件。

## 许可证

CurfewX 使用 [MIT License](LICENSE) 发布。
