from __future__ import annotations

from mcdreforged.api.types import PluginServerInterface

from curfew_x.runtime import CurfewRuntime

runtime: CurfewRuntime | None = None


def on_load(server: PluginServerInterface, _prev_module: object | None) -> None:
    global runtime
    runtime = CurfewRuntime(server)
    runtime.load()


def on_unload(_server: PluginServerInterface) -> None:
    global runtime
    if runtime is not None:
        runtime.unload()
        runtime = None


def on_server_start(_server: PluginServerInterface) -> None:
    if runtime is not None:
        runtime.on_server_start()


def on_server_startup(_server: PluginServerInterface) -> None:
    if runtime is not None:
        runtime.on_server_startup()


def on_server_stop(_server: PluginServerInterface, return_code: int) -> None:
    if runtime is not None:
        runtime.on_server_stop(return_code)
