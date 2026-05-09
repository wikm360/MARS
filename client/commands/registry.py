"""
Command registry — the single source of truth for all client commands.

Each command module registers itself by calling register().
New commands can be added by dropping a file into this package and importing it.
"""
from __future__ import annotations
import importlib
import logging
import pkgutil
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)

# Handler signature: async def handler(payload: dict) -> dict
CommandHandler = Callable[[dict], Coroutine[Any, Any, dict]]

_registry: dict[str, CommandHandler] = {}


def register(name: str):
    """Decorator to register an async command handler."""
    def decorator(fn: CommandHandler) -> CommandHandler:
        _registry[name] = fn
        log.debug("Command registered: %s", name)
        return fn
    return decorator


def get(name: str) -> CommandHandler | None:
    return _registry.get(name)


def list_commands() -> list[str]:
    return list(_registry.keys())


def auto_discover() -> None:
    """Import every module in the commands package so handlers self-register."""
    import client.commands as pkg
    for finder, module_name, _ in pkgutil.iter_modules(pkg.__path__):
        full = f"client.commands.{module_name}"
        if module_name == "registry":
            continue
        try:
            importlib.import_module(full)
        except Exception as exc:
            log.warning("Failed to load command module %s: %s", full, exc)
