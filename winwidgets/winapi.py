"""Windows system data straight from the Win32 API - no psutil needed.

Why this exists: on a brand new Python (3.15, 3.14, ...) ``pip install psutil``
has no ready-made wheel yet and tries to compile itself, which needs Visual
Studio Build Tools.  That is a silly reason for a widget board not to start.

So this module reads the same numbers with ``ctypes`` and documented Win32
structures:

===================  ==========================================================
what                 how
===================  ==========================================================
CPU total + per core ``NtQuerySystemInformation`` class 8
                     (``SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION``) - the same
                     call psutil uses, deltas of idle/kernel/user times
memory + swap        ``GlobalMemoryStatusEx``
disks                ``GetLogicalDrives`` + ``GetDriveTypeW`` +
                     ``GetDiskFreeSpaceExW``
processes            ``K32EnumProcesses`` + ``OpenProcess`` with
                     ``GetProcessTimes`` (CPU deltas) and ``GetProcessMemoryInfo``
battery              ``GetSystemPowerStatus``
uptime               ``GetTickCount64``
network              *not* here - the interface table structures are too fragile
                     to fake; the network card says so instead of showing zeros
===================  ==========================================================

Everything is wrapped so that a missing symbol (older Windows, a locked-down
process) returns nothing instead of raising.
"""

from __future__ import annotations

import ctypes
import os
import time

_WINDOWS = os.name == "nt"
# On Linux/macOS there is no WinDLL: the module still imports (so the structures
# can be checked in tests), it just reports itself as unavailable.
_LIB = ctypes.WinDLL if _WINDOWS else (lambda *a, **k: None)


def _lib(name):
    if not _WINDOWS:
        return None
    try:                                     # pragma: no cover - Windows only
        return _LIB(name, use_last_error=True)
    except OSError:
        return None


KERNEL32 = _lib("kernel32")
PSAPI = _lib("psapi")
NTDLL = _lib("ntdll")

SYSTEM_PROCESSOR_INFORMATION = 8
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DRIVE_FIXED = 3


# --------------------------------------------------------------------------- #
# structures
# --------------------------------------------------------------------------- #
# Fixed-width types on purpose: ``wintypes.ULONG`` is 8 bytes on a 64 bit Linux,
# and a structure whose layout depends on the OS is exactly the kind of bug that
# only shows up as "the numbers are nonsense" on the user's machine.
ULONG = ctypes.c_uint32
DWORD = ctypes.c_uint32
BYTE = ctypes.c_uint8
BOOL = ctypes.c_int
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
LPCWSTR = ctypes.c_wchar_p
LPWSTR = ctypes.c_wchar_p


class _ProcessorPerformance(ctypes.Structure):
    """SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION - 100 ns units."""

    _fields_ = [
        ("IdleTime", ctypes.c_int64),
        ("KernelTime", ctypes.c_int64),
        ("UserTime", ctypes.c_int64),
        ("DpcTime", ctypes.c_int64),
        ("InterruptTime", ctypes.c_int64),
        ("InterruptCount", ULONG),
    ]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ULONG),
        ("dwMemoryLoad", ULONG),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class _PowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", BYTE),
        ("BatteryFlag", BYTE),
        ("BatteryLifePercent", BYTE),
        ("SystemStatusFlag", BYTE),
        ("BatteryLifeTime", DWORD),
        ("BatteryFullLifeTime", DWORD),
    ]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("PageFaultCount", DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class _FileTime(ctypes.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]

    @property
    def value(self):
        return (self.high << 32) | self.low


# --------------------------------------------------------------------------- #
# prototypes (argtypes/restype matter: 64 bit handles truncate otherwise)
# --------------------------------------------------------------------------- #
def _prototype(lib, name, restype, *argtypes):
    if lib is None:
        return None
    try:
        fn = getattr(lib, name)
    except (AttributeError, OSError):        # pragma: no cover - old Windows
        return None
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


_nt_query = _prototype(NTDLL, "NtQuerySystemInformation", ctypes.c_long,
                       ctypes.c_uint32, ctypes.c_void_p, ULONG,
                       ctypes.POINTER(ULONG))
_global_memory = _prototype(KERNEL32, "GlobalMemoryStatusEx", BOOL,
                            ctypes.POINTER(_MemoryStatus))
_get_drives = _prototype(KERNEL32, "GetLogicalDrives", DWORD)
_get_drive_type = _prototype(KERNEL32, "GetDriveTypeW", ctypes.c_uint32, LPCWSTR)
_get_disk_free = _prototype(KERNEL32, "GetDiskFreeSpaceExW", BOOL,
                            LPCWSTR, ctypes.POINTER(ctypes.c_uint64),
                            ctypes.POINTER(ctypes.c_uint64),
                            ctypes.POINTER(ctypes.c_uint64))
_get_tick = _prototype(KERNEL32, "GetTickCount64", ctypes.c_uint64)
_power = _prototype(KERNEL32, "GetSystemPowerStatus", BOOL,
                    ctypes.POINTER(_PowerStatus))
_enum_processes = _prototype(PSAPI, "K32EnumProcesses", BOOL,
                             ctypes.POINTER(DWORD), DWORD,
                             ctypes.POINTER(DWORD))
_open_process = _prototype(KERNEL32, "OpenProcess", HANDLE, DWORD,
                           BOOL, DWORD)
_close_handle = _prototype(KERNEL32, "CloseHandle", BOOL, HANDLE)
_get_process_times = _prototype(KERNEL32, "GetProcessTimes", BOOL,
                                HANDLE, ctypes.POINTER(_FileTime),
                                ctypes.POINTER(_FileTime),
                                ctypes.POINTER(_FileTime),
                                ctypes.POINTER(_FileTime))
_get_process_memory = _prototype(PSAPI, "K32GetProcessMemoryInfo", BOOL,
                                 HANDLE,
                                 ctypes.POINTER(_ProcessMemoryCounters), DWORD)
_query_full_name = _prototype(KERNEL32, "QueryFullProcessImageNameW", BOOL,
                              HANDLE, DWORD, LPWSTR,
                              ctypes.POINTER(DWORD))


# --------------------------------------------------------------------------- #
# the sampler
# --------------------------------------------------------------------------- #
class WindowsSampler:
    """Same shape as the psutil branch of :class:`winwidgets.collect.Collector`."""

    name = "windows"

    def __init__(self):
        self._last_cores = None
        self._last_proc = {}
        self._last_t = None
        self.process_memory_only = _get_process_times is None

    # -- cpu --------------------------------------------------------------- #
    def _processor_times(self):
        if _nt_query is None:
            return []
        count = os.cpu_count() or 1
        size = ctypes.sizeof(_ProcessorPerformance) * count
        buf = (ctypes.c_ubyte * size)()
        returned = ULONG(0)
        status = _nt_query(SYSTEM_PROCESSOR_INFORMATION, buf, size,
                           ctypes.byref(returned))
        if status != 0:
            return []
        n = min(count, returned.value // ctypes.sizeof(_ProcessorPerformance))
        arr = (_ProcessorPerformance * n).from_buffer(buf)
        return [(p.IdleTime, p.KernelTime, p.UserTime) for p in arr]

    def cpu(self):
        """(total percent, [per core percent])."""
        now = self._processor_times()
        if not now:
            return 0.0, []
        if self._last_cores is None or len(self._last_cores) != len(now):
            self._last_cores = now
            return 0.0, [0.0] * len(now)
        per_core = []
        for (i0, k0, u0), (i1, k1, u1) in zip(self._last_cores, now):
            idle = max(0, i1 - i0)
            total = max(1, (k1 - k0) + (u1 - u0))
            per_core.append(min(100.0, max(0.0, 100.0 * (total - idle) / total)))
        self._last_cores = now
        total = sum(per_core) / len(per_core) if per_core else 0.0
        return total, per_core

    # -- memory ------------------------------------------------------------ #
    def memory(self):
        """(percent, used GiB, total GiB, swap percent)."""
        if _global_memory is None:
            return 0.0, 0.0, 0.0, 0.0
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if not _global_memory(ctypes.byref(status)):
            return 0.0, 0.0, 0.0, 0.0
        total = status.ullTotalPhys / 2**30
        used = (status.ullTotalPhys - status.ullAvailPhys) / 2**30
        pct = 100.0 * used / total if total else 0.0
        page_total = status.ullTotalPageFile or 0
        page_used = page_total - status.ullAvailPageFile
        swap = 100.0 * page_used / page_total if page_total else 0.0
        return pct, used, total, swap

    # -- disks ------------------------------------------------------------- #
    def disks(self):
        out = []
        if _get_drives is None or _get_disk_free is None:
            return out
        mask = _get_drives()
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = "%s:\\" % chr(ord("A") + i)
            if _get_drive_type is not None and _get_drive_type(root) != DRIVE_FIXED:
                continue
            free = ctypes.c_uint64(0)
            total = ctypes.c_uint64(0)
            avail = ctypes.c_uint64(0)
            if not _get_disk_free(root, ctypes.byref(avail), ctypes.byref(total),
                                  ctypes.byref(free)):
                continue
            if not total.value:
                continue
            used = total.value - free.value
            out.append((root.rstrip("\\"),
                        used / 2**30, total.value / 2**30,
                        100.0 * used / total.value))
        return out

    # -- processes --------------------------------------------------------- #
    def _enum_pids(self, limit=1024):
        if _enum_processes is None:
            return []
        arr = (DWORD * limit)()
        used = DWORD(0)
        if not _enum_processes(arr, ctypes.sizeof(arr), ctypes.byref(used)):
            return []
        n = used.value // ctypes.sizeof(DWORD)
        return [arr[i] for i in range(min(n, limit))]

    def _proc_name(self, handle):
        if _query_full_name is None:
            return ""
        size = DWORD(512)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _query_full_name(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1]

    def processes(self, count=6):
        """[(name, cpu percent, mem percent)] sorted by cpu."""
        pids = self._enum_pids()
        if not pids:
            return []
        now = time.monotonic()
        dt = (now - self._last_t) if self._last_t else 0.0
        cores = os.cpu_count() or 1
        rows, current = [], {}
        for pid in pids:
            handle = _open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                continue
            try:
                name = self._proc_name(handle) or ("pid %d" % pid)
                mem_pct = 0.0
                if _get_process_memory is not None:
                    counters = _ProcessMemoryCounters()
                    counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
                    if _get_process_memory(handle, ctypes.byref(counters),
                                           counters.cb):
                        total = ctypes.c_uint64(0)
                        status = _MemoryStatus()
                        status.dwLength = ctypes.sizeof(_MemoryStatus)
                        if _global_memory and _global_memory(ctypes.byref(status)) \
                                and status.ullTotalPhys:
                            total.value = status.ullTotalPhys
                            mem_pct = 100.0 * counters.WorkingSetSize / total.value
                cpu_pct = 0.0
                if _get_process_times is not None and dt > 0:
                    created, exited, kernel, user = (_FileTime() for _ in range(4))
                    if _get_process_times(handle, ctypes.byref(created),
                                          ctypes.byref(exited), ctypes.byref(kernel),
                                          ctypes.byref(user)):
                        ticks = (kernel.value + user.value) / 1e7     # seconds
                        current[pid] = ticks
                        previous = self._last_proc.get(pid)
                        if previous is not None:
                            cpu_pct = 100.0 * max(0.0, ticks - previous) / dt
                            cpu_pct = max(0.0, min(100.0 * cores, cpu_pct))
                rows.append((name, cpu_pct, mem_pct))
            finally:
                if _close_handle is not None:
                    _close_handle(handle)
        self._last_proc = current
        self._last_t = now
        rows.sort(key=lambda r: (r[1], r[2]), reverse=True)
        return rows[:count]

    # -- battery / uptime -------------------------------------------------- #
    def battery(self):
        """(percent | None, plugged | None)."""
        if _power is None:
            return None, None
        status = _PowerStatus()
        if not _power(ctypes.byref(status)):
            return None, None
        plugged = status.ACLineStatus == 1
        if status.BatteryLifePercent == 255:
            return None, plugged
        return float(status.BatteryLifePercent), plugged

    def uptime(self):
        if _get_tick is None:
            return 0.0
        return _get_tick() / 1000.0

    def net(self):
        """No honest source without psutil - the card says so instead."""
        return None


def available() -> bool:
    return _WINDOWS and _global_memory is not None
