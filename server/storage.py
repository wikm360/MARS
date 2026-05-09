"""
In-memory registry of connected clients, admins, ciphers, and pending queues.
Thread-safe via asyncio — all access is from the event loop.
"""
from __future__ import annotations
import logging
from collections import defaultdict, deque
from typing import Optional, TYPE_CHECKING

from shared.models import ClientInfo, Message

if TYPE_CHECKING:
    from shared.crypto import SessionCipher

log = logging.getLogger(__name__)


class ClientRegistry:
    def __init__(self, max_pending: int = 100) -> None:
        self._clients: dict[str, ClientInfo] = {}
        self._client_ws: dict[str, object] = {}        # client_id → ws
        self._client_cipher: dict[str, object] = {}    # client_id → SessionCipher|None
        self._pending: dict[str, deque[Message]] = defaultdict(deque)
        self._max_pending = max_pending
        self._admin_ws: dict[int, object] = {}         # id(ws) → ws
        self._admin_cipher: dict[int, object] = {}     # id(ws) → SessionCipher|None

    # ── Client management ──────────────────────────────────────────────────

    def register_client(self, client_id: str, info: ClientInfo,
                        ws: object, cipher=None) -> None:
        self._clients[client_id] = info
        self._client_ws[client_id] = ws
        self._client_cipher[client_id] = cipher
        log.info("Client registered: %s (%s@%s)", client_id, info.username, info.ip)

    def unregister_client(self, client_id: str) -> Optional[ClientInfo]:
        info = self._clients.pop(client_id, None)
        self._client_ws.pop(client_id, None)
        self._client_cipher.pop(client_id, None)
        if info:
            log.info("Client disconnected: %s", client_id)
        return info

    def get_client_ws(self, client_id: str) -> object | None:
        return self._client_ws.get(client_id)

    def get_client_cipher(self, client_id: str):
        return self._client_cipher.get(client_id)

    def update_last_seen(self, client_id: str, ts: float) -> None:
        if client_id in self._clients:
            self._clients[client_id].last_seen = ts

    def list_clients(self) -> list[ClientInfo]:
        return list(self._clients.values())

    def client_ids(self) -> list[str]:
        return list(self._clients.keys())

    # ── Pending queue ──────────────────────────────────────────────────────

    def enqueue(self, client_id: str, msg: Message) -> bool:
        q = self._pending[client_id]
        if len(q) >= self._max_pending:
            log.warning("Pending queue full for %s, dropping command", client_id)
            return False
        q.append(msg)
        return True

    def dequeue_all(self, client_id: str) -> list[Message]:
        q = self._pending.pop(client_id, deque())
        return list(q)

    # ── Admin management ───────────────────────────────────────────────────

    def add_admin(self, ws: object, cipher=None) -> None:
        self._admin_ws[id(ws)] = ws
        self._admin_cipher[id(ws)] = cipher

    def remove_admin(self, ws: object) -> None:
        key = id(ws)
        self._admin_ws.pop(key, None)
        self._admin_cipher.pop(key, None)

    def admin_items(self) -> list[tuple[object, object]]:
        """Return list of (ws, cipher) for all connected admins."""
        return [
            (ws, self._admin_cipher.get(key))
            for key, ws in self._admin_ws.items()
        ]
