"""
MRAS Admin Web Server — FastAPI + WebSocket bridge.

Acts as a proxy between the browser UI and the MRAS C2 server:
  Browser <──WS──> FastAPI (this) <──WS──> MRAS Server

Run:
    python admin/web_server.py [--host 127.0.0.1] [--port 8080]
    Then open: http://127.0.0.1:8080
"""
from __future__ import annotations
import asyncio
import json
import logging
import logging.handlers
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.connection import AdminConnection
from shared.models import Message, MessageType

# ── Config ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    p = Path(__file__).parent / "config.yaml"
    with open(p) as f:
        cfg = yaml.safe_load(f)
    cfg["admin"]["secret"] = os.environ.get("MRAS_SECRET", cfg["admin"]["secret"])
    cfg["admin"]["admin_token"] = os.environ.get("MRAS_ADMIN_TOKEN", cfg["admin"]["admin_token"])
    return cfg

def setup_logging(cfg: dict) -> None:
    Path("logs").mkdir(exist_ok=True)
    lc = cfg["logging"]
    level = getattr(logging, lc["level"].upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    fh = logging.handlers.RotatingFileHandler(lc["file"], maxBytes=5*1024*1024, backupCount=3)
    fh.setFormatter(logging.Formatter(fmt))
    logging.basicConfig(level=level, format=fmt, handlers=[fh, logging.StreamHandler()])

log = logging.getLogger("mras.webserver")

# ── App state ────────────────────────────────────────────────────────────────

CFG: dict = {}
mras_conn: AdminConnection | None = None
browser_clients: set[WebSocket] = set()

# Tracks pending command futures: msg_id → Future
pending: dict[str, asyncio.Future] = {}

# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(title="MRAS Admin Panel", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"

@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Browser WebSocket endpoint ───────────────────────────────────────────────

@app.websocket("/ws")
async def browser_ws(ws: WebSocket):
    await ws.accept()
    browser_clients.add(ws)
    log.info("Browser connected (total: %d)", len(browser_clients))

    # Send current state snapshot
    if mras_conn and mras_conn.connected:
        await ws.send_text(json.dumps({"type": "server_status", "connected": True}))
        # Request fresh client list
        await mras_conn.send(Message(type=MessageType.CLIENT_LIST, payload={}))
    else:
        await ws.send_text(json.dumps({"type": "server_status", "connected": False}))

    try:
        async for raw in ws.iter_text():
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await handle_browser_message(ws, data)
    except WebSocketDisconnect:
        pass
    finally:
        browser_clients.discard(ws)
        log.info("Browser disconnected (total: %d)", len(browser_clients))


async def handle_browser_message(ws: WebSocket, data: dict) -> None:
    """Route messages from the browser to the MRAS server."""
    if not mras_conn or not mras_conn.connected:
        await ws.send_text(json.dumps({"type": "error", "message": "Not connected to MRAS server"}))
        return

    msg_type_str = data.get("type", "")
    target_id = data.get("target_id")
    payload = data.get("payload", {})
    msg_id = data.get("msg_id") or str(uuid.uuid4())
    payload["msg_id"] = msg_id

    try:
        msg_type = MessageType(msg_type_str)
    except ValueError:
        await ws.send_text(json.dumps({"type": "error", "message": f"Unknown type: {msg_type_str}"}))
        return

    msg = Message(type=msg_type, payload=payload, target_id=target_id, msg_id=msg_id)
    await mras_conn.send(msg)


# ── Broadcast to all browsers ─────────────────────────────────────────────────

async def broadcast(data: dict) -> None:
    raw = json.dumps(data)
    dead = set()
    for ws in list(browser_clients):
        try:
            await ws.send_text(raw)
        except Exception:
            dead.add(ws)
    browser_clients.difference_update(dead)


# ── MRAS server listener ─────────────────────────────────────────────────────

async def mras_listen_loop() -> None:
    """Forward messages from the MRAS server to all connected browsers."""
    while True:
        if not mras_conn or not mras_conn.connected:
            await asyncio.sleep(2)
            continue
        msg = await mras_conn.recv()
        if msg is None:
            continue

        data = msg.to_dict()
        data["type"] = msg.type.value

        # Resolve pending futures (for direct command-response tracking)
        mid = msg.payload.get("msg_id") or msg.msg_id
        fut = pending.pop(mid, None)
        if fut and not fut.done():
            fut.set_result(msg)

        await broadcast(data)


# ── MRAS reconnect loop ───────────────────────────────────────────────────────

async def mras_reconnect_loop() -> None:
    global mras_conn
    delay = 3
    while True:
        try:
            mras_conn = AdminConnection(CFG)
            await mras_conn.connect()
            log.info("Connected to MRAS server")
            await broadcast({"type": "server_status", "connected": True})
            delay = 3
            # Start listening
            await asyncio.gather(
                mras_conn.listen(),
                mras_listen_loop(),
            )
        except Exception as exc:
            log.warning("MRAS server connection failed: %s — retry in %ds", exc, delay)
            await broadcast({"type": "server_status", "connected": False, "error": str(exc)})
            if mras_conn:
                try:
                    await mras_conn.close()
                except Exception:
                    pass
                mras_conn = None
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    asyncio.create_task(mras_reconnect_loop())
    log.info("MRAS Web Server started")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MRAS Admin Web Panel")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--mras-server", help="Override MRAS server URI")
    parser.add_argument("--open", action="store_true", help="Open browser on startup")
    args = parser.parse_args()

    global CFG
    CFG = load_config()
    setup_logging(CFG)

    if args.mras_server:
        CFG["admin"]["server"] = args.mras_server

    if args.open:
        import webbrowser, threading
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()

    log.info("Starting web panel on http://%s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
