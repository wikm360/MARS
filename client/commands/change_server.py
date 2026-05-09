"""
Command: change_server / update_servers
Allows the admin to push a new server list to the client at runtime.
The client will persist the list and use it on next reconnect.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path

from client.commands.registry import register

log = logging.getLogger(__name__)
_RUNTIME_SERVERS: list[str] = []   # updated at runtime; read by connection module


@register("change_server")
async def change_server(payload: dict) -> dict:
    """
    payload:
        server (str): single server URI, e.g. "ws://1.2.3.4:8765"
    """
    server = payload.get("server", "")
    if not server:
        return {"error": "server URI is required"}
    _update_servers([server])
    return {"updated": True, "servers": [server]}


@register("update_servers")
async def update_servers(payload: dict) -> dict:
    """
    payload:
        servers (list[str]): ordered list of server URIs
    """
    servers: list[str] = payload.get("servers", [])
    if not servers:
        return {"error": "servers list is required"}
    _update_servers(servers)
    return {"updated": True, "servers": servers}


def _update_servers(servers: list[str]) -> None:
    global _RUNTIME_SERVERS
    _RUNTIME_SERVERS = list(servers)
    # Persist alongside the config so restarts pick it up
    override_path = Path(__file__).resolve().parents[1] / "servers_override.json"
    override_path.write_text(json.dumps(servers, indent=2))
    log.info("Server list updated: %s", servers)


def get_runtime_servers() -> list[str]:
    return list(_RUNTIME_SERVERS)
