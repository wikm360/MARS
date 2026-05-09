"""
Commands: monitor_stats, list_processes, kill_process
Real-time monitoring data for the admin panel.
"""
from __future__ import annotations
import logging
import os
import time

from client.commands.registry import register

log = logging.getLogger(__name__)

# Cached previous net/disk counters for calculating per-second rates
_prev_net   = {"t": 0.0, "sent": 0, "recv": 0}
_prev_disk  = {"t": 0.0, "read": 0, "write": 0}


@register("monitor_stats")
async def monitor_stats(payload: dict) -> dict:
    import psutil
    global _prev_net, _prev_disk
    now = time.monotonic()

    cpu = psutil.cpu_percent(interval=None)
    cpu_cores = psutil.cpu_percent(interval=None, percpu=True)

    vm = psutil.virtual_memory()

    try:
        du = psutil.disk_usage("C:\\" if os.name == "nt" else "/")
    except Exception:
        du = None

    nc = psutil.net_io_counters()
    dt_net = now - _prev_net["t"] if _prev_net["t"] else 1.0
    net_sent_kb = max(0, (nc.bytes_sent - _prev_net["sent"]) / 1024 / max(dt_net, 0.1))
    net_recv_kb = max(0, (nc.bytes_recv - _prev_net["recv"]) / 1024 / max(dt_net, 0.1))
    _prev_net = {"t": now, "sent": nc.bytes_sent, "recv": nc.bytes_recv}

    try:
        dc = psutil.disk_io_counters()
        dt_disk = now - _prev_disk["t"] if _prev_disk["t"] else 1.0
        disk_read_kb  = max(0, (dc.read_bytes  - _prev_disk["read"])  / 1024 / max(dt_disk, 0.1))
        disk_write_kb = max(0, (dc.write_bytes - _prev_disk["write"]) / 1024 / max(dt_disk, 0.1))
        _prev_disk = {"t": now, "read": dc.read_bytes, "write": dc.write_bytes}
    except Exception:
        disk_read_kb = disk_write_kb = 0.0

    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status", "username"]):
        try:
            mi = p.info["memory_info"]
            procs.append({
                "pid":    p.info["pid"],
                "name":   p.info["name"] or "?",
                "cpu":    round(p.info["cpu_percent"] or 0, 1),
                "ram_mb": round(mi.rss / 1024 / 1024, 1) if mi else 0,
                "status": p.info["status"] or "?",
                "user":   (p.info["username"] or "")[:20],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    procs.sort(key=lambda x: x["cpu"], reverse=True)

    return {
        "cpu_percent":   round(cpu, 1),
        "cpu_cores":     [round(c, 1) for c in (cpu_cores or [])],
        "ram_percent":   round(vm.percent, 1),
        "ram_used_mb":   round(vm.used / 1024 / 1024, 1),
        "ram_total_mb":  round(vm.total / 1024 / 1024, 1),
        "disk_percent":  round(du.percent, 1) if du else 0,
        "disk_used_gb":  round(du.used / 1024**3, 2) if du else 0,
        "disk_total_gb": round(du.total / 1024**3, 2) if du else 0,
        "net_sent_kb":   round(net_sent_kb, 1),
        "net_recv_kb":   round(net_recv_kb, 1),
        "disk_read_kb":  round(disk_read_kb, 1),
        "disk_write_kb": round(disk_write_kb, 1),
        "top_processes": procs[:12],
        "ts": time.time(),
    }


@register("list_processes")
async def list_processes(payload: dict) -> dict:
    import psutil
    sort_by = payload.get("sort", "cpu")
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info",
                                   "status", "username", "create_time", "cmdline"]):
        try:
            mi = p.info["memory_info"]
            procs.append({
                "pid":      p.info["pid"],
                "name":     p.info["name"] or "?",
                "cpu":      round(p.info["cpu_percent"] or 0, 1),
                "ram_mb":   round(mi.rss / 1024 / 1024, 1) if mi else 0,
                "status":   p.info["status"] or "?",
                "user":     (p.info["username"] or "")[:24],
                "started":  round(p.info.get("create_time") or 0),
                "cmdline":  " ".join(p.info.get("cmdline") or [])[:120],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    reverse = sort_by in ("cpu", "ram_mb")
    procs.sort(key=lambda x: x.get(sort_by, 0), reverse=reverse)
    return {"processes": procs, "count": len(procs)}


@register("kill_process")
async def kill_process(payload: dict) -> dict:
    import psutil
    pid = payload.get("pid")
    if not pid:
        return {"error": "pid required"}
    try:
        p = psutil.Process(int(pid))
        name = p.name()
        p.terminate()
        return {"killed": True, "pid": pid, "name": name}
    except psutil.NoSuchProcess:
        return {"error": f"Process {pid} not found"}
    except psutil.AccessDenied:
        return {"error": f"Access denied to kill process {pid}"}
    except Exception as exc:
        return {"error": str(exc)}
