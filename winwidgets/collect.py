"""Read the numbers the widgets show.

One collector, one snapshot per tick, so every widget in a frame agrees with
every other one - two widgets asking ``cpu_percent()`` separately would show
two different numbers in the same frame.

``psutil`` is used when it is there; without it the collector still returns a
usable snapshot on Linux (``/proc``) and on Windows (ctypes), so the board is
never blank.  History is kept here too: sparklines need the last N samples.
"""

from __future__ import annotations

import os
import platform
import socket
import time
from dataclasses import dataclass, field

try:                                            # pragma: no cover - optional
    import psutil
except Exception:                               # pragma: no cover
    psutil = None

try:                                            # pragma: no cover - Windows only
    from .winapi import WindowsSampler, available as _winapi_available
except Exception:                               # pragma: no cover
    WindowsSampler = None
    _winapi_available = lambda: False           # noqa: E731


def backend() -> str:
    """Which source the board is using - shown by ``--list`` and the snapshot."""
    if psutil is not None:
        return "psutil"
    if _winapi_available():
        return "windows-api"
    return "proc" if os.path.isdir("/proc") else "none"

HISTORY = 96            # samples kept per series (~8 minutes at 5 s)


# --------------------------------------------------------------------------- #
# snapshot
# --------------------------------------------------------------------------- #
@dataclass
class Proc:
    name: str
    cpu: float          # percent, can exceed 100 on multi-core
    mem: float          # percent of RAM


@dataclass
class Snapshot:
    t: float
    cpu: float = 0.0
    cpu_cores: list = field(default_factory=list)
    cores: int = 0
    freq: float = 0.0                       # MHz, 0 when unknown
    mem_percent: float = 0.0
    mem_used: float = 0.0                   # GiB
    mem_total: float = 0.0
    swap_percent: float = 0.0
    disks: list = field(default_factory=list)   # (mount, used GiB, total GiB, percent)
    net_down: float = 0.0                   # MiB/s
    net_up: float = 0.0
    net_total: tuple = (0.0, 0.0)           # GiB (recv, sent)
    net_known: bool = True                  # False: no honest source available
    procs: list = field(default_factory=list)
    proc_count: int = 0
    battery: float | None = None            # percent
    plugged: bool | None = None
    uptime: float = 0.0                     # seconds
    host: str = ""
    os_name: str = ""


class Collector:
    """Samples the machine and keeps a short history for the sparklines."""

    def __init__(self, history=HISTORY):
        self.history = history
        self.h_cpu = []
        self.h_mem = []
        self.h_down = []
        self.h_up = []
        self._last_net = None
        self._last_t = None
        self._last_proc_cpu = {}
        self._boot = self._boot_time()
        self._cpu_freq_warned = False
        self.sampler = None
        if psutil is None and _winapi_available():      # Windows, no psutil
            self.sampler = WindowsSampler()
        if psutil is not None:
            # first call is the baseline for every later percentage
            psutil.cpu_percent(interval=None, percpu=True)
            psutil.cpu_percent(interval=None)
        self.sample = Snapshot(t=time.time(), host=socket.gethostname(),
                               os_name=_os_name())
        self.tick()                              # seed the first frame

    # -- internals --------------------------------------------------------- #
    def _boot_time(self):
        if psutil is not None:
            try:
                return psutil.boot_time()
            except Exception:
                pass
        try:
            with open("/proc/uptime") as fh:      # Linux fallback
                return time.time() - float(fh.read().split()[0])
        except Exception:
            return time.time()

    def _disks(self):
        out = []
        if psutil is not None:
            seen = set()
            for part in psutil.disk_partitions(all=False):
                if part.fstype in ("", "squashfs", "tmpfs", "overlay", "devtmpfs"):
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                except (PermissionError, OSError):
                    continue
                if usage.total <= 0 or part.mountpoint in seen:
                    continue
                seen.add(part.mountpoint)
                out.append((part.mountpoint,
                            usage.used / 2**30, usage.total / 2**30, usage.percent))
        elif self.sampler is not None:           # pragma: no cover - Windows only
            out = self.sampler.disks()
        elif hasattr(os, "statvfs"):
            try:
                st = os.statvfs("/")
                total = st.f_blocks * st.f_frsize
                free = st.f_bavail * st.f_frsize
                used = total - free
                pct = 100.0 * used / total if total else 0.0
                out.append(("/", used / 2**30, total / 2**30, pct))
            except OSError:
                pass
        return out

    def _processes(self, count=6):
        if psutil is None:
            return []
        rows = []
        for proc in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
            try:
                info = proc.info
                name = (info.get("name") or "?").strip()
                cpu = info.get("cpu_percent") or 0.0
                mem = info.get("memory_percent") or 0.0
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            rows.append(Proc(name[:22], float(cpu), float(mem)))
        rows.sort(key=lambda p: (p.cpu, p.mem), reverse=True)
        return rows[:count]

    # -- the tick ---------------------------------------------------------- #
    def tick(self) -> Snapshot:
        now = time.time()
        snap = Snapshot(t=now, host=self.sample.host, os_name=self.sample.os_name)

        if psutil is not None:
            try:
                snap.cpu_cores = [float(v) for v in psutil.cpu_percent(interval=None, percpu=True)]
                snap.cpu = float(psutil.cpu_percent(interval=None))
            except Exception:
                snap.cpu_cores = []
            if not snap.cpu_cores:
                snap.cpu_cores = [snap.cpu]
            snap.cores = len(snap.cpu_cores)
            try:
                freq = psutil.cpu_freq()
                snap.freq = float(freq.current) if freq else 0.0
            except Exception:
                snap.freq = 0.0

            vm = psutil.virtual_memory()
            snap.mem_percent = float(vm.percent)
            snap.mem_total = vm.total / 2**30
            snap.mem_used = (vm.total - vm.available) / 2**30
            try:
                snap.swap_percent = float(psutil.swap_memory().percent)
            except Exception:
                pass

            try:
                counters = psutil.net_io_counters()
            except Exception:
                counters = None
            if counters is not None:
                snap.net_total = (counters.bytes_recv / 2**30, counters.bytes_sent / 2**30)
                if self._last_net is not None and self._last_t is not None:
                    dt = max(now - self._last_t, 1e-3)
                    snap.net_down = max(0.0, (counters.bytes_recv - self._last_net[0]) / dt) / 2**20
                    snap.net_up = max(0.0, (counters.bytes_sent - self._last_net[1]) / dt) / 2**20
                self._last_net = (counters.bytes_recv, counters.bytes_sent)
                self._last_t = now

            try:
                battery = psutil.sensors_battery()
            except Exception:
                battery = None
            if battery is not None:
                snap.battery = float(battery.percent)
                snap.plugged = bool(battery.power_plugged)

            try:
                snap.proc_count = len(psutil.pids())
            except Exception:
                snap.proc_count = 0
        elif self.sampler is not None:            # pragma: no cover - Windows only
            total, per_core = self.sampler.cpu()
            snap.cpu = total
            snap.cpu_cores = per_core
            snap.cores = len(per_core) or (os.cpu_count() or 1)
            pct, used, total_gib, swap = self.sampler.memory()
            snap.mem_percent, snap.mem_used, snap.mem_total = pct, used, total_gib
            snap.swap_percent = swap
            snap.battery, snap.plugged = self.sampler.battery()
            snap.uptime = self.sampler.uptime()
            procs = self.sampler.processes()
            snap.procs = [Proc(name[:22], cpu, mem) for name, cpu, mem in procs]
            snap.proc_count = len(procs)
            snap.net_known = self.sampler.net() is not None
        else:                                    # pragma: no cover - fallback path
            try:
                snap.cpu = float(os.getloadavg()[0] / (os.cpu_count() or 1) * 100.0)
            except Exception:
                snap.cpu = 0.0
            snap.cpu_cores = [snap.cpu]
            snap.cores = os.cpu_count() or 1

        snap.disks = snap.disks or self._disks()
        if not snap.procs and self.sampler is None:
            snap.procs = self._processes()
        if not snap.uptime:
            snap.uptime = max(0.0, now - self._boot)
        if snap.net_known and self.sampler is not None:
            snap.net_known = False

        # history
        self.h_cpu = (self.h_cpu + [snap.cpu])[-self.history:]
        self.h_mem = (self.h_mem + [snap.mem_percent])[-self.history:]
        scale = max(1.0, snap.net_down + snap.net_up)
        self.h_down = (self.h_down + [snap.net_down / scale * 100.0])[-self.history:]
        self.h_up = (self.h_up + [snap.net_up / scale * 100.0])[-self.history:]

        self.sample = snap
        return snap

    # -- convenience ------------------------------------------------------- #
    def reset(self):
        self.h_cpu.clear()
        self.h_mem.clear()
        self.h_down.clear()
        self.h_up.clear()
        self.tick()


def _os_name():
    try:
        if os.name == "nt":
            release = platform.release()
            build = platform.version().split(".")[-1]
            name = "Windows 11" if release == "10" and build.isdigit() and int(build) >= 22000 else f"Windows {release}"
            return name
        return platform.system()
    except Exception:                            # pragma: no cover
        return platform.system() or "?"
