"""
MRAS Server — asyncio WebSocket relay / C2.

Clients and admins both connect here; the server authenticates each role,
routes commands from admins to targeted clients, and forwards results back.
"""
from __future__ import annotations
import asyncio
import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

import websockets
import yaml

# Allow running directly from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.crypto import SessionCipher, derive_key
from shared.models import ClientInfo, Message, MessageType, Role
from shared.protocol import decode_message, encode_message
from server.auth import RateLimiter, verify_admin_token, verify_client_token
from server.storage import ClientRegistry

# ── Configuration ──────────────────────────────────────────────────────────

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    s = cfg["server"]
    s["secret"] = os.environ.get("MRAS_SECRET", s["secret"])
    s["admin_token"] = os.environ.get("MRAS_ADMIN_TOKEN", s["admin_token"])
    return cfg

# ── Logging ────────────────────────────────────────────────────────────────

def setup_logging(cfg: dict) -> None:
    lc = cfg["logging"]
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    level = getattr(logging, lc["level"].upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    fh = logging.handlers.RotatingFileHandler(
        lc["file"], maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(fmt))
    handlers.append(fh)
    logging.basicConfig(level=level, format=fmt, handlers=handlers)

log = logging.getLogger("mras.server")

# ── Server state ───────────────────────────────────────────────────────────

registry: ClientRegistry
rate_limiter: RateLimiter
CFG: dict

# ── Helpers ────────────────────────────────────────────────────────────────

async def send(ws, msg: Message, cipher: SessionCipher | None = None) -> None:
    try:
        await ws.send(encode_message(msg, cipher))
    except Exception as exc:
        log.debug("send error: %s", exc)


async def broadcast_admins(msg: Message) -> None:
    for admin_ws in registry.admin_websockets():
        await send(admin_ws, msg)

# ── Client handler ─────────────────────────────────────────────────────────

async def handle_client(ws) -> None:
    conn_id = id(ws)
    secret = CFG["server"]["secret"]
    cipher: SessionCipher | None = None
    client_id: str | None = None

    try:
        # --- Auth handshake ---
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        auth_msg = decode_message(raw)
        if not auth_msg or auth_msg.type != MessageType.AUTH:
            await send(ws, Message(MessageType.AUTH_FAIL, {"reason": "expected AUTH"}))
            return

        token = auth_msg.payload.get("token", "")
        if not verify_client_token(token, secret):
            await send(ws, Message(MessageType.AUTH_FAIL, {"reason": "bad token"}))
            log.warning("Client auth failed from %s", ws.remote_address)
            return

        # Establish session cipher using salt provided by client
        salt_hex = auth_msg.payload.get("salt", "")
        salt = bytes.fromhex(salt_hex) if salt_hex else None
        if salt:
            key, _ = derive_key(secret, salt)
            cipher = SessionCipher(key)

        client_id = auth_msg.payload.get("client_id", f"unknown-{conn_id}")
        info = ClientInfo(
            client_id=client_id,
            hostname=auth_msg.payload.get("hostname", "unknown"),
            os=auth_msg.payload.get("os", "unknown"),
            username=auth_msg.payload.get("username", "unknown"),
            ip=str(ws.remote_address[0]) if ws.remote_address else "unknown",
            connected_at=time.time(),
            last_seen=time.time(),
            version=auth_msg.payload.get("version", "1.0.0"),
        )
        registry.register_client(client_id, info, ws)
        await send(ws, Message(MessageType.AUTH_OK, {"session": "encrypted" if cipher else "plain"}), cipher)

        # Flush any pending commands
        for pending_msg in registry.dequeue_all(client_id):
            await send(ws, pending_msg, cipher)

        # Notify admins
        await broadcast_admins(Message(MessageType.CLIENT_CONNECTED, info.to_dict()))

        log.info("Client authenticated: %s", client_id)

        # --- Main loop ---
        async for raw in ws:
            if not rate_limiter.allow(str(conn_id)):
                log.warning("Rate limit hit for client %s", client_id)
                continue

            msg = decode_message(raw, cipher)
            if not msg:
                continue

            registry.update_last_seen(client_id, time.time())

            if msg.type == MessageType.PING:
                await send(ws, Message(MessageType.PONG), cipher)

            elif msg.type in (MessageType.COMMAND_RESULT, MessageType.COMMAND_ERROR,
                              MessageType.FILE_DATA):
                msg.sender_id = client_id
                await broadcast_admins(msg)

            else:
                log.debug("Unhandled message from client %s: %s", client_id, msg.type)

    except asyncio.TimeoutError:
        log.warning("Auth timeout for connection %s", conn_id)
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as exc:
        log.exception("Client handler error: %s", exc)
    finally:
        if client_id:
            registry.unregister_client(client_id)
            await broadcast_admins(Message(MessageType.CLIENT_DISCONNECTED, {"client_id": client_id}))
        rate_limiter.remove(str(conn_id))

# ── Admin handler ──────────────────────────────────────────────────────────

async def handle_admin(ws) -> None:
    conn_id = id(ws)
    secret = CFG["server"]["secret"]
    admin_token = CFG["server"]["admin_token"]
    cipher: SessionCipher | None = None

    try:
        # --- Auth ---
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        auth_msg = decode_message(raw)
        if not auth_msg or auth_msg.type != MessageType.AUTH:
            await send(ws, Message(MessageType.AUTH_FAIL, {"reason": "expected AUTH"}))
            return

        token = auth_msg.payload.get("token", "")
        if not verify_admin_token(token, admin_token):
            await send(ws, Message(MessageType.AUTH_FAIL, {"reason": "bad admin token"}))
            log.warning("Admin auth failed from %s", ws.remote_address)
            return

        salt_hex = auth_msg.payload.get("salt", "")
        salt = bytes.fromhex(salt_hex) if salt_hex else None
        if salt:
            key, _ = derive_key(secret, salt)
            cipher = SessionCipher(key)

        registry.add_admin(ws)
        await send(ws, Message(MessageType.AUTH_OK, {"role": "admin"}), cipher)
        log.info("Admin connected from %s", ws.remote_address)

        # Send current client list
        clients = [c.to_dict() for c in registry.list_clients()]
        await send(ws, Message(MessageType.CLIENT_LIST, {"clients": clients}), cipher)

        # --- Main loop ---
        async for raw in ws:
            if not rate_limiter.allow(f"admin-{conn_id}"):
                log.warning("Rate limit hit for admin")
                continue

            msg = decode_message(raw, cipher)
            if not msg:
                continue

            if msg.type == MessageType.PING:
                await send(ws, Message(MessageType.PONG), cipher)

            elif msg.type == MessageType.CLIENT_LIST:
                clients = [c.to_dict() for c in registry.list_clients()]
                await send(ws, Message(MessageType.CLIENT_LIST, {"clients": clients}), cipher)

            elif msg.type in (MessageType.COMMAND, MessageType.FILE_UPLOAD,
                              MessageType.FILE_DOWNLOAD, MessageType.UPDATE_SERVERS):
                target_id = msg.target_id
                if not target_id:
                    await send(ws, Message(MessageType.ERROR, {"reason": "missing target_id"}), cipher)
                    continue

                client_ws = registry.get_client_ws(target_id)
                if client_ws:
                    # Get client cipher (we re-derive; for simplicity use same secret)
                    await send(client_ws, msg)
                    log.info("Routed %s → %s", msg.type, target_id)
                else:
                    # Client offline: queue for later
                    queued = registry.enqueue(target_id, msg)
                    status = "queued" if queued else "dropped"
                    await send(ws, Message(MessageType.ERROR, {
                        "reason": f"client {target_id} offline — command {status}"
                    }), cipher)

            else:
                log.debug("Unhandled admin message: %s", msg.type)

    except asyncio.TimeoutError:
        log.warning("Admin auth timeout")
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as exc:
        log.exception("Admin handler error: %s", exc)
    finally:
        registry.remove_admin(ws)
        rate_limiter.remove(f"admin-{conn_id}")
        log.info("Admin disconnected")

# ── Routing ────────────────────────────────────────────────────────────────

async def router(ws) -> None:
    """Determine role from first message and dispatch."""
    try:
        # Peek at the first message without consuming it by using a queue trick
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    role = data.get("payload", {}).get("role", Role.CLIENT)

    # Re-inject the first message into a queue so handlers can read it
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(raw)

    class _PeekedWS:
        """Wraps a real websocket and prepends the already-read frame."""
        def __init__(self, real_ws, first_frame: str):
            self._ws = real_ws
            self._first = first_frame
            self._used = False
            self.remote_address = real_ws.remote_address

        async def recv(self):
            if not self._used:
                self._used = True
                return self._first
            return await self._ws.recv()

        async def send(self, data):
            return await self._ws.send(data)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return await self._ws.recv()
            except websockets.exceptions.ConnectionClosed:
                raise StopAsyncIteration

    peeked = _PeekedWS(ws, raw)

    if role == Role.ADMIN:
        await handle_admin(peeked)
    else:
        await handle_client(peeked)

# ── Entry point ────────────────────────────────────────────────────────────

async def main() -> None:
    global registry, rate_limiter, CFG
    CFG = load_config()
    setup_logging(CFG)
    registry = ClientRegistry(max_pending=CFG["server"]["max_pending_commands"])
    rate_limiter = RateLimiter(rate=CFG["server"]["rate_limit_per_second"])

    host = CFG["server"]["host"]
    port = CFG["server"]["port"]
    log.info("MRAS Server starting on %s:%d", host, port)

    async with websockets.serve(router, host, port, ping_interval=20, ping_timeout=30):
        log.info("Server ready. Waiting for connections...")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped.")
