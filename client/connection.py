"""
Client connection manager.
Handles WebSocket connection lifecycle, authentication, heartbeat,
and transparent failover across the server list with exponential backoff.
"""
from __future__ import annotations
import asyncio
import logging
import os
import platform
import socket
import sys
import time
import uuid
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.crypto import SessionCipher, derive_key
from shared.models import Message, MessageType, Role
from shared.protocol import decode_message, encode_message
from server.auth import client_token_from_secret
from client.commands.registry import auto_discover, get

from client.paths import client_id_path, servers_override_path

log = logging.getLogger(__name__)


def get_or_create_client_id() -> str:
    path = client_id_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text().strip()
    cid = str(uuid.uuid4())
    path.write_text(cid)
    return cid


def build_server_list(cfg: dict) -> list[str]:
    """Merge config server list with any runtime override."""
    import json
    base = list(cfg["client"]["servers"])
    override_path = servers_override_path()
    if override_path.exists():
        try:
            override = json.loads(override_path.read_text())
            if isinstance(override, list):
                # prepend override servers (higher priority)
                seen = set()
                merged = []
                for s in override + base:
                    if s not in seen:
                        seen.add(s)
                        merged.append(s)
                return merged
        except Exception:
            pass
    return base


class ClientConnection:
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._client_id = get_or_create_client_id()
        self._secret = os.environ.get("MRAS_SECRET", cfg["client"]["secret"])
        self._heartbeat_interval = cfg["client"]["heartbeat_interval"]
        self._reconnect_delay = cfg["client"]["reconnect_delay"]
        self._reconnect_max = cfg["client"]["reconnect_max_delay"]
        auto_discover()

    async def run(self) -> None:
        delay = self._reconnect_delay
        while True:
            servers = build_server_list(self._cfg)
            connected = False
            for server_uri in servers:
                try:
                    log.info("Connecting to %s ...", server_uri)
                    await self._connect(server_uri)
                    connected = True
                    delay = self._reconnect_delay  # reset on success
                    break
                except (websockets.exceptions.WebSocketException,
                        ConnectionRefusedError, OSError) as exc:
                    log.warning("Connection to %s failed: %s", server_uri, exc)
                except Exception as exc:
                    log.exception("Unexpected error with %s: %s", server_uri, exc)

            if not connected:
                log.info("All servers unreachable. Retrying in %ds ...", delay)
            else:
                log.info("Disconnected. Retrying in %ds ...", delay)

            await asyncio.sleep(delay)
            delay = min(delay * 2, self._reconnect_max)

    async def _connect(self, uri: str) -> None:
        import os
        salt = os.urandom(16)
        key, _ = derive_key(self._secret, salt)
        cipher = SessionCipher(key)
        token = client_token_from_secret(self._secret)

        async with websockets.connect(uri, ping_interval=None,
                                      max_size=20 * 1024 * 1024) as ws:
            # --- Authenticate ---
            auth_msg = Message(
                type=MessageType.AUTH,
                payload={
                    "role": Role.CLIENT,
                    "token": token,
                    "salt": salt.hex(),
                    "client_id": self._client_id,
                    "hostname": socket.gethostname(),
                    "os": platform.platform(),
                    "username": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
                    "version": "1.0.0",
                },
            )
            await ws.send(encode_message(auth_msg))  # auth sent plain

            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            resp = decode_message(raw, cipher)
            if not resp or resp.type != MessageType.AUTH_OK:
                log.error("Auth rejected by server")
                return

            log.info("Authenticated to %s (client_id=%s)", uri, self._client_id)

            # --- Run heartbeat + message loop concurrently ---
            await asyncio.gather(
                self._heartbeat_loop(ws, cipher),
                self._message_loop(ws, cipher),
            )

    async def _heartbeat_loop(self, ws, cipher: SessionCipher) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await ws.send(encode_message(Message(MessageType.PING), cipher))
            except Exception:
                return

    async def _message_loop(self, ws, cipher: SessionCipher) -> None:
        async for raw in ws:
            msg = decode_message(raw, cipher)
            if not msg:
                continue

            if msg.type == MessageType.PONG:
                continue

            elif msg.type == MessageType.PING:
                await ws.send(encode_message(Message(MessageType.PONG), cipher))

            elif msg.type == MessageType.COMMAND:
                asyncio.create_task(self._handle_command(ws, cipher, msg))

            elif msg.type in (MessageType.FILE_UPLOAD, MessageType.FILE_DOWNLOAD):
                cmd_name = msg.type.value.replace("_", "_")
                asyncio.create_task(self._handle_command(ws, cipher, msg))

            elif msg.type == MessageType.UPDATE_SERVERS:
                from client.commands.change_server import update_servers as _us
                result = await _us(msg.payload)
                log.info("Server list updated via push: %s", result)

            elif msg.type == MessageType.REDIRECT:
                new_uri = msg.payload.get("uri", "")
                if new_uri:
                    from client.commands.change_server import _update_servers
                    _update_servers([new_uri])
                    log.info("Redirected to %s — reconnecting", new_uri)
                    await ws.close()
                    return

    async def _handle_command(self, ws, cipher: SessionCipher, msg: Message) -> None:
        cmd_name = msg.payload.get("command") or msg.type.value
        handler = get(cmd_name)

        if handler is None:
            result_msg = Message(
                type=MessageType.COMMAND_ERROR,
                msg_id=msg.msg_id,
                payload={"error": f"unknown command: {cmd_name}", "msg_id": msg.msg_id},
            )
        else:
            try:
                result = await handler(msg.payload)
                result_msg = Message(
                    type=MessageType.COMMAND_RESULT,
                    msg_id=msg.msg_id,
                    payload={"result": result, "command": cmd_name, "msg_id": msg.msg_id},
                )
            except Exception as exc:
                log.exception("Command %s failed", cmd_name)
                result_msg = Message(
                    type=MessageType.COMMAND_ERROR,
                    msg_id=msg.msg_id,
                    payload={"error": str(exc), "command": cmd_name, "msg_id": msg.msg_id},
                )

        try:
            await ws.send(encode_message(result_msg, cipher))
        except Exception:
            pass
