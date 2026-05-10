"""
Path helpers — works correctly both as .py source and frozen PyInstaller exe.

PyInstaller --onefile extracts bundled files to a temp _MEIxxxxxx folder.
sys._MEIPASS  → temp extraction dir  (read-only bundled resources)
sys.executable → the actual .exe path (use its parent for user-writable files)

Rule:
  - Config / state files (config.yaml, .client_id, overrides) → next to the exe
  - Python imports / bundled libs                              → sys._MEIPASS (auto)
"""
from __future__ import annotations
import sys
from pathlib import Path


def _exe_dir() -> Path:
    """Directory that contains the running exe (or project root for .py)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # Running as plain Python: project root is two levels above this file
    return Path(__file__).resolve().parents[1]


# The single source of truth used everywhere in the client
EXE_DIR: Path = _exe_dir()


def config_path() -> Path:
    """
    Locate client/config.yaml.
    Search order:
      1. Next to the exe (user-editable, preferred)
      2. Bundled inside the PyInstaller archive (sys._MEIPASS) as fallback
    """
    external = EXE_DIR / "client" / "config.yaml"
    if external.exists():
        return external
    # Fallback: config bundled inside the exe via PyInstaller datas
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        bundled = meipass / "client" / "config.yaml"
        if bundled.exists():
            return bundled
    return external  # not found — caller will raise a clear error


def client_id_path() -> Path:
    """Persistent unique client ID file."""
    return EXE_DIR / "client" / ".client_id"


def servers_override_path() -> Path:
    """Runtime server-list override written by change_server command."""
    return EXE_DIR / "client" / "servers_override.json"


def log_dir() -> Path:
    """Directory for log files."""
    d = EXE_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
