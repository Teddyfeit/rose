"""Small, fast numpy helpers: vector math, value noise, image warps and blurs.

Everything here works on ``float32`` arrays of shape (N, 3) (points/vectors) or
(H, W, C) images and avoids Python level loops so the renderer stays vectorised.
"""

from __future__ import annotations

import numpy as np

TAU = np.float32(2.0 * np.pi)


# --------------------------------------------------------------------------- #
# scalar / vector helpers
# --------------------------------------------------------------------------- #
def clamp(x, lo=0.0, hi=1.0):
    return np.minimum(np.maximum(x, lo), hi)


def smoothstep(e0, e1, x):
    t = clamp((x - e0) / (e1 - e0))
    return t * t * (3.0 - 2.0 * t)


def mix(a, b, t):
    """Linear interpolation that also works for RGB triples."""
    return a + (b - a) * t


def normalize(v, eps=1e-8):
    n = np.sqrt(np.sum(v * v, axis=-1, keepdims=True)) + eps
    return v / n


def dot(a, b):
    return np.sum(a * b, axis=-1)


def cross(a, b):
    return np.stack(
        [
            a[:, 1] * b[:, 2] - a[:, 2] * b[:, 1],
            a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2],
            a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0],
        ],
        axis=-1,
    )


def length(v):
    return np.sqrt(np.sum(v * v, axis=-1) + 1e-12)


def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.asarray(c, dtype=np.float32)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.maximum(c, 0.0) ** (1.0 / 2.4) - 0.055)


def rgb(hex_string: str) -> np.ndarray:
    """'#1a0a12' -> linear float32 RGB triple."""
    h = hex_string.lstrip("#")
    srgb = np.array([int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)
    return srgb_to_linear(srgb)


# --------------------------------------------------------------------------- #
# hash based value noise (deterministic, vectorised)
# --------------------------------------------------------------------------- #
def _hash3(ix, iy, iz, seed):
    h = (ix.astype(np.int64) * np.int64(374761393)
         + iy.astype(np.int64) * np.int64(668265263)
         + iz.astype(np.int64) * np.int64(2246822519)
         + np.int64(seed) * np.int64(3266489917))
    h = (h ^ (h >> np.int64(13))) * np.int64(1274126177)
    h = h ^ (h >> np.int64(16))
    return (h & np.int64(0xFFFFFF)).astype(np.float32) / np.float32(16777215.0)


def value_noise3(p, seed=0):
    """Smooth value noise in [0, 1) for an (N, 3) array of sample points."""
    i = np.floor(p)
    f = p - i
    ix, iy, iz = i[:, 0], i[:, 1], i[:, 2]
    u = f * f * (3.0 - 2.0 * f)
    ux, uy, uz = u[:, 0], u[:, 1], u[:, 2]
    acc = 0.0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                w = ((ux if dx else 1.0 - ux)
                     * (uy if dy else 1.0 - uy)
                     * (uz if dz else 1.0 - uz))
                acc = acc + w * _hash3(ix + dx, iy + dy, iz + dz, seed)
    return acc


def fbm3(p, octaves=3, seed=0, lacunarity=2.03, gain=0.5):
    total = np.zeros(p.shape[0], dtype=np.float32)
    amp = np.float32(1.0)
    norm = np.float32(0.0)
    q = p.copy()
    for o in range(octaves):
        total += amp * value_noise3(q, seed + o * 17)
        norm += amp
        q *= lacunarity
        amp *= gain
    return total / norm


def value_noise2(p, seed=0):
    """2D value noise (uses the 3D version with z=0)."""
    z = np.zeros((p.shape[0], 1), dtype=np.float32)
    return value_noise3(np.concatenate([p, z], axis=1), seed)


# --------------------------------------------------------------------------- #
# image ops
# --------------------------------------------------------------------------- #
def bilinear_warp(src, sx, sy, y0, x0, w00, w01, w10, w11, idx, P):
    """Gather-bilinear resample of ``src`` using pre-computed sampling data.

    ``src`` is (H, W, C); the sampling data must be produced by
    :func:`warp_setup`.  Returning a copy of the src as a plain (M, C) array
    keeps the gather cache friendly.
    """
    s = np.ascontiguousarray(src).reshape(-1, src.shape[2])
    out = s[idx] * w00
    out += s[idx + 1] * w01
    out += s[idx + P] * w10
    out += s[idx + P + 1] * w11
    return out


def warp_setup(sx, sy, width):
    """Pre-compute integer indices / bilinear weights for a warp.

    ``sx``/``sy`` are flattened float sample coordinates (>= 0, < width/height).
    The source array passed to :func:`bilinear_warp` must therefore have one
    extra row and column of padding.
    """
    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    fx = (sx - x0).astype(np.float32)[:, None]
    fy = (sy - y0).astype(np.float32)[:, None]
    P = width + 1
    idx = y0 * P + x0
    w00 = (1.0 - fx) * (1.0 - fy)
    w01 = fx * (1.0 - fy)
    w10 = (1.0 - fx) * fy
    w11 = fx * fy
    return idx, P, w00, w01, w10, w11, x0, y0, fx, fy


def downsample(img):
    """2x2 box downsample with odd-size handling."""
    h, w = img.shape[:2]
    if h % 2:
        img = np.pad(img, ((0, 1), (0, 0), (0, 0)), mode="edge")
        h += 1
    if w % 2:
        img = np.pad(img, ((0, 0), (0, 1), (0, 0)), mode="edge")
        w += 1
    return 0.25 * (
        img[0::2, 0::2] + img[1::2, 0::2] + img[0::2, 1::2] + img[1::2, 1::2]
    )


def blur_separable(img, sigma):
    """Gaussian blur (separable, edge-clamped) on a float32 (H, W, C) image."""
    if sigma <= 0.35:
        return img
    radius = max(1, int(np.ceil(sigma * 2.6)))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    k = np.exp(-(x * x) / (2.0 * sigma * sigma))
    k /= k.sum()

    # horizontal
    pad = np.pad(img, ((0, 0), (radius, radius), (0, 0)), mode="edge")
    acc = np.zeros_like(img)
    for i, w in enumerate(k):
        acc += w * pad[:, i : i + img.shape[1]]
    img = acc

    # vertical
    pad = np.pad(img, ((radius, radius), (0, 0), (0, 0)), mode="edge")
    acc = np.zeros_like(img)
    for i, w in enumerate(k):
        acc += w * pad[i : i + img.shape[0]]
    return acc


def resize_bilinear(img, out_h, out_w):
    """Bilinear resize of a float32 (H, W, C) image to (out_h, out_w)."""
    h, w = img.shape[:2]
    ys = (np.arange(out_h, dtype=np.float32) + 0.5) * (h / out_h) - 0.5
    xs = (np.arange(out_w, dtype=np.float32) + 0.5) * (w / out_w) - 0.5
    ys = np.clip(ys, 0, h - 1)
    xs = np.clip(xs, 0, w - 1)
    y0 = np.floor(ys).astype(np.int32)
    x0 = np.floor(xs).astype(np.int32)
    y1 = np.minimum(y0 + 1, h - 1)
    x1 = np.minimum(x0 + 1, w - 1)
    fy = (ys - y0)[:, None, None]
    fx = (xs - x0)[None, :, None]
    top = img[y0][:, x0] * (1 - fx) + img[y0][:, x1] * fx
    bot = img[y1][:, x0] * (1 - fx) + img[y1][:, x1] * fx
    return top * (1 - fy) + bot * fy
