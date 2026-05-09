"""
Commands: upload_file, download_file
Safe file transfers between admin and client.
"""
from __future__ import annotations
import base64
import logging
import os
from pathlib import Path

from client.commands.registry import register

log = logging.getLogger(__name__)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB guard


@register("download_file")
async def download_file(payload: dict) -> dict:
    """
    Admin requests a file from the client.
    payload:
        path (str): absolute or relative path on the client machine
    """
    raw_path: str = payload.get("path", "")
    if not raw_path:
        return {"error": "path is required"}

    path = Path(raw_path).resolve()
    if not path.exists():
        return {"error": f"file not found: {path}"}
    if not path.is_file():
        return {"error": f"not a file: {path}"}

    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        return {"error": f"file too large ({size} bytes); limit is {MAX_FILE_SIZE}"}

    data = base64.b64encode(path.read_bytes()).decode()
    return {"filename": path.name, "path": str(path), "data": data, "size": size}


@register("upload_file")
async def upload_file(payload: dict) -> dict:
    """
    Admin sends a file to be saved on the client.
    payload:
        path     (str): destination path on client
        data     (str): base64-encoded file content
        overwrite (bool): allow overwriting existing file (default False)
    """
    dest_path_str: str = payload.get("path", "")
    data_b64: str = payload.get("data", "")
    overwrite: bool = bool(payload.get("overwrite", False))

    if not dest_path_str or not data_b64:
        return {"error": "path and data are required"}

    dest = Path(dest_path_str).resolve()
    if dest.exists() and not overwrite:
        return {"error": f"file already exists: {dest}. Use overwrite=true to replace."}

    try:
        file_bytes = base64.b64decode(data_b64)
    except Exception:
        return {"error": "invalid base64 data"}

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(file_bytes)
    log.info("File written: %s (%d bytes)", dest, len(file_bytes))
    return {"saved": str(dest), "size": len(file_bytes)}


@register("list_dir")
async def list_dir(payload: dict) -> dict:
    """List directory contents."""
    raw_path: str = payload.get("path", ".")
    path = Path(raw_path).resolve()
    if not path.exists():
        return {"error": f"path not found: {path}"}
    if not path.is_dir():
        return {"error": f"not a directory: {path}"}

    entries = []
    for entry in sorted(path.iterdir()):
        try:
            stat = entry.stat()
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": stat.st_size if entry.is_file() else 0,
                "modified": stat.st_mtime,
            })
        except PermissionError:
            entries.append({"name": entry.name, "type": "unknown", "error": "permission denied"})

    return {"path": str(path), "entries": entries}
