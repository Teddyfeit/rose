"""The terminal artist.

Every frame of the film is painted into the console with Unicode half blocks
(``▀``): the upper half of a character cell shows the *top* pixel colour, the
cell background shows the *bottom* one.  That doubles the vertical resolution
of the character grid, so a 190 x 52 console becomes a 190 x 104 pixel canvas.

Colour is emitted as 24 bit ANSI ("truecolor") when the terminal supports it,
otherwise the frame is drawn with a brightness ramp of ASCII characters, so the
rose is still readable on a black-and-white terminal.
"""

from __future__ import annotations

import os
import shutil
import sys

import numpy as np

from .mathutil import resize_bilinear

UPPER = "\u2580"  # ▀
LOWER = "\u2584"  # ▄
FULL = "\u2588"   # █

# brightness ramp for terminals without truecolor
RAMP = " .:-=+*#%@"

RESET = "\x1b[0m"
HOME = "\x1b[H"
CLEAR = "\x1b[2J"

# a 8x8 ordered dither: the last bit of smoothness in the dark background
_BAYER8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
], np.float32) / 64.0 - 0.5


def bayer(h, w):
    """Ordered dither tile of the given size, in the range -0.5 .. 0.5."""
    return np.tile(_BAYER8, (h // 8 + 1, w // 8 + 1))[:h, :w]


# --------------------------------------------------------------------------- #
# terminal capabilities
# --------------------------------------------------------------------------- #
def supports_truecolor(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    return "256color" in os.environ.get("TERM", "") or stream.isatty()


def terminal_size(default=(100, 40)):
    try:
        size = shutil.get_terminal_size((default[0], default[1]))
    except Exception:  # pragma: no cover - exotic environments
        return default
    return max(size.columns, 20), max(size.lines, 12)


# --------------------------------------------------------------------------- #
# frame -> character cells
# --------------------------------------------------------------------------- #
def cells(srgb, cols, rows, dither=0.5, supersample=1):
    """Resample a frame to ``cols`` x ``2*rows`` block colours.

    Returns float32 ``(rows, cols, 2, 3)`` in sRGB 0..1: the first entry of the
    last axis is the upper half of the character cell, the second the lower one.
    """
    img = np.asarray(srgb)
    img = img.astype(np.float32)
    if img.max(initial=0.0) > 1.5:  # uint8 in, 0..1 out
        img /= 255.0
    if img.ndim == 2:
        img = np.repeat(img[:, :, None], 3, axis=2)
    ss = max(1.0, float(supersample))
    sh, sw = max(rows * 2, 2), max(cols, 2)
    shs, sws = int(round(sh * ss)), int(round(sw * ss))
    small = resize_bilinear(np.ascontiguousarray(img), shs, sws)
    if ss > 1.0:
        # box filter: a supersampled render keeps the petal edges sharp where a
        # 1:1 bilinear downsample would smear them
        small = resize_bilinear(small, sh, sw)
    if dither:
        small = small + bayer(small.shape[0], small.shape[1])[:, :, None] * (dither / 255.0)
    small = np.clip(small, 0.0, 1.0)
    small = np.concatenate([small, np.repeat(small[-1:], 2 * rows - small.shape[0], 0)], 0)[: rows * 2]
    return small.reshape(rows, 2, cols, 3).transpose(0, 2, 1, 3).copy()


def _luma(c):
    return c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722


# --------------------------------------------------------------------------- #
# character cells -> text
# --------------------------------------------------------------------------- #
def paint(cell, truecolor=True, floor=0.030, default_rgb=(0, 0, 0), mono=False):
    """Turn block colours into a list of strings (one per terminal row)."""
    top, bot = cell[:, :, 0], cell[:, :, 1]
    rows, cols = top.shape[:2]

    lum = np.maximum(_luma(top), _luma(bot))
    dark = lum < floor

    if mono and not truecolor:
        # the ramp is linear in perceived brightness, the film is not: a gamma
        # lift (and a small gain) is what makes a dark key-visual readable
        mx = np.maximum(np.maximum(top[..., 0], top[..., 1]), top[..., 2])
        mx2 = np.maximum(np.maximum(bot[..., 0], bot[..., 1]), bot[..., 2])
        # the key-visual is dark *and* saturated, so pure luma turns the petals
        # to grey mush: mix in the brightest channel, then auto-level the frame
        # (a percentile stretch) so the ramp always spans the full range
        tone = 0.55 * lum + 0.45 * np.maximum(mx, mx2)
        lo = np.percentile(tone, 2.0)
        hi = np.percentile(tone, 99.5)
        v = np.clip((tone - lo) / max(hi - lo, 1e-3), 0.0, 1.0) ** 0.85
        step = np.clip(np.rint(v * (len(RAMP) - 1)), 0, len(RAMP) - 1).astype(np.int32)
        chars = np.array(list(RAMP))
        return ["".join(chars[line]) for line in step]

    rgb = np.clip(np.rint(np.concatenate([top, bot], axis=2) * 255.0), 0, 255).astype(np.int32)
    top8, bot8 = rgb[:, :, :3], rgb[:, :, 3:]

    cache = {}

    def fg(c):
        key = (c[0] << 16) | (c[1] << 8) | c[2]
        s = cache.get(key)
        if s is None:
            s = "\x1b[38;2;%d;%d;%dm" % (c[0], c[1], c[2])
            cache[key] = s
        return s

    def bg(c):
        key = 1 << 24 | (c[0] << 16) | (c[1] << 8) | c[2]
        s = cache.get(key)
        if s is None:
            s = "\x1b[48;2;%d;%d;%dm" % (c[0], c[1], c[2])
            cache[key] = s
        return s

    if not truecolor:
        # 256 colour fallback: nearest cube entry, one glyph per cell
        def c256(c):
            key = 2 << 24 | (c[0] << 16) | (c[1] << 8) | c[2]
            s = cache.get(key)
            if s is None:
                q = (np.clip(c, 0, 255) / 51.0).round().astype(int)
                s = "\x1b[38;5;%dm" % (16 + 36 * q[0] + 6 * q[1] + q[2])
                cache[key] = s
            return s

        lines = []
        for y in range(rows):
            parts = []
            state = None
            for x in range(cols):
                if dark[y, x]:
                    if state is not None:
                        parts.append(RESET)
                        state = None
                    parts.append(" ")
                    continue
                col = c256(top8[y, x])
                if col != state:
                    parts.append(col)
                    state = col
                parts.append(FULL)
            parts.append(RESET)
            lines.append("".join(parts))
        return lines

    dstr = "\x1b[48;2;%d;%d;%dm" % default_rgb
    lines = []
    for y in range(rows):
        parts = []
        state = None  # None means "default background, no glyph"
        for x in range(cols):
            if dark[y, x]:
                if state is not None:
                    parts.append("\x1b[49m" + RESET)
                    state = None
                parts.append(" ")
                continue
            s = fg(top8[y, x]) + bg(bot8[y, x]) + UPPER
            if s != state:
                parts.append(s)
                state = s
            else:
                parts.append(UPPER)
        parts.append("\x1b[49m" + RESET)
        lines.append("".join(parts))
    _ = dstr
    return lines


def frame_lines(srgb, cols, rows, truecolor=True, supersample=1, **kw):
    return paint(cells(srgb, cols, rows, supersample=supersample),
                 truecolor=truecolor, **kw)


# --------------------------------------------------------------------------- #
# the artist object the renderer talks to
# --------------------------------------------------------------------------- #
class Artist:
    """Paints frames into a text stream (by default: stdout)."""

    def __init__(self, cols=None, rows=None, out=None, truecolor=None, mono=False,
                 auto_size=True, supersample=1):
        if cols is None or rows is None:
            c, r = terminal_size()
            cols = cols or c
            rows = rows or r - 1   # one row is used by the status line
        self.cols, self.rows = int(cols), int(rows)
        self.out = sys.stdout if out is None else out
        self.truecolor = supports_truecolor(self.out) if truecolor is None else bool(truecolor)
        self.mono = mono
        self.auto_size = auto_size
        self.supersample = supersample
        self.frames = 0

    # -- geometry ---------------------------------------------------------- #
    def fit(self, aspect=16 / 9, max_cols=None, max_rows=None):
        """Shrink to the current terminal while keeping the film's aspect."""
        c, r = terminal_size()
        c = min(c, max_cols) if max_cols else c
        r = r - 1                       # leave the last row for the status
        r = min(r, max_rows) if max_rows else r
        # a character cell is roughly twice as tall as it is wide, and we use
        # two vertical pixels per cell -> cols/rows should follow aspect * 4/... :
        # height_px = 2 * rows, width_px = cols  =>  cols = aspect * 2 * rows
        rows = max(8, min(r, int(round(c / (aspect * 2.0)))))
        cols = max(16, int(round(rows * aspect * 2.0)))
        self.cols, self.rows = cols, rows
        return self

    def size(self):
        return self.cols, self.rows

    # -- output ------------------------------------------------------------ #
    def paint(self, srgb):
        return frame_lines(srgb, self.cols, self.rows, truecolor=self.truecolor,
                           mono=self.mono, supersample=self.supersample)

    def show(self, srgb, home=True):
        lines = self.paint(srgb)
        buf = (HOME if home else "") + "\n".join(lines)
        self.out.write(buf)
        self.out.flush()
        self.frames += 1
        return len(lines)

    def status(self, text, row=None):
        """One dim line at the bottom of the canvas."""
        if not text:
            return
        row = self.rows + 1 if row is None else row
        self.out.write("\x1b[%d;1H\x1b[0m\x1b[2m%s\x1b[0m" % (row, text[: self.cols]))
        self.out.flush()


# --------------------------------------------------------------------------- #
# little helpers used by the intro titles
# --------------------------------------------------------------------------- #
def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def visible_prefix(text, amount):
    """The first ``amount`` (0..1) of a string, on grapheme-free ASCII."""
    n = int(round(len(text) * min(max(amount, 0.0), 1.0)))
    return text[:n]


def centre(text, width):
    pad = max(0, (width - len(text)) // 2)
    return " " * pad + text


def wrap(text, width):
    words, lines, line = text.split(), [], ""
    for w in words:
        if not line:
            line = w
        elif len(line) + 1 + len(w) <= width:
            line += " " + w
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines
