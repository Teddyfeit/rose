"""Render a preview of the desktop widgets as a PNG.

    .venv/bin/python tools/desktop_shot.py [wallpaper.png] [--out docs/desktop.png]

It draws the *same* primitives the tkinter cards draw
(``winwidgets.content.build``) with the *same* colours (``palette.palette``),
on top of your wallpaper, at the configured opacity - so the picture shows what
the windows will look like.  Pillow stands in for the Tk canvas: rounded
card, accent stroke, bars, sparklines, text.

This is a preview, not a screenshot: the real thing is a set of transparent
windows over the actual desktop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from winwidgets import content, palette as P          # noqa: E402
from winwidgets.term import Canvas, Theme              # noqa: E402
from winwidgets.widgets import make as make_terminal_widgets     # noqa: E402
from winwidgets.collect import Collector              # noqa: E402
from winwidgets.wallpaper import (find_wallpaper, sample_pixels,   # noqa: E402
                                  scheme_from_pixels)

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SCALE = 2                       # draw at 2x, downscale: text stays crisp
OPACITY = 1.0
TRANSPARENT = True      # only the characters - no card behind them
MONO = None
MONO_W = 7          # char advance in *device* px (set in main)
MONO_H = 12         # line height in device px


def font(size, bold=False, path=None):
    try:
        if path:
            return ImageFont.truetype(path, int(size * SCALE))
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, int(size * SCALE))
    except OSError:
        return ImageFont.load_default()


def demo_wallpaper(size, seed=7):
    """A stand-in wallpaper, so the preview never shows someone's real desktop.

    Dark teal-to-indigo gradient with a warm light blob off-centre - the kind of
    picture the colour picker is designed for.
    """
    rng = np.random.default_rng(seed)
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xx / w, yy / h
    top = np.array([24, 30, 54], np.float32)
    bottom = np.array([8, 10, 16], np.float32)
    img = top[None, None, :] * (1 - v[..., None]) + bottom[None, None, :] * v[..., None]
    blobs = [
        (0.68, 0.30, 0.30, np.array([180, 72, 58], np.float32), 1.1),   # warm
        (0.25, 0.72, 0.42, np.array([42, 96, 150], np.float32), 0.8),   # cool
        (0.85, 0.85, 0.22, np.array([120, 60, 140], np.float32), 0.7),  # violet
    ]
    for cx, cy, rad, colour, gain in blobs:
        d2 = ((u - cx) ** 2 + (v - cy) ** 2) / (rad * rad)
        img += (np.exp(-d2 * 3.0) * gain)[..., None] * colour[None, None, :]
    img += rng.normal(0, 1.6, img.shape).astype(np.float32)   # a little grain
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def fit_wallpaper(path, size):
    """The wallpaper, cropped to fill - the way Windows fills the desktop."""
    if path is None:
        return Image.new("RGB", size, (18, 20, 26))
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return Image.new("RGB", size, (18, 20, 26))
    target_w, target_h = size
    scale = max(target_w / img.width, target_h / img.height)
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                     Image.LANCZOS)
    left = (img.width - target_w) // 2
    top = (img.height - target_h) // 3
    return img.crop((left, top, left + target_w, top + target_h))


def _wal_luminance(rgb):
    r, g, b = (c / 255.0 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def colour_of(hex_string):
    return tuple(int(hex_string[i:i + 2], 16) for i in (1, 3, 5))


def rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


ASCII_COLS = {"s": 34, "m": 44, "l": 56}
ASCII_ROWS = {"cpu": 10, "memory": 9, "disk": 9, "network": 9, "clock": 9,
              "processes": 11, "battery": 7}


def ascii_theme(pal):
    """The terminal theme, coloured from the wallpaper palette (like the app)."""
    def rgb(h):
        return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))

    theme = Theme(accent=rgb(pal["accent"]), mode="truecolor")
    for name in ("text", "dim", "faint", "track"):
        style = theme.styles[name]
        theme.styles[name] = type(style)(name, rgb(pal[name]))
    theme.styles["card"] = type(theme.styles["card"])("card", rgb(pal["stroke"]))
    theme._cache.clear()
    return theme


def draw_ascii_card(kind, cols, snap, hist, pal, alpha, mono):
    """A card drawn as character cells - the exact terminal widget output.

    No panel and no caption: the ASCII frame (``╭─╮``) *is* the card, just like
    on the terminal board.  All coordinates are device pixels (2x canvas).
    """
    pad = content.PAD * SCALE
    line_h = MONO_H * SCALE
    char_w = MONO_W

    theme = ascii_theme(pal)
    widget = make_terminal_widgets([kind])[0]
    rows = max(4, widget.height(cols))
    grid = Canvas(cols, rows, theme)
    grid.clear(style="text")
    widget.draw(grid, 0, 0, cols, snap, hist)

    w = int(2 * pad + cols * char_w)
    h = int(rows * line_h + 2 * pad - 6 * SCALE)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    a = int(round(alpha * 255))
    if not TRANSPARENT:
        rounded(draw, (1, 1, w - 1, h - 1), 7 * SCALE,
                fill=colour_of(pal["bg"]) + (a,), outline=None)

    for row, (chars, styles) in enumerate(zip(grid.ch, grid.st)):
        line = "".join(chars)
        if not line.strip():
            continue
        y_px = pad - 6 * SCALE + row * line_h
        col = 0
        while col < len(chars):
            name = styles[col]
            run_start = col
            while col < len(chars) and styles[col] == name:
                col += 1
            text = line[run_start:col]
            if not text.strip():
                continue
            style = theme.styles.get(name)
            colour = style.fg if style and style.fg else colour_of(pal["text"])
            draw.text((pad + run_start * char_w, y_px), text, font=mono,
                      fill=colour + (a,), anchor="la")
    return layer, w / SCALE, h / SCALE


def draw_card(base, kind, x, y, w, h, prim, pal, alpha, title=None, title_font=None):
    """One card: a translucent rounded panel + the primitives."""
    layer = Image.new("RGBA", (w * SCALE, h * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bg = tuple(int(pal["bg"][i:i + 2], 16) for i in (1, 3, 5))
    stroke = tuple(int(pal["stroke"][i:i + 2], 16) for i in (1, 3, 5))
    a = int(round(alpha * 255))
    rounded(draw, (1, 1, w * SCALE - 1, h * SCALE - 1), 9 * SCALE,
            fill=bg + (a,), outline=stroke + (min(255, a + 30),), width=max(1, SCALE // 2))
    if title:
        # after the panel: the caption sits on the card, not under it
        draw.text((content.PAD * SCALE, 11 * SCALE), title.upper(),
                  font=title_font, fill=colour_of(pal["dim"]) + (a,), anchor="la")

    def colour(item):
        return tuple(int(item[i:i + 2], 16) for i in (1, 3, 5))

    for item in prim:
        kind_ = item["type"]
        if kind_ == "text":
            f = font(15 if item.get("role") == "hero" else 9,
                     bold=item.get("role") == "hero")
            anchor = {"nw": "la", "ne": "ra", "sw": "ld", "se": "rd"}.get(item["anchor"], "la")
            draw.text((item["x"] * SCALE, item["y"] * SCALE), item["text"],
                      font=f, fill=colour(item["colour"]) + (a,), anchor=anchor)
        elif kind_ == "bar":
            bx, by, bw, bh = (item[k] * SCALE for k in ("x", "y", "w", "h"))
            track = tuple(int(pal["track"][i:i + 2], 16) for i in (1, 3, 5))
            rounded(draw, (bx, by, bx + bw, by + bh), bh / 2, fill=track + (a,))
            filled = item["pct"] / 100.0 * bw
            if filled > 1:
                rounded(draw, (bx, by, bx + max(bh, filled), by + bh), bh / 2,
                        fill=colour(item["colour"]) + (a,))
        elif kind_ == "spark":
            values = item["values"]
            if len(values) < 2:
                continue
            sx, sy, sw, sh = (item[k] * SCALE for k in ("x", "y", "w", "h"))
            high = max(1e-6, max(values))
            step = sw / max(1, len(values) - 1)
            points = [(sx + i * step, sy + sh - max(0.0, min(1.0, v / high)) * sh)
                      for i, v in enumerate(values)]
            fill = tuple(int(item["fill"][i:i + 2], 16) for i in (1, 3, 5))
            draw.polygon(points + [(sx + sw, sy + sh), (sx, sy + sh)],
                         fill=fill + (a,))
            draw.line(points, fill=colour(item["colour"]) + (a,), width=max(1, SCALE))
        elif kind_ == "ticks":
            slot, height = item["slot"] * SCALE, item["h"] * SCALE
            for i, value in enumerate(item["values"]):
                bx = (item["x"] * SCALE) + i * (slot + SCALE)
                if bx + slot > (item["x"] + item["w"]) * SCALE:
                    break
                bh = 3 + max(0.0, min(100.0, value)) / 100.0 * height
                top = item["y"] * SCALE + height - bh
                draw.rectangle((bx, top, bx + slot, top + bh),
                               fill=colour(P.level_colour(value, pal)) + (a,))
    return layer


def main(argv):
    out = ROOT / "docs" / "desktop.png"
    wallpaper = None
    args = [a for a in argv if not a.startswith("--")]
    if "--out" in argv:
        out = Path(argv[argv.index("--out") + 1])
        args = [a for a in args if a != str(out)]
    if "--demo" in argv or not args:
        wallpaper = None if "--demo" in argv else find_wallpaper()
    if args:
        wallpaper = Path(args[0])

    pixels = sample_pixels(wallpaper, 96) if wallpaper else None
    scheme = scheme_from_pixels(pixels)
    if pixels is None:
        demo = demo_wallpaper((640, 360))
        getter = getattr(demo, "get_flattened_data", None) or demo.getdata
        pixels = list(getter())
        scheme = scheme_from_pixels(pixels)
    pal = P.palette(scheme["accent"], scheme["tint"], scheme["mode"])

    width, height = 1600, 900
    # the desktop is drawn at 2x and downscaled at the end: cards are composited
    # at (x * SCALE, y * SCALE), so the background must be 2x too
    canvas_size = (width * SCALE, height * SCALE)
    if wallpaper is not None:
        base = fit_wallpaper(wallpaper, canvas_size).convert("RGBA")
    else:
        base = demo_wallpaper(canvas_size).convert("RGB").convert("RGBA")

    collector = Collector()
    for _ in range(5):
        collector.tick()
    snap = collector.sample
    hist = {"cpu": collector.h_cpu, "mem": collector.h_mem,
            "down": collector.h_down, "up": collector.h_up}

    title_font = font(8)
    global MONO, MONO_W, MONO_H
    MONO = font(10, path=FONT_MONO)
    MONO_W = int(round(MONO.getlength("0")))            # device px per character
    MONO_H = int(round(10 * 1.30))

    # without a card the characters sit directly on the wallpaper, so the
    # palette must be readable on the picture itself, not on a dark panel
    if TRANSPARENT:
        lum = sum(_wal_luminance(p) for p in (pixels or [(30, 30, 30)])) / max(1, len(pixels or [1]))
        if lum > 0.55:
            pal = dict(pal)
            for key, value in (("text", (18, 20, 26)), ("dim", (52, 56, 66)),
                               ("faint", (86, 90, 100))):
                pal[key] = "#%02x%02x%02x" % value
            pal["track"] = "#3a3d46"

    kinds = ["clock", "cpu", "memory", "disk", "network", "processes"]
    cols = 36
    sizes, layers = {}, {}
    for kind in kinds:
        layer, w, h = draw_ascii_card(kind, cols, snap, hist, pal, OPACITY, MONO)
        layers[kind] = layer
        sizes[kind] = (w, h)

    gap = 12
    total = sum(sizes[k][1] for k in kinds) + gap * (len(kinds) - 1)
    y = max(30, (height - total) / 2)
    x = width - max(sizes[k][0] for k in kinds) - 36
    for kind in kinds:
        base.alpha_composite(layers[kind], (int(x * SCALE), int(y * SCALE)))
        y += sizes[kind][1] + gap

    img = base.convert("RGB").resize((width // 2, height // 2), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True, quality=88)
    source = str(wallpaper) if wallpaper else "no wallpaper found"
    print(f"  {out}  {out.stat().st_size / 1e3:.0f} kB")
    print(f"  wallpaper: {source}")
    print("  accent   : #%02x%02x%02x   tint: #%02x%02x%02x   mode: %s"
          % (*scheme["accent"], *scheme["tint"], scheme["mode"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
