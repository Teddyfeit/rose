"""Frame composition: camera move → raster → shade → particles → grade."""

from __future__ import annotations

import numpy as np

from . import effects
from .mathutil import (TAU, clamp, fbm3, mix, normalize, resize_bilinear, rgb,
                       smoothstep, srgb_to_linear, _hash3)
from .raster import (Camera, build_texture_atlas, make_rays, rasterize,
                     shade_surface, Texture)
from .scene import (MAT_LEAF, MAT_PETAL, MAT_PETAL_INNER, PALETTE, Dust,
                    Mesh, Rose, RoseParams, build_background, make_dust,
                    make_lights)
from .shading import bake
from .sky import SkySampler, build_sky

# --------------------------------------------------------------------------- #
# falling petals in flight
# --------------------------------------------------------------------------- #
N_FALLING = 40


class Faller:
    """A petal in flight.  Everything is a function of the loop phase."""

    def __init__(self, rng, n=N_FALLING):
        self.n = n
        self.phase = rng.random(n).astype(np.float32)         # loop offset
        self.spin = (0.4 + 1.9 * rng.random(n)).astype(np.float32)
        self.flap = (1.4 + 2.6 * rng.random(n)).astype(np.float32)
        self.tilt = rng.random(n).astype(np.float32) * TAU
        self.size = (0.12 + 0.15 * rng.random(n) ** 1.3).astype(np.float32)
        self.azim0 = rng.random(n).astype(np.float32) * TAU
        self.rad0 = (0.35 + 1.4 * rng.random(n) ** 0.7).astype(np.float32)
        self.drift = (0.5 + 0.9 * rng.random(n)).astype(np.float32)
        self.seed = rng.random(n).astype(np.float32)

        # base petal grid in local coords (x across, y along, z curl)
        nu, nv = 9, 7
        self.nu, self.nv = nu, nv
        u = np.linspace(0.05, 1.0, nu, dtype=np.float32)[:, None]
        v = np.linspace(-1.0, 1.0, nv, dtype=np.float32)[None, :]
        wprof = np.maximum(np.sin(np.pi * np.clip(u, 0.0, 1.0) ** 0.62), 0.0) ** 0.7
        W = wprof * 0.55
        curl = -(np.abs(v) ** 2) * 0.28 * W + 0.05 * np.sin(v * 3.0) * np.ones_like(W)
        self.base = np.stack(
            [v * W, np.broadcast_to(u, (nu, nv)) * 1.0, curl], axis=-1
        ).astype(np.float32)                                  # (nu,nv,3)
        # local tri indices
        idx = np.arange(nu * nv).reshape(nu, nv)
        a = idx[:-1, :-1].ravel()
        b = idx[:-1, 1:].ravel()
        c = idx[1:, :-1].ravel()
        d = idx[1:, 1:].ravel()
        self.tris = np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)], 0)

    def mesh_at(self, phase, center, scale=1.0, spread=1.0):
        """Return a Mesh with all falling petals at loop-``phase`` in [0,1)."""
        n = self.n
        ph = (phase + self.phase) % 1.0
        verts = np.zeros((n, self.nu, self.nv, 3), np.float32)

        # fall from just above the flower to below the frame
        y = 0.75 - ph * 3.6
        ang = self.azim0 + ph * self.drift * TAU * 0.6
        rad = self.rad0 + ph * 1.5
        cx = np.cos(ang) * rad * spread
        cz = (np.sin(ang) * rad - 1.15) * spread
        # flutter
        wob = 0.22 * np.sin(ph * TAU * self.flap + self.tilt)
        cx = cx + wob * np.cos(self.tilt * 1.3)
        cz = cz + wob * np.sin(self.tilt * 1.7)

        spin_a = self.tilt + ph * TAU * self.spin
        pitch = 0.5 + 0.9 * np.sin(ph * TAU * self.flap + self.tilt)
        roll = 0.6 * np.cos(ph * TAU * self.spin * 0.7 + self.tilt)

        ca, sa = np.cos(spin_a), np.sin(spin_a)
        cp, spp = np.cos(pitch), np.sin(pitch)
        cr, sr = np.cos(roll), np.sin(roll)
        # broadcast (n,) -> (n,1,1) so they multiply the petal grids
        ca, sa, cp, spp, cr, sr = (v[:, None, None] for v in (ca, sa, cp, spp, cr, sr))

        # rotation: Rz(spin) * Rx(pitch) * Ry(roll)
        def rot(x, y_, z):
            # Ry
            x1 = cr * x + sr * z
            z1 = -sr * x + cr * z
            y1 = y_
            # Rx
            y2 = cp * y1 - spp * z1
            z2 = spp * y1 + cp * z1
            # Rz
            x3 = ca * x1 - sa * y2
            y3 = sa * x1 + ca * y2
            return x3, y3, z2

        B = self.base[None] * (self.size[:, None, None, None] * scale)
        bx, by, bz = B[..., 0], B[..., 1], B[..., 2]
        X, Y, Z = rot(bx, by, bz)
        verts[..., 0] = X + cx[:, None, None]
        verts[..., 1] = Y + y[:, None, None]
        verts[..., 2] = Z + cz[:, None, None]

        # fade window: petals appear near the top and dissolve at the bottom
        fade = np.maximum(np.sin(clamp(ph * 1.15 - 0.02) * np.pi), 0.0) ** 0.6
        fade = np.clip(fade * 2.4, 0, 1)

        m = Mesh()
        P = verts.reshape(n, self.nu, self.nv, 3)
        # normals
        du = np.gradient(P, axis=1)
        dv = np.gradient(P, axis=2)
        N = np.cross(dv, du)
        N = normalize(N.reshape(-1, 3)).reshape(P.shape)
        UV = np.zeros(P.shape[:3] + (3,), np.float32)
        UV[..., 0] = np.linspace(0, 1, self.nu, dtype=np.float32)[None, :, None]
        UV[..., 1] = np.linspace(-1, 1, self.nv, dtype=np.float32)[None, None, :]
        UV[..., 2] = 0

        V = P.reshape(-1, 3)
        N = N.reshape(-1, 3)
        UV = UV.reshape(-1, 3)
        idx = self.tris
        T = []
        for k in range(n):
            T.append(idx + k * self.nu * self.nv)
        T = np.concatenate(T, 0)
        # per-vertex fade + seed stored in the third uv slot
        F = np.repeat(fade, self.nu * self.nv)
        S = np.repeat(self.seed, self.nu * self.nv)
        m.v = [V.astype(np.float32)]
        m.n = [N.astype(np.float32)]
        m.uv = [UV.astype(np.float32)]
        m.mat = [np.full(len(V), MAT_PETAL, np.int16)]
        m.tris = [T.astype(np.int32)]
        m.finalize()
        m.V = m.V + center[None, :]
        m.fade = F.astype(np.float32)
        return m


# --------------------------------------------------------------------------- #
# the rose alone: an infinite studio backdrop instead of the sky dome
# --------------------------------------------------------------------------- #
SPOT_AZ = 0.0
SOLO_DIST = 1.02        # the solo shot sits close: the bloom fills the frame
SOLO_TARGET_Y = -0.26   # ... aimed a little under the flower


def backdrop(rays, spot_az=SPOT_AZ):
    """Crimson studio void behind the flower (no skyline, no horizon)."""
    d = rays / (np.linalg.norm(rays, axis=1, keepdims=True) + 1e-9)
    az = np.arctan2(d[:, 0], -d[:, 2])
    el = np.arcsin(np.clip(d[:, 1], -1.0, 1.0))
    daz = np.angle(np.exp(1j * (az - spot_az)))
    dist = np.sqrt((daz * np.cos(el)) ** 2 + (el - 0.02) ** 2)

    spot = np.exp(-(dist / 0.52) ** 2)[:, None]
    wide = np.exp(-(dist / 1.80) ** 2)[:, None]

    col = (rgb("#020206")[None, :] * 0.55
           + rgb("#1a040c")[None, :] * wide * 0.95
           + rgb("#4e0816")[None, :] * spot * 0.70)

    # slow crimson smoke so the void is never flat
    p = d * 2.6
    n = fbm3(p, octaves=3, seed=61)[:, None]
    smoke = smoothstep(0.34, 0.82, n)
    col = col * (1.0 - 0.50 * smoke) + (rgb("#4a0813")[None, :] * 0.6
                                        + rgb("#0b0610")[None, :] * 0.4) * spot * smoke
    # a darker rim at the very edge of the lens
    edge = smoothstep(0.55, 1.15, dist)[:, None]
    return col * (1.0 - 0.72 * edge)


# --------------------------------------------------------------------------- #
# renderer
# --------------------------------------------------------------------------- #
class RoseRenderer:
    def __init__(self, w=854, h=480, loop_seconds=26.0, quality=1.0, seed=7):
        self.w, self.h = w, h
        self.loop = float(loop_seconds)
        self.seed = seed
        self.q = quality
        self.mesh_res = None        # (nu, nv) petal grid, None = default
        self.bloom_scale = 1.0      # the terminal player can dial the bloom
        self.dist_scale = 1.0       # ... and the framing
        self.target_y = 0.95
        self.only_rose = False      # no city, no sky: just the flower
        self.exposure = 1.0
        rng = np.random.default_rng(seed)
        self.rng = rng

        self.params = RoseParams(seed=seed)
        self.rose = Rose(self.params)
        self.center = self.params.center
        self.lights = make_lights(self.center)
        # a second light rig for the bare-backdrop shot: the front of the bloom
        # must be lit, because there is no sky glow left to do it
        self.lights_solo = make_lights(self.center, front=True)
        self.sky_tex, self.sky_meta = build_sky(seed=seed + 4, glow_az=0.0)
        self.sky = SkySampler(self.sky_tex)
        atlas_rgb, atlas_alpha, atlas_h = build_texture_atlas(rng, n_tiles=20, size=128)
        self.tex = Texture(atlas_rgb, atlas_alpha, 128)
        self.dust = make_dust(rng, n=int(150 * self.q), center=self.center)
        self.fallers = Faller(rng)
        self.bg_mesh = build_background(np.random.default_rng(seed + 9), self.center)
        bgm = self.bg_mesh
        bgm.finalize()
        bgm.attr = np.zeros((bgm.V.shape[0], 8), np.float32)
        bgm.G = np.zeros(bgm.V.shape[0], np.int32)

        # static buffers
        self.frame_i = 0

    # ------------------------------------------------------------------ #
    def camera_at(self, phase):
        """Slow, loopable orbit: the flower seems to breathe with the light."""
        t = phase * TAU
        az = 0.30 * np.sin(t) + 0.10 * np.sin(t * 2 + 0.7)
        el = 0.345 + 0.040 * np.sin(t + 1.1)
        dist = (3.95 + 0.16 * np.sin(t + 2.0)) * self.dist_scale
        ty = self.target_y
        if self.only_rose:
            # nothing but the bloom: closer, calmer orbit, aimed at the flower
            az *= 0.55
            el = 0.380 + 0.028 * np.sin(t + 1.1)
            dist = (3.12 + 0.12 * np.sin(t + 2.0)) * self.dist_scale
            ty = self.target_y
        px = np.sin(az) * dist * np.cos(el)
        pz = np.cos(az) * dist * np.cos(el)
        py = dist * np.sin(el)
        roll = 0.0
        if self.only_rose:
            # a slow dutch tilt: the signature of the anime key visual
            roll = 0.030 * np.sin(t + 0.6) + 0.012 * np.sin(t * 2.0)
        cam = Camera(self.w, self.h, fov_deg=37.0, pos=(px, py, pz),
                     target=(0.0, ty, 0.0), roll=roll)
        return cam

    # ------------------------------------------------------------------ #
    def render(self, phase, out_uint8=True, sub_pixels=1.0):
        phase = float(phase % 1.0)
        t = phase * self.loop
        cam = self.camera_at(phase)
        w, h = self.w, self.h
        cam.simplify = 0.0 if w >= 480 else 0.45
        # every frame's noise is a pure function of the loop phase: the loop is
        # then perfectly seamless *and* the frame cache of the server is exact
        rng = np.random.default_rng(int(round(phase * 12000)) * 7919 + 13)
        self.frame_i += 1

        # ------------------------------------------------ rose surface mesh
        breath = 0.5 + 0.5 * np.sin(phase * TAU - 1.2)
        open_amount = 0.86 + 0.14 * breath
        mesh = self.rose.build(time=t, wind=1.0, open_amount=open_amount,
                               res=self.mesh_res, head=self.only_rose)

        lights = self.lights_solo if self.only_rose else self.lights
        mesh.attr = bake(mesh, lights, self.center)

        # falling petals share the depth buffer with the flower
        fall = self.fallers.mesh_at(phase, self.center,
                                   spread=0.70 if self.only_rose else 1.0)
        fall.G = np.round(fall.UV[:, 2]).astype(np.int32)
        fall.attr = bake(fall, lights, self.center)
        # shade flying petals a bit hotter so they read against the dark bg
        fall.attr[:, 3:6] *= 1.30
        fall.attr[:, 3:6] += srgb_to_linear(np.array([0.45, 0.06, 0.08], np.float32)) * 0.22

        merged = Mesh.__new__(Mesh)
        merged.V = np.concatenate([mesh.V, fall.V], 0)
        merged.N = np.concatenate([mesh.N, fall.N], 0)
        merged.attr = np.concatenate([mesh.attr, fall.attr], 0)
        merged.M = np.concatenate([mesh.M, fall.M], 0)
        merged.G = np.concatenate([mesh.G, fall.G], 0)
        merged.T = np.concatenate(
            [mesh.T, fall.T + mesh.V.shape[0]], 0
        )
        merged.G = np.concatenate([mesh.G, fall.G], 0)

        surf = rasterize(merged, cam)

        # ------------------------------------------------------- background
        # sample the baked sky dome at quarter resolution, then upscale
        sw, sh = (w + 1) // 2, (h + 1) // 2
        sx = (np.arange(sw, dtype=np.float32) + 0.5) / sw * 2 - 1
        sy = 1 - (np.arange(sh, dtype=np.float32) + 0.5) / sh * 2
        SX, SY = np.meshgrid(sx, sy)
        rays, _ = make_rays(cam, SX.ravel(), -SY.ravel(), None)
        if self.only_rose:
            bg_small = backdrop(rays[:, 3:6]).reshape(sh, sw, 3)
        else:
            bg_small = self.sky.sample(rays[:, 3:6]).reshape(sh, sw, 3)
        bg = resize_bilinear(bg_small, h, w)

        # a broad crimson halo behind the flower: the classic anime key-visual
        halo = effects.Splatter(w, h)
        hz = effects.project(cam, (self.center + np.array([0, 0.55, 0], np.float32))[None, :])
        halo_r = cam.focal * (0.46 if self.only_rose else 0.30)
        halo_i = 0.17 if self.only_rose else 0.20
        halo.add(hz[0], hz[1], halo_r,
                 srgb_to_linear(np.array([1.0, 0.15, 0.22], np.float32))[None, :], halo_i)
        img = bg.reshape(-1, 3) + halo.image(h, w).reshape(-1, 3) * 0.55

        # ------------------------------------------------------- surface shade
        if len(surf["pix"]):
            col = shade_surface(surf, cam, lights, self.tex, self.center)
            img[surf["pix"]] = col

        # ------------------------------------------------ glow from the heart
        # the inner flower core leaks a crimson bloom through the petals
        ex = effects.Splatter(w, h)
        heart = self.center + np.array([0, 0.18, 0], np.float32)
        hx, hy, hz = effects.project(cam, heart[None, :])
        pulse = 0.65 + 0.35 * np.sin(phase * TAU * 2.0)
        ex.add(hx, hy, cam.focal * 0.045, srgb_to_linear(np.array([1.0, 0.12, 0.16], np.float32))[None, :],
               0.22 * pulse)

        # ------------------------------------------------------------ bokeh
        # a handful of huge, defocused lights deep behind the flower: this is
        # what gives the shot its cinematic depth
        bk = effects.Splatter(w, h)
        n_b = 26
        bph = np.arange(n_b) * 2.399963 + 0.7
        bx = (np.sin(bph * 1.7 + phase * 2.0) * 0.75) * w * 0.75 + w * 0.5
        by = (np.cos(bph * 1.3 + phase * 1.4) * 0.55 - 0.10) * h * 0.8 + h * 0.5
        bcol = np.where((bph[:, None] % 2 < 1),
                        srgb_to_linear(np.array([1.0, 0.20, 0.26], np.float32))[None, :],
                        srgb_to_linear(np.array([0.30, 0.62, 0.85], np.float32))[None, :])
        bint = (0.012 + 0.02 * (0.5 + 0.5 * np.sin(bph * 3.1 + phase * 6.0)))
        bk.add(bx, by, np.full(n_b, cam.focal * 0.035), bcol, bint)
        ex_buf_extra = effects.blur_separable(bk.image(h, w), 6.0)

        # ------------------------------------------------------- dust/embers
        ex_buf = ex.buf + effects.dust_layer(cam, self.dust, t, rng).reshape(-1, 3)
        ex_buf += ex_buf_extra.reshape(-1, 3)

        # bright surface pixels sparkle (silk sheen glints) - only on a canvas
        # big enough for a glint to be a glint; on the terminal's few hundred
        # pixels they would be single white cells, i.e. noise
        if len(surf["pix"]) and w >= 480:
            bright = col @ np.array([0.2126, 0.7152, 0.0722], np.float32)
            pick = np.nonzero(bright > 1.6)[0]
            if len(pick):
                pick = pick[rng.random(len(pick)) < 0.10 * self.q]
                px = surf["pix"][pick]
                ix = (px % w).astype(np.float32) + 0.5
                iy = (px // w).astype(np.float32) + 0.5
                tw = 0.4 + 0.6 * rng.random(len(pick))
                ex_buf += 0.0
                sp = effects.Splatter(w, h)
                sp.add(ix, iy, np.full(len(pick), 2.2), np.tile(
                    srgb_to_linear(np.array([1.0, 0.55, 0.55], np.float32)), (len(pick), 1)),
                    tw * 0.9)
                ex_buf += sp.image(h, w).reshape(-1, 3)

        # falling petals get a soft crimson halo so they bloom
        fy = fall.V.reshape(fall.n_pts if hasattr(fall, "n_pts") else -1, 3) if False else None
        fp = fall.V.reshape(self.fallers.n, self.fallers.nu * self.fallers.nv, 3).mean(axis=1)
        fxv, fyv, fzv = effects.project(cam, fp)
        okf = (fzv > 0.3) & (fxv > -50) & (fxv < w + 50) & (fyv > -50) & (fyv < h + 50)
        sp = effects.Splatter(w, h)
        sp.add(fxv[okf], fyv[okf], np.maximum(cam.focal * 0.012 / np.maximum(fzv[okf], 0.5) * 2.0, 1.4),
               np.tile(srgb_to_linear(np.array([1.0, 0.16, 0.22], np.float32)), (int(okf.sum()), 1)),
               np.full(int(okf.sum()), 0.10))
        ex_buf += sp.image(h, w).reshape(-1, 3)

        img = img + ex_buf

        # ------------------------------------------------------------- bloom
        lin = img.reshape(h, w, 3)
        bloom_gain = 1.35 * self.bloom_scale * (1.45 if self.only_rose else 1.0)
        bl = effects.bloom(lin, threshold=0.74, strength=bloom_gain)
        lin = lin + bl

        # ---------------------------------------------------------- god rays
        small = resize_bilinear(bl, max(8, h // 5), max(8, w // 5))
        mdir = normalize(self.lights[0].pos[None, :] - cam.pos[None, :])[0]
        cam_fwd, cam_right, cam_up = cam.fwd, cam.right, cam.up
        mx = (mdir @ cam_right) / max(mdir @ cam_fwd, 1e-3)
        my = (mdir @ cam_up) / max(mdir @ cam_fwd, 1e-3)
        center_screen = (mx * 0.5 * w, my * 0.5 * h)
        rays_small = effects.god_rays(
            small, (mx * 1.0, my * 1.0), n=18, step=0.05, decay=0.955, gain=0.55
        )
        rays_up = resize_bilinear(rays_small, h, w)
        # warm shafts falling from the key light
        ray_gain = 0.14 if self.only_rose else 0.30
        lin = lin + rays_up * srgb_to_linear(np.array([1.0, 0.55, 0.45], np.float32))[None, None, :] * ray_gain

        lin = lin * self.exposure
        toned = effects.tonemap_aces(lin)
        graded = effects.filmic_grade(toned, rng)

        # CA only in the corners: cheap approximation (offset the red channel)
        if self.q >= 1.0:
            graded = effects.chromatic_aberration(graded, amount=0.9)

        graded *= effects.vignette(h, w, strength=0.68, power=2.2)
        graded += effects.grain(h, w, rng, amount=0.0045)

        # subtle washi/ink texture in the deep shadows
        graded = np.clip(graded, 0, 1)
        srgb = np.where(graded <= 0.0031308, graded * 12.92,
                        1.055 * np.maximum(graded, 0) ** (1 / 2.4) - 0.055)
        if not out_uint8:
            return np.clip(srgb, 0, 1)
        # ordered dither to kill banding in the near-black gradients
        bayer = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                         np.float32) / 16.0 - 0.5
        d = np.tile(bayer, (h // 4 + 1, w // 4 + 1))[:h, :w, None]
        out = np.clip(np.rint((srgb + d / 255.0) * 255.0), 0, 255).astype(np.uint8)
        return out

    # ------------------------------------------------------------------ #
    def lite(self):
        """The same picture for a coarse canvas (the terminal).

        A terminal is ~200x110 *pixels*, so most of the petal tessellation is
        sub-pixel: the rasteriser may drop it, and the full-resolution effects
        chain is wasted.  Everything that carries the look - petals, bloom,
        skyline, moon, glow - stays exactly as it is.
        """
        self.q = 0.85
        self.exposure = 1.30        # a coarse canvas needs a hotter exposure
        if self.only_rose:
            self.solo()
        else:
            self.mesh_res = (13, 15)
            self.dist_scale = 1.15  # the terminal pulls back: whole bloom
            self.target_y = 0.80
        return self

    def solo(self, on=True):
        """Only the rose: no city, no sky, no horizon - just the bloom.

        The shot is composed for that: nothing is allowed to poke out of the
        lens, so the camera closes in and glides instead of orbiting.
        """
        self.only_rose = bool(on)
        self.q = 1.0 if on else self.q
        self.exposure = 1.18 if on else self.exposure
        self.dist_scale = SOLO_DIST
        self.target_y = SOLO_TARGET_Y
        self.mesh_res = None        # full petal grid: it is the whole picture
        return self

    def resize(self, w, h):
        """The terminal was resized: rebuild at the new pixel grid.

        The sky is sampled per ray and the texture atlas is indexed by UV, so
        nothing pre-baked depends on the resolution - only the camera does.
        """
        self.w, self.h = int(w), int(h)
        return self
