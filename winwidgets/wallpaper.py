"""Find the desktop wallpaper and pull a colour scheme out of it.

Two sources for the image path, in this order:

* ``W11WIDGETS_WALLPAPER`` - an explicit override (a path or a directory)
* Windows: ``HKCU\\Control Panel\\Desktop\\Wallpaper`` (what the desktop actually
  uses, including Slideshow folders) and the Windows Spotlight cache, which is
  where Win11's "Windows-Blickpunkt" images live when no path is set.

The scheme itself is a *saliency* pick, not an average: a wallpaper is usually
mostly sky or mostly dark, so an average comes out grey.  Instead the pixels
are clustered coarsely, and the cluster that is (a) far from the image's mean
colour and (b) bright enough to be seen on a dark card wins.  That is the one
that looks like "the accent of this picture" to a human.

Only the standard library and ``tkinter.PhotoImage``/Pillow are needed; if
neither is present the caller gets ``None`` and keeps its default palette.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from pathlib import Path

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


# --------------------------------------------------------------------------- #
# finding the image
# --------------------------------------------------------------------------- #
def _registry_wallpaper():
    try:                                     # pragma: no cover - Windows only
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Control Panel\Desktop") as key:
            value, _ = winreg.QueryValueEx(key, "Wallpaper")
        return value if value else None
    except Exception:
        return None


def _spotlight_dir():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return (Path(base) / "Packages"
            / "Microsoft.Windows.ContentDeliveryManager_cw5n1h2txyewy"
            / "LocalState" / "Assets")


def find_wallpaper():
    """Path of the desktop wallpaper, or None."""
    override = os.environ.get("W11WIDGETS_WALLPAPER")
    if override:
        path = Path(override).expanduser()
        if path.is_dir():
            newest = _newest_image(path)
            if newest:
                return newest
        elif path.is_file():
            return path

    candidates = []
    registered = _registry_wallpaper()
    if registered:
        path = Path(os.path.expandvars(registered))
        if path.is_file():
            candidates.append(path)

    spotlight = _spotlight_dir()
    if spotlight and spotlight.is_dir():
        newest = _newest_image(spotlight, min_bytes=80_000)
        if newest:
            candidates.append(newest)

    for path in candidates:
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return path
    return None


def _newest_image(directory: Path, min_bytes=10_000):
    best, best_mtime = None, -1.0
    try:
        for entry in directory.iterdir():
            try:
                if not entry.is_file() or entry.stat().st_size < min_bytes:
                    continue
            except OSError:
                continue
            if entry.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            mtime = entry.stat().st_mtime
            if mtime > best_mtime:
                best, best_mtime = entry, mtime
    except OSError:
        return None
    return best


# --------------------------------------------------------------------------- #
# decoding without Pillow
# --------------------------------------------------------------------------- #
def _read_png(path, max_side=96):
    """A tiny PNG reader (8 bit RGB/RGBA, no interlace).

    Enough for wallpapers; anything else returns None and the caller tries
    Pillow or gives up.  It exists so the colour scheme also works on a Python
    without Pillow, which is the same "no wheels for your version" problem the
    rest of this package tries not to have.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, width, height, depth, colour = 8, 0, 0, 0, 0
    idat = bytearray()
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if depth != 8 or colour not in (2, 6) or not width or not height:
        return None
    channels = 3 if colour == 2 else 4
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return None
    stride = width * channels
    out = bytearray(stride * height)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        filter_type = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    # same reduction rule as Image.thumbnail(max_side): the *larger* side is
    # scaled down, the aspect ratio is kept
    longest = max(width, height)
    step = max(1, int(round(longest / float(max_side)))) if longest > max_side else 1
    pixels = []
    for y in range(0, height, step):
        row = y * stride
        for x in range(0, width, step):
            i = row + x * channels
            pixels.append((out[i], out[i + 1], out[i + 2]))
    return pixels


def _read_with_pillow(path, max_side=96):
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side))
            data = getattr(img, "get_flattened_data", None)
            if data is not None:                    # Pillow 12+
                return list(data())
            return list(img.getdata())
    except Exception:
        return None


def sample_pixels(path, max_side=96):
    """Downsampled RGB pixels of an image (Pillow if present, else small PNG)."""
    if path is None:
        return None
    pixels = _read_with_pillow(path, max_side)
    if pixels:
        return pixels
    return _read_png(path, max_side)


# --------------------------------------------------------------------------- #
# colour maths
# --------------------------------------------------------------------------- #
def luminance(rgb):
    r, g, b = (c / 255.0 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hsv(rgb):
    r, g, b = (c / 255.0 for c in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d < 1e-6:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / d)) % 360
    elif mx == g:
        h = 60 * ((b - r) / d) + 120
    else:
        h = 60 * ((r - g) / d) + 240
    s = 0 if mx <= 0 else d / mx
    return h, s, mx


def _from_hsv(h, s, v):
    c = v * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return tuple(int(round((v + n) * 255)) for n, v in zip((m, m, m), (r, g, b)))


def _blend(a, b, amount):
    """``amount`` 0..1 of ``b`` mixed into ``a``."""
    return tuple(int(round(x * (1 - amount) + y * amount)) for x, y in zip(a, b))


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def clusters(pixels, count=6, iterations=6):
    """Cheap k-means; returns ``[(weight, rgb)]`` sorted by weight."""
    if not pixels:
        return []
    # deterministic seeding: spread the seeds over the pixels themselves
    step = max(1, len(pixels) // count)
    centres = [tuple(float(c) for c in pixels[i * step])
               for i in range(count) if i * step < len(pixels)]
    if not centres:
        return []
    for _ in range(iterations):
        sums = [[0.0, 0.0, 0.0, 0] for _ in centres]
        for px in pixels:
            best, best_d = 0, float("inf")
            for i, c in enumerate(centres):
                d = (px[0] - c[0]) ** 2 + (px[1] - c[1]) ** 2 + (px[2] - c[2]) ** 2
                if d < best_d:
                    best, best_d = i, d
            s = sums[best]
            s[0] += px[0]
            s[1] += px[1]
            s[2] += px[2]
            s[3] += 1
        moved = False
        for i, s in enumerate(sums):
            if s[3]:
                new = (s[0] / s[3], s[1] / s[3], s[2] / s[3])
                if _distance(new, centres[i]) > 1.0:
                    moved = True
                centres[i] = new
        if not moved:
            break
    weights = [0] * len(centres)
    for px in pixels:
        best, best_d = 0, float("inf")
        for i, c in enumerate(centres):
            d = (px[0] - c[0]) ** 2 + (px[1] - c[1]) ** 2 + (px[2] - c[2]) ** 2
            if d < best_d:
                best, best_d = i, d
        weights[best] += 1
    total = sum(weights) or 1
    out = [(w / total, tuple(int(round(v)) for v in c))
           for w, c in zip(weights, centres)]
    out.sort(reverse=True)
    return out


# --------------------------------------------------------------------------- #
# the scheme
# --------------------------------------------------------------------------- #
SCHEME_DEFAULTS = {
    "accent": (0x60, 0xCD, 0xFF),
    "tint": (24, 24, 30),
    "mode": "dark",          # "dark" or "light"
    "wallpaper": None,
    "source": "default",     # default | wallpaper | override
}


def scheme_from_pixels(pixels):
    """Turn wallpaper pixels into ``{accent, tint, mode, ...}``."""
    if not pixels:
        return dict(SCHEME_DEFAULTS)

    lum_mean = sum(luminance(p) for p in pixels) / len(pixels)
    mean = tuple(sum(p[i] for p in pixels) / len(pixels) for i in range(3))
    mode = "dark" if lum_mean < 0.55 else "light"

    parts = clusters(pixels, count=8)

    # Split the picture into "grey" (sky, clouds, concrete) and "colour".
    # An accent has to be an area a human would point at, so the score rewards
    # saturation and size, and punishes the huge neutral areas that dominate
    # most wallpapers - a vignette-black or a cloud-white is not an accent.
    chroma = [(w, c) for w, c in parts if _hsv(c)[1] >= 0.25]
    pool = chroma or parts
    best, best_score = None, -1.0
    for weight, rgb in pool:
        h, sat, val = _hsv(rgb)
        lum = luminance(rgb)
        if lum < 0.06 or (val > 0.96 and sat < 0.10):
            continue                          # cannot carry an accent colour
        separation = min(1.0, _distance(rgb, mean) / 320.0)
        score = (0.55 * sat + 0.25 * separation + 0.20 * min(1.0, val * 1.4))
        score *= 0.35 + 0.65 * min(1.0, weight / 0.12)     # tiny specks do not win
        if score > best_score:
            best, best_score = rgb, score

    if not chroma:
        # a grey wallpaper has no accent to borrow: stay neutral instead of
        # inventing a random hue
        accent = (205, 209, 214)          # Windows' own "no accent" grey
    else:
        accent = best
        h, ac_s, _ = _hsv(accent)
        # The accent is text and thin bars, so it must be readable on the card:
        # the hue is kept (that is the wallpaper's identity), the value is put
        # into a band that works on dark acrylic, and a deep colour is lifted
        # toward white so small text does not sit at 2:1 contrast.
        accent = _from_hsv(h, max(0.55, ac_s), 0.95)
        if luminance(accent) < 0.32:
            accent = _blend(accent, (255, 255, 255), 0.30)

    # the tint: the dominant *dark* cluster, pushed a little toward the accent's
    # hue, so the cards sit on the wallpaper instead of on a grey box
    dark_parts = [(w, c) for w, c in parts if luminance(c) < 0.35]
    if dark_parts:
        tint = max(dark_parts, key=lambda p: p[0])[1]
    else:
        tint = mean
    th, ts, tv = _hsv(tint)
    tint = _from_hsv(h, min(0.45, max(0.10, ts * 0.7)), min(0.22, max(0.06, tv * 0.55)))
    if mode == "light":
        tint = _from_hsv(h, min(0.25, ts * 0.4), 0.96)

    return {
        "accent": tuple(accent),
        "tint": tuple(tint),
        "mode": mode,
        "wallpaper": None,
        "source": "wallpaper",
    }
