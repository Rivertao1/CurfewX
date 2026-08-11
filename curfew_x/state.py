from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class PersistentState:
    enabled: bool = True
    managed_shutdown: bool = False
    pardon_until: str | None = None
    shutdown_requested_at: str | None = None
    force_kill_at: str | None = None
    force_kill_issued: bool = False
    enable_grace_until: str | None = None

    @classmethod
    def load(cls, path: Path) -> PersistentState:
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf8"))
            if not isinstance(raw, dict):
                raise ValueError("root is not an object")
            return cls(
                enabled=_boolean(raw, "enabled", True),
                managed_shutdown=_boolean(raw, "managed_shutdown", False),
                pardon_until=_optional_datetime(raw, "pardon_until"),
                shutdown_requested_at=_optional_datetime(raw, "shutdown_requested_at"),
                force_kill_at=_optional_datetime(raw, "force_kill_at"),
                force_kill_issued=_boolean(raw, "force_kill_issued", False),
                enable_grace_until=_optional_datetime(raw, "enable_grace_until"),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取状态文件 {path}: {exc}") from exc

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
            encoding="utf8",
        )
        os.replace(temporary, path)


def parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("持久化时间缺少时区")
    return result


def _boolean(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} is not a boolean")
    return value


def _optional_datetime(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} is not a string")
    parse_optional_datetime(value)
    return value
