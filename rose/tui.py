"""``rose tui`` - the whole film inside a terminal.

Nothing is streamed, downloaded or decoded: the rose is built, lit, bloomed and
tonemapped by numpy on the CPU, then painted into the console with Unicode half
blocks, so every character cell carries two pixels of the picture.  A 100x30
console is a 100x60 pixel canvas.

Controls
    space      pause / resume
    n          step one frame while paused
    b          cycle the bloom strength          (0.6 / 1.0 / 1.4)
    a          cycle colour: truecolor -> brightness ramp -> automatic
    s          save the current frame as a PNG into assets/
    f          3 = 6 fps, 4 = 10 fps, 5 = 16 fps
    h          hide / show the status line
    q, Esc     quit
"""

from __future__ import annotations

import os
import select
import signal
import sys
import time
from pathlib import Path

import numpy as np

from .renderer import RoseRenderer
from .terminal import (Artist, CLEAR, HOME, RESET, centre, ease, terminal_size)

ASPECT = 16 / 9

TITLE = "R O S E"
SUBTITLE = "crimson bloom"
CREDIT = "procedural \u00b7 numpy on the cpu \u00b7 no shaders, no gpu"

SS = 2                          # supersampling: render at 2x the cell grid
DEFAULT_FPS = 8.0
FRAME_RATES = {"3": 6.0, "4": 10.0, "5": 16.0}
BLOOM_LEVELS = (0.6, 1.0, 1.4)

RED = (255, 64, 84)
DIM_RED = (120, 26, 40)
PALE = (214, 184, 188)


# --------------------------------------------------------------------------- #
# keyboard
# --------------------------------------------------------------------------- #
def _isatty(stream):
    try:
        return bool(stream.isatty())
    except Exception:
        return False


class Keys:
    """Non-blocking single key reader (POSIX tty only)."""

    def __init__(self, stream=None):
        stream = stream or sys.stdin
        self.fd = stream.fileno() if _isatty(stream) else None
        self.enabled = self.fd is not None

    def poll(self):
        if not self.enabled:
            return None
        try:
            ready, _, _ = select.select([self.fd], [], [], 0)
        except (OSError, ValueError):
            self.enabled = False
            return None
        if not ready:
            return None
        try:
            data = os.read(self.fd, 16)
        except OSError:
            self.enabled = False
            return None
        if not data:
            self.enabled = False
            return None
        text = data.decode("utf-8", "ignore")
        if text.startswith("\x1b[") or text.startswith("\x1bO"):
            return None          # arrow / function keys
        return text[:1]


# --------------------------------------------------------------------------- #
# the player
# --------------------------------------------------------------------------- #
class TerminalRose:
    def __init__(self, seed=7, loop=26.0, cols=None, rows=None, stream=None,
                 only_rose=True):
        self.out = stream or sys.stdout
        self.seed = seed
        self.loop = loop
        # the default shot is the rose on its own; ``city=True`` brings the
        # old skyline back (there is only one subject here)
        self.only_rose = bool(only_rose)
        self.artist = Artist(cols=cols, rows=rows, out=self.out, supersample=SS)
        if cols is None or rows is None:
            self.artist.fit(ASPECT)
        self.renderer = RoseRenderer(120, 68, loop_seconds=loop, seed=seed)
        if self.only_rose:
            self.renderer.solo()
        else:
            self.renderer.lite()
        self._fit_renderer()

        self.phase = 0.145
        self.paused = False
        self.running = True
        self.fps = DEFAULT_FPS
        self.bloom = BLOOM_LEVELS[1]
        self.ascii = None            # None = automatic (off on colour terminals)
        self.show_status = True
        self.frames = 0
        self.last_frame = None
        self._cache = {}
        self.measured = 0.0
        self._size = (0, 0)

    # -- geometry ---------------------------------------------------------- #
    def _fit_renderer(self):
        cols, rows = self.artist.size()
        # supersample the cell grid a little: enough to keep the petal edges
        # readable, cheap enough that the player stays interactive on a laptop
        ss = 1.5 if cols <= 140 else 1.0
        self.artist.supersample = ss
        cap = 420 if self.only_rose else 360      # the bloom is the whole shot
        # a character cell shows two pixels and is ~twice as tall as wide, so
        # the canvas must keep the film's aspect: it is stretched onto the grid
        w = min(int(round(max(96, cols) * ss)), cap)
        h = int(round(max(54, cols / ASPECT) * ss))
        step = 2 * int(ss) if ss >= 2 else 2
        if h % step:
            h += step - h % step
        self.renderer.resize(w, h)
        self._size = (cols, rows)

    def _use_ascii(self):
        return (not self.artist.truecolor) if self.ascii is None else bool(self.ascii)

    def _sync_size(self):
        c, r = terminal_size()
        if (c, r) == self._last_terminal:
            return False
        self._last_terminal = (c, r)
        self.artist.fit(ASPECT)
        self._fit_renderer()
        return True

    # -- frame ------------------------------------------------------------- #
    def current(self, phase=None):
        """A frame, memoised per loop phase (the server and the intro use it)."""
        phase = self.phase if phase is None else float(phase % 1.0)
        key = (int(round(phase * 1200)), self.renderer.w, self.renderer.h)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        img = self.renderer.render(phase)
        if len(self._cache) > 64:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = img
        return img

    def render_frame(self):
        t0 = time.time()
        img = self.current()
        self.last_frame = img
        dt = time.time() - t0
        inst = 1.0 / dt if dt > 0 else 0.0
        self.measured = inst if not self.measured else 0.8 * self.measured + 0.2 * inst
        return img

    def paint_frame(self, img=None, home=True):
        img = self.render_frame() if img is None else img
        self.artist.mono = self._use_ascii()
        lines = self.artist.paint(img)
        cols, rows = self.artist.size()
        buf = [HOME if home else ""]
        buf.append("\n".join(lines))
        if self.show_status:
            buf.append("\x1b[%d;1H\x1b[0m\x1b[2m%-*s%s" % (rows + 1, cols, self._status(), RESET))
        else:
            buf.append("\x1b[%d;1H\x1b[0m%-*s" % (rows + 1, cols, ""))
        self.out.write("".join(buf))
        self.out.flush()
        self.frames += 1

    def _status(self):
        cols, rows = self.artist.size()
        items = ["rose", "%5.1f%%" % (self.phase * 100.0), "%4.1f f/s" % self.measured,
                 "bloom x%.1f" % self.bloom, "%dx%d px" % (cols, rows * 2)]
        if self.paused:
            items.insert(1, "PAUSED")
        head = "  ".join(items)
        tail = "space pause  n step  b bloom  f speed  a ascii  s save  q quit"
        if len(head) + len(tail) + 3 <= cols:
            pad = cols - len(head) - len(tail)
            return head + " " * max(2, pad) + tail
        return head

    # -- input ------------------------------------------------------------- #
    def _handle(self, key):
        if key in ("q", "Q", "\x03", "\x1b"):
            self.running = False
        elif key == " ":
            self.paused = not self.paused
        elif key in ("n", "N"):
            self.paused = True
            self.phase = (self.phase + 1.0 / 240.0) % 1.0
            self.paint_frame()
        elif key in ("b", "B"):
            i = BLOOM_LEVELS.index(self.bloom)
            self.bloom = BLOOM_LEVELS[(i + 1) % len(BLOOM_LEVELS)]
            self.renderer.bloom_scale = self.bloom
        elif key in ("a", "A"):
            # None means "whatever the terminal supports"; the first press
            # flips *that*, later presses just toggle
            self.ascii = self.artist.truecolor if self.ascii is None else (not self.ascii)
        elif key in ("s", "S"):
            self.save_frame()
        elif key in ("h", "H"):
            self.show_status = not self.show_status
        elif key in FRAME_RATES:
            self.fps = FRAME_RATES[key]

    def save_frame(self):
        from PIL import Image
        if self.last_frame is None:
            return None
        out = Path("assets")
        out.mkdir(exist_ok=True)
        path = out / ("terminal_%03d.png" % (self.frames % 1000))
        Image.fromarray(self.last_frame).resize(
            (self.last_frame.shape[1] * 3, self.last_frame.shape[0] * 3), Image.NEAREST).save(path)
        return path

    # -- intro ------------------------------------------------------------- #
    def intro(self):
        cols, rows = self.artist.size()
        self.out.write(CLEAR + HOME)

        top = max(1, rows // 2 - 2)
        reveal = "".join("\x1b[38;2;%d;%d;%dm%s" % (RED[0], RED[1], RED[2], ch)
                         if ch.strip() else " "
                         for ch in TITLE)
        for i in range(1, len(TITLE) + 1):
            frag = "".join(TITLE[:i])
            self.out.write(HOME + "\n" * top
                           + "\x1b[1m\x1b[38;2;%d;%d;%dm%s%s" % (*RED, centre(frag, cols), RESET))
            self.out.flush()
            time.sleep(0.07)
        time.sleep(0.18)
        steps = 12
        for i in range(1, steps + 1):
            t = ease(i / steps)
            sub = self._fade(SUBTITLE.upper(), t, PALE)
            self.out.write(HOME + "\n" * top
                           + "\x1b[1m\x1b[38;2;%d;%d;%dm%s%s" % (*RED, centre(TITLE, cols), RESET)
                           + "\n\n" + sub)
            self.out.flush()
            time.sleep(0.035)
        for i in range(1, 10):
            t = ease(i / 9.0)
            credit = self._fade(CREDIT, t * 0.75, DIM_RED)
            self.out.write(HOME + "\n" * top
                           + "\x1b[1m\x1b[38;2;%d;%d;%dm%s%s" % (*RED, centre(TITLE, cols), RESET)
                           + "\n\n" + self._fade(SUBTITLE.upper(), 1.0, PALE)
                           + "\n\n\n" + credit)
            self.out.flush()
            time.sleep(0.04)
        time.sleep(0.5)
        self.out.write(CLEAR + HOME)

    @staticmethod
    def _fade(text, amount, rgb, width=0):
        r, g, b = (int(c * amount) for c in rgb)
        if width:
            text = centre(text, width)
        return "\x1b[2m\x1b[38;2;%d;%d;%dm%s%s" % (r, g, b, text, RESET)

    # -- main loop --------------------------------------------------------- #
    def run(self, seconds=None, do_intro=True, fps=None):
        if fps:
            self.fps = fps
        keys = Keys()
        old = term_setup()
        self._last_terminal = terminal_size()
        t_start = time.time()
        t_prev = t_start
        try:
            if do_intro:
                self.intro()
                t_prev = time.time()
            while self.running:
                if seconds is not None and time.time() - t_start >= seconds:
                    break
                t0 = time.time()
                # the film runs at wall-clock speed: each frame advances by the
                # time it actually took, so a slow terminal plays in real time
                # instead of in slow motion
                if not self.paused:
                    dt = min(max(t0 - t_prev, 1e-3), 0.5)
                    self.phase = (self.phase + dt / self.loop) % 1.0
                t_prev = t0
                if self._sync_size():
                    self.out.write(CLEAR + HOME)
                self.paint_frame(home=self.frames > 0)
                key = keys.poll()
                if key:
                    self._handle(key)
                # sleep out the remaining budget (if any), still listening
                while self.running and not self.paused:
                    left = (1.0 / self.fps) - (time.time() - t0)
                    if left <= 0:
                        break
                    key = keys.poll()
                    if key:
                        self._handle(key)
                    time.sleep(min(left, 0.02))
        finally:
            term_restore(old)
            self.out.write(RESET + "\n")
            self.out.flush()
        return self.frames


# --------------------------------------------------------------------------- #
# terminal plumbing
# --------------------------------------------------------------------------- #
def term_setup():
    """Alternate screen, hidden cursor, cbreak keys."""
    state = {"ok": False, "fd": None, "tc": None}
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        if not sys.stdin.isatty():
            raise OSError("stdin is not a tty")
        state["tc"] = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        state["fd"] = fd
        state["ok"] = True
    except Exception:
        state["ok"] = False
    sys.stdout.write("\x1b[?1049h\x1b[?25l" + CLEAR + HOME)
    sys.stdout.flush()
    return state


def term_restore(state):
    sys.stdout.write(RESET + "\x1b[?25h\x1b[?1049l")
    sys.stdout.flush()
    if not state.get("ok"):
        return
    try:
        import termios
        termios.tcsetattr(state["fd"], termios.TCSADRAIN, state["tc"])
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def add_arguments(p):
    g = p.add_argument_group("terminal player")
    g.add_argument("--seconds", type=float, default=None,
                   help="stop after N seconds (default: until q)")
    g.add_argument("--fps", type=float, default=DEFAULT_FPS, help="frames per second (default 8)")
    g.add_argument("--cols", type=int, default=None, help="character columns (default: terminal)")
    g.add_argument("--rows", type=int, default=None, help="character rows (default: terminal)")
    g.add_argument("--no-intro", action="store_true", help="skip the title card")
    g.add_argument("--ascii", action="store_true", help="brightness ramp instead of colour")
    g.add_argument("--no-color", action="store_true", help="never emit colour escapes")
    g.add_argument("--city", action="store_true",
                   help="bring back the skyline behind the rose (default: the rose alone)")
    g.add_argument("--dump", default=None, metavar="DIR",
                   help="write a short clip as text frames instead of animating")
    return p


def main(args):
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    if args.dump:
        return dump(args)
    if not _isatty(sys.stdout):
        # piped into a file or another program: a single frame, no escapes
        # (use --dump to write a whole clip as text)
        return still(args)

    player = TerminalRose(seed=args.seed, loop=args.loop, cols=args.cols, rows=args.rows,
                          only_rose=not args.city)
    if args.ascii:
        player.ascii = True
    player.bloom = BLOOM_LEVELS[1]
    try:
        signal.signal(signal.SIGINT, lambda *a: setattr(player, "running", False))
    except ValueError:  # pragma: no cover - not on the main thread
        pass
    t0 = time.time()
    n = player.run(seconds=args.seconds, do_intro=not args.no_intro, fps=args.fps)
    print("  %d frames  \u00b7  %.0f s  \u00b7  phase %.0f%%  \u00b7  %dx%d px"
          % (n, time.time() - t0, player.phase * 100.0,
             player.artist.size()[0], player.artist.size()[1] * 2))
    return 0


def still(args):
    """One frame as plain text - the sane thing to do with a pipe."""
    r = RoseRenderer(320, 180, loop_seconds=args.loop, seed=args.seed).solo(not args.city)
    a = Artist(cols=args.cols or 100, rows=args.rows or 28, out=sys.stdout,
               truecolor=False, mono=True)
    img = r.render(0.145)
    sys.stdout.write("\n".join(a.paint(img)) + "\n")
    sys.stdout.flush()
    return 0


def dump(args):
    """Render a short clip into text files (used by the docs and by tests)."""
    out = Path(args.dump)
    out.mkdir(parents=True, exist_ok=True)
    r = RoseRenderer(320, 180, loop_seconds=args.loop, seed=args.seed).solo(not args.city)
    a = Artist(cols=args.cols or 100, rows=args.rows or 28, out=open(os.devnull, "w"),
               truecolor=not args.no_color)
    n = max(1, int(round((args.seconds or 2.0) * args.fps)))
    for i in range(n):
        img = r.render(i / n)
        a.mono = args.ascii
        (out / ("frame_%03d.txt" % i)).write_text("\n".join(a.paint(img)) + "\n")
    print("  %d frames -> %s" % (n, out))
    return 0
