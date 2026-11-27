"""Render ``docs/terminal.png`` - the terminal player as a picture.

The mock-up is built from the *same* cell grid the player writes into a real
console (``rose.terminal.cells``), so what you see here is what the terminal
shows: two pixels per character cell, drawn as rectangles.

    PYTHONPATH=. .venv/bin/python tools/terminal_shot.py
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rose.terminal import cells
from rose.tui import TerminalRose

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
COLS, ROWS = 110, 31
CW, CH = 9, 19                  # character cell of the mock-up, in pixels
PHASE = 0.145


def _font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:             # pragma: no cover - no DejaVu on this box
        return ImageFont.load_default()


def draw_blocks(draw, x0, y0, grid, cw=CW, chh=CH):
    """Half-block cells -> rectangles (exactly what ▀ + colours produce)."""
    rows, cols = grid.shape[:2]
    for y in range(rows):
        for x in range(cols):
            top = tuple(int(v) for v in np.rint(grid[y, x, 0] * 255.0))
            bot = tuple(int(v) for v in np.rint(grid[y, x, 1] * 255.0))
            px, py = x0 + x * cw, y0 + y * chh
            draw.rectangle([px, py, px + cw - 1, py + chh // 2 - 1], fill=top)
            draw.rectangle([px, py + chh // 2, px + cw - 1, py + chh - 1], fill=bot)


def main():
    player = TerminalRose(cols=COLS, rows=ROWS, stream=io.StringIO())
    player.artist.truecolor = True
    img = player.renderer.render(PHASE)
    grid = cells(img, COLS, ROWS, supersample=player.artist.supersample)

    player.artist.truecolor = False        # the plain-ASCII panel
    player.artist.mono = True
    ascii_lines = player.artist.paint(img)

    sheet_w = COLS * CW + 90
    sheet_h = 150 + ROWS * CH + 60 + len(ascii_lines) * 15 + 60
    sheet = Image.new("RGB", (sheet_w, sheet_h), (12, 10, 14))
    d = ImageDraw.Draw(sheet)

    title_font, sub_font = _font(44), _font(17)
    small, mono_font = _font(13), _font(11)

    d.text((40, 26), "R O S E", font=title_font, fill=(255, 64, 84))
    d.text((44, 84), "crimson bloom  \u00b7  nur die rose, im terminal",
           font=sub_font, fill=(150, 120, 126))

    # --- the player window ------------------------------------------------ #
    win_w = COLS * CW + 24
    win_h = ROWS * CH + 24 + 30
    ox, oy = 40, 132
    d.rounded_rectangle([ox, oy, ox + win_w, oy + win_h], 8,
                        fill=(10, 9, 11), outline=(52, 26, 34))
    d.rounded_rectangle([ox, oy, ox + win_w, oy + 30], 8, fill=(28, 20, 24))
    d.ellipse([ox + 12, oy + 11, ox + 21, oy + 20], fill=(255, 95, 86))
    d.ellipse([ox + 27, oy + 11, ox + 36, oy + 20], fill=(255, 189, 46))
    d.ellipse([ox + 42, oy + 11, ox + 51, oy + 20], fill=(39, 201, 63))
    d.text((ox + win_w - 150, oy + 8), "python -m rose", font=small, fill=(150, 126, 132))

    inner_x, inner_y = ox + 12, oy + 42
    draw_blocks(d, inner_x, inner_y, grid)
    d.text((inner_x, inner_y + ROWS * CH + 6),
           "rose   14.5%   7.6 f/s   bloom x1.0   110x62 px",
           font=small, fill=(126, 42, 56))

    # --- the same frame without colour ----------------------------------- #
    ay = oy + win_h + 34
    d.text((ox, ay), "dieselbe rose ohne farbe (taste a)", font=sub_font, fill=(168, 124, 132))
    panel_h = len(ascii_lines) * 15 + 22
    d.rounded_rectangle([ox, ay + 28, ox + COLS * CW + 24, ay + 28 + panel_h], 6,
                        fill=(10, 9, 11), outline=(46, 26, 32))
    for y, line in enumerate(ascii_lines):
        d.text((ox + 12, ay + 36 + y * 15), line, font=mono_font, fill=(214, 202, 206))

    out = Path("docs/terminal.png")
    out.parent.mkdir(exist_ok=True)
    sheet.save(out)
    print(f"  {out}  ({sheet.width}x{sheet.height})")


if __name__ == "__main__":
    main()
