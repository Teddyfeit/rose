"""Smoke tests: geometry is sane, the rasteriser covers the flower, the
pipeline produces a stable, loopable image."""
import numpy as np
import pytest

from rose.mathutil import linear_to_srgb, srgb_to_linear, normalize
from rose.renderer import RoseRenderer
from rose.raster import Camera, rasterize
from rose.scene import Rose, RoseParams


@pytest.fixture(scope="module")
def rose():
    return Rose(RoseParams(seed=7)).build()


@pytest.fixture(scope="module")
def renderer():
    return RoseRenderer(240, 135, seed=7)


def test_rose_geometry(rose):
    assert rose.V.shape[0] > 4000
    assert rose.T.shape[1] == 3
    assert np.isfinite(rose.V).all() and np.isfinite(rose.N).all()
    # normals are unit length
    n = np.linalg.norm(rose.N, axis=1)
    assert np.allclose(n, 1.0, atol=1e-3)
    # the flower is roughly round and centred on its origin
    r = np.linalg.norm(rose.V[:, [0, 2]], axis=1)
    assert 0.5 < r.max() < 2.0


def test_rose_opens(rose):
    """The breath animation must actually change the flower."""
    tight = Rose(RoseParams(seed=7)).build(open_amount=0.80)
    open_ = Rose(RoseParams(seed=7)).build(open_amount=1.05)
    assert open_.V[:, [0, 2]].std() > tight.V[:, [0, 2]].std()


def test_rasterizer_covers(rose):
    rose.attr = np.concatenate(
        [rose.N, np.zeros((len(rose.V), 3), np.float32), rose.UV[:, :2]], 1
    )
    cam = Camera(180, 180, 30.0, (0.0, 1.6, 3.4), (0.0, 0.2, 0.0))
    surf = rasterize(rose, cam)
    assert len(surf["pix"]) > 3000
    assert surf["z"].min() > 0
    assert np.isfinite(surf["pos"]).all()
    assert (surf["ao"] >= 0).all() and (surf["ao"] <= 1).all()


def test_frame(renderer):
    img = renderer.render(0.0)
    assert img.shape == (135, 240, 3) and img.dtype == np.uint8
    assert img.mean() > 3          # not a black frame
    assert img.std() > 8           # has structure


def test_loop_is_deterministic(renderer):
    """Two calls with the same phase differ only by noise (grain/ember seeds),
    so the loop stays visually seamless."""
    a = renderer.render(0.42).astype(np.int16)
    b = renderer.render(0.42).astype(np.int16)
    assert np.abs(a - b).mean() < 3.0


def test_phase_wraps(renderer):
    a = renderer.render(0.0)
    b = renderer.render(1.0)
    assert np.abs(a.astype(int) - b.astype(int)).mean() < 4.0


def test_colour_helpers():
    c = np.array([0.2, 0.5, 0.9], np.float32)
    assert np.allclose(linear_to_srgb(srgb_to_linear(c)), c, atol=1e-4)


# --------------------------------------------------------------------------- #
# the terminal player
# --------------------------------------------------------------------------- #
def test_terminal_cells_shape():
    from rose.terminal import cells

    frame = np.zeros((40, 80, 3), np.float32)
    frame[10:20, 30:50] = 1.0
    cell = cells(frame, 20, 10)
    assert cell.shape == (10, 20, 2, 3)
    assert cell.min() >= 0.0 and cell.max() <= 1.0
    # the white patch lands in the middle rows, upper half first
    assert cell[3, 10, 0].mean() > cell[0, 10, 0].mean()


def test_terminal_frame_has_visible_structure(renderer):
    """A renderer built for a small canvas still paints a readable image."""
    from rose.terminal import Artist, RAMP

    small = RoseRenderer(200, 112, seed=7).lite()
    img = small.render(0.145)
    assert img.shape == (112, 200, 3)

    ascii_art = Artist(cols=100, rows=28, truecolor=False, mono=True).paint(img)
    assert len(ascii_art) == 28
    assert all(len(line) == 100 for line in ascii_art)
    used = {ch for line in ascii_art for ch in line}
    assert len(used) >= 5                       # more than "empty"
    # brightness must vary across the frame: a blank canvas is not a picture
    assert sum(ch != " " for line in ascii_art for ch in line) > 300
    assert set(RAMP) >= used


def test_terminal_truecolor_rows_are_escaped(renderer):
    from rose.terminal import Artist

    small = RoseRenderer(160, 90, seed=7).lite()
    img = small.render(0.30)
    art = Artist(cols=60, rows=17, truecolor=True).paint(img)
    assert len(art) == 17
    assert all("\x1b[38;2;" in line and "\u2580" in line for line in art)
    assert all(line.endswith("\x1b[49m\x1b[0m") for line in art)


def test_lite_frames_faster_and_keeps_the_look(renderer):
    """The terminal renderer is a coarse canvas, not a different picture."""
    full = RoseRenderer(200, 112, seed=7)
    lite = RoseRenderer(200, 112, seed=7).lite()
    a = full.render(0.145).astype(np.float32)
    b = lite.render(0.145).astype(np.float32)
    assert b.mean() > a.mean()                  # hotter exposure for the cells
    # correlation of the two frames: same composition, not a different shot
    ca, cb = a - a.mean(), b - b.mean()
    corr = (ca * cb).sum() / np.sqrt((ca * ca).sum() * (cb * cb).sum())
    assert corr > 0.55, corr


def test_terminal_player_paints_and_handles_keys():
    """The player draws into a stream and its controls change state."""
    import io

    from rose.tui import BLOOM_LEVELS, TerminalRose

    out = io.StringIO()
    player = TerminalRose(cols=74, rows=20, stream=out)
    player.artist.truecolor = True      # no tty here: pretend a modern terminal
    assert player.artist.size() == (74, 20)
    assert player.renderer.w <= 360

    player.paint_frame()
    text = out.getvalue()
    assert player.frames == 1
    assert "\u2580" in text and "rose" in text       # picture + status line

    player._handle(" ")
    assert player.paused is True
    player._handle("b")
    assert player.bloom in BLOOM_LEVELS
    assert player.renderer.bloom_scale == player.bloom
    player._handle("a")                # truecolor -> brightness ramp
    assert player.ascii is True and player._use_ascii() is True
    player._handle("3")
    assert player.fps == 6.0
    player._handle("q")
    assert player.running is False


def test_solo_shot_is_only_the_rose():
    """The default film has one subject: no sky, no skyline, no stem."""
    from rose.renderer import backdrop

    solo = RoseRenderer(160, 90, seed=7).solo()
    assert solo.only_rose is True
    full = RoseRenderer(160, 90, seed=7)
    assert full.only_rose is False

    # the backdrop is a plain crimson void: no horizon line, no city windows
    rays = np.stack([np.linspace(-0.3, 0.3, 4000, dtype=np.float32),
                     np.full(4000, 0.05, np.float32),
                     np.linspace(-1.0, -0.2, 4000, dtype=np.float32)], 1)
    col = backdrop(rays)
    assert col.shape == (4000, 3) and np.isfinite(col).all()
    assert col[:, 0].max() > col[:, 2].max()      # crimson, not blue
    # a ray at the spot is brighter than one far away
    spot = backdrop(np.array([[-0.0, 0.02, -1.0], [3.0, 0.0, -1.0]], np.float32))
    assert spot[0].sum() > spot[1].sum()

    img = solo.render(0.145).astype(np.float32)
    assert img.shape == (90, 160, 3) and img.mean() > 4
    # ... and the bloom on its own carries the frame: a solid rose in the
    # middle, an empty void in the corners
    lum = np.clip(img.mean(axis=2), 0, 255)
    h, w = lum.shape
    mid = lum[h // 4: 3 * h // 4, w // 5: 4 * w // 5]
    assert (mid > 60).mean() > 0.20
    corner = np.concatenate([lum[:12, :12].ravel(), lum[:12, -12:].ravel()])
    assert (corner < 40).mean() > 0.80
    # it is painted in crimson, not in grey
    red = img[..., 0].mean()
    assert red > 1.6 * img[..., 2].mean()


def test_stem_is_only_in_the_city_shot():
    from rose.scene import MAT_STEM, Rose, RoseParams

    head = Rose(RoseParams(seed=7)).build(head=True)
    whole = Rose(RoseParams(seed=7)).build()
    assert (whole.M == MAT_STEM).any()
    assert not (head.M == MAT_STEM).any()
    assert np.allclose(head.V[:, 1].max(), whole.V[:, 1].max(), atol=0.2)


def test_terminal_ramp_keeps_colour_on_a_colour_terminal():
    """``a`` on a truecolor terminal must not throw the rose away."""
    import io

    from rose.tui import TerminalRose

    p = TerminalRose(cols=60, rows=17, stream=io.StringIO())
    p.artist.truecolor = True
    img = p.renderer.render(0.3)
    p.artist.mono = True
    lines = p.artist.paint(img)
    assert len(lines) == 17
    assert all("\x1b[38;2;" in line for line in lines)      # still crimson
    chars = {c for line in lines for c in line if c.isprintable()}
    assert len(chars & set(" .:-=+*#%@")) >= 4              # ... and a ramp
    p.artist.mono = False
    blocks = p.artist.paint(img)
    assert all("\u2580" in line for line in blocks)         # back to half blocks
