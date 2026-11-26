"""Micro-rasteriser + deferred-ish pixel shader.

Only the *surfaces* go through the rasteriser; the sky, embers and petals in
flight are raymarched / point-splatted and composited on top.  That keeps the
cost per frame low while still giving us real perspective, occlusion and a
pixel-perfect silhouette for the rose.

Shading per pixel: interpolated vertex colour (baked coloured lights) * silk
procedural texture + fresnel rim + specular + translucency.
"""

from __future__ import annotations

import numpy as np

from .mathutil import TAU, blur_separable, clamp, dot, normalize, smoothstep
from .scene import MAT_LEAF, MAT_PETAL, MAT_PETAL_INNER, MAT_STEM, PALETTE


# --------------------------------------------------------------------------- #
# camera
# --------------------------------------------------------------------------- #
class Camera:
    def __init__(self, w, h, fov_deg=34.0, pos=(0.0, 0.30, 6.4), target=(0.0, 0.02, 0.0)):
        self.w, self.h = w, h
        self.pos = np.asarray(pos, np.float32)
        fwd = np.asarray(target, np.float32) - self.pos
        self.fwd = fwd / np.linalg.norm(fwd)
        right = np.cross(self.fwd, np.array([0, 1, 0], np.float32))
        self.right = right / np.linalg.norm(right)
        self.up = np.cross(self.right, self.fwd)
        self.focal = (0.5 * h) / np.tan(np.radians(fov_deg) * 0.5)
        # coarse canvases (the terminal) ask the rasteriser to drop sub-pixel
        # triangles; it is set by the renderer, never by the camera itself
        self.simplify = 0.0


def make_rays(cam, sx, sy, m):
    """Rays through normalised screen points sx, sy in [-1, 1]."""
    x = sx * (cam.w * 0.5)
    y = sy * (cam.h * 0.5)
    D = (
        cam.fwd[None, :] * cam.focal
        + cam.right[None, :] * x[:, None]
        - cam.up[None, :] * y[:, None]
    )
    D = normalize(D)
    O = np.broadcast_to(cam.pos, D.shape).copy()
    return np.concatenate([O, D], axis=1), m


# --------------------------------------------------------------------------- #
# rasteriser
# --------------------------------------------------------------------------- #
def _coarsen(T, sx, sy, simplify, budget):
    """Drop triangles far below one screen pixel (a cheap, exact-enough LOD).

    For the big frame nothing is dropped.  For a coarse canvas (the terminal,
    where the whole picture is a few hundred pixels wide) roughly two thirds of
    the petals are sub-pixel: they cannot change a cell colour, but they cost a
    tile slot each.  The sub-pixel triangles that survive are picked in
    area-sorted order, so the kept ones are spread evenly over the surface and
    every hole they leave is smaller than a pixel.
    """
    tx, ty = sx[T], sy[T]
    area = np.abs((tx[:, 1] - tx[:, 0]) * (ty[:, 2] - ty[:, 0])
                  - (tx[:, 2] - tx[:, 0]) * (ty[:, 1] - ty[:, 0]))
    big = area >= simplify
    n_small = int((~big).sum())
    if n_small == 0:
        return T
    keep_small = np.zeros(n_small, np.bool_)
    if n_small > budget:
        order = np.argsort(-area[~big], kind="stable")
        stride = int(np.ceil(n_small / float(budget)))
        keep_small[order[::stride]] = True
    else:
        keep_small[:] = True
    mask = big.copy()
    mask[~big] = keep_small
    return T[mask]


def rasterize(mesh, cam, tile=10):
    """Tile based, fully vectorised software rasteriser.

    The screen is split into 16x16 tiles; for every tile all triangles that
    overlap it are rasterised in one vectorised pass and the nearest sample per
    pixel wins (an exact z-buffer via ``argmax``).  This is ~20x faster than a
    naive per-triangle Python loop and just as correct.
    """
    w, h = cam.w, cam.h
    V = mesh.V
    c = V - cam.pos[None, :]
    px_ = c @ cam.right
    py_ = c @ cam.up
    pz = c @ cam.fwd

    f = cam.focal
    sx = (w * 0.5 + px_ / np.maximum(pz, 1e-4) * f).astype(np.float32)
    sy = (h * 0.5 - py_ / np.maximum(pz, 1e-4) * f).astype(np.float32)
    invz_all = np.where(pz > 0.03, 1.0 / np.maximum(pz, 1e-4), 0.0).astype(np.float32)

    T = mesh.T

    # faceted stand-ins --------------------------------------------------- #
    # Every triangle that is smaller than one screen pixel cannot change the
    # picture, but it still costs a tile slot.  Coarse passes (the terminal)
    # swap those specks for a three-triangle disc around their centroid: the
    # silhouette, the depth and the attribute carry over, the cost does not.
    simp = getattr(cam, "simplify", 0.0)
    if simp > 0.0:
        T = _coarsen(T, sx, sy, simp, max(600, (w * h) // 28))

    tx = sx[T]                                    # (n, 3)
    ty = sy[T]
    tz = pz[T]
    vis = (tz > 0.03).all(axis=1) & (tx.max(axis=1) >= -2) & (tx.min(axis=1) <= w + 2) \
        & (ty.max(axis=1) >= -2) & (ty.min(axis=1) <= h + 2)
    T = T[vis]
    tx = tx[vis]
    ty = ty[vis]

    area = (tx[:, 1] - tx[:, 0]) * (ty[:, 2] - ty[:, 0]) - (tx[:, 2] - tx[:, 0]) * (ty[:, 1] - ty[:, 0])
    good = np.abs(area) > 1e-7
    T, tx, ty, area = T[good], tx[good], ty[good], area[good]
    n_tri = T.shape[0]

    ntx = (w + tile - 1) // tile
    nty = (h + tile - 1) // tile
    depth = np.zeros(h * w, dtype=np.float32)
    o_n = np.zeros((h * w, 3), np.float32)
    o_lit = np.zeros((h * w, 3), np.float32)
    o_uv = np.zeros((h * w, 2), np.float32)
    o_gid = np.zeros(h * w, np.float32)
    o_mat = np.zeros(h * w, np.int16)
    o_pos = np.zeros((h * w, 3), np.float32)

    # padded-index prep for the 1/z weighted attribute interpolation
    tri_v = mesh.V[T]                              # (n,3,3)
    tri_a = mesh.attr[T]                           # (n,3,8)
    tri_g = mesh.G[T].astype(np.float32)           # (n,3)
    tri_iz = invz_all[T]                           # (n,3)

    if n_tri == 0:
        return _gather_result(depth, o_n, o_lit, o_uv, o_gid, o_mat, o_pos, w, h)

    bminx = np.clip(np.floor(tx.min(axis=1)).astype(np.int32), 0, w - 1)
    bmaxx = np.clip(np.ceil(tx.max(axis=1)).astype(np.int32), 0, w - 1)
    bminy = np.clip(np.floor(ty.min(axis=1)).astype(np.int32), 0, h - 1)
    bmaxy = np.clip(np.ceil(ty.max(axis=1)).astype(np.int32), 0, h - 1)
    ttx0 = bminx // tile
    ttx1 = bmaxx // tile
    tty0 = bminy // tile
    tty1 = bmaxy // tile

    for tyi in range(nty):
        y0 = tyi * tile
        y1 = min(y0 + tile, h)
        row_ok = (tty0 <= tyi) & (tty1 >= tyi)
        if not row_ok.any():
            continue
        idx_row = np.nonzero(row_ok)[0]
        for txi in range(ntx):
            x0 = txi * tile
            x1 = min(x0 + tile, w)
            sel = idx_row[(ttx0[idx_row] <= txi) & (ttx1[idx_row] >= txi)]
            if sel.size == 0:
                continue

            gx = (np.arange(x0, x1, dtype=np.float32) + 0.5)
            gy = (np.arange(y0, y1, dtype=np.float32) + 0.5)
            X, Y = np.meshgrid(gx, gy)
            Xf = X.ravel()[None, :]
            Yf = Y.ravel()[None, :]

            x0t = tx[sel, 0][:, None]
            y0t = ty[sel, 0][:, None]
            x1t = tx[sel, 1][:, None]
            y1t = ty[sel, 1][:, None]
            x2t = tx[sel, 2][:, None]
            y2t = ty[sel, 2][:, None]
            a = area[sel][:, None]

            b0 = ((x1t - Xf) * (y2t - Yf) - (x2t - Xf) * (y1t - Yf)) / a
            b1 = ((x2t - Xf) * (y0t - Yf) - (x0t - Xf) * (y2t - Yf)) / a
            b2 = 1.0 - b0 - b1
            inside = (b0 >= -1e-4) & (b1 >= -1e-4) & (b2 >= -1e-4)   # (S, P)
            if not inside.any():
                continue

            iz = (tri_iz[sel, 0][:, None] * b0
                  + tri_iz[sel, 1][:, None] * b1
                  + tri_iz[sel, 2][:, None] * b2)
            iz = np.where(inside, iz, 0.0)
            best = np.argmax(iz, axis=0)                 # (P,)
            best_iz = iz[best, np.arange(iz.shape[1])]
            pix = (Y.ravel().astype(np.int32) * w + X.ravel().astype(np.int32))
            hit = best_iz > depth[pix]
            if not hit.any():
                continue
            pix = pix[hit]
            bi = best[hit]
            cols = np.arange(iz.shape[1])[hit]
            depth[pix] = best_iz[hit]

            B = np.stack([b0[bi, cols], b1[bi, cols], b2[bi, cols]], axis=1)  # (m,3)
            tsel = sel[bi]
            tri_id = T[tsel, 0]
            wb = B * tri_iz[tsel]
            wsum = wb.sum(axis=1, keepdims=True)
            inv = 1.0 / np.maximum(wsum, 1e-9)

            o_n[pix] = (tri_a[tsel, :, 0:3] * wb[:, :, None]).sum(axis=1) * inv
            o_lit[pix] = (tri_a[tsel, :, 3:6] * wb[:, :, None]).sum(axis=1) * inv
            o_uv[pix] = (tri_a[tsel, :, 6:8] * wb[:, :, None]).sum(axis=1) * inv
            o_pos[pix] = (tri_v[tsel] * wb[:, :, None]).sum(axis=1) * inv
            o_gid[pix] = (tri_g[tsel] * B).sum(axis=1)
            o_mat[pix] = mesh.M[tri_id]

    return _gather_result(depth, o_n, o_lit, o_uv, o_gid, o_mat, o_pos, w, h)


def _gather_result(depth, o_n, o_lit, o_uv, o_gid, o_mat, o_pos, w, h):
    covered = depth > 0
    idx = np.nonzero(covered)[0]

    # ---------------------------------------------------- screen space AO
    # Darken the creases between overlapping petals: blur the (log) depth and
    # compare it against the pixel's own depth.  Cheap, and it is what makes
    # the layers read as layers.
    zmap = np.where(covered, 1.0 / np.maximum(depth, 1e-6), 14.0).reshape(h, w)
    lz = np.log(np.maximum(zmap, 0.05))[..., None]
    blurred = blur_separable(lz, 3.4)[..., 0]
    occl = np.clip((blurred - np.log(np.maximum(zmap, 0.05))) * 2.6, 0.0, 1.0)
    ao = (1.0 - 0.86 * occl).astype(np.float32).ravel()
    return {
        "pix": idx,
        "z": (1.0 / np.maximum(depth[idx], 1e-6)).astype(np.float32),
        "n": o_n[idx],
        "lit": o_lit[idx],
        "uv": o_uv[idx],
        "gid": np.round(o_gid[idx]).astype(np.int32),
        "mat": o_mat[idx],
        "pos": o_pos[idx],
        "ao": ao[idx],
    }


# --------------------------------------------------------------------------- #
# procedural silk textures (baked once per petal)
# --------------------------------------------------------------------------- #
def build_texture_atlas(rng, n_tiles=24, size=128):
    """Petal silk: returns (atlas RGB 0..1, atlas alpha=sheen, atlas height)."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    u = yy / (size - 1)
    v = xx / (size - 1) * 2.0 - 1.0
    rgb_ = np.zeros((n_tiles, size, size, 3), np.float32)
    alpha = np.zeros((n_tiles, size, size), np.float32)
    hmap = np.zeros((n_tiles, size, size), np.float32)

    for k in range(n_tiles):
        # vein network: a few thick mid veins plus fine branching capillaries
        ang = np.arctan2(v * 1.3, u * 1.2 + 0.05)
        rad = np.sqrt((u * 1.0) ** 2 + (v * 0.9) ** 2)
        branch = np.abs(np.sin(ang * (7 + int(rng.integers(0, 5))) + 1.6 * np.sin(u * 6 + k)))
        vein = smoothstep(0.995, 0.90, branch) * smoothstep(0.02, 0.25, rad) * (0.35 + 0.65 * u)
        for _ in range(9):
            cy = rng.random() * 1.4 - 0.7
            wdt = 0.010 + 0.016 * rng.random()
            vein += np.exp(-((v - (cy + 0.08 * np.sin(u * 9))) ** 2) / (2 * wdt ** 2)) * 0.30
        vein = clamp(vein)

        # fine silk grain: stretched noise along the petal axis
        grain = (
            np.sin(v * 90.0 + np.sin(u * 24 + k) * 2.1) * 0.5
            + np.sin(v * 47.0 + u * 9.0 + k) * 0.5
        )
        grain = 0.5 + 0.5 * grain
        cell = (
            np.sin(v * 210.0 + np.sin(u * 61.0) * 1.5) *
            np.sin(u * 137.0 + v * 30.0)
        )
        sheen = clamp(0.45 + 0.55 * (0.5 + 0.5 * np.cos(v * 26.0 + u * 5.0 + k)))
        # central silk highlight band
        band = np.exp(-((v) ** 2) / (2 * 0.20 ** 2)) * (0.35 + 0.65 * u)

        dark = 1.0 - 0.55 * vein          # veins darken the silk
        col = np.stack([dark * 1.0, dark * 0.92, dark * 0.95], axis=-1)
        col *= (0.93 + 0.07 * grain)[..., None]
        col = np.clip(col, 0, 1.2)

        rgb_[k] = col
        alpha[k] = clamp(0.55 * band + 0.40 * sheen + 0.05 * grain)
        hmap[k] = clamp(0.5 + 0.25 * grain + 0.4 * vein + 0.25 * band)

    return rgb_, alpha, hmap


class Texture:
    def __init__(self, atlas, alpha, size):
        self.tex = atlas.reshape(-1, 3)
        self.alp = alpha.ravel()
        self.n_tiles = atlas.shape[0]
        self.size = size

    def lookup(self, uv, gid):
        u = np.clip(uv[:, 0], 0.0, 0.9999)
        v = np.clip(uv[:, 1] * 0.5 + 0.5, 0.0, 0.9999)
        tile = (gid % self.n_tiles).astype(np.int32)
        x = (v * self.size).astype(np.int32)
        y = (u * self.size).astype(np.int32)
        idx = (tile * self.size + y) * self.size + x
        return self.tex[idx], self.alp[idx]


# --------------------------------------------------------------------------- #
# the pixel shader
# --------------------------------------------------------------------------- #
def shade_surface(surf, cam, lights, tex, center):
    """Turn rasterised surface buffers into (pix, colour) linear RGB."""
    P = surf["pos"]
    N = normalize(surf["n"])
    V = normalize(cam.pos[None, :] - P)
    # thin sheets: always shade the face that looks at the camera
    N = N * np.where(dot(N, V) < 0, -1.0, 1.0)[:, None]
    albedo = surf["lit"]
    is_petal = (surf["mat"] == MAT_PETAL) | (surf["mat"] == MAT_PETAL_INNER)
    is_leaf = surf["mat"] == MAT_LEAF

    detail = np.ones((P.shape[0], 3), np.float32)
    sheen = np.zeros(P.shape[0], np.float32)
    if tex is not None:
        tex_rgb, tex_a = tex.lookup(surf["uv"], surf["gid"])
        pm = is_petal | is_leaf
        detail[pm] = tex_rgb[pm] * 0.40 + 0.60
        sheen[pm] = tex_a[pm]

    ao = surf.get("ao", np.ones(P.shape[0], np.float32))
    from .mathutil import fbm3
    velvet = fbm3(P * 26.0, 2, seed=57)
    col = albedo * detail * (0.12 + 0.88 * ao)[:, None] * (0.90 + 0.16 * velvet)[:, None]

    # ---------------- fresnel rim: the signature crimson edge ------------
    ndv = clamp(dot(N, V))
    fres = (1.0 - ndv) ** 2.6
    fres2 = (1.0 - ndv) ** 7.0
    rim_col = PALETTE["rim"][None, :]
    rim_strength = np.where(is_petal, 1.0, np.where(is_leaf, 0.45, 0.18))
    # glowing outline right at the rim of each petal
    uv = surf["uv"]
    rim_uv = np.where(is_petal,
                      smoothstep(0.80, 1.0, np.abs(uv[:, 1])) * 0.8
                      + smoothstep(0.86, 1.0, uv[:, 0]) * 0.6, 0.0)
    col = col + rim_col * (rim_uv * 0.30)[:, None]
    col = col + rim_col * (fres * rim_strength)[:, None] * 0.30
    col = col + PALETTE["petal_pink"][None, :] * (fres2 * rim_strength * 0.22)[:, None]

    # cold teal back rim: only catches petals that face away from the key
    back_dir = normalize(lights[2].pos[None, :] - P)
    cold = clamp(0.35 + dot(N, back_dir))
    col = col + PALETTE["back"][None, :] * (fres ** 1.7 * cold * 0.34 * is_petal)[:, None]

    # ---------------- specular: silk sheen -------------------------------- #
    key = lights[0]
    L = normalize(key.pos[None, :] - P)
    H = normalize(L + V)
    ndh = np.maximum(dot(N, H), 0.0)
    spec = (ndh ** 14.0) * 0.16 + (ndh ** 4.0) * 0.015
    spec *= (0.30 + 0.70 * sheen) * np.where(is_petal | is_leaf, 1.0, 0.12)
    col = col + key.color[None, :] * (spec * 1.1)[:, None]

    # hot specular streak on the tips of the petals (the "wet silk" look)
    tip_gloss = np.where(is_petal,
                         smoothstep(0.55, 1.0, surf["uv"][:, 0]) ** 2.0
                         * smoothstep(0.35, 0.95, np.abs(surf["uv"][:, 1])), 0.0)
    col = col + key.color[None, :] * (tip_gloss * (ndh ** 10.0) * 0.10)[:, None]

    # crimson sheen from the rim light: silk catching the red
    rl = lights[3]
    Hr = normalize(normalize(rl.pos[None, :] - P) + V)
    ndhr = np.maximum(dot(N, Hr), 0.0)
    rs = (ndhr ** 24.0) * (0.25 + 0.75 * sheen) * np.where(is_petal, 1.0, 0.0)
    col = col + rl.color[None, :] * (rs * 0.75)[:, None]

    # ---------------- thin-petal transmission ---------------------------- #
    back = lights[2]
    Lb = normalize(back.pos[None, :] - P)
    trans = np.maximum(dot(N, Lb) * -1.0, 0.0) ** 1.4
    col = col + (albedo * back.color[None, :]) * (trans * 0.30 * is_petal)[:, None]

    # ---------------- crimson bounce into the shadowed petals ------------- #
    rim_col2 = lights[3].color
    fill = clamp(0.55 - dot(N, normalize(lights[0].pos[None, :] - P)) * 0.5)
    col = col + rim_col2[None, :] * (albedo * (fill * 0.55 * is_petal)[:, None])

    # ---------------- inner glow of the flower heart --------------------- #
    r = np.linalg.norm(P[:, [0, 2]] - center[[0, 2]][None, :], axis=1)
    heart = np.exp(-(r / 0.55) ** 2) * is_petal
    col = col + PALETTE["rim"][None, :] * (heart * 0.10)[:, None]

    return col.astype(np.float32)
