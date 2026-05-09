"""
Command: update_client
Receives a new executable from the admin, replaces the current binary, and restarts.

Flow:
  1. Admin uploads new exe (base64) via web panel or CLI
  2. Client saves it to a temp path
  3. Client creates a small batch/shell script that:
       - waits for current process to exit (ping delay on Windows, sleep on Unix)
       - copies new exe over the old one
       - starts the new exe
       - deletes the update script itself
  4. Client launches the script detached and calls os._exit(0)
"""
from __future__ import annotations
import asyncio
import base64
import logging
import os
import sys
import tempfile
from pathlib import Path

from client.commands.registry import register

log = logging.getLogger(__name__)


@register("update_client")
async def update_client(payload: dict) -> dict:
    data_b64: str = payload.get("data", "")
    new_version: str = payload.get("version", "unknown")

    if not data_b64:
        return {"error": "no executable data provided"}

    try:
        new_exe_bytes = base64.b64decode(data_b64)
    except Exception:
        return {"error": "invalid base64 data"}

    if len(new_exe_bytes) < 1024:
        return {"error": "received data too small to be a valid executable"}

    if getattr(sys, "frozen", False):
        current_exe = Path(sys.executable).resolve()
    else:
        return {"error": "update only works on compiled binary (PyInstaller exe)"}

    tmp_dir = Path(tempfile.gettempdir())
    new_exe_tmp = tmp_dir / f"mars_update_{os.getpid()}.exe"

    try:
        new_exe_tmp.write_bytes(new_exe_bytes)
        log.info("New exe saved to %s (%d bytes)", new_exe_tmp, len(new_exe_bytes))
    except Exception as exc:
        return {"error": f"failed to write new exe: {exc}"}

    asyncio.create_task(_apply_update(current_exe, new_exe_tmp, new_version))
    return {
        "status": "updating",
        "version": new_version,
        "message": f"Update to v{new_version} received. Client will restart in ~5 seconds.",
    }


async def _apply_update(current_exe: Path, new_exe_tmp: Path, version: str) -> None:
    """Wait briefly so the result can be sent, then swap and restart."""
    await asyncio.sleep(2)
    log.info("Applying update → %s", current_exe)

    if sys.platform == "win32":
        _windows_swap(current_exe, new_exe_tmp)
    else:
        _unix_swap(current_exe, new_exe_tmp)

    os._exit(0)


def _windows_swap(current_exe: Path, new_exe_tmp: Path) -> None:
    """
    Windows cannot replace a running exe directly.
    Use a cmd script with a ping-delay to do the swap after we exit.
    """
    import subprocess
    tmp_dir = Path(tempfile.gettempdir())
    script = tmp_dir / "mars_update.bat"
    script.write_text(
        "@echo off\n"
        "ping 127.0.0.1 -n 6 > nul\n"                          # ~5s delay
        f'copy /y "{new_exe_tmp}" "{current_exe}"\n'
        f'start "" "{current_exe}"\n'
        f'del /f /q "{new_exe_tmp}"\n'
        'del /f /q "%~f0"\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd.exe", "/c", str(script)],
        creationflags=0x08000000,   # CREATE_NO_WINDOW
        close_fds=True,
    )


def _unix_swap(current_exe: Path, new_exe_tmp: Path) -> None:
    """On Unix: overwrite directly (file can be replaced while running)."""
    import shutil, subprocess
    try:
        shutil.copy2(str(new_exe_tmp), str(current_exe))
        current_exe.chmod(0o755)
        new_exe_tmp.unlink(missing_ok=True)
        subprocess.Popen([str(current_exe)], close_fds=True)
    except Exception as exc:
        log.error("Unix update swap failed: %s", exc)
