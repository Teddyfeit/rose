"""ANSI plumbing: colours, a cell buffer, and a flicker-free screen.

The whole app renders into a :class:`Canvas` - a grid of characters plus a
colour per character - and the canvas is then written in one ``write()``.  That
is what keeps a terminal widget board from tearing: one frame = one string.

Colour is *not* baked into the canvas (that would make every cell carry ~18
bytes of escape code).  Each cell stores a small style id, and ``render()``
walks the frame emitting an escape only when the style changes.
"""

from __future__ import annotations

import os
import shutil
import sys

# --------------------------------------------------------------------------- #
# glyphs that exist in the stock Windows consoles
# --------------------------------------------------------------------------- #
H = "\u2500"   # ─
V = "\u2502"   # │
TL = "\u256d"  # ╭  rounded, the Windows 11 corner
TR = "\u256e"  # ╮
BL = "\u2570"  # ╰
BR = "\u256f"  # ╯
LT = "\u251c"  # ├
RT = "\u2524"  # ┤
BLOCK = "\u2588"     # █
DENSE = "\u2593"     # ▓
MED = "\u2592"       # ▒
LIGHT = "\u2591"     # ░
HALF = "\u258c"      # ▌
LEFT8 = "\u258f"     # ▏
RIGHT8 = "\u2595"    # ▕
UP = "\u2580"        # ▀
ARROW_UP = "\u25b2"  # ▲
ARROW_DN = "\u25bc"  # ▼

SPARK = " " + LIGHT + MED + DENSE + BLOCK


# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #
class Style:
    """A named colour pair, resolved lazily to an escape sequence."""

    __slots__ = ("name", "fg", "bg", "dim", "bold")

    def __init__(self, name, fg=None, bg=None, dim=False, bold=False):
        self.name = name
        self.fg = fg
        self.bg = bg
        self.dim = dim
        self.bold = bold

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Style({self.name})"


class Theme:
    """Colour modes: ``truecolor``, ``256``, ``mono``."""

    def __init__(self, accent=(0x60, 0xCD, 0xFF), mode="truecolor"):
        self.mode = mode
        self.accent = tuple(accent)
        self._cache = {}
        a = self.accent
        # the W11 accent, as used in the widgets
        self.styles = {
            "text": Style("text", (235, 235, 235)),
            "dim": Style("dim", (140, 140, 140)),
            "faint": Style("faint", (88, 88, 94)),
            "accent": Style("accent", a, bold=True),
            "accent_dim": Style("accent_dim", self.pick(a, 0.62)),
            "warn": Style("warn", (255, 190, 80)),
            "bad": Style("bad", (255, 96, 96)),
            "good": Style("good", (110, 220, 150)),
            "track": Style("track", (74, 74, 82)),
            "spark": Style("spark", self.pick(a, 0.85)),
            "card": Style("card", (210, 210, 216)),
            "inverse": Style("inverse", (12, 12, 14), a),
        }

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def pick(rgb, k=1.0):
        return tuple(max(0, min(255, int(round(c * k)))) for c in rgb)

    def by_percent(self, pct):
        """Colour for a 0..100 value: accent -> amber -> red."""
        if pct >= 90:
            return self.styles["bad"]
        if pct >= 75:
            return self.styles["warn"]
        return self.styles["accent"]

    def style(self, name):
        return self.styles[name]

    # -- escape emission --------------------------------------------------- #
    def code(self, style):
        key = style.name
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if self.mode == "mono":
            code = "\x1b[1m" if style.bold else "\x1b[0m"
        else:
            parts = []
            if style.bold:
                parts.append("1")
            if style.dim:
                parts.append("2")
            if style.fg is not None:
                parts.append(self._fg(style.fg))
            if style.bg is not None:
                parts.append(self._bg(style.bg))
            code = "\x1b[" + ";".join(parts) + "m" if parts else "\x1b[0m"
        self._cache[key] = code
        return code

    def _fg(self, rgb):
        if self.mode == "truecolor":
            return "38;2;%d;%d;%d" % rgb
        return "38;5;%d" % self._cube(rgb)

    def _bg(self, rgb):
        if self.mode == "truecolor":
            return "48;2;%d;%d;%d" % rgb
        return "48;5;%d" % self._cube(rgb)

    @staticmethod
    def _cube(rgb):
        r, g, b = (max(0, min(255, int(c))) for c in rgb)
        # 6x6x6 colour cube + a little grey ramp for the near-greyscales
        if abs(r - g) < 10 and abs(g - b) < 10:
            grey = int(round((r + g + b) / 3.0))
            idx = 232 + max(0, min(23, int(round((grey - 8) / 10.0))))
            return idx
        step = lambda c: int(round((c / 255.0) * 5.0))  # noqa: E731
        return 16 + 36 * step(r) + 6 * step(g) + step(b)


# --------------------------------------------------------------------------- #
# capability detection
# --------------------------------------------------------------------------- #
def supports_truecolor(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    if os.environ.get("WT_SESSION"):          # Windows Terminal: yes
        return True
    if os.environ.get("TERM_PROGRAM") in ("vscode", "WezTerm", "iTerm.app"):
        return True
    try:
        return bool(stream.isatty()) and os.environ.get("TERM", "") != "dumb"
    except Exception:
        return False


def windows_accent(default=(0x60, 0xCD, 0xFF)):
    """The user's Windows accent colour, or the W11 default blue."""
    override = os.environ.get("W11WIDGETS_ACCENT", "").lstrip("#")
    if len(override) == 6:
        try:
            return (int(override[0:2], 16), int(override[2:4], 16), int(override[4:6], 16))
        except ValueError:
            pass
    try:                                       # pragma: no cover - Windows only
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\DWM") as key:
            value, _ = winreg.QueryValueEx(key, "ColorizationColor")
        # COLORREF-ish, 0xAABBGGRR
        b, g, r = value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF
        if r + g + b < 60:                     # transparent / disabled accent
            return default
        return (r, g, b)
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# canvas
# --------------------------------------------------------------------------- #
class Canvas:
    """A grid of ``(char, style)`` cells; nothing touches stdout until flush."""

    def __init__(self, width, height, theme: Theme):
        self.w = max(1, int(width))
        self.h = max(1, int(height))
        self.theme = theme
        self.ch = [[" "] * self.w for _ in range(self.h)]
        self.st = [[""] * self.w for _ in range(self.h)]

    # -- primitives -------------------------------------------------------- #
    def clear(self, style=""):
        for y in range(self.h):
            self.ch[y] = [" "] * self.w
            self.st[y] = [style] * self.w

    def put(self, x, y, text, style="text"):
        if y < 0 or y >= self.h:
            return
        row, strow = self.ch[y], self.st[y]
        for i, c in enumerate(text):
            xx = x + i
            if 0 <= xx < self.w:
                row[xx] = c
                strow[xx] = style

    def fill(self, x, y, w, h, style="track", char=" "):
        for yy in range(y, y + h):
            if 0 <= yy < self.h:
                self.put(x, yy, char * w, style)

    def box(self, x, y, w, h, style="card", fill=False, fill_style="card_bg"):
        """A rounded rectangle with a 1-cell stroke."""
        if w < 2 or h < 2:
            return
        if fill:
            self.fill(x + 1, y + 1, w - 2, h - 2, fill_style, " ")
        self.put(x, y, TL + H * (w - 2) + TR, style)
        self.put(x, y + h - 1, BL + H * (w - 2) + BR, style)
        for yy in range(y + 1, y + h - 1):
            self.put(x, yy, V, style)
            self.put(x + w - 1, yy, V, style)

    def hline(self, x, y, w, style="track", left=LT, right=RT):
        self.put(x, y, left + H * max(0, w - 2) + right, style)

    # -- widgets ----------------------------------------------------------- #
    def text(self, x, y, text, style="text", width=None):
        if width is not None:
            text = text[:width]
        self.put(x, y, text, style)
        return len(text)

    def sparkline(self, x, y, values, width, style="spark", low=0.0, high=100.0,
                  char=None):
        """A one-row chart.  ``values`` is oldest -> newest."""
        if width <= 0:
            return
        vals = list(values)[-width:]
        pad = width - len(vals)
        for i in range(pad):
            self.put(x + i, y, " ", "faint")
        for i, v in enumerate(vals):
            t = 0.0 if high <= low else (v - low) / (high - low)
            t = max(0.0, min(1.0, t))
            if char is not None:
                c = char
            elif t <= 0.0:
                c = SPARK[0]
            else:
                c = SPARK[max(1, min(len(SPARK) - 1, int(1 + t * (len(SPARK) - 1))))]
            self.put(x + pad + i, y, c, style if t > 0 else "faint")

    def bar(self, x, y, width, pct, style="accent", track="track", char=BLOCK):
        """A solid progress bar made of full blocks (crisp at any width)."""
        if width <= 0:
            return
        pct = max(0.0, min(100.0, pct))
        filled = int(round(pct / 100.0 * width))
        if filled >= width:
            self.put(x, y, char * width, style)
            return
        if filled > 0:
            self.put(x, y, char * filled, style)
        self.put(x + filled, y, char * (width - filled), track)

    def meter(self, x, y, width, pct, style="accent", track="track",
              glyphs=" \u258f\u258e\u258d\u258c\u258b\u258a\u2589\u2588"):
        """A sub-cell accurate bar: partial blocks make the end smooth."""
        pct = max(0.0, min(100.0, pct))
        full = int(pct / 100.0 * width)
        rest = (pct / 100.0 * width) - full
        if full > 0:
            self.put(x, y, "\u2588" * full, style)
        if full < width:
            idx = int(round(rest * (len(glyphs) - 1)))
            self.put(x + full, y, glyphs[idx], style if idx else track)
            self.put(x + full + 1, y, "\u2500" * max(0, width - full - 1), track)

    def label(self, x, y, text, width, style="dim", right=None, right_style=None):
        """``text`` left, optional ``right`` text right-aligned in ``width``."""
        self.put(x, y, text[:width], style)
        if right:
            r = right[:width]
            self.put(x + width - len(r), y, r, right_style or style)

    # -- output ------------------------------------------------------------ #
    def lines(self):
        theme = self.theme
        out = []
        for y in range(self.h):
            chrow, strow = self.ch[y], self.st[y]
            parts = []
            cur = None
            for x in range(self.w):
                s = strow[x]
                if s != cur:
                    parts.append("\x1b[0m" + (theme.code(theme.styles[s])
                                              if s else ""))
                    cur = s
                parts.append(chrow[x])
            parts.append("\x1b[0m")
            out.append("".join(parts))
        return out

    def text_lines(self):
        """Frame without escapes - what ``--plain`` and the tests look at."""
        return ["".join(row).rstrip() for row in self.ch]

    def snapshot(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(self.text_lines()) + "\n")
        return path


# --------------------------------------------------------------------------- #
# screen
# --------------------------------------------------------------------------- #
def terminal_size(default=(120, 40)):
    try:
        size = shutil.get_terminal_size(default)
    except Exception:  # pragma: no cover
        return default
    return max(size.columns, 30), max(size.lines, 10)


class Screen:
    """Hides the cursor, owns the alt-screen, restores everything on exit."""

    def __init__(self, stream=None, alt_screen=True):
        self.out = stream or sys.stdout
        self.alt = alt_screen
        self.active = False
        self.hidden_cursor = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def open(self):
        if self.active:
            return self
        parts = ["\x1b[?1049h" if self.alt else "\x1b[2J",
                 "\x1b[?25l",      # no cursor: it would blink in the middle
                 "\x1b[?7l",       # no auto-wrap: a full-width row must not scroll
                 "\x1b[H"]
        self.out.write("".join(parts))
        self.out.flush()
        self.active = True
        self.hidden_cursor = True
        return self

    def close(self):
        if not self.active:
            return
        self.out.write("\x1b[0m\x1b[?7h\x1b[?25h"
                       + ("\x1b[?1049l" if self.alt else "\n"))
        self.out.flush()
        self.active = False
        self.hidden_cursor = False

    def draw(self, canvas: Canvas):
        self.out.write("\x1b[H" + "\n".join(canvas.lines()))
        self.out.flush()

    def bell(self):
        """A short beep for "this key did something you cannot see" moments."""
        self.out.write("\a")
        self.out.flush()

    def resize_hint(self):  # pragma: no cover - Windows events are best effort
        return terminal_size()


def enable_windows_ansi():  # pragma: no cover - Windows only
    """Turn on VT processing for the current console (Win10/11)."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def enter_windows_utf8():  # pragma: no cover - Windows only
    """UTF-8 output on legacy consoles; harmless elsewhere.

    The cards are drawn with box characters (``╭ ─ █``).  ``cmd.exe`` defaults to
    codepage 850/437, where those bytes turn into mojibake, so the console
    codepage is switched to 65001 as well - the Python-level ``reconfigure``
    alone only fixes what Python writes, not what the console decodes.
    """
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass
