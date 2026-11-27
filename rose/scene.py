"""Procedural scene: the rose itself, lights, background geometry and palette.

The rose is built from layered curved petal grids.  The look is deliberately
"anime": near-black body, intense crimson rim light, a cold teal back light and
a faint underworld glow — the *Tokyo Ghoul* colour script.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .mathutil import TAU, dot, fbm3, normalize, rgb, srgb_to_linear

# --------------------------------------------------------------------------- #
# palette (linear RGB)
# --------------------------------------------------------------------------- #
PALETTE = {
    "petal_deep": rgb("#150409"),      # almost black heart
    "petal_dark": rgb("#4a0715"),
    "petal_mid": rgb("#9c0f2c"),
    "petal_hot": rgb("#e13a4f"),
    "petal_pink": rgb("#ff7a86"),      # anime highlight / silky edge
    "vein": rgb("#2a0309"),
    "leaf": rgb("#10231a"),
    "leaf_edge": rgb("#25422c"),
    "stem_fog": rgb("#0b2b2a"),
    "rim": rgb("#ff2740"),             # warm crimson key/rim
    "rim_soft": rgb("#c31b3c"),
    "back": rgb("#2f7d8c"),            # cold teal back light
    "back_soft": rgb("#1c4b5a"),
    "moon": rgb("#cfe4ff"),
    "bg_top": rgb("#04060b"),
    "bg_mid": rgb("#0a0d17"),
    "glow": rgb("#ff3355"),
}


# --------------------------------------------------------------------------- #
# mesh builder
# --------------------------------------------------------------------------- #
class Mesh:
    """Triangle soup with smooth normals and a small attribute channel."""

    def __init__(self):
        self.v = []      # (n, 3) positions
        self.n = []      # (n, 3) normals
        self.uv = []     # (n, 3) -> (u, v, petal_id)
        self.mat = []    # (n,) material id
        self.tris = []   # (m, 3) vertex indices

    def add_grid(self, P, N, UV, mat_id):
        """Add an (nu, nv, 3) grid of vertices as a quad mesh."""
        nu, nv = P.shape[:2]
        base = sum(len(x) for x in self.v) if self.v else 0
        self.v.append(P.reshape(-1, 3).astype(np.float32))
        self.n.append(N.reshape(-1, 3).astype(np.float32))
        self.uv.append(UV.reshape(-1, 3).astype(np.float32))
        self.mat.append(np.full(nu * nv, mat_id, dtype=np.int16))
        idx = np.arange(nu * nv, dtype=np.int32).reshape(nu, nv) + base
        a = idx[:-1, :-1].ravel()
        b = idx[:-1, 1:].ravel()
        c = idx[1:, :-1].ravel()
        d = idx[1:, 1:].ravel()
        tri = np.concatenate(
            [np.stack([a, b, d], 1), np.stack([a, d, c], 1)], axis=0
        )
        self.tris.append(tri)

    def add_mesh(self, other: "Mesh"):
        base = sum(len(x) for x in self.v) if self.v else 0
        self.v.append(other.v)
        self.n.append(other.n)
        self.uv.append(other.uv)
        self.mat.append(other.mat)
        self.tris.append(other.tris + base)

    def finalize(self):
        self.V = np.concatenate(self.v, axis=0)
        self.N = np.concatenate(self.n, axis=0)
        self.UV = np.concatenate(self.uv, axis=0)
        self.M = np.concatenate(self.mat, axis=0)
        self.T = np.concatenate(self.tris, axis=0).astype(np.int32)
        # area for importance sampling of the "spark" glints
        v0 = self.V[self.T[:, 0]]
        v1 = self.V[self.T[:, 1]]
        v2 = self.V[self.T[:, 2]]
        self.area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
        self.cum_area = np.cumsum(self.area)
        return self


MAT_PETAL = 0
MAT_LEAF = 1
MAT_STEM = 2
MAT_PETAL_INNER = 3


# --------------------------------------------------------------------------- #
# rose construction
# --------------------------------------------------------------------------- #
@dataclass
class RoseParams:
    scale: float = 1.0
    center: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.05, 0.0]))
    seed: int = 7


def _bend_curve(u, alpha0, bend, L, r_attach, z_attach, curl_at=2.3):
    """Integrate the mid rib of a petal.

    ``alpha`` is the angle of the mid rib away from the flower's axis.  The
    shape of the curve is what separates a rose from a daisy: the inner petals
    climb almost vertically and only *curl over* near their rim, which is why
    the heart of a rose is a closed dome and not an open hole.
    """
    alpha = alpha0 + bend * np.clip(u, 0.0, 1.0) ** curl_at
    s = L / (len(u) - 1)
    dr = np.sin(alpha[:, 0]) * s
    dz = np.cos(alpha[:, 0]) * s
    r = r_attach + np.concatenate([[0.0], np.cumsum(dr)[:-1]])
    z = z_attach + np.concatenate([[0.0], np.cumsum(dz)[:-1]])
    return r[:, None], z[:, None], alpha


def _shell(x, e1=0.45, e2=3.2, tip=0.6, peak=0.42):
    """Soft box profile: 0 at the base, 1 over the belly, rounded at the tip."""
    x = np.clip(x, 0.0, 1.0)
    base = np.maximum(np.sin(np.pi * x ** peak), 0.0) ** tip * (
        e1 + (1.0 - e1) * np.clip(x * e2 / 0.55, 0.0, 1.0)
    )
    # rounder shoulders near the tip so the outer silhouette is not hexagonal
    return base * (0.55 + 0.45 * np.sqrt(np.clip(1.0 - (1.0 - x) ** 2, 0.0, 1.0)))


@dataclass
class PetalStyle:
    """Art-directed shape of one whorl of petals."""
    r_attach: float
    length: float
    tilt: float        # angle of the mid rib at the base (0 = straight up)
    bend: float        # how much the mid rib keeps curling over
    half_w: float
    cup: float         # 0 = flat sheet, 1 = almost closed tube
    z: float
    count: int
    roll: float        # how far the rim rolls over outwards


# The rose is seven whorls.  The numbers are hand-tuned: the heart is a tight,
# nearly closed dome, the outer whorls open into a wide bowl with a gently
# rolling rim.  This table *is* the flower's shape.
PETAL_STYLES = [
    #          r     len   tilt  curl  half_w cup    z      n  roll
    PetalStyle(0.025, 0.34, 0.05, 0.50, 0.46, 0.94, 0.06, 5, 0.05),
    PetalStyle(0.065, 0.50, 0.12, 0.58, 0.56, 0.90, 0.02, 6, 0.10),
    PetalStyle(0.135, 0.68, 0.22, 0.68, 0.68, 0.84, -0.02, 6, 0.16),
    PetalStyle(0.235, 0.88, 0.36, 0.80, 0.76, 0.74, -0.06, 7, 0.24),
    PetalStyle(0.320, 1.02, 0.54, 0.92, 0.84, 0.66, -0.11, 8, 0.32),
    PetalStyle(0.400, 1.12, 0.74, 1.02, 0.88, 0.58, -0.16, 8, 0.40),
    PetalStyle(0.470, 1.20, 0.96, 1.12, 0.92, 0.50, -0.21, 8, 0.48),
]


def build_petal(psi, style: PetalStyle, open_amount=1.0, rng=None, scale=1.0,
                nu=17, nv=19):
    """One petal as an (nu, nv, 3) point grid in world space.

    The petal is a bent sword: a mid rib that spirals out of the flower, a
    cupped cross-section (a real arc, not a polynomial) and a round tip.  The
    arc is what gives the silhouettes their rolled, silk-like look.
    """
    rng = rng if rng is not None else np.random.default_rng(0)
    f = min(psi * 0 - 1 + 1, 1)  # placeholder, unused

    r_attach = style.r_attach * scale
    L = style.length * scale
    alpha0 = (style.tilt * open_amount + style.bend * 0.30 * (open_amount - 1.0))
    bend = style.bend * (0.75 + 0.25 * open_amount)
    half_w = style.half_w * scale
    cup = style.cup
    z_attach = style.z
    roll = style.roll

    # organic variation
    L *= 1.0 + 0.05 * rng.normal()
    alpha0 += 0.055 * rng.normal()
    half_w *= 1.0 + 0.06 * rng.normal()
    cup *= 1.0 + 0.07 * rng.normal()

    u = np.linspace(0.0, 1.0, nu, dtype=np.float32)[:, None]
    v = np.linspace(-1.0, 1.0, nv, dtype=np.float32)[None, :]

    # ---------------------------------------------------------- mid rib
    r_mid, z_mid, alpha = _bend_curve(u, alpha0, bend, L, r_attach, z_attach)
    cp, sp = np.cos(psi), np.sin(psi)
    C = np.stack([cp * r_mid[:, 0], z_mid[:, 0], sp * r_mid[:, 0]], axis=1).astype(np.float32)
    # The petal's own surface normal: perpendicular to both the mid-rib
    # tangent and the tangential "side" vector.  This is the *outer* face.
    n_petal = np.stack(
        [np.sin(alpha[:, 0]) * cp, -np.cos(alpha[:, 0]), np.sin(alpha[:, 0]) * sp],
        axis=1,
    ).astype(np.float32)
    side = np.array([-sp, 0.0, cp], dtype=np.float32)

    # where the cup bends towards: horizontal-inwards in the bud, up in the
    # open whorls (the flower's inner face)
    inward = np.stack([-cp * np.ones(nu), np.zeros(nu), -sp * np.ones(nu)], 1).astype(np.float32)
    sin_a = np.sin(alpha[:, 0])[:, None]
    cup_dir = inward * (1.0 - sin_a) - n_petal * sin_a

    # ---------------------------------------------------------- width
    tip_sharp = 0.34 + 0.14 * (1.0 - min(style.roll, 1.0))   # soft, rounded tips
    width = _shell(u, tip=tip_sharp, peak=0.40) * half_w * L   # (nu,1)

    # ---------------------------------------------------- cross section (arc)
    theta_max = cup * np.pi * 0.66
    theta = v * theta_max
    across = (np.sin(theta)[..., None] * side[None, None, :]
              + (1.0 - np.cos(theta))[..., None] * cup_dir[:, None, :])
    # a slight ridge along the mid rib, and the rim rolling over outwards
    edge = np.abs(v) ** 3
    P = (C[:, None, :]
         + width[:, :, None] * across
         - n_petal[:, None, :] * (0.10 * roll * edge * width * u)[:, :, None]
         + n_petal[:, None, :] * (0.025 * width * (1.0 - v * v))[:, :, None])
    # a soft wave along the rim: never perfectly symmetric
    ruff = 0.05 * np.sin(u * 5.5 + v * 2.2 + psi * 1.7) * (u ** 2) * width
    P[..., 1] += ruff
    # subtle asymmetry
    P[..., 0] += 0.02 * scale * (u ** 2) * np.sin(psi * 2.0 + 1.0)
    P[..., 2] += 0.02 * scale * (u ** 2) * np.cos(psi * 2.0 + 1.0)

    # ---------------------------------------------------------- normals
    du = np.gradient(P, axis=0)
    dv = np.gradient(P, axis=1)
    N = normalize(np.cross(du, dv))
    flip = dot(N.mean(axis=1), n_petal.mean(axis=0)) < 0
    N[flip] *= -1.0
    # where the petal degenerates into a point (the tip) the gradient is 0:
    # fall back to the analytic normal so the silhouette stays round
    bad = np.linalg.norm(np.cross(du, dv), axis=-1) < 1e-9
    if bad.any():
        analytic = np.broadcast_to(n_petal[:, None, :], N.shape).copy()
        N[bad] = analytic[bad]
        N[bad] = normalize(N[bad])

    UV = np.stack(
        [np.broadcast_to(u, (nu, nv)), np.broadcast_to(v, (nu, nv)),
         np.zeros((nu, nv))],
        axis=-1,
    )
    return P.astype(np.float32), N.astype(np.float32), UV.astype(np.float32)


def build_sepal(psi, rng, scale=1.0, nu=11, nv=13):
    """Small bracts under the flower that flick outward."""
    r_attach, L, alpha0, bend = 0.26 * scale, 0.52 * scale, 1.85, 1.25
    r_attach *= 1.0 + 0.05 * rng.normal()
    u = np.linspace(0.0, 1.0, nu, dtype=np.float32)[:, None]
    v = np.linspace(-1.0, 1.0, nv, dtype=np.float32)[None, :]
    r_mid, z_mid, alpha = _bend_curve(u, alpha0, bend, L, r_attach, -0.12, curl_at=1.6)
    cp, sp = np.cos(psi), np.sin(psi)
    C = np.stack([cp * r_mid[:, 0], z_mid[:, 0], sp * r_mid[:, 0]], axis=1).astype(np.float32)
    n_petal = np.stack(
        [np.sin(alpha[:, 0]) * cp, -np.cos(alpha[:, 0]), np.sin(alpha[:, 0]) * sp], 1
    ).astype(np.float32)
    side = np.array([-sp, 0.0, cp], dtype=np.float32)
    width = _shell(u, e1=0.25, e2=2.4) * 0.17 * L
    theta = v * 0.85
    across = np.sin(theta)[..., None] * side[None, None, :] - (
        1.0 - np.cos(theta))[..., None] * n_petal[:, None, :]
    P = C[:, None, :] + width[:, :, None] * across
    du = np.gradient(P, axis=0)
    dv = np.gradient(P, axis=1)
    N = normalize(np.cross(du, dv))
    flip = dot(N.mean(axis=1), n_petal.mean(axis=0)) < 0
    N[flip] *= -1.0
    bad = np.linalg.norm(np.cross(du, dv), axis=-1) < 1e-9
    if bad.any():
        analytic = np.broadcast_to(n_petal[:, None, :], N.shape).copy()
        N[bad] = normalize(analytic[bad])
    UV = np.stack([np.broadcast_to(u, (nu, nv)), np.broadcast_to(v, (nu, nv)),
                   np.zeros((nu, nv))], -1)
    return P.astype(np.float32), N.astype(np.float32), UV.astype(np.float32)


@dataclass
class Rose:
    """The rose: a layered set of petals + stem + leaves."""

    params: RoseParams = field(default_factory=RoseParams)
    mesh: Mesh | None = None

    def build(self, time=0.0, wind=0.0, open_amount=1.0, res=None, head=False):
        p = self.params
        rng = np.random.default_rng(p.seed)
        mesh = Mesh()
        # a coarse canvas (the terminal) is happy with fewer vertices per petal
        res_kw = {} if res is None else {"nu": int(res[0]), "nv": int(res[1])}

        petal_id = 0
        for li, st in enumerate(PETAL_STYLES):
            # each whorl is rotated so the seams never line up
            phase = li * 1.31 + 0.13 * np.sin(li * 2.3)
            for k in range(st.count):
                psi = TAU * (k / st.count) + phase
                P, N, UV = build_petal(psi, st, open_amount=open_amount,
                                       rng=rng, scale=p.scale, **res_kw)
                if wind:
                    sway = 0.014 * wind * np.sin(time * 0.9 + psi * 1.7 + li)
                    P[..., 1] += sway * (UV[..., 0] ** 2)
                UV[..., 2] = float(petal_id)
                petal_id += 1
                mesh.add_grid(P, N, UV, MAT_PETAL if li > 1 else MAT_PETAL_INNER)

        # --- the heart: a dark silk bowl that blocks every gap between the
        # inner petals, so the bloom never shows the background through it ---
        nb, nphi = 9, 40
        bu = np.linspace(0.02, 1.0, nb)
        phi = np.linspace(0, TAU, nphi, endpoint=False)
        BU, PH = np.meshgrid(bu, phi, indexing="ij")
        R = 0.10 + 0.46 * BU ** 0.85
        Z = 0.34 - 0.42 * BU ** 1.5
        bP = np.stack([np.cos(PH) * R, Z, np.sin(PH) * R], -1).astype(np.float32)
        # normals point into the bowl (towards the camera above)
        d1 = np.gradient(bP, axis=0)
        d2 = np.gradient(bP, axis=1)
        bN = normalize(np.cross(d2, d1))
        flip = bN[..., 1] < 0
        bN[flip] *= -1.0
        bUV = np.stack([BU * 0.55, np.zeros_like(BU), np.full_like(BU, float(petal_id + 3))], -1)
        mesh.add_grid(bP, bN, bUV.astype(np.float32), MAT_PETAL_INNER)

        # --- sepals / leaves below the flower ---------------------------- #
        nsep = 0 if head else 6
        for k in range(nsep):
            psi = TAU * k / nsep + 0.35
            P, N, UV = build_sepal(psi, rng, scale=p.scale)
            UV[..., 2] = float(petal_id)
            petal_id += 1
            mesh.add_grid(P, N, UV, MAT_LEAF)

        if head:
            # the solo shot frames the bloom itself: a stem and a leaf poking
            # out of the bottom of the lens read as debris, not as a flower
            mesh.finalize()
            mesh.G = np.round(mesh.UV[:, 2]).astype(np.int32) * 3 + mesh.M * 7
            mesh.V = mesh.V + p.center[None, :]
            self.mesh = mesh
            return mesh

        # --- stem --------------------------------------------------------- #
        seg = 30
        t = np.linspace(0, 1, seg)
        stem_r = 0.019 * p.scale
        pts = []
        for i in range(seg):
            rr = stem_r * (1.0 - 0.20 * t[i])
            ring_ang = np.linspace(0, TAU, 9, endpoint=False)
            x = np.cos(ring_ang) * rr + 0.34 * t[i] ** 2.2 * p.scale
            z = np.sin(ring_ang) * rr - 0.30 * t[i] ** 1.6 * p.scale
            y = -1.55 * t[i] * p.scale + 0.04 * np.sin(t[i] * 5)
            pts.append(np.stack([x, np.full(9, y), z], 1))
        Pstem = np.stack(pts, 0).astype(np.float32)
        # normals point outwards from the stem axis
        Nstem = Pstem.copy()
        Nstem[..., 1] = 0
        Nstem = normalize(Nstem)
        UVs = np.zeros_like(Pstem)
        UVs[..., 0] = np.linspace(0, 1, seg)[:, None]
        UVs[..., 2] = float(petal_id + 1)
        mesh.add_grid(Pstem, Nstem, UVs, MAT_STEM)

        # one small leaf on the stem so the silhouette reads as a flower
        lseg = 9
        lu = np.linspace(0.0, 1.0, lseg, dtype=np.float32)
        lv = np.linspace(-1.0, 1.0, 7, dtype=np.float32)
        ang_leaf = 2.2
        lx = 0.50 + lu * 0.55
        ly = -1.02 - lu * 0.12 + 0.10 * lu ** 2
        lw = np.sin(np.pi * lu ** 0.7) * 0.16
        LX, LV = np.meshgrid(lu, lv, indexing="ij")
        Pleaf = np.stack(
            [0.50 + LX * 0.55,
             -1.05 - LX * 0.10 + 0.08 * LX ** 2 + LV * 0.02,
             LV * lw[:, None]],
            2,
        ).astype(np.float32)
        du_l = np.gradient(Pleaf, axis=0)
        dv_l = np.gradient(Pleaf, axis=1)
        Nleaf = normalize(np.cross(dv_l, du_l))
        UVleaf = np.stack([LX, LV, np.full_like(LX, float(petal_id + 2))], -1)
        mesh.add_grid(Pleaf, Nleaf, UVleaf.astype(np.float32), MAT_LEAF)

        mesh.finalize()
        mesh.G = np.round(mesh.UV[:, 2]).astype(np.int32) * 3 + mesh.M * 7
        # move to the rose's world position
        mesh.V = mesh.V + p.center[None, :]
        self.mesh = mesh
        return mesh


# --------------------------------------------------------------------------- #
# lights & background
# --------------------------------------------------------------------------- #
@dataclass
class Light:
    pos: np.ndarray
    color: np.ndarray
    power: float
    radius: float          # 0 = point light, >0 = soft area light


def make_lights(center, front=False):
    """The colour script: warm silky key, crimson rim, cold teal back light.

    ``front`` swings the key round to face the camera and drops the back rim:
    the bare-backdrop shot has no sky left to light the near side of the bloom.
    """
    c = center
    if front:
        return [
            # 0 · key: warm silk on the upper petals, but never the whole face
            Light(np.array([-1.3, 2.3, 3.0]) + c, rgb("#ffe0cf"), 1.15, 0.50),
            # 1 · fill: crimson from the right, lifts the shadow side
            Light(np.array([2.8, 0.9, 1.7]) + c, PALETTE["rim_soft"], 0.60, 1.2),
            # 2 · back: cold teal, rims the silhouette from behind
            Light(np.array([0.4, 2.5, -2.5]) + c, PALETTE["back"], 2.3, 0.5),
            # 3 · rim: crimson behind-left — the *Tokyo Ghoul* tell
            Light(np.array([-2.0, 0.8, -2.2]) + c, PALETTE["rim"], 5.2, 0.35),
            # 4 · under-glow: crimson haze licking the lowest petals
            Light(np.array([0.2, -2.1, 2.0]) + c, rgb("#ff2c20"), 1.10, 1.2),
        ]
    return [
        # 0 · key: warm, upper front-left — paints the silk gradients
        Light(np.array([-2.4, 2.6, 1.9]) + c, rgb("#ffd9c6"), 1.30, 0.45),
        # 1 · fill: weak crimson from the right, keeps the shadow side alive
        Light(np.array([2.4, 0.6, 1.2]) + c, PALETTE["rim_soft"], 0.55, 1.0),
        # 2 · back: cold teal from above/behind, rims the silhouette
        Light(np.array([0.9, 2.6, -2.9]) + c, PALETTE["back"], 2.6, 0.5),
        # 3 · rim: crimson, low behind-left — the *Tokyo Ghoul* tell
        Light(np.array([-2.3, 0.55, -2.2]) + c, PALETTE["rim"], 4.4, 0.35),
        # 4 · under-glow: the crimson haze below the flower licking the petals
        Light(np.array([0.2, -2.4, 1.4]) + c, rgb("#ff3b2e"), 0.85, 1.2),
    ]


def build_background(rng, center):
    """Distant, half-ruined skyline + a huge dark sky sphere."""
    mesh = Mesh()
    nb = 90
    for i in range(nb):
        ang = TAU * rng.random()
        dist = 28.0 + 55.0 * rng.random() ** 0.6
        h = 2.0 + 20.0 * rng.random() ** 2.2
        w = 1.4 + 4.5 * rng.random()
        x = np.cos(ang) * dist
        z = np.sin(ang) * dist
        y0 = -3.0
        corners = np.array(
            [
                [x - w, y0, z - w],
                [x + w, y0, z - w],
                [x + w, y0, z + w],
                [x - w, y0, z + w],
                [x - w, y0 + h, z - w],
                [x + w, y0 + h, z - w],
                [x + w, y0 + h, z + w],
                [x - w, y0 + h, z + w],
            ],
            dtype=np.float32,
        )
        faces = [
            (0, 1, 2, 3),   # front
            (4, 7, 6, 5),   # back
            (0, 4, 5, 1),   # left
            (1, 5, 6, 2),   # right
            (3, 2, 6, 7),   # front-ish
            (0, 3, 7, 4),   # other side
        ]
        for f in faces:
            quad = corners[list(f)]
            nrm = normalize(np.cross(quad[1] - quad[0], quad[3] - quad[0])[None, :])
            P = quad[None, :, :].astype(np.float32)
            N = np.broadcast_to(nrm, (1, 4, 3)).copy()
            UV = np.zeros((1, 4, 3), np.float32)
            UV[..., 1] = i
            mesh.add_grid(P, N, UV, MAT_LEAF)

    return mesh


# --------------------------------------------------------------------------- #
# floating dust / embers
# --------------------------------------------------------------------------- #
@dataclass
class Dust:
    pos: np.ndarray       # (n, 3)
    speed: np.ndarray     # (n,)
    phase: np.ndarray
    size: np.ndarray
    warm: np.ndarray      # 1 = crimson mote, 0 = cold


def make_dust(rng, n=260, center=np.zeros(3, np.float32)):
    near = 0.35
    pos = rng.normal(size=(n, 3)).astype(np.float32) * np.array([1.9, 1.9, 1.9], np.float32)
    # keep motes away from the very centre so they read as background
    r = np.linalg.norm(pos, axis=1)
    pos[r < near] *= 2.2
    pos += center
    speed = (0.02 + 0.10 * rng.random(n)).astype(np.float32)
    phase = (rng.random(n) * TAU).astype(np.float32)
    size = (0.35 + 0.9 * rng.random(n) ** 2).astype(np.float32)
    warm = (rng.random(n) ** 1.5).astype(np.float32)
    return Dust(pos.ravel().reshape(n, 3), speed, phase, size, warm)
