"""
Command: sysinfo
Return detailed system information (OS, CPU, RAM, disk, network).
"""
from __future__ import annotations
import platform
import socket

from client.commands.registry import register


@register("sysinfo")
async def sysinfo(payload: dict) -> dict:
    info: dict = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
    }

    try:
        import psutil
        info["cpu_count"] = psutil.cpu_count()
        info["cpu_percent"] = psutil.cpu_percent(interval=0.5)
        vm = psutil.virtual_memory()
        info["ram_total_mb"] = round(vm.total / 1024 / 1024)
        info["ram_used_mb"] = round(vm.used / 1024 / 1024)
        info["ram_percent"] = vm.percent
        disk = psutil.disk_usage("/")
        info["disk_total_gb"] = round(disk.total / 1024 / 1024 / 1024, 1)
        info["disk_used_gb"] = round(disk.used / 1024 / 1024 / 1024, 1)
        info["disk_percent"] = disk.percent
        info["boot_time"] = psutil.boot_time()
    except ImportError:
        info["psutil"] = "not installed — install for detailed stats"

    try:
        info["local_ip"] = socket.gethostbyname(socket.gethostname())
    except Exception:
        pass

    return info
