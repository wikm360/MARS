"""
Command: execute
Run a shell command safely (no shell=True) and return stdout/stderr/exit_code.
"""
from __future__ import annotations
import asyncio
import logging
import shlex
import sys

from client.commands.registry import register

log = logging.getLogger(__name__)


@register("execute")
async def execute(payload: dict) -> dict:
    """
    payload:
        command (str): the command string to execute
        timeout (int): seconds to wait (default 30)
    """
    # "shell" is the shell command string; "command" is reserved for handler name
    command_str: str = payload.get("shell", payload.get("cmd", ""))
    timeout: int = int(payload.get("timeout", 30))

    if not command_str.strip():
        return {"error": "empty command"}

    try:
        if sys.platform == "win32":
            args = ["cmd.exe", "/c", command_str]
        else:
            args = ["/bin/sh", "-c", command_str]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"error": f"command timed out after {timeout}s"}

        return {
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "exit_code": proc.returncode,
        }
    except Exception as exc:
        log.exception("execute error")
        return {"error": str(exc)}
