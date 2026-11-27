"""Baked sky dome: gradient, drifting cloud banks, moon, skyline and ember glow.

Because the sky is at infinity it only depends on the ray direction, so it can be
pre-computed once into an (elevation, azimuth) texture and simply bilinearly
sampled for every ray.  That buys a very expensive looking matte painting for
almost no per-frame cost.
"""

from __future__ import annotations

import numpy as np

from .mathutil import TAU, clamp, fbm3, rgb, smoothstep, srgb_to_linear

N_AZ = 1536
N_EL = 512
EL_MIN = -1.15
EL_MAX = 0.85


def _skyline(az, rng):
    """Height (in elevation radians) of the ruined city on the horizon."""
    # three octaves of 1D noise, built from interpolated random control points
    h = np.zeros_like(az)
    for octave, (freq, amp) in enumerate([(7, 0.055), (23, 0.035), (61, 0.022)]):
        pts = rng.random(freq + 1)
        pts[-1] = pts[0]
        x = az / TAU * freq
        i = np.floor(x).astype(np.int32) % freq
        f = x - np.floor(x)
        f = f * f * (3 - 2 * f)
        h += amp * (pts[i] * (1 - f) + pts[i + 1] * f)
    # occasional tower spikes
    spikes = np.zeros_like(az)
    for _ in range(26):
        c = rng.random() * TAU
        w = 0.004 + 0.010 * rng.random()
        spikes += 0.10 * rng.random() * np.exp(-((az - c) ** 2) / (2 * w * w)) * rng.random()
    return h + spikes - 0.035


def build_sky(seed: int = 11, glow_az: float = 0.0):
    """Return (tex, meta) where tex is (N_EL, N_AZ, 3) linear RGB."""
    rng = np.random.default_rng(seed)
    az = (np.arange(N_AZ, dtype=np.float32) + 0.5) / N_AZ * TAU
    el = np.linspace(EL_MIN, EL_MAX, N_EL, dtype=np.float32)
    AZ, EL = np.meshgrid(az, el)            # (N_EL, N_AZ)

    # ---------------------------------------------------------------- base sky
    # angular distance to the ember glow that sits behind the rose
    daz = np.angle(np.exp(1j * (AZ - glow_az)))
    dist_glow = np.sqrt((daz * np.cos(EL)) ** 2 + (EL - 0.06) ** 2)

    t = clamp((EL + 0.1) / 0.6)
    top = rgb("#04060d")
    mid = rgb("#0d1224")
    hor = rgb("#2a0d1a")
    tex = mid[None, None, :] * (1 - t[..., None]) + top[None, None, :] * t[..., None]
    haze = np.exp(-np.maximum(EL + 0.02, 0) * 9.0)[..., None]
    tex = tex + (hor - mid)[None, None, :] * haze * 0.9

    # deep crimson ember glow rising from behind the city
    glow = np.exp(-(dist_glow / 0.42) ** 2)[..., None]
    glow2 = np.exp(-(dist_glow / 1.25) ** 2)[..., None]
    above = smoothstep(-0.16, 0.06, EL)[..., None]
    tex = tex + rgb("#8f1024")[None, None, :] * glow * 0.95 * above
    tex = tex + rgb("#340a16")[None, None, :] * glow2 * 0.55 * above

    # ------------------------------------------------------------ cloud banks
    p = np.stack([np.cos(AZ) * 2.6, np.sin(AZ) * 2.6, EL * 5.0], axis=-1).reshape(-1, 3)
    n1 = fbm3(p, octaves=4, seed=3).reshape(N_EL, N_AZ)
    p2 = np.stack([np.cos(AZ) * 7.5, np.sin(AZ) * 7.5, EL * 14.0], axis=-1).reshape(-1, 3)
    n2 = fbm3(p2, octaves=3, seed=9).reshape(N_EL, N_AZ)
    cloud = smoothstep(0.46, 0.80, 0.62 * n1 + 0.38 * n2)
    cloud *= smoothstep(0.0, 0.18, EL) * (1.0 - smoothstep(0.5, 0.85, EL))
    cloud_col = rgb("#1b1c2e")[None, None, :] * 0.7 + rgb("#43101f")[None, None, :] * glow * 1.1
    tex = tex * (1 - 0.85 * cloud[..., None]) + cloud_col * cloud[..., None]

    # ------------------------------------------------------------------ stars
    star_hash = rng.random((N_EL, N_AZ))
    stars = (star_hash > 0.99915) * smoothstep(0.03, 0.35, EL)
    if stars.any():
        idx = np.nonzero(stars)
        bright = rng.random(len(idx[0])) ** 2
        star_col = rgb("#cfe0ff")[None, :] * (0.25 + 0.9 * bright[:, None])
        star_val = star_col * smoothstep(0.05, 0.4, el[idx[0]])[:, None]
        # 5x5 gaussian-ish falloff: a star must survive being sampled at
        # half resolution, otherwise it aliases into single-pixel speckles
        for dy_ in range(-2, 3):
            for dx_ in range(-2, 3):
                wgt = np.exp(-(dy_ * dy_ + dx_ * dx_) / 2.2) * 0.42
                yy_ = np.clip(idx[0] + dy_, 0, N_EL - 1)
                xx_ = (idx[1] + dx_) % N_AZ
                tex[yy_, xx_] = tex[yy_, xx_] + star_val * wgt

    # ------------------------------------------------------------------- moon
    moon_az = glow_az - 0.42
    moon_el = 0.345
    dmoon = np.sqrt(
        ((AZ - moon_az) * np.cos(EL)) ** 2 + (EL - moon_el) ** 2
    )
    disc = smoothstep(0.060, 0.046, dmoon)
    tex = tex + rgb("#e8f0ff")[None, None, :] * disc[..., None] * 3.4
    halo = np.exp(-(dmoon / 0.16) ** 2)[..., None] + 0.35 * np.exp(-(dmoon / 0.36) ** 2)[..., None]
    tex = tex + rgb("#54719c")[None, None, :] * halo * 0.9
    # thin veil of cloud across the moon
    tex = tex * (1 - 0.45 * cloud[..., None] * smoothstep(0.45, 0.0, dmoon)[..., None])

    # --------------------------------------------------------------- skyline
    top = _skyline(az, rng)                          # (N_AZ,)
    el_rel = EL - top[None, :]
    is_bld = el_rel < 0
    # rim of cold teal light on the silhouette edge
    rim = np.exp(-((el_rel + 0.004) / 0.006) ** 2)
    city = (rgb("#04060a")[None, None, :]
            + rgb("#141a24")[None, None, :] * 0.5
            + rgb("#2a1220")[None, None, :] * glow[..., 0][..., None] * 0.6)
    city = city + rgb("#22404a")[None, None, :] * (rim * 0.55)[..., None]
    tex = np.where(is_bld[..., None], city, tex)

    # far windows: sparse warm/cold dots on the towers
    wy = np.floor((EL + 0.5) * 420).astype(np.int32)
    wax = np.floor(AZ * (N_AZ / TAU) / 2.0).astype(np.int32)
    hsh = (np.sin(wy * 127.1 + wax * 311.7) * 43758.5453) % 1.0
    hsh2 = (np.sin(wy * 269.5 + wax * 183.3) * 24634.6345) % 1.0
    # windows only sit on the *towers*, i.e. in a thin band under the roof line
    band = smoothstep(-0.004, -0.012, el_rel) * smoothstep(-0.075, -0.030, el_rel)
    win = is_bld & (hsh > 0.90) & (hsh2 > 0.55) & (band > 0.05)
    warm_win = rgb("#ff8a3c")[None, :] * 0.34
    cold_win = rgb("#8fd0ff")[None, :] * 0.22
    wcol = np.where((hsh2 > 0.86)[..., None], cold_win[None], warm_win[None])
    tex = tex + win[..., None] * wcol * band[..., None]

    # ground fog swallowing the bottom of the city
    fog = smoothstep(-0.055, -0.30, EL)[..., None]
    fog_col = rgb("#0b0a14")[None, None, :] + rgb("#420c18")[None, None, :] * glow * 1.1
    tex = tex * (1 - fog) + fog_col * fog

    # below the horizon: slow crimson smoke lit from above (this is what fills
    # the frame, since the camera looks down on the flower)
    smoke_n = smoothstep(0.35, 0.85, fbm3(
        np.stack([np.cos(AZ) * 1.7, np.sin(AZ) * 1.7, EL * 1.5 - 1.0], -1).reshape(-1, 3),
        4, seed=61).reshape(N_EL, N_AZ))
    depth = smoothstep(0.05, -0.60, EL)
    smoke_col = (rgb("#1e0818")[None, None, :]
                 + rgb("#8c1226")[None, None, :] * (glow * 1.5 + 0.16)
                 + rgb("#1d4a60")[None, None, :] * (1.0 - glow) * 0.45)
    smoke_col = smoke_col * (0.55 + 1.35 * smoke_n[..., None]) * 1.45
    # wispy streaks of light drifting through the smoke
    wisp = smoothstep(0.52, 0.95, fbm3(
        np.stack([np.cos(AZ) * 5.5, np.sin(AZ) * 5.5, EL * 3.0], -1).reshape(-1, 3),
        3, seed=97).reshape(N_EL, N_AZ))
    smoke_col = smoke_col + rgb("#ff2b45")[None, None, :] * (wisp * glow[..., 0] * 0.55)[..., None]
    tex = tex * (1 - depth[..., None]) + smoke_col * depth[..., None]
    # a last whisper of dark at the very bottom of the frame
    tex = tex * (1.0 - 0.35 * smoothstep(-0.25, -0.5, EL)[..., None])

    meta = {
        "moon_az": moon_az,
        "moon_el": moon_el,
        "glow_az": glow_az,
    }
    return tex.astype(np.float32), meta


class SkySampler:
    """Bilinear, azimuth-wrapped sampler for the baked sky."""

    def __init__(self, tex):
        self.tex = np.ascontiguousarray(tex, dtype=np.float32)
        self.h, self.w = tex.shape[:2]

    def sample(self, D):
        d = D / (np.linalg.norm(D, axis=1, keepdims=True) + 1e-9)
        az = (np.arctan2(d[:, 0], -d[:, 2]) % TAU) / TAU * self.w - 0.5
        el = (np.arcsin(np.clip(d[:, 1], -1, 1)) - EL_MIN) / (EL_MAX - EL_MIN) * (self.h - 1) - 0.5
        x0 = np.floor(az).astype(np.int32)
        y0 = np.clip(np.floor(el).astype(np.int32), 0, self.h - 2)
        fx = (az - x0)[:, None]
        fy = (el - y0)[:, None]
        x0 %= self.w
        x1 = (x0 + 1) % self.w
        a = self.tex[y0, x0]
        b = self.tex[y0, x1]
        c = self.tex[y0 + 1, x0]
        d2 = self.tex[y0 + 1, x1]
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d2 * fx) * fy
