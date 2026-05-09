"""
Admin WebSocket connection — authenticates as ADMIN role,
sends commands, and yields incoming messages via an asyncio Queue.
"""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path

import websockets

from shared.crypto import SessionCipher, derive_key
from shared.models import Message, MessageType, Role
from shared.protocol import decode_message, encode_message
from server.auth import verify_admin_token

log = logging.getLogger(__name__)


class AdminConnection:
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._secret = os.environ.get("MARS_SECRET", cfg["admin"]["secret"])
        self._admin_token = os.environ.get("MARS_ADMIN_TOKEN", cfg["admin"]["admin_token"])
        self._server_uri = cfg["admin"]["server"]
        self._cipher: SessionCipher | None = None
        self._ws = None
        self._inbox: asyncio.Queue[Message] = asyncio.Queue()
        self._connected = False

    async def connect(self) -> None:
        salt = os.urandom(16)
        key, _ = derive_key(self._secret, salt)
        self._cipher = SessionCipher(key)

        self._ws = await websockets.connect(self._server_uri, ping_interval=None,
                                            max_size=20 * 1024 * 1024)

        auth_msg = Message(
            type=MessageType.AUTH,
            payload={
                "role": Role.ADMIN,
                "token": self._admin_token,
                "salt": salt.hex(),
            },
        )
        await self._ws.send(encode_message(auth_msg))

        raw = await asyncio.wait_for(self._ws.recv(), timeout=15)
        resp = decode_message(raw, self._cipher)
        if not resp or resp.type != MessageType.AUTH_OK:
            raise ConnectionError(f"Admin auth failed: {resp}")

        self._connected = True
        log.info("Admin authenticated to %s", self._server_uri)

    async def send(self, msg: Message) -> None:
        if not self._ws or not self._connected:
            raise RuntimeError("Not connected")
        await self._ws.send(encode_message(msg, self._cipher))

    async def recv(self) -> Message | None:
        """Receive one message (blocking)."""
        return await self._inbox.get()

    async def listen(self) -> None:
        """Background task: pump incoming messages into the queue."""
        try:
            async for raw in self._ws:
                msg = decode_message(raw, self._cipher)
                if msg:
                    await self._inbox.put(msg)
        except websockets.exceptions.ConnectionClosed:
            self._connected = False
            log.warning("Connection to server closed")

    async def close(self) -> None:
        self._connected = False
        if self._ws:
            await self._ws.close()

    @property
    def connected(self) -> bool:
        return self._connected
