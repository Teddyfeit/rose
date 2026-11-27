"""Real desktop widgets: one transparent, movable window per card.

This is the tkinter side of the project - the same widgets as the terminal
board, but as windows you can drag around your wallpaper.

How the Windows-11 look is achieved without a compositor API
------------------------------------------------------------
* ``overrideredirect(True)`` - no title bar, no border, no OS frame.
* ``-alpha`` (a real per-window alpha on Windows) + ``-topmost``.
* The card is painted on a ``Canvas`` whose background is the *wallpaper tint*
  and whose pixels are pushed toward the wallpaper colour (see
  ``palette.palette``); a 1 px stroke in the accent hue does the rest.
* Rounded corners come from ``create_polygon(..., smooth=True)`` - a Canvas
  polygon with many points and smoothing reads as a rounded rectangle.
* Dragging: press-drag-release on any empty part of the card; positions are
  written to ``widgets.json`` so they survive a restart.
* Right-click a card: colour/opacity/size menu.  Double-click: refresh now.

If tkinter is missing (some Linux builds package it separately) the module
raises a clear message instead of a traceback, and ``python -m winwidgets``
still gives you the terminal board.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import content
from . import palette as P
from .collect import Collector
from .wallpaper import find_wallpaper, sample_pixels, scheme_from_pixels

CONFIG_NAME = "widgets.json"

STATE_PATH = Path(os.environ.get("APPDATA") or Path.home()) / "winwidgets" / CONFIG_NAME

KINDS = ["cpu", "memory", "disk", "network", "clock", "processes", "battery"]


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
DEFAULTS = {
    "opacity": 0.82,
    "size": "m",
    "interval": 1.0,
    "positions": {},
    "hidden": [],
    "x": None,
    "y": None,
    "wallpaper_scheme": True,
    "accent": None,          # "#rrggbb" overrides the wallpaper pick
}


def load_config(path=None):
    path = Path(path or STATE_PATH)
    config = dict(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            config.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return config


def save_config(config, path=None):
    path = Path(path or STATE_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# scheme
# --------------------------------------------------------------------------- #
def current_scheme(config):
    """Wallpaper colours for this machine (never raises)."""
    try:
        path = find_wallpaper()
        pixels = sample_pixels(path, 80) if path else None
        scheme = scheme_from_pixels(pixels)
        scheme["wallpaper"] = str(path) if path else None
        if not config.get("wallpaper_scheme", True):
            from .term import windows_accent

            scheme["accent"] = windows_accent()
            scheme["source"] = "windows"
        if config.get("accent"):
            text = config["accent"].lstrip("#")
            scheme["accent"] = tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
            scheme["source"] = "override"
        return scheme
    except Exception:
        from . import wallpaper

        return dict(wallpaper.SCHEME_DEFAULTS)


# --------------------------------------------------------------------------- #
# the widgets
# --------------------------------------------------------------------------- #
def _fmt_rate(mibs):
    if mibs >= 1024:
        return "%.1f GiB/s" % (mibs / 1024.0)
    if mibs >= 10:
        return "%.1f MiB/s" % mibs
    if mibs >= 0.1:
        return "%.2f MiB/s" % mibs
    return "%.0f KiB/s" % (mibs * 1024)


def _fmt_uptime(seconds):
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "%dd %dh %02dm" % (days, hours, minutes)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm" % minutes


class Card:
    """Base card: owns one Toplevel, paints primitives, handles the dragging."""

    kind = "base"
    title = "BASE"

    def __init__(self, app, kind, x, y, width, height):
        import tkinter as tk

        self.tk = tk
        self.app = app
        self.kind = kind
        self.w = width
        self.h = height
        self.pal = app.pal
        self.title = content.TITLES.get(kind, kind.upper())
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:                                   # Windows: a real per-window alpha
            self.win.attributes("-alpha", app.opacity)
        except tk.TclError:
            pass
        self.win.geometry("%dx%d+%d+%d" % (width, height, x, y))
        self.win.configure(bg=self.pal["bg"])
        self.canvas = tk.Canvas(self.win, width=width, height=height,
                                highlightthickness=0, bd=0, bg=self.pal["bg"])
        self.canvas.pack(fill="both", expand=True)
        self._drag = None
        self._build()

    # -- geometry ---------------------------------------------------------- #
    def _corner_points(self, inset=0, radius=9):
        """Many points around a rectangle; smoothing turns it into a capsule."""
        import math

        x0, y0 = inset + radius, inset + radius
        x1, y1 = self.w - 1 - inset - radius, self.h - 1 - inset - radius
        points = []
        for cx, cy, start in ((x0, y0, 180), (x1, y0, 270), (x1, y1, 0), (x0, y1, 90)):
            for step in range(4):
                angle = math.radians(start + step * 30)
                points.extend([cx + radius * math.cos(angle),
                               cy + radius * math.sin(angle)])
        return points

    def _build(self):
        self.canvas.create_polygon(self._corner_points(), smooth=True,
                                   fill=self.pal["bg"], outline=self.pal["stroke"],
                                   width=1, tags="frame")
        self.canvas.create_text(content.PAD, 11, anchor="nw", text=self.title,
                                fill=self.pal["dim"], font=self.app.font_title,
                                tags="frame")
        for sequence in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>",
                         "<Double-Button-1>", "<Button-3>"):
            self.canvas.bind(sequence, getattr(self, {
                "<ButtonPress-1>": "_press", "<B1-Motion>": "_motion",
                "<ButtonRelease-1>": "_release",
                "<Double-Button-1>": "_double", "<Button-3>": "_menu"}[sequence]))

    # -- dragging ---------------------------------------------------------- #
    def _press(self, event):
        self._drag = (event.x_root, event.y_root,
                      self.win.winfo_x(), self.win.winfo_y())

    def _motion(self, event):
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        self.win.geometry("+%d+%d" % (wx + event.x_root - x0, wy + event.y_root - y0))

    def _release(self, _event):
        self._drag = None
        self.app.remember_position(self.kind, self.win.winfo_x(), self.win.winfo_y())

    def _double(self, _event):
        self.app.refresh(force=True)

    # -- right-click menu -------------------------------------------------- #
    def _menu(self, event):
        tk = self.tk
        menu = tk.Menu(self.win, tearoff=0, bg=self.pal["bg"], fg=self.pal["text"],
                       activebackground=self.pal["accent"],
                       activeforeground=self.pal["bg"], borderwidth=0)
        menu.add_command(label="Alle Widgets", command=self.app.show_all)
        menu.add_command(label="Nur dieses", command=lambda: self.app.solo(self.kind))
        menu.add_separator()
        size_menu = tk.Menu(menu, tearoff=0, bg=self.pal["bg"], fg=self.pal["text"],
                            activebackground=self.pal["accent"],
                            activeforeground=self.pal["bg"])
        for key, label in (("s", "Klein"), ("m", "Mittel"), ("l", "Gro\u00df")):
            size_menu.add_command(label=label,
                                  command=lambda k=key: self.app.set_size(k))
        menu.add_cascade(label="Gr\u00f6\u00dfe", menu=size_menu)
        op_menu = tk.Menu(menu, tearoff=0, bg=self.pal["bg"], fg=self.pal["text"],
                          activebackground=self.pal["accent"],
                          activeforeground=self.pal["bg"])
        for value in (0.55, 0.70, 0.82, 0.92, 1.0):
            op_menu.add_command(label="%d %%" % round(value * 100),
                                command=lambda v=value: self.app.set_opacity(v))
        menu.add_cascade(label="Transparenz", menu=op_menu)
        menu.add_separator()
        menu.add_command(label="Farbe aus Hintergrund",
                         command=lambda: self.app.use_wallpaper_colours(True))
        menu.add_command(label="Windows-Akzentfarbe",
                         command=lambda: self.app.use_wallpaper_colours(False))
        menu.add_command(label="Positionen zur\u00fccksetzen",
                         command=self.app.reset_positions)
        menu.add_separator()
        menu.add_command(label="Beenden", command=self.app.quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # -- painting ---------------------------------------------------------- #
    def _font(self, role):
        return {"hero": self.app.font_hero, "small": self.app.font_title}.get(
            role, self.app.font)

    def _draw_text(self, item):
        x, y = item["x"], item["y"]
        anchor = item["anchor"]
        self.canvas.create_text(x, y, anchor=anchor, text=item["text"],
                                fill=item["colour"], font=self._font(item.get("role")),
                                tags="content")

    def _draw_bar(self, item):
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        colour = item["colour"]
        r = h / 2.0
        canvas = self.canvas
        canvas.create_oval(x, y, x + h, y + h, fill=self.pal["track"],
                           outline="", tags="content")
        canvas.create_oval(x + w - h, y, x + w, y + h, fill=self.pal["track"],
                           outline="", tags="content")
        canvas.create_rectangle(x + r, y, x + w - r, y + h, fill=self.pal["track"],
                                outline="", tags="content")
        filled = item["pct"] / 100.0 * w
        if filled >= 2 * r:
            canvas.create_oval(x, y, x + h, y + h, fill=colour, outline="", tags="content")
            canvas.create_rectangle(x + r, y, x - r + filled, y + h, fill=colour,
                                    outline="", tags="content")
            canvas.create_oval(x - h + filled, y, x + filled, y + h, fill=colour,
                               outline="", tags="content")
        elif filled > 0:
            canvas.create_oval(x, y, x + max(filled, h), y + h, fill=colour,
                               outline="", tags="content")

    def _draw_spark(self, item):
        values = item["values"]
        if len(values) < 2:
            return
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        slot = w / max(1, len(values) - 1)
        high = max(1e-6, max(values))
        points = []
        for i, value in enumerate(values[-int(w / 3):]):
            t = max(0.0, min(1.0, value / high))
            points.extend([x + i * slot, y + h - t * h])
        self.canvas.create_polygon(points + [x + len(points) // 2 * slot, y + h, x, y + h],
                                   fill=item["fill"], outline="", tags="content")
        self.canvas.create_line(*points, fill=item["colour"], width=1.4,
                                smooth=True, tags="content")

    def _draw_ticks(self, item):
        x, y, w = item["x"], item["y"], item["w"]
        slot, height = item["slot"], item["h"]
        for i, value in enumerate(item["values"]):
            bar_x = x + i * (slot + 1)
            if bar_x + slot > x + w:
                break
            bar_h = 3 + (max(0.0, min(100.0, value)) / 100.0) * height
            self.canvas.create_rectangle(bar_x, y + height - bar_h, bar_x + slot, y + height,
                                         fill=P.level_colour(value, self.pal),
                                         outline="", tags="content")

    def _draw(self, primitives):
        for item in primitives:
            kind = item["type"]
            if kind == "text":
                self._draw_text(item)
            elif kind == "bar":
                self._draw_bar(item)
            elif kind == "spark":
                self._draw_spark(item)
            elif kind == "ticks":
                self._draw_ticks(item)

    # -- public ------------------------------------------------------------ #
    def refresh(self, snap, history):
        self.canvas.delete("content")
        self._draw(content.build(self.kind, snap, history, self.pal, self.w, self.h))

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


CARD_TYPES = {kind: type(kind.title() + "Card", (Card,), {}) for kind in KINDS}


# --------------------------------------------------------------------------- #
# the application
# --------------------------------------------------------------------------- #
class DesktopApp:
    def __init__(self, kinds=None, config_path=None):
        import tkinter as tk

        self.tk = tk
        self.config = load_config(config_path)
        self.config_path = Path(config_path or STATE_PATH)
        self.scheme = current_scheme(self.config)
        self.pal = P.palette(self.scheme["accent"], self.scheme["tint"],
                             self.scheme["mode"])
        self.opacity = float(self.config.get("opacity", 0.82))
        self.size = self.config.get("size", "m")

        self.root = tk.Tk()
        self.root.withdraw()                      # the root is only a parent
        self.root.title("Windows 11 Widgets")
        self._fonts()
        self.collector = Collector()
        self.cards = {}
        self.running = True
        self._after = None
        self._refresh_seen = 0

        hidden = set(self.config.get("hidden") or [])
        if kinds:
            order = [k for k in KINDS if k in kinds]
        else:
            order = [k for k in KINDS if k not in hidden]
            if self.collector.sample.battery is None and "battery" not in hidden:
                order = [k for k in order if k != "battery"]
        self.order = order
        self._spawn(order)
        self._tick()
        try:                                  # window-manager close button
            self.root.protocol("WM_DELETE_WINDOW", self.quit)
        except Exception:
            pass
        try:                                  # Ctrl+C in the console quits too
            self.root.createcommand("tk::mac::Quit", self.quit)
        except Exception:
            pass
        print("  Widgets laufen.")
        print("  Ziehen = verschieben · Rechtsklick auf eine Karte = Menü "
              "(Größe, Transparenz, Farben, Beenden) · Doppelklick = aktualisieren")
        print("  Farben aus dem Hintergrundbild: "
              + (str(self.scheme.get("wallpaper") or "Windows-Akzentfarbe")))

    # -- fonts ------------------------------------------------------------- #
    def _fonts(self):
        import tkinter.font as tkfont

        spec = P.SIZES.get(self.size, P.SIZES["m"])
        family = "Segoe UI Variable Display"
        available = set(tkfont.families())
        if family not in available:
            family = "Segoe UI" if "Segoe UI" in available else "DejaVu Sans"
        self.font_family = family
        self.font = (family, spec["font"])
        self.font_title = (family, max(7, spec["font"] - 1))
        self.font_hero = (family, spec["hero"], "bold")

    # -- cards ------------------------------------------------------------- #
    def _spawn(self, kinds):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        positions = dict(self.config.get("positions") or {})
        y = 60
        for kind in kinds:
            width, height = P.card_size(kind, self.size)
            x, y0 = positions.get(kind, (screen_w - width - 40, y))
            x, y0 = P.fit_in_screen(int(x), int(y0), width, height, screen_w, screen_h)
            card = CARD_TYPES[kind](self, kind, x, y0, width, height)
            self.cards[kind] = card
            if kind not in positions:
                y = y0 + height + 12
        self.refresh(force=True)

    def _drop(self, kind):
        card = self.cards.pop(kind, None)
        if card:
            card.destroy()

    # -- data -------------------------------------------------------------- #
    def _tick(self):
        if not self.running:
            return
        try:
            self.collector.tick()
            self.refresh()
        except Exception as exc:                  # pragma: no cover - GUI
            print("  Sammler-Fehler:", exc)
        interval = max(0.25, float(self.config.get("interval", 1.0)))
        self._after = self.root.after(int(interval * 1000), self._tick)

    def history(self):
        c = self.collector
        return {"cpu": c.h_cpu, "mem": c.h_mem, "down": c.h_down, "up": c.h_up}

    def refresh(self, force=False):
        snap = self.collector.sample
        hist = self.history()
        for card in list(self.cards.values()):
            try:
                card.refresh(snap, hist)
            except Exception:                     # pragma: no cover - GUI
                pass
        self._refresh_seen += 1

    # -- menu actions ------------------------------------------------------ #
    def remember_position(self, kind, x, y):
        self.config.setdefault("positions", {})[kind] = [int(x), int(y)]
        save_config(self.config, self.config_path)

    def set_opacity(self, value):
        self.opacity = float(value)
        self.config["opacity"] = self.opacity
        save_config(self.config, self.config_path)
        for card in self.cards.values():
            try:
                card.win.attributes("-alpha", self.opacity)
            except Exception:
                pass

    def set_size(self, size):
        if size not in P.SIZES:
            return
        self.size = size
        self.config["size"] = size
        save_config(self.config, self.config_path)
        self._fonts()
        self.rebuild()

    def use_wallpaper_colours(self, on):
        self.config["wallpaper_scheme"] = bool(on)
        self.config["accent"] = None
        save_config(self.config, self.config_path)
        self.restyle()

    def restyle(self):
        self.scheme = current_scheme(self.config)
        self.pal = P.palette(self.scheme["accent"], self.scheme["tint"],
                             self.scheme["mode"])
        self.rebuild()

    def rebuild(self):
        positions = {kind: (card.win.winfo_x(), card.win.winfo_y())
                     for kind, card in self.cards.items()}
        for card in self.cards.values():
            card.destroy()
        self.cards.clear()
        self.config.setdefault("positions", {}).update(
            {k: list(v) for k, v in positions.items()})
        self._spawn(self.order)

    def show_all(self):
        self.config["hidden"] = []
        save_config(self.config, self.config_path)
        self.order = [k for k in KINDS if k in CARD_TYPES]
        self.rebuild()

    def solo(self, kind):
        for other in list(self.cards):
            if other != kind:
                self._drop(other)
        self.order = [kind]

    def reset_positions(self):
        self.config["positions"] = {}
        save_config(self.config, self.config_path)
        self.rebuild()

    def quit(self):
        self.running = False
        if self._after:
            try:
                self.root.after_cancel(self._after)
            except Exception:
                pass
        save_config(self.config, self.config_path)
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit()
        return 0


def options_from(argv, config):
    """``--opacity 0.7 --size l --accent #ff4d6d --no-wallpaper --reset``."""
    args = list(argv)
    rest = []
    while args:
        arg = args.pop(0)
        if arg in ("--opacity", "-o") and args:
            config["opacity"] = max(0.25, min(1.0, float(args.pop(0))))
        elif arg in ("--size", "-g") and args:
            value = args.pop(0).lower()
            config["size"] = {"s": "s", "klein": "s", "m": "m", "mittel": "m",
                              "l": "l", "gross": "l", "groß": "l"}.get(value, "m")
        elif arg == "--accent" and args:
            config["accent"] = "#" + args.pop(0).lstrip("#")
        elif arg in ("--no-wallpaper", "--windows-accent"):
            config["wallpaper_scheme"] = False
        elif arg == "--reset":
            config["positions"] = {}
        elif arg in ("--interval", "-i") and args:
            config["interval"] = max(0.25, float(args.pop(0)))
        elif not arg.startswith("-"):
            rest.append(arg)
    return rest, config


def main(kinds=None, config_path=None, argv=None, config=None):
    try:
        import tkinter  # noqa: F401
    except Exception:
        raise SystemExit(
            "  tkinter fehlt.\n"
            "  Windows/macOS: bei python.org mitinstalliert - Python neu "
            "installieren und 'tcl/tk' anhaken.\n"
            "  Linux: sudo apt install python3-tk\n"
            "  Ohne Fenster laeuft das Board weiter im Terminal: "
            "python -m winwidgets")
    config = dict(config or {})
    if argv:
        kinds_extra, config = options_from(argv, {**load_config(config_path), **config})
        kinds = list(kinds or []) + kinds_extra
        save_config(config, config_path)
    return DesktopApp(kinds or None, config_path).run()
