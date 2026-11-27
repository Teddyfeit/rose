"""Render the widget board to a PNG, for the README and for the README's preview.

    .venv/bin/python tools/widget_shot.py            # -> docs/widgets.png

It is *not* a mock-up: the characters come from the real board
(``winwidgets.board.build(...).render()``) and the colours come from the same
theme, looked up through the escape codes the frame contains.  So the PNG can
only ever be as good as the terminal output - which is the point.

A terminal cell is 1 wide x 1 tall *of the font's cell*, and Cascadia Mono at
16 px is about 8.8 x 16.6 px; we draw at 2x and downscale for a clean look.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from winwidgets.board import build          # noqa: E402
from winwidgets.term import terminal_size   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
COLS, ROWS = 116, 42
SCALE = 2
FONT_SIZE = 16 * SCALE
PAD = 14 * SCALE
BG_TOP = (24, 24, 28)
BG_BOTTOM = (14, 14, 17)

ANSI = re.compile(r"\x1b\[([0-9;]*)m")


def cells_with_colour(lines):
    """``[(char, (r,g,b) | None, bold)]`` per row, parsed back from ANSI."""
    rows = []
    for line in lines:
        row, fg, bold = [], None, False
        last = 0
        for match in ANSI.finditer(line):
            for ch in line[last:match.start()]:
                row.append((ch, fg, bold))
            codes = [c for c in match.group(1).split(";") if c != ""]
            if not codes or codes == ["0"]:
                fg, bold = None, False
            else:
                i = 0
                while i < len(codes):
                    c = codes[i]
                    if c == "1":
                        bold = True
                        i += 1
                    elif c == "0":
                        fg, bold = None, False
                        i += 1
                    elif c == "38" and i + 4 < len(codes) and codes[i + 1] == "2":
                        fg = tuple(int(v) for v in codes[i + 2:i + 5])
                        i += 5
                    elif c == "38" and i + 2 < len(codes) and codes[i + 1] == "5":
                        n = int(codes[i + 2])
                        fg = cube256(n)
                        i += 3
                    else:
                        i += 1
            last = match.end()
        for ch in line[last:]:
            row.append((ch, fg, bold))
        rows.append(row)
    return rows


def cube256(n):
    if n >= 232:
        v = 8 + (n - 232) * 10
        return (v, v, v)
    n -= 16
    steps = [0, 95, 135, 175, 215, 255]
    return (steps[n // 36 % 6], steps[n // 6 % 6], steps[n % 6])


def sample(board, seconds=3.0, hz=4.0):
    """Collect real samples, optionally under real load, so the charts are real."""
    import time

    import psutil

    load = []
    if psutil is not None:
        import subprocess

        for _ in range(1):                     # a little honest CPU work
            load.append(subprocess.Popen(
                [sys.executable, "-c", "while True: pass"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        chunks = []
        for _ in range(3):                     # some traffic in the counters
            chunks.append(b"x" * 300_000)
        import socket

        try:
            sock = socket.socket()
            sock.close()
        except OSError:
            pass
    try:
        steps = max(2, int(seconds * hz))
        for _ in range(steps):
            time.sleep(seconds / steps)
            board.collector.tick()
    finally:
        for proc in load:
            proc.kill()


def main(out_path, live=True):
    board = build(None, plain=False)
    board.theme.mode = "truecolor"
    board.theme._cache.clear()
    # a few samples so the sparklines have a shape instead of one dot
    if live:
        sample(board)
    else:
        for _ in range(6):
            board.collector.tick()
    canvas = board.render(COLS, ROWS)
    rows = cells_with_colour(canvas.lines())

    theme = board.theme
    normal = ImageFont.truetype(FONT, FONT_SIZE)
    bold = ImageFont.truetype(FONT_BOLD, FONT_SIZE)
    # take the cell width from the font itself: a guessed width leaves 1 px gaps
    # between neighbouring box-drawing glyphs and the card borders fall apart
    adv = int(round(normal.getlength("\u2500")))
    cell_h = int(round(FONT_SIZE * 1.16))
    width = COLS * adv + PAD * 2
    height = ROWS * cell_h + PAD * 2
    img = Image.new("RGB", (width, height), BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    for y in range(height):                     # a subtle vertical gradient
        t = y / max(1, height - 1)
        colour = tuple(int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=colour)

    for y, row in enumerate(rows[:ROWS]):
        for x, (ch, fg, is_bold) in enumerate(row[:COLS]):
            if ch == " " or not fg:
                continue
            draw.text((PAD + x * adv, PAD + y * cell_h), ch,
                      font=bold if is_bold else normal, fill=fg)

    img = img.resize((round(width / SCALE), round(height / SCALE)), Image.LANCZOS)
    img.save(out_path, optimize=True)
    return out_path, board


if __name__ == "__main__":
    target = ROOT / "docs" / "widgets.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    path, board = main(target, live="--static" not in sys.argv)
    print(f"  {path}  {path.stat().st_size / 1e3:.0f} kB  "
          f"({board.collector.sample.cpu:.0f}% cpu)")
