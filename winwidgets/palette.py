"""Colours and geometry for the *desktop* widgets (the tkinter ones).

The terminal board carries colours as ANSI styles; a desktop window needs plain
hex strings and a contrast check, so this module is the single place that turns
"accent + wallpaper tint" into a complete set of roles.

Kept free of tkinter on purpose: every rule here can be tested headless, which
matters because the GUI itself cannot run in CI.
"""

from __future__ import annotations

from .wallpaper import luminance

LIGHT_TEXT = (245, 246, 250)
DARK_TEXT = (16, 17, 22)


def hex_colour(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def blend(a, b, amount: float):
    amount = max(0.0, min(1.0, amount))
    return tuple(x * (1 - amount) + y * amount for x, y in zip(a, b))


def contrast(fg, bg) -> float:
    """WCAG contrast ratio, 1..21."""
    def rel(rgb):
        out = []
        for c in rgb:
            c = c / 255.0
            out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    a, b = rel(fg), rel(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def ensure_contrast(fg, bg, ratio=4.5, toward=(255, 255, 255)):
    """Push ``fg`` toward white (or black) until it is readable on ``bg``."""
    if contrast(fg, bg) >= ratio:
        return fg
    best = fg
    for step in range(1, 21):
        candidate = blend(fg, toward, step / 20.0)
        best = candidate
        if contrast(candidate, bg) >= ratio:
            break
    return best


def palette(accent, tint, mode="dark", opacity_floor=0.0):
    """Every colour a card needs, derived from the wallpaper.

    ``tint`` is the wallpaper's dominant dark (or light) colour.  The card
    background is that tint, *not* a fixed grey: it is what makes the widgets
    look like they belong to the picture behind them.
    """
    accent = tuple(accent)
    tint = tuple(tint)
    if mode == "light":
        base = blend(tint, (255, 255, 255), 0.55)
        text = DARK_TEXT
    else:
        # acrylic, not a black box: the wallpaper's tint is lifted toward a
        # neutral grey so the glass reads as glass and the wallpaper shows
        # through the 80 % alpha; pure tint would look like a hole
        base = blend(tint, (36, 36, 41), 0.42)
        text = LIGHT_TEXT

    accent_usable = ensure_contrast(accent, base, 4.0)
    return {
        "bg": hex_colour(base),
        "bg_rgb": base,
        "stroke": hex_colour(blend(accent_usable, base, 0.72)),
        "text": hex_colour(text),
        "dim": hex_colour(blend(text, base, 0.42)),
        "faint": hex_colour(blend(text, base, 0.66)),
        "track": hex_colour(blend(text, base, 0.88)),
        "highlight": hex_colour(blend(text, base, 0.94)),
        "accent": hex_colour(accent_usable),
        "accent_soft": hex_colour(blend(accent_usable, base, 0.55)),
        "good": "#6ede96",
        "warn": "#ffbe50",
        "bad": "#ff6060",
        "mode": mode,
    }


def level_colour(pct: float, pal) -> str:
    """Load colouring, identical to the terminal board."""
    if pct >= 90:
        return pal["bad"]
    if pct >= 75:
        return pal["warn"]
    return pal["accent"]


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
SIZES = {
    "s": {"w": 230, "h": 108, "font": 8, "title": 9, "hero": 12},
    "m": {"w": 300, "h": 148, "font": 9, "title": 10, "hero": 15},
    "l": {"w": 380, "h": 190, "font": 11, "title": 12, "hero": 19},
}

# how much content a card type needs, in "rows", so a widget can ask for the
# right height instead of every card being the same box
ROWS = {
    "cpu": 3,
    "memory": 2,
    "disk": 2,
    "network": 3,
    "processes": 4,
    "clock": 3,
    "battery": 1,
}


def card_size(kind, size="m"):
    """The card's own content decides the height; the size preset the width.

    Rows are 5 rows of text: title, hero number, a bar or sparkline, and the
    detail line - roughly ``34 + rows * (font + 9)`` px, which is what the
    text actually needs, instead of one flat box for every widget.
    """
    spec = SIZES.get(size, SIZES["m"])
    rows = ROWS.get(kind, 2)
    w = spec["w"] if kind != "processes" else int(spec["w"] * 1.06)
    h = int(34 + (rows + 1) * (spec["font"] + 9))
    return int(w), int(h)


def fit_in_screen(x, y, w, h, screen_w, screen_h, margin=8):
    """Keep a window fully on screen (after a resolution change, say)."""
    x = max(margin, min(int(x), screen_w - w - margin))
    y = max(margin, min(int(y), screen_h - h - margin))
    return x, y


def default_positions(kinds, sizes, screen_w, screen_h, margin=24, gap=14):
    """A tidy first-run arrangement: one column on the right of the screen."""
    out = {}
    y = margin + 40
    for kind in kinds:
        w, h = sizes[kind]
        out[kind] = (screen_w - w - margin, y)
        y += h + gap
        if y + h > screen_h - margin:              # start a second column
            y = margin + 40
    return out
