"""Vertex level "texturing" and lighting bake.

The petals are painted procedurally in object space: a crimson silk gradient,
dark throat, radial veins, glowing tips and a razor-thin edge highlight.  The
coloured key/rim/back lights are then baked into the vertex colour so the
per-pixel shader only has to interpolate, add fresnel rim and sparkle.
"""

from __future__ import annotations

import numpy as np

from .mathutil import (clamp, dot, fbm3, mix, normalize, smoothstep, TAU,
                       _hash3)
from .scene import MAT_LEAF, MAT_PETAL, MAT_PETAL_INNER, MAT_STEM, PALETTE


def _gid_randoms(gid):
    """Three deterministic pseudo random values per object id."""
    g = gid.astype(np.int64)
    z = np.zeros_like(g)
    r1 = _hash3(g, z, z, 101)
    r2 = _hash3(g, z, z, 202)
    r3 = _hash3(g, z, z, 303)
    return r1, r2, r3


def _petal_albedo(mesh, center):
    """Procedural crimson silk for every petal / leaf / stem vertex."""
    P = mesh.V
    uv = mesh.UV
    gid = mesh.G
    r1, r2, r3 = _gid_randoms(gid)

    u = uv[:, 0]
    v = uv[:, 1]
    is_petal = (mesh.M == MAT_PETAL) | (mesh.M == MAT_PETAL_INNER)
    is_leaf = mesh.M == MAT_LEAF
    is_stem = mesh.M == MAT_STEM

    n = P.shape[0]
    col = np.zeros((n, 3), np.float32)

    # ---------------------------------------------------------------- petals
    if is_petal.any():
        m = is_petal
        uu, vv = u[m], v[m]
        pm = P[m]
        nz = fbm3(pm * 7.5, 3, seed=5)
        nz2 = fbm3(pm * 19.0, 2, seed=23)
        vein_n = fbm3(
            pm * 5.2 + np.stack([r1[m] * 9.0, r2[m] * 9.0, r3[m] * 9.0], axis=1), 3, seed=41
        )
        strand_n = fbm3(pm * 13.0, 2, seed=77)

        body = PALETTE["petal_deep"][None, :] * np.ones((int(m.sum()), 1), np.float32)
        body = mix(body, PALETTE["petal_dark"][None, :], smoothstep(0.05, 0.55, uu)[:, None] * 0.9)
        body = mix(body, PALETTE["petal_mid"][None, :], (0.10 + 0.90 * uu ** 1.6)[:, None] * 0.82)
        # silky rim: a thin hot line right at the edge of every petal
        edge = smoothstep(0.55, 1.0, np.abs(vv)) ** 2.2
        body = mix(body, PALETTE["petal_hot"][None, :], (0.34 * edge * (0.25 + 0.75 * uu))[:, None])
        # glowing tip (the anime "burning petal" tell)
        tip = smoothstep(0.86, 1.0, uu) * smoothstep(0.80, 1.0, np.abs(vv))
        body = mix(body, PALETTE["petal_pink"][None, :], (0.30 * tip)[:, None])

        # veins: dark cracks with a warm core, branching towards the rim
        vein = smoothstep(0.56, 0.67, vein_n) * (0.15 + 0.85 * uu ** 1.3)
        body *= (1.0 - 0.66 * vein)[:, None]
        body += PALETTE["rim_soft"][None, :] * (0.16 * smoothstep(0.68, 0.84, vein_n) * uu)[:, None]

        # glossy filaments running up the petal
        strands = smoothstep(0.80, 0.95, strand_n) * smoothstep(0.20, 0.95, uu) * (0.3 + 0.7 * np.abs(vv))
        body += PALETTE["petal_pink"][None, :] * (0.12 * strands)[:, None]

        # micro silk grain
        body *= (0.86 + 0.20 * nz + 0.10 * nz2)[:, None]

        # every petal is a slightly different silk: warmer, cooler, brighter
        hue = (r1[m] - 0.5) * 2.0
        body *= (1.0 + 0.14 * hue)[:, None]
        body = body + PALETTE["petal_hot"][None, :] * (0.08 * np.maximum(hue, 0))[:, None]

        # ambient occlusion: petals deep in the flower are almost black
        r = np.linalg.norm(pm[:, [0, 2]] - center[[0, 2]], axis=1)
        ao = 0.30 + 0.70 * smoothstep(0.10, 1.15, r)
        body *= ao[:, None] ** 1.3
        col[m] = col[m] + body

    # ----------------------------------------------------------------- leaves
    if is_leaf.any():
        m = is_leaf
        uu, vv = u[m], v[m]
        pm = P[m]
        nz = fbm3(pm * 9.0, 3, seed=131)
        leaf = mix(
            PALETTE["leaf"][None, :] * np.ones((m.sum(), 1), np.float32),
            PALETTE["leaf_edge"][None, :],
            smoothstep(0.25, 1.0, uu)[:, None] * 0.55,
        )
        leaf *= (0.7 + 0.6 * nz)[:, None]
        # glowing veins on the leaves, lit by the crimson rim
        vn = smoothstep(0.60, 0.72, fbm3(pm * 16.0, 2, seed=17))
        leaf += PALETTE["leaf_edge"][None, :] * (0.8 * vn * uu)[:, None]
        leaf += PALETTE["back_soft"][None, :] * (0.35 * smoothstep(0.6, 1.0, uu))[:, None]
        col[m] = col[m] + leaf

    # ------------------------------------------------------------------ stem
    if is_stem.any():
        m = is_stem
        pm = P[m]
        uu = u[m]
        nz = fbm3(pm * 11.0, 3, seed=211)
        stem = PALETTE["stem_fog"][None, :] * (0.20 + 0.45 * nz)[:, None]
        stem += PALETTE["leaf"][None, :] * 0.35
        col[m] = col[m] + stem

    return col


def bake(mesh, lights, center):
    """Return (nv, 8) attribute array: normal(3), lit colour(3), uv(2)."""
    N = mesh.N
    P = mesh.V
    albedo = _petal_albedo(mesh, center)

    key, fill, back, rim, under = lights

    def lambert(light):
        L = normalize(light.pos[None, :] - P)
        d = np.linalg.norm(light.pos[None, :] - P, axis=1)
        att = light.power / (0.35 + 0.10 * d * d + 0.25 * light.radius ** 2)
        ndl = dot(N, L)
        return L, d, att, ndl

    # --- key / rim ------------------------------------------------------ #
    Lk, dk, ak, ndlk = lambert(key)
    key_term = np.maximum(ndlk, 0.0)[:, None] ** 1.35 * ak[:, None] * key.color[None, :]
    # wrap lighting: petals are thin, light bleeds through them
    key_wrap = np.maximum(ndlk * 0.5 + 0.5, 0.0)[:, None] ** 3.0 * (0.22 * ak[:, None])
    key_term = key_term + key_wrap * key.color[None, :]

    Lf, df, af, ndlf = lambert(fill)
    fill_term = np.maximum(ndlf, 0.0)[:, None] ** 1.6 * af[:, None] * fill.color[None, :]

    # --- cold back light: transmission through the petals --------------- #
    Lb, db, ab, ndlb = lambert(back)
    trans = np.maximum(-ndlb, 0.0)[:, None] ** 0.8 + np.maximum(ndlb, 0.0)[:, None] * 0.15
    back_term = trans * ab[:, None] * back.color[None, :] * 1.25

    Lr, dr, ar, ndlr = lambert(rim)
    rim_term = np.maximum(ndlr, 0.0)[:, None] ** 1.6 * ar[:, None] * rim.color[None, :]
    Lu, du, au, ndlu = lambert(under)
    bounce_term = rim_term + np.maximum(ndlu, 0.0)[:, None] ** 2.0 * au[:, None] * under.color[None, :]

    # --- ambient sky gradient ------------------------------------------- #
    up = clamp(N[:, 1] * 0.5 + 0.5)[:, None]
    amb = (PALETTE["bg_mid"][None, :] * (1.0 - up) + PALETTE["back_soft"][None, :] * up) * 0.30

    lit = albedo * (amb + key_term + fill_term + back_term + bounce_term) * 0.85

    # --- emissive tips: hot spots that bloom ----------------------------- #
    is_petal = (mesh.M == MAT_PETAL) | (mesh.M == MAT_PETAL_INNER)
    u = mesh.UV[:, 0]
    v = mesh.UV[:, 1]
    tip = np.where(
        is_petal,
        smoothstep(0.80, 1.0, u) * smoothstep(0.62, 1.0, np.abs(v)) * 0.55,
        0.0,
    )
    lit = lit + PALETTE["petal_pink"][None, :] * tip[:, None] * 0.16
    # faint breath of light from the flower's heart
    heart = np.where(is_petal, smoothstep(0.55, 0.0, u) * 0.10, 0.0)
    lit = lit + PALETTE["rim"][None, :] * heart[:, None] * 0.6

    attr = np.concatenate([N.astype(np.float32), lit.astype(np.float32), mesh.UV[:, :2]], axis=1)
    return attr.astype(np.float32)
