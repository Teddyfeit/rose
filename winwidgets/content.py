"""What each card *says*, independent of who draws it.

The desktop cards (tkinter, ``desktop.py``) and the PNG preview
(``tools/desktop_shot.py``) both ask this module for a list of primitives:

    {"type": "text",  "x": 13, "y": 30, "text": "42%", "colour": "#...",
     "anchor": "nw", "role": "hero"}
    {"type": "bar",   "x": 13, "y": 38, "w": 274, "pct": 42.0, "h": 5,
     "colour": "#..."}
    {"type": "spark", "x": 13, "y": 50, "w": 274, "h": 14, "values": [...],
     "colour": "#...", "fill": "#..."}
    {"type": "ticks", "x": 13, "y": 60, "w": 274, "values": [...],
     "slot": 4, "h": 16}

Coordinates are card-local pixels, so a renderer only has to know how to draw
those five shapes.  That is what keeps the preview honest: the same numbers,
in the same places, in the same colours.
"""

from __future__ import annotations

import time

from . import palette as P

PAD = 13
TOP = 38            # first content row under the title


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #
def rate(mibs):
    if mibs >= 1024:
        return "%.1f GiB/s" % (mibs / 1024.0)
    if mibs >= 10:
        return "%.1f MiB/s" % mibs
    if mibs >= 0.1:
        return "%.2f MiB/s" % mibs
    return "%.0f KiB/s" % (mibs * 1024)


def uptime(seconds):
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "%dd %dh %02dm" % (days, hours, minutes)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm" % minutes


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def _text(x, y, text, colour, anchor="nw", role="body"):
    return {"type": "text", "x": x, "y": y, "text": str(text), "colour": colour,
            "anchor": anchor, "role": role}


def _bar(x, y, w, pct, colour, height=5):
    return {"type": "bar", "x": x, "y": y, "w": w, "pct": max(0.0, min(100.0, pct)),
            "colour": colour, "h": height}


def _spark(x, y, w, values, colour, fill, height=16):
    return {"type": "spark", "x": x, "y": y, "w": w, "h": height,
            "values": [float(v) for v in (values or [])],
            "colour": colour, "fill": fill}


def _ticks(x, y, w, values, slot, height, cols=4):
    return {"type": "ticks", "x": x, "y": y, "w": w, "values": list(values),
            "slot": slot, "h": height, "colour": cols}


# --------------------------------------------------------------------------- #
# per-card content
# --------------------------------------------------------------------------- #
def cpu(snap, history, pal, width, height):
    out = [_text(width - PAD, 11, "%3.0f%%" % snap.cpu,
                 P.level_colour(snap.cpu, pal), "ne", "hero")]
    colour = P.level_colour(snap.cpu, pal)
    bar_w = width - 2 * PAD
    out.append(_bar(PAD, TOP, bar_w, snap.cpu, colour))
    cores = (snap.cpu_cores or [snap.cpu])
    slot = max(3, min(11, bar_w // max(1, len(cores))))
    out.append(_ticks(PAD, TOP + 12, bar_w, cores, slot, 17))
    detail = "%d Kerne" % (snap.cores or len(cores))
    if snap.freq:
        detail += "  \u00b7  %.2f GHz" % (snap.freq / 1000.0)
    out.append(_text(PAD, TOP + 34, detail, pal["faint"]))
    return out


def memory(snap, history, pal, width, height):
    colour = P.level_colour(snap.mem_percent, pal)
    out = [_text(width - PAD, 11, "%3.0f%%" % snap.mem_percent, colour, "ne", "hero"),
           _bar(PAD, TOP, width - 2 * PAD, snap.mem_percent, colour),
           _text(PAD, TOP + 12, "%.1f / %.1f GB belegt" % (snap.mem_used, snap.mem_total),
                 pal["text"])]
    if snap.swap_percent:
        out.append(_text(width - PAD, TOP + 12, "Swap %.0f%%" % snap.swap_percent,
                         pal["faint"], "ne"))
    return out


def disk(snap, history, pal, width, height):
    if not snap.disks:
        return [_text(PAD, TOP, "kein Laufwerk", pal["faint"])]
    mount, used, total, pct = max(snap.disks, key=lambda d: d[1])
    colour = P.level_colour(pct, pal)
    out = [_text(width - PAD, 11, "%3.0f%%" % pct, colour, "ne", "hero"),
           _bar(PAD, TOP, width - 2 * PAD, pct, colour),
           _text(PAD, TOP + 12, "%.0f / %.0f GB   %s" % (used, total, mount),
                 pal["text"])]
    others = [d for d in snap.disks if d[0] != mount]
    if others:
        m2, _u2, _t2, p2 = others[0]
        out.append(_text(width - PAD, TOP + 12, "%s %.0f%%" % (m2, p2),
                         pal["faint"], "ne"))
    return out


def network(snap, history, pal, width, height):
    if not getattr(snap, "net_known", True):
        return [_text(PAD, TOP - 2, "kein Messwert", pal["dim"]),
                _text(PAD, TOP + 14, "psutil fehlt (Python zu neu?)", pal["faint"])]
    out = [_text(PAD, TOP - 6, "\u25bc " + rate(snap.net_down), pal["accent"]),
           _text(width - PAD, TOP - 6, "\u25b2 " + rate(snap.net_up), pal["dim"], "ne")]
    down = history.get("down") or []
    combined = max(1e-6, max(down + (history.get("up") or [0])))
    out.append(_spark(PAD, TOP + 12, width - 2 * PAD, down, pal["accent"],
                      pal["accent_soft"], height=16))
    recv, sent = snap.net_total
    out.append(_text(PAD, height - 14, "gesamt %.1f / %.1f GB" % (recv, sent),
                     pal["faint"], "sw"))
    _ = combined
    return out


def clock(snap, history, pal, width, height):
    now = time.localtime(snap.t)
    return [_text(PAD, TOP - 8, time.strftime("%H:%M", now), pal["accent"], "nw", "hero"),
            _text(PAD, TOP + 22, time.strftime("%A, %d.%m.%Y", now), pal["text"]),
            _text(PAD, TOP + 38, "l\u00e4uft seit " + uptime(snap.uptime), pal["faint"])]


def processes(snap, history, pal, width, height):
    # two numeric columns, both right-aligned so a three-digit CPU value grows
    # leftwards instead of running into the RAM column
    cpu_x, ram_x = width - 84, width - PAD        # two columns, 20 px apart
    out = [_text(width - PAD, 12, "%d laufen" % snap.proc_count, pal["faint"], "ne"),
           _text(PAD, TOP - 6, "Programm", pal["faint"], "nw", "small"),
           _text(cpu_x, TOP - 6, "CPU", pal["faint"], "ne", "small"),
           _text(ram_x, TOP - 6, "RAM", pal["faint"], "ne", "small")]
    y = TOP + 8
    for proc in snap.procs[:4]:
        out.append(_text(PAD, y, proc.name[:22], pal["text"]))
        out.append(_text(cpu_x, y, "%4.1f%%" % proc.cpu,
                         P.level_colour(min(100.0, proc.cpu), pal), "ne"))
        out.append(_text(ram_x, y, "%4.1f%%" % proc.mem, pal["faint"], "ne"))
        y += 15
    return out


def battery(snap, history, pal, width, height):
    if snap.battery is None:
        return [_text(PAD, TOP, "kein Akku \u00b7 Netzbetrieb", pal["dim"])]
    pct = snap.battery
    colour = pal["accent"] if pct >= 30 else pal["warn"]
    return [_text(width - PAD, 11, "%3.0f%%" % pct, colour, "ne", "hero"),
            _bar(PAD, TOP, width - 2 * PAD, pct, colour),
            _text(PAD, TOP + 12, "l\u00e4dt" if snap.plugged else "im Akkubetrieb",
                  pal["dim"])]


BUILDERS = {
    "cpu": cpu,
    "memory": memory,
    "disk": disk,
    "network": network,
    "clock": clock,
    "processes": processes,
    "battery": battery,
}

TITLES = {
    "cpu": "CPU",
    "memory": "ARBEITSSPEICHER",
    "disk": "DATENTR\u00c4GER",
    "network": "NETZWERK",
    "clock": "UHR",
    "processes": "TOP-PROZESSE",
    "battery": "AKKU",
}


def build(kind, snap, history, pal, width, height):
    """The primitives for one card at the given size."""
    builder = BUILDERS.get(kind)
    if builder is None:
        return []
    return builder(snap, history, pal, width, height)
