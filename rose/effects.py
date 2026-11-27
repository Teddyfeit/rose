"""Screen space effects: particles, bloom, god rays, grading and film grain.

Written from scratch in numpy — no GPU, no shader language, just arrays.
"""

from __future__ import annotations

import numpy as np

from .mathutil import (clamp, downsample, mix, resize_bilinear, smoothstep,
                       srgb_to_linear, blur_separable)


# --------------------------------------------------------------------------- #
# soft additive splats (embers, dust, glow points, sparkles)
# --------------------------------------------------------------------------- #
class Splatter:
    """Accumulates gaussian splats into a preallocated float buffer."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.buf = np.zeros((h * w, 3), np.float32)

    def add(self, x, y, radius, color, intensity):
        """x, y: pixel centres; radius: pixels; color: (N,3); intensity: (N,)."""
        x = np.atleast_1d(np.asarray(x, np.float32)).ravel()
        y = np.atleast_1d(np.asarray(y, np.float32)).ravel()
        radius = np.atleast_1d(np.asarray(radius, np.float32)).ravel()
        intensity = np.atleast_1d(np.asarray(intensity, np.float32)).ravel()
        color = np.asarray(color, np.float32).reshape(-1, 3)
        n = x.size
        if n == 0 or not np.isfinite(x).all():
            return self
        if radius.size == 1:
            radius = np.full(n, radius[0], np.float32)
        if intensity.size == 1:
            intensity = np.full(n, intensity[0], np.float32)
        if color.shape[0] == 1:
            color = np.repeat(color, n, axis=0)

        # very large blobs (halos, glows) are evaluated analytically per pixel
        big = radius > 22.0
        if big.any():
            self._add_big(x[big], y[big], radius[big], color[big], intensity[big])
        keep = ~big
        if not keep.any():
            return self
        x, y, radius, color, intensity = x[keep], y[keep], radius[keep], color[keep], intensity[keep]
        n = x.size

        # group by radius bucket so the offset grids stay small
        rb = np.clip(np.ceil(np.log2(np.maximum(radius, 0.6))), 0, 6).astype(np.int32)
        for b in np.unique(rb):
            sel = rb == b
            R = int(2 ** b)
            if not sel.any():
                continue
            ax = np.arange(-R, R + 1, dtype=np.float32)
            oy, ox = np.meshgrid(ax, ax, indexing="ij")
            oy = oy.ravel()[None, :]
            ox = ox.ravel()[None, :]
            rr = np.float32(max(radius[sel].max(), 0.7))
            fall = np.exp(-(ox * ox + oy * oy) / (2.0 * (rr * 0.5) ** 2))
            py = (y[sel][:, None] + oy).astype(np.int32)
            px = (x[sel][:, None] + ox).astype(np.int32)
            ok = (px >= 0) & (px < self.w) & (py >= 0) & (py < self.h)
            pix = np.where(ok, py * self.w + px, 0)
            wgt = fall * np.where(ok, intensity[sel][:, None], 0.0)
            col = color[sel]
            for c in range(3):
                self.buf[:, c] += np.bincount(
                    pix.ravel(),
                    weights=(wgt * col[:, c : c + 1]).ravel(),
                    minlength=self.w * self.h,
                )[: self.w * self.h]
        return self

    def _add_big(self, x, y, radius, color, intensity):
        """Analytic gaussian blobs, evaluated at full resolution in strips."""
        h, w = self.h, self.w
        xs = np.arange(w, dtype=np.float32) + 0.5
        for i in range(len(x)):
            if not np.isfinite(x[i]) or not np.isfinite(y[i]):
                continue
            sigma = max(radius[i] * 0.5, 1.0)
            amp = intensity[i] / (2.0 * np.pi * sigma * sigma) * 6.283
            x0 = max(int(x[i] - 4 * sigma), 0)
            x1 = min(int(x[i] + 4 * sigma) + 1, w)
            y0 = max(int(y[i] - 4 * sigma), 0)
            y1 = min(int(y[i] + 4 * sigma) + 1, h)
            if x1 <= x0 or y1 <= y0:
                continue
            gx = np.exp(-((xs[x0:x1] - x[i]) ** 2) / (2 * sigma * sigma))
            ys = np.arange(y0, y1, dtype=np.float32) + 0.5
            gy = np.exp(-((ys - y[i]) ** 2) / (2 * sigma * sigma))
            field = (gy[:, None] * gx[None, :]).ravel()
            idx = (np.arange(y0, y1)[:, None] * w + np.arange(x0, x1)[None, :]).ravel()
            for c in range(3):
                self.buf[idx, c] += field * amp * color[i, c]

    def image(self, h, w):
        return self.buf.reshape(h, w, 3)


def project(cam, pts):
    c = pts - cam.pos[None, :]
    pz = c @ cam.fwd
    x = cam.w * 0.5 + (c @ cam.right) / np.maximum(pz, 1e-3) * cam.focal
    y = cam.h * 0.5 - (c @ cam.up) / np.maximum(pz, 1e-3) * cam.focal
    return x, y, pz


def dust_layer(cam, dust, time, rng):
    """Slow floating motes + the occasional rising ember."""
    w, h = cam.w, cam.h
    sp = Splatter(w, h)
    p = dust.pos.copy()
    t = time
    p[:, 1] += 0.10 * np.sin(t * 0.45 + dust.phase) - t * dust.speed * 0.22
    p[:, 0] += 0.09 * np.sin(t * 0.31 + dust.phase * 1.7)
    p[:, 2] += 0.07 * np.cos(t * 0.27 + dust.phase * 1.3)
    # wrap vertically so motes keep coming
    p[:, 1] = (p[:, 1] + 2.2) % 4.4 - 2.2
    x, y, z = project(cam, p)
    ok = (z > 0.2) & (x > -40) & (x < w + 40) & (y > -40) & (y < h + 40)
    # size by distance: a mote behind the flower must not become a bright dot
    # that reads as a hole in the petals
    near = np.clip(3.2 / np.maximum(z, 0.4), 0.0, 1.6)

    twinkle = 0.35 + 0.65 * (0.5 + 0.5 * np.sin(t * 3.1 + dust.phase * 5.0))
    size = dust.size * cam.focal / np.maximum(z, 0.5) * 0.045
    warm_col = srgb_to_linear(np.array([1.0, 0.24, 0.30], np.float32))
    cold_col = srgb_to_linear(np.array([0.45, 0.85, 1.0], np.float32))
    cols = warm_col[None, :] * dust.warm[:, None] + cold_col[None, :] * (1 - dust.warm)[:, None]
    inten = 0.055 * twinkle * dust.size ** 1.4 * near ** 1.5
    sp.add(x[ok], y[ok], np.maximum(size[ok], 0.8), cols[ok], inten[ok])

    # sparse hot embers that rise and flicker
    n_e = 26
    ph = rng.random(n_e) * 6.28
    life = (t * (0.10 + 0.06 * rng.random(n_e)) + ph) % 1.0
    ex = (rng.random(n_e) * 2 - 1) * 2.3
    ez = -0.4 + rng.random(n_e) * 2.2
    ey = -1.7 + life * 4.4
    ep = np.stack([ex, ey, ez], 1).astype(np.float32)
    ep[:, 0] += 0.16 * np.sin(t * 1.7 + ph)
    ex_, ey_, ez_ = project(cam, ep)
    fade = np.sin(life * np.pi) ** 0.8
    ok2 = (ez_ > 0.2) & (ex_ > -30) & (ex_ < w + 30) & (ey_ > -30) & (ey_ < h + 30)
    s2 = cam.focal / np.maximum(ez_, 0.5) * (0.016 + 0.02 * rng.random(n_e))
    sp.add(ex_[ok2], ey_[ok2], np.maximum(s2[ok2], 1.0),
           np.tile(srgb_to_linear(np.array([1.0, 0.30, 0.12], np.float32)), (int(ok2.sum()), 1)),
           0.55 * fade[ok2])
    return sp.image(h, w)


def glow_points(cam, pts, colors, sizes, intensity):
    """Small additive points (glints, petals catching the rim light)."""
    sp = Splatter(cam.w, cam.h)
    if len(pts) == 0:
        return sp.image(cam.h, cam.w)
    x, y, z = project(cam, pts)
    ok = (z > 0.3)
    r = sizes * cam.focal / np.maximum(z, 0.5)
    sp.add(x[ok], y[ok], np.maximum(r[ok], 0.9), colors[ok], intensity[ok])
    return sp.image(cam.h, cam.w)


# --------------------------------------------------------------------------- #
# bloom: multi-scale gaussian pyramid of the bright pass
# --------------------------------------------------------------------------- #
def bloom(img, threshold=0.75, strength=0.85, radius=0.55):
    lum = img @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    knee = smoothstep(threshold * 0.6, threshold * 1.6, lum)
    bright = img * (knee * knee)[..., None]
    # a touch of tint separation: warm core, cool halo
    py = []
    cur = bright
    for level in range(5):
        cur = downsample(cur)
        py.append(cur)
    acc = None
    for i, lvl in enumerate(py):
        sigma = (0.8 + 1.9 * i) * 1.1
        b = blur_separable(lvl, sigma)
        # squash to keep the bloom soft and not too "local"
        if acc is None:
            acc = b
        else:
            acc = resize_bilinear(acc, b.shape[0], b.shape[1]) * 0.86 + b
    acc = resize_bilinear(acc, img.shape[0], img.shape[1])
    return acc * strength


# --------------------------------------------------------------------------- #
# god rays
# --------------------------------------------------------------------------- #
def god_rays(mask, center, n=22, step=0.045, decay=0.94, gain=1.4):
    """Radial light shafts from ``center`` (normalised x, y in [-1, 1])."""
    h, w = mask.shape[:2]
    acc = np.zeros_like(mask)
    weight = 1.0
    total = 0.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = (xx - (center[0] * 0.5 + 0.5) * w)
    dy = (yy - (-center[1] * 0.5 + 0.5) * h)
    for i in range(n):
        t = i / n * step * 8.0
        sx = np.clip(xx - dx * t, 0, w - 1)
        sy = np.clip(yy - dy * t, 0, h - 1)
        x0 = sx.astype(np.int32)
        y0 = sy.astype(np.int32)
        x1 = np.minimum(x0 + 1, w - 1)
        y1 = np.minimum(y0 + 1, h - 1)
        fx = (sx - x0)[..., None]
        fy = (sy - y0)[..., None]
        val = ((mask[y0, x0] * (1 - fx) + mask[y0, x1] * fx) * (1 - fy)
               + (mask[y1, x0] * (1 - fx) + mask[y1, x1] * fx) * fy)
        acc += val * weight
        total += weight
        weight *= decay
    return acc * gain


# --------------------------------------------------------------------------- #
# grading
# --------------------------------------------------------------------------- #
def chromatic_aberration(img, amount=1.6):
    """Shift the red/blue channels radially (very cheap, very cinematic)."""
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w * 0.5, h * 0.5
    dx = (xx - cx) / cx
    dy = (yy - cy) / cy
    out = img.copy()
    for chan, scl in ((0, 1.0 + amount / 300.0), (2, 1.0 - amount / 300.0)):
        sx = np.clip(cx + dx * cx * scl, 0, w - 1)
        sy = np.clip(cy + dy * cy * scl, 0, h - 1)
        x0 = np.floor(sx).astype(np.int32)
        y0 = np.floor(sy).astype(np.int32)
        x1 = np.minimum(x0 + 1, w - 1)
        y1 = np.minimum(y0 + 1, h - 1)
        fx = sx - x0
        fy = sy - y0
        c = img[..., chan]
        top = c[y0, x0] * (1.0 - fx) + c[y0, x1] * fx
        bot = c[y1, x0] * (1.0 - fx) + c[y1, x1] * fx
        out[..., chan] = top * (1.0 - fy) + bot * fy
    return out


def vignette(h, w, strength=0.62, power=1.9):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = (xx - w * 0.5) / (w * 0.5)
    dy = (yy - h * 0.5) / (h * 0.5)
    r = np.sqrt(dx * dx * 0.92 + dy * dy)
    v = 1.0 - strength * clamp(r / 1.25) ** power
    return v[..., None].astype(np.float32)


def grain(h, w, rng, amount=0.035):
    """Luminance-only film grain (colour noise reads as digital, not film)."""
    g = rng.standard_normal((h, w)).astype(np.float32)
    return (g * amount)[..., None] * np.ones((1, 1, 3), np.float32)


def tonemap_aces(x):
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    return np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0)


def filmic_grade(img, rng, vignette_strength=0.55):
    """Split tone: cold, crushed blacks with warm highlights + vignette."""
    lum = np.clip(img @ np.array([0.2126, 0.7152, 0.0722], np.float32), 0, 2)[..., None]
    shadow_tint = srgb_to_linear(np.array([0.30, 0.45, 0.85], np.float32))
    high_tint = srgb_to_linear(np.array([1.0, 0.72, 0.52], np.float32))
    img = img * (1.0 + shadow_tint[None, None, :] * 0.22 * (1.0 - clamp(lum)))
    img = img * (1.0 + high_tint[None, None, :] * 0.06 * clamp(lum))
    # gentle S curve
    img = clamp(img)
    img = img * img * (3.0 - 2.0 * img) * 0.35 + img * 0.65
    return img
