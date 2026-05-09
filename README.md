# MRAS — Modular Remote Admin System

A professional, modular, production-ready remote administration framework built in Python.

## Architecture

```
MRAS/
├── shared/          # Protocol, crypto, data models (used by all components)
│   ├── models.py    # Message types, ClientInfo, enums
│   ├── crypto.py    # Fernet-based session encryption
│   └── protocol.py  # Wire encode/decode helpers
│
├── server/          # C2 Relay Server (asyncio WebSocket)
│   ├── main.py      # Entry point, connection router
│   ├── auth.py      # Token verification, rate limiting
│   ├── storage.py   # Client registry, pending queue
│   └── config.yaml
│
├── client/          # Remote Agent
│   ├── main.py      # Entry point, config update loop
│   ├── connection.py # WebSocket connect/failover/heartbeat
│   ├── config.yaml
│   └── commands/    # Command Pattern — drop files here to add commands
│       ├── registry.py       # Register + auto-discover
│       ├── execute.py        # Shell command execution
│       ├── screenshot.py     # Screen capture (mss + Pillow)
│       ├── file_transfer.py  # Upload / download / list_dir
│       ├── change_server.py  # Runtime server list update
│       └── sysinfo.py        # System information
│
└── admin/           # Admin Control Panel (Rich CLI)
    ├── main.py      # Interactive REPL
    ├── connection.py # Authenticated WebSocket connection
    └── config.yaml
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure secrets

Edit `server/config.yaml` and set a strong `secret` and `admin_token`, OR use environment variables:

```bash
export MRAS_SECRET="your-strong-shared-secret"
export MRAS_ADMIN_TOKEN="your-admin-token"
```

Apply the same `MRAS_SECRET` on client and admin machines.

### 3. Start the server

```bash
python -m server.main
# or
python server/main.py
```

### 4. Start a client

```bash
python client/main.py
```

### 5. Open the admin panel

```bash
python admin/main.py [--server ws://host:8765] [--token TOKEN]
```

## Admin Panel Commands

| Command | Description |
|---------|-------------|
| `list` | List all connected clients |
| `select <id>` | Select a target client (prefix match) |
| `exec <cmd>` | Execute shell command on target |
| `screenshot [--jpeg]` | Capture screenshot; saved to `downloads/` |
| `download <path>` | Download a file from the client |
| `upload <local> <remote>` | Upload a file to the client |
| `sysinfo` | Get system information |
| `dir [path]` | List directory on client |
| `servers <uri> [...]` | Push a new server list to the client |
| `redirect <uri>` | Tell client to reconnect to a new server |
| `help` | Show help |
| `quit` | Exit |

## Adding New Commands

1. Create `client/commands/mycommand.py`
2. Import the registry and decorate your handler:

```python
from client.commands.registry import register

@register("my_command")
async def my_command(payload: dict) -> dict:
    # payload contains parameters sent by the admin
    return {"result": "done"}
```

3. That's it — the module is auto-discovered on startup.

## Security Features

- **Encrypted channel**: All post-auth messages are Fernet-encrypted (AES-128-CBC + HMAC-SHA256) using a key derived from the shared secret via PBKDF2 (100,000 iterations).
- **Token authentication**: Clients use an HMAC-derived token; admins use a separate admin token.
- **Salt per session**: A fresh random 16-byte salt is generated each connection, so session keys are unique.
- **Rate limiting**: Per-connection message rate limiter prevents flooding.
- **No shell injection**: `execute` command uses `subprocess` with a shell wrapper (`/bin/sh -c` or `cmd.exe /c`) — never raw `shell=True` with unsanitised input piped to dangerous ops.
- **File size guard**: Downloads are capped at 50 MB by default.

## Failover & Resilience

- Client holds an **ordered list of servers** in `config.yaml`.
- On disconnect, it cycles through all servers with **exponential backoff** (default 5s → up to 60s).
- Admin can **push a new server list** (`servers` command) or **redirect** the client instantly.
- Admin can configure a **remote config URL** (`config_update_url`) for DNS/config-based failover.
- Server stores commands in a **pending queue** for offline clients (up to 100 entries).

## Production Checklist

- [ ] Change `secret` and `admin_token` in both server and admin configs.
- [ ] Run behind a TLS reverse proxy (nginx/caddy) and use `wss://` URIs.
- [ ] Set `MRAS_SECRET` and `MRAS_ADMIN_TOKEN` via environment variables, not config files.
- [ ] Run server with a process supervisor (systemd, supervisor, PM2).
- [ ] Review log rotation settings (`logs/` directory).
- [ ] Build client as a standalone binary with PyInstaller for deployment:
  ```bash
  pyinstaller --onefile --noconsole client/main.py
  ```
