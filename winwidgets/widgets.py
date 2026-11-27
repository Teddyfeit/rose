"""The widgets themselves.

Every widget is a small class with the same two methods::

    w = CpuWidget()
    w.height(canvas_width)     # how many rows it needs at that width
    w.draw(canvas, x, y, width, snap, history)

so the board can lay them out in columns and forget about their contents.

Design rules kept across all of them (Windows 11 look, terminal means):

* the card is a rounded 1-cell stroke, a dim title on the first text row and
  the important number/word on the right of the same row
* one "hero" number per card, large where the terminal allows (bold + accent)
* a chart or bar takes the middle, detail lines go below in ``dim``
* never more than ~14 columns of labels - the numbers are what counts
"""

from __future__ import annotations

import math
import time

from .term import ARROW_DN, ARROW_UP, DENSE


def fmt_bytes_per_sec(mibs):
    if mibs >= 1024:
        return "%.1f GiB/s" % (mibs / 1024.0)
    if mibs >= 10:
        return "%.1f MiB/s" % mibs
    if mibs >= 0.1:
        return "%.2f MiB/s" % mibs
    return "%.0f KiB/s" % (mibs * 1024)


def fmt_uptime(seconds):
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "%dd %dh %02dm" % (days, hours, minutes)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm" % minutes


class Widget:
    key = "base"
    title = "BASE"

    def height(self, width):        # pragma: no cover - overridden
        return 6

    def draw(self, canvas, x, y, width, snap, history):
        raise NotImplementedError

    # -- shared bits ------------------------------------------------------- #
    def card(self, canvas, x, y, width, height):
        canvas.box(x, y, width, height, style="card")
        inner = width - 4
        canvas.put(x + 2, y + 1, self.title, "dim")
        return inner

    @staticmethod
    def headline(canvas, x, y, width, text, style="accent"):
        """Right-align the hero value on the title row."""
        canvas.put(x + width - len(text), y, text, style)


# --------------------------------------------------------------------------- #
class CpuWidget(Widget):
    key = "cpu"
    title = "CPU"

    def height(self, width):
        return 9

    def draw(self, canvas, x, y, width, snap, history):
        h = self.height(width)
        inner = self.card(canvas, x, y, width, h)
        self.headline(canvas, x + 2, y + 1, inner, "%3.0f%%" % snap.cpu,
                      canvas.theme.by_percent(snap.cpu).name)

        canvas.meter(x + 2, y + 3, inner, snap.cpu,
                     style=canvas.theme.by_percent(snap.cpu).name)

        # per-core strip: one glyph per core, density = load, colour = level
        cores = snap.cpu_cores or [snap.cpu]
        glyphs = " \u2591\u2592\u2593\u2588"
        per = max(1, min(2, inner // max(1, len(cores))))
        for i, value in enumerate(cores[:inner // per]):
            g = glyphs[max(1, min(4, 1 + int(value / 25)))]
            canvas.put(x + 2 + i * per, y + 5, g * per,
                       canvas.theme.by_percent(value).name)
        detail = "%d cores" % len(cores)
        if snap.freq:
            detail += " \u00b7 %.2f GHz" % (snap.freq / 1000.0)
        canvas.label(x + 2, y + 6, detail, inner, "faint")

        # the sparkline is the last row inside the card
        canvas.sparkline(x + 2, y + h - 2, history.get("cpu", []), inner,
                         style=canvas.theme.by_percent(snap.cpu).name)


class MemoryWidget(Widget):
    key = "memory"
    title = "MEMORY"

    def height(self, width):
        return 8

    def draw(self, canvas, x, y, width, snap, history):
        h = self.height(width)
        inner = self.card(canvas, x, y, width, h)
        style = canvas.theme.by_percent(snap.mem_percent).name
        self.headline(canvas, x + 2, y + 1, inner, "%3.0f%%" % snap.mem_percent, style)
        canvas.meter(x + 2, y + 3, inner, snap.mem_percent, style=style)
        canvas.label(x + 2, y + 4,
                     "%.1f / %.1f GiB used" % (snap.mem_used, snap.mem_total),
                     inner, "text")
        if snap.swap_percent:
            canvas.label(x + 2, y + 5, "swap %.0f%%" % snap.swap_percent,
                         inner // 2, "faint")
        canvas.sparkline(x + 2, y + h - 2, history.get("mem", []), inner, style=style)


class DiskWidget(Widget):
    key = "disk"
    title = "STORAGE"

    def height(self, width):
        return 8

    def draw(self, canvas, x, y, width, snap, history):
        h = self.height(width)
        inner = self.card(canvas, x, y, width, h)
        if not snap.disks:
            canvas.label(x + 2, y + 3, "no fixed disks", inner, "faint")
            return
        mount, used, total, pct = max(snap.disks, key=lambda d: d[1])
        style = canvas.theme.by_percent(pct).name
        self.headline(canvas, x + 2, y + 1, inner, "%3.0f%%" % pct, style)
        canvas.meter(x + 2, y + 3, inner, pct, style=style)
        canvas.label(x + 2, y + 4,
                     "%.0f / %.0f GB  %s" % (used, total, mount.rstrip("\\/") or "/"),
                     inner, "text")
        row = y + 5
        for mount2, used2, total2, pct2 in snap.disks[1:3]:
            if row >= y + h - 2:
                break
            canvas.label(x + 2, row, mount2.rstrip("\\/") or "/", 12, "faint",
                         right="%.0f%%" % pct2, right_style="dim")
            row += 1
        if len(snap.disks) > 3:
            canvas.label(x + 2, row, "+%d more" % (len(snap.disks) - 3), inner, "faint")


class NetworkWidget(Widget):
    key = "network"
    title = "NETWORK"

    def height(self, width):
        return 8

    def draw(self, canvas, x, y, width, snap, history):
        h = self.height(width)
        inner = self.card(canvas, x, y, width, h)
        if not snap.net_known:
            # no wheel for psutil and no honest Win32 source for interface
            # counters: an empty card with a note beats a card full of zeros
            canvas.put(x + 2, y + 3, "kein Messwert", "dim")
            canvas.put(x + 2, y + 4, "psutil fehlt (Python zu neu?)", "faint")
            canvas.put(x + 2, y + h - 2, "pip install psutil", "accent_dim")
            return
        canvas.put(x + 2, y + 2, ARROW_DN + " " + fmt_bytes_per_sec(snap.net_down),
                   "accent")
        up = ARROW_UP + " " + fmt_bytes_per_sec(snap.net_up)
        canvas.put(x + 2 + inner - len(up), y + 2, up, "dim")

        canvas.sparkline(x + 2, y + 4, history.get("down", []), inner, style="accent")
        canvas.sparkline(x + 2, y + 5, history.get("up", []), inner, style="dim")

        recv, sent = snap.net_total
        canvas.label(x + 2, y + h - 2,
                     "total %.1f / %.1f GiB" % (recv, sent), inner, "faint")


class ProcessesWidget(Widget):
    key = "processes"
    title = "TOP PROCESSES"

    def height(self, width):
        return 10

    def draw(self, canvas, x, y, width, snap, history):
        h = self.height(width)
        inner = self.card(canvas, x, y, width, h)
        count = "%d running" % snap.proc_count
        canvas.put(x + 2 + inner - len(count), y + 1, count, "faint")

        name_w = max(8, inner - 14)
        header = " " * name_w + "   CPU    MEM"
        canvas.put(x + 2, y + 2, header[:inner], "faint")
        row = y + 3
        for proc in snap.procs:
            if row >= y + h - 1:
                break
            cpu = ("%4.1f%%" % proc.cpu).rjust(6)
            mem = ("%4.1f%%" % proc.mem).rjust(6)
            canvas.put(x + 2, row, proc.name[:name_w].ljust(name_w), "text")
            canvas.put(x + 2 + name_w + 1, row, cpu,
                       canvas.theme.by_percent(min(100.0, proc.cpu)).name)
            canvas.put(x + 2 + name_w + 7, row, mem, "faint")
            row += 1


class ClockWidget(Widget):
    key = "clock"
    title = "CLOCK"

    def height(self, width):
        return 8

    def draw(self, canvas, x, y, width, snap, history):
        h = self.height(width)
        inner = self.card(canvas, x, y, width, h)
        now = time.localtime(snap.t)
        big = time.strftime("%H:%M:%S", now)
        canvas.put(x + 2, y + 2, big, "accent")
        canvas.put(x + 2 + len(big) + 2, y + 2, time.strftime("%d.%m.%Y", now), "dim")

        # a weekday/date line and a tiny 12-hour arc so the card is not static
        hour, minute = now.tm_hour % 12, now.tm_min
        total = (hour * 60 + minute) / (12 * 60)
        bar_w = max(8, inner - 12)
        canvas.meter(x + 2, y + 4, bar_w, total * 100.0, style="accent_dim")
        canvas.put(x + 2 + bar_w + 1, y + 4, "12h", "faint")
        canvas.label(x + 2, y + 5, time.strftime("%A", now), inner, "dim")
        canvas.label(x + 2, y + h - 2, "up " + fmt_uptime(snap.uptime), inner, "faint")


class BatteryWidget(Widget):
    key = "battery"
    title = "BATTERY"

    def height(self, width):
        return 7

    def draw(self, canvas, x, y, width, snap, history):
        h = self.height(width)
        inner = self.card(canvas, x, y, width, h)
        if snap.battery is None:
            canvas.label(x + 2, y + 3, "no battery \u00b7 mains", inner, "faint")
            if snap.plugged is not None:
                canvas.label(x + 2, y + 4, "on AC power", inner, "dim")
            return
        pct = snap.battery
        style = canvas.theme.by_percent(100.0 - pct).name if pct < 95 else "accent"
        self.headline(canvas, x + 2, y + 1, inner, "%3.0f%%" % pct, style)
        canvas.meter(x + 2, y + 3, inner, pct, style=style)
        state = "charging" if snap.plugged else "on battery"
        canvas.label(x + 2, y + 4, state, inner, "dim")


# --------------------------------------------------------------------------- #
REGISTRY = {
    "cpu": CpuWidget,
    "memory": MemoryWidget,
    "disk": DiskWidget,
    "network": NetworkWidget,
    "processes": ProcessesWidget,
    "clock": ClockWidget,
    "battery": BatteryWidget,
}

ORDER = ["cpu", "memory", "disk", "network", "clock", "processes", "battery"]


def make(names=None):
    keys = list(names) if names else list(ORDER)
    widgets = []
    for key in keys:
        cls = REGISTRY.get(key)
        if cls is not None:
            widgets.append(cls())
    return widgets
