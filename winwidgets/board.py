"""Layout + the interactive loop.

The board is laid out in *columns* of cards (2 columns on a wide terminal, 1 on
a narrow one), each card sized to what is left of the screen.  A header line
carries the machine name, the OS and the time; a footer line carries the key
hints, so nothing floats contextless.

Everything here is deterministic given (width, height, snapshot, history),
which is why the layout is testable without a terminal.
"""

from __future__ import annotations

import time

from . import BOARD_TITLE, __version__
from .collect import Collector
from .term import (Canvas, Screen, Theme, enter_windows_utf8, enable_windows_ansi,
                   terminal_size, windows_accent)
from .widgets import make

MIN_W = 46
MIN_H = 14
HEADER_H = 1
FOOTER_H = 1


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #
def columns_for(width):
    return 2 if width >= 96 else 1


def plan(widgets, width, height):
    """Return ``[(widget, x, y, w, h), ...]`` for the given screen size.

    Cards flow in columns; a card that would not fit is left out (the footer
    then says how many are missing) instead of being drawn off-screen.
    """
    if not widgets:
        return []
    top = HEADER_H + 1
    usable = max(5, height - top - FOOTER_H)
    cols = min(columns_for(width), len(widgets))
    gap = 1
    col_w = (width - gap * (cols - 1)) // cols

    placed = []
    y = top
    for start in range(0, len(widgets), cols):
        chunk = widgets[start:start + cols]
        need = max(w.height(col_w) for w in chunk)
        if y + need > top + usable:
            break
        for i, widget in enumerate(chunk):
            x = i * (col_w + gap)
            w = col_w if i < cols - 1 else min(col_w, width - x)
            placed.append((widget, x, y, w, need))
        y += need + gap
    return placed


def untruncated_height(widgets, width):
    cols = min(columns_for(width), max(1, len(widgets)))
    total = HEADER_H + 1 + FOOTER_H
    for i in range(0, len(widgets), cols):
        chunk = widgets[i:i + cols]
        total += max(w.height(width) for w in chunk) + 1
    return total


# --------------------------------------------------------------------------- #
# drawing one frame
# --------------------------------------------------------------------------- #
class Board:
    def __init__(self, widgets, theme: Theme, collector: Collector, running=True,
                 frozen=False, plain=False):
        self.widgets = widgets
        self.theme = theme
        self.collector = collector
        self.running = running
        self.frozen = frozen
        self.plain = plain
        self.hidden = set()
        self.notice = ""
        self.notice_until = 0.0
        self.frames = 0

    # -- helpers ----------------------------------------------------------- #
    def say(self, text, seconds=2.5):
        self.notice = text
        self.notice_until = time.time() + seconds

    def visible(self):
        return [w for w in self.widgets if w.key not in self.hidden]

    def history(self):
        c = self.collector
        return {"cpu": c.h_cpu, "mem": c.h_mem, "down": c.h_down, "up": c.h_up}

    # -- the frame --------------------------------------------------------- #
    def render(self, width=None, height=None) -> Canvas:
        width, height = width or terminal_size()[0], height or terminal_size()[1]
        theme = self.theme
        canvas = Canvas(width, height, theme)
        snap = self.collector.sample
        hist = self.history()

        # header
        left = "%s  %s" % (BOARD_TITLE, "\u00b7 " + __version__)
        right = "%s \u00b7 %s" % (snap.os_name or "?", time.strftime("%H:%M:%S"))
        canvas.put(0, 0, left, "accent")
        canvas.put(max(0, width - len(right)), 0, right[:width], "dim")

        placed = plan(self.visible(), width, height)
        for widget, x, y, w, h in placed:
            widget.draw(canvas, x, y, w, snap, hist)

        # footer: keys + a hint when the cards do not all fit
        keys = "q quit \u00b7 space freeze \u00b7 1-9 toggle \u00b7 c colour \u00b7 s snapshot \u00b7 r reset"
        if self.notice and time.time() < self.notice_until:
            canvas.put(0, height - 1, self.notice[:width], "accent")
        else:
            canvas.put(0, height - 1, keys[:width], "faint")
        if len(placed) < len(self.visible()) and width > 0:
            more = " \u2191 %d more: resize" % (len(self.visible()) - len(placed))
            canvas.put(max(0, width - len(more)), height - 1, more[:width], "warn")

        self.frames += 1
        return canvas


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
class Keys:
    """Non-blocking key reader: ``select`` on POSIX, ``msvcrt`` on Windows."""

    def __init__(self, stream=None):
        import sys

        stream = stream or sys.stdin
        self.fd = None
        self.mode = "none"
        try:                                  # Windows console
            import msvcrt  # noqa: F401

            if stream is sys.stdin and sys.stdin.isatty():
                self.mode = "windows"
                return
        except (ImportError, ValueError, OSError):
            pass
        try:                                  # POSIX terminal
            if stream.isatty():
                self.fd = stream.fileno()
                self.mode = "posix"
        except Exception:
            self.mode = "none"

    def poll(self):
        if self.mode == "posix":
            import os
            import select

            try:
                ready, _, _ = select.select([self.fd], [], [], 0)
                if not ready:
                    return None
                data = os.read(self.fd, 16)
            except (OSError, ValueError):
                self.mode = "none"
                return None
            if not data:
                self.mode = "none"
                return None
            text = data.decode("utf-8", "ignore")
            if text.startswith("\x1b"):
                return "esc" if len(text) == 1 else None   # arrow keys: ignore
            return text[:1]
        if self.mode == "windows":            # pragma: no cover - Windows only
            import msvcrt

            try:
                if not msvcrt.kbhit():
                    return None
                ch = msvcrt.getwch()
            except Exception:
                self.mode = "none"
                return None
            if ch == "\x1b":
                return "esc"
            if ch in ("\x00", "\xe0"):        # function/arrow key: eat the code
                msvcrt.getwch()
                return None
            return ch
        return None


def build(keys=None, plain=False, stream=None, theme=None):
    """The board, ready to render.  Shared by ``run`` and the snapshot tool."""
    from os import environ

    from .term import supports_truecolor

    widgets = make(keys)
    if not widgets:
        raise SystemExit("unknown widget - try: python -m winwidgets --list")
    if theme is None:
        if plain:
            mode = "mono"
        else:
            mode = "truecolor" if supports_truecolor(stream) else "256"
        theme = Theme(accent=windows_accent(), mode=mode)
    collector = Collector()
    if keys is None and collector.sample.battery is None:
        # a desktop has no battery: do not give it a card full of "no battery"
        widgets = [w for w in widgets if w.key != "battery"]
    return Board(widgets, theme, collector)


def set_console_title(text="Windows 11 widgets"):
    """Give the terminal tab a name (Windows), so the board is easy to find."""
    import os

    if os.name != "nt":
        return False
    try:                                       # pragma: no cover - Windows only
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(text)
        return True
    except Exception:
        return False


def run(keys=None, interval=1.0, plain=False, once=0, stream=None, size=None):
    """Draw the board until quit.  ``once`` renders exactly that many frames."""
    import sys

    enter_windows_utf8()
    enable_windows_ansi()
    set_console_title()
    if stream is None and not plain and not sys.stdout.isatty():
        plain = True                        # piped output: no escapes, no loop
        once = once or 1
    board = build(keys, plain=plain, stream=stream)
    theme = board.theme
    collector = board.collector
    reader = Keys(sys.stdin)
    interactive = reader.mode != "none" and not plain

    screen = Screen(stream=stream, alt_screen=interactive)
    screen.open()
    frames = 0
    try:
        while True:
            width, height = size or terminal_size()
            if frames and not board.frozen:
                collector.tick()
            canvas = board.render(width, height)
            if plain:
                console = stream or sys.stdout
                console.write("\n".join(canvas.text_lines()) + "\n")
                console.flush()
            else:
                screen.draw(canvas)
            frames += 1
            if once and frames >= once:
                break
            if not interactive:
                break

            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                key = reader.poll()
                if key is not None:
                    if handle_key(key, board, collector, theme, canvas):
                        return 0
                    break                 # redraw at once: the key was an action
                time.sleep(0.02)
    except KeyboardInterrupt:
        return 0
    finally:
        screen.close()
    return 0
