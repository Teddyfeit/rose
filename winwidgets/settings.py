"""The settings window - ``widgets-settings.bat`` / ``python -m winwidgets settings``.

A small Tk window with the few things worth changing, and a button that starts
or restarts the widgets with the new values:

* style:      ASCII characters (default) or smooth cards
* background: none (only the characters) or a card panel
* layer:      on the desktop / normal window / always on top
* size, opacity, refresh rate, accent colour, which widgets
* where the settings live: ``%APPDATA%\\winwidgets\\widgets.json``

The window is dark and quiet on purpose: it is a settings dialog, not a
dashboard. Everything it writes comes from ``desktop.DEFAULTS``, so the two can
never drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import __version__
from .desktop import DEFAULTS, KINDS, load_config, save_config
from .widgets import ORDER

TITLES = {
    "cpu": "CPU", "memory": "Arbeitsspeicher", "disk": "Datenträger",
    "network": "Netzwerk", "clock": "Uhr", "processes": "Top-Prozesse",
    "battery": "Akku",
}

BG = "#15161a"
PANEL = "#1e2026"
TEXT = "#e9eaf0"
DIM = "#9a9ca6"
ACCENT = "#4cc2ff"


# --------------------------------------------------------------------------- #
# config helpers (pure, testable)
# --------------------------------------------------------------------------- #
def normalise(config):
    """Fill in anything missing and clamp anything that is out of range."""
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (config or {}).items() if k in DEFAULTS})
    def number(key, low, high, default):
        try:
            value = float(out.get(key))
        except (TypeError, ValueError):
            return default
        if value != value or value < 0:           # NaN or nonsense
            return default
        return max(low, min(high, value))

    out["opacity"] = number("opacity", 0.25, 1.0, DEFAULTS["opacity"])
    out["interval"] = number("interval", 0.25, 10.0, DEFAULTS["interval"])
    out["size"] = out["size"] if out.get("size") in ("s", "m", "l") else "m"
    out["style"] = out["style"] if out.get("style") in ("ascii", "flat") else "ascii"
    out["layer"] = out["layer"] if out.get("layer") in ("desktop", "normal",
                                                       "topmost") else "desktop"
    out["transparent"] = bool(out.get("transparent", True))
    hidden = [k for k in (out.get("hidden") or []) if k in ORDER]
    out["hidden"] = hidden
    accent = out.get("accent")
    if accent:
        text = str(accent).lstrip("#")
        if len(text) == 6:
            try:
                int(text, 16)
                out["accent"] = "#" + text.lower()
            except ValueError:
                out["accent"] = None
        else:
            out["accent"] = None
    return out


def visible_widgets(config):
    hidden = set(config.get("hidden") or [])
    return [k for k in ORDER if k not in hidden]


def toggle_widget(config, kind, on):
    hidden = [k for k in (config.get("hidden") or []) if k != kind]
    if not on:
        hidden.append(kind)
    config["hidden"] = [k for k in ORDER if k in hidden]
    return config


def launch_arguments(config):
    """The command line that reproduces this config."""
    visible = visible_widgets(config)
    args = list(visible) if len(visible) < len(ORDER) else []
    args += ["--opacity", "%.2f" % config["opacity"],
             "--size", config["size"],
             "--interval", "%.2f" % config["interval"]]
    args += ["--ascii"] if config["style"] == "ascii" else ["--flat"]
    args += ["--no-panel"] if config["transparent"] else ["--panel"]
    args += {"desktop": ["--desktop-layer"], "normal": ["--normal"],
             "topmost": ["--topmost"]}[config["layer"]]
    if config.get("accent"):
        args += ["--accent", config["accent"]]
    if not config.get("wallpaper_scheme", True):
        args += ["--no-wallpaper"]
    return args


def start_widgets(config, detach=True, config_path_hint=None):
    """Start the widget windows with the given config (in their own console)."""
    from .terminal import open_terminal

    args = ["desktop"] + launch_arguments(config)
    save_config(config, config_path_hint)
    return open_terminal(args)


def settings_path():
    """Where the settings live - shown in the window and used by both modes."""
    from .desktop import STATE_PATH

    return STATE_PATH


# --------------------------------------------------------------------------- #
# the window
# --------------------------------------------------------------------------- #
class SettingsWindow:
    def __init__(self, config_path=None):
        import tkinter as tk
        from tkinter import colorchooser, ttk

        self.tk = tk
        self.colorchooser = colorchooser
        self.config_path = Path(config_path) if config_path else None
        self.config = normalise(load_config(self.config_path))

        self.root = tk.Tk()
        self.root.title(f"Widgets · Einstellungen {__version__}")
        self.root.configure(bg=BG)
        self.root.geometry("560x620")
        self.root.minsize(520, 560)
        self.root.attributes("-topmost", True)

        self.var_style = tk.StringVar(value=self.config["style"])
        self.var_panel = tk.BooleanVar(value=not self.config["transparent"])
        self.var_layer = tk.StringVar(value=self.config["layer"])
        self.var_size = tk.StringVar(value=self.config["size"])
        self.var_opacity = tk.DoubleVar(value=self.config["opacity"] * 100)
        self.var_interval = tk.DoubleVar(value=self.config["interval"])
        self.var_accent = tk.StringVar(value=self.config.get("accent") or "")
        self.var_wallpaper = tk.BooleanVar(value=self.config.get("wallpaper_scheme", True))
        self.var_widgets = {k: tk.BooleanVar(value=k in visible_widgets(self.config))
                            for k in ORDER}

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Dim.TLabel", background=BG, foreground=DIM, font=("Segoe UI", 9))
        style.configure("Head.TLabel", background=BG, foreground=TEXT,
                        font=("Segoe UI", 12, "bold"))
        style.configure("TRadiobutton", background=BG, foreground=TEXT,
                        font=("Segoe UI", 10))
        style.configure("TCheckbutton", background=BG, foreground=TEXT,
                        font=("Segoe UI", 10))
        style.map("TRadiobutton", background=[("active", BG)])
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TScale", background=BG, troughcolor=PANEL)
        style.configure("Accent.TButton", background=ACCENT, foreground="#10131a",
                        font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("TButton", background=PANEL, foreground=TEXT,
                        font=("Segoe UI", 10), padding=6)

        self._build(ttk)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    # -- layout ------------------------------------------------------------ #
    def _build(self, ttk):
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Widgets", style="Head.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Was auf dem Desktop liegt und wie es aussieht.",
                  style="Dim.TLabel").pack(anchor="w", pady=(0, 14))

        # style
        box = ttk.Frame(outer)
        box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, text="Stil").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Radiobutton(row, text="ASCII-Zeichen (wie im Terminal)", value="ascii",
                        variable=self.var_style).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(row, text="Glatte Karten", value="flat",
                        variable=self.var_style).pack(side="left")

        ttk.Checkbutton(outer, text="Karten-Hintergrund zeichnen (aus = nur die Zeichen)",
                        variable=self.var_panel).pack(anchor="w", pady=(0, 10))

        # layer
        box = ttk.Frame(outer)
        box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, text="Ebene").pack(anchor="w")
        for value, label in (("desktop", "auf dem Desktop (hinter den Fenstern)"),
                             ("normal", "normales Fenster"),
                             ("topmost", "immer im Vordergrund")):
            ttk.Radiobutton(box, text=label, value=value,
                            variable=self.var_layer).pack(anchor="w")

        # size
        box = ttk.Frame(outer)
        box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, text="Größe").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        for value, label in (("s", "klein"), ("m", "mittel"), ("l", "groß")):
            ttk.Radiobutton(row, text=label, value=value,
                            variable=self.var_size).pack(side="left", padx=(0, 16))

        # sliders
        box = ttk.Frame(outer)
        box.pack(fill="x", pady=(0, 6))
        ttk.Label(box, text="Deckkraft").pack(anchor="w")
        self.lbl_opacity = ttk.Label(box, text="", style="Dim.TLabel")
        self.lbl_opacity.pack(anchor="e")
        scale = ttk.Scale(box, from_=25, to=100, variable=self.var_opacity,
                          command=lambda _v: self.lbl_opacity.configure(
                              text="%d %%" % self.var_opacity.get()))
        scale.pack(fill="x")
        self.lbl_opacity.configure(text="%d %%" % self.var_opacity.get())

        box = ttk.Frame(outer)
        box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, text="Aktualisierung (Sekunden)").pack(anchor="w")
        ttk.Scale(box, from_=0.25, to=5.0, variable=self.var_interval).pack(fill="x")

        # accent
        box = ttk.Frame(outer)
        box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, text="Akzentfarbe").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", fill="x")
        ttk.Entry(row, textvariable=self.var_accent, width=12).pack(side="left")
        ttk.Button(row, text="wählen …", command=self.pick_colour).pack(side="left",
                                                                        padx=6)
        ttk.Button(row, text="aus Hintergrundbild", command=self.clear_colour
                   ).pack(side="left")
        ttk.Checkbutton(outer, text="Farben aus dem Hintergrundbild ableiten",
                        variable=self.var_wallpaper).pack(anchor="w", pady=(0, 10))

        # widgets
        ttk.Label(outer, text="Widgets").pack(anchor="w")
        grid = ttk.Frame(outer)
        grid.pack(fill="x", pady=(2, 10))
        for index, kind in enumerate(ORDER):
            ttk.Checkbutton(grid, text=TITLES.get(kind, kind), variable=self.var_widgets[kind]
                            ).grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 18))

        # buttons
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", side="bottom", pady=(14, 0))
        ttk.Button(buttons, text="Speichern & starten", style="Accent.TButton",
                   command=self.save_and_start).pack(side="left")
        ttk.Button(buttons, text="Speichern", command=self.save).pack(side="left", padx=6)
        ttk.Button(buttons, text="zurücksetzen", command=self.reset).pack(side="left")
        ttk.Button(buttons, text="schließen", command=self.close).pack(side="right")

        self.status = ttk.Label(outer, text="", style="Dim.TLabel")
        self.status.pack(anchor="w", pady=(8, 0))
        ttk.Label(outer, text=f"Datei: {self.config_path or 'Standard'}",
                  style="Dim.TLabel").pack(anchor="w")

    # -- actions ----------------------------------------------------------- #
    def pick_colour(self):
        current = self.var_accent.get() or ACCENT
        try:
            _rgb, hex_value = self.colorchooser.askcolor(color=current,
                                                        title="Akzentfarbe")
        except Exception:
            return
        if hex_value:
            self.var_accent.set(hex_value)

    def clear_colour(self):
        self.var_accent.set("")
        self.var_wallpaper.set(True)

    def collect(self):
        config = dict(self.config)
        config.update({
            "style": self.var_style.get(),
            "transparent": not self.var_panel.get(),
            "layer": self.var_layer.get(),
            "size": self.var_size.get(),
            "opacity": round(self.var_opacity.get() / 100.0, 2),
            "interval": round(float(self.var_interval.get()), 2),
            "accent": self.var_accent.get().strip() or None,
            "wallpaper_scheme": bool(self.var_wallpaper.get()),
            "hidden": [k for k in ORDER if not self.var_widgets[k].get()],
        })
        return normalise(config)

    def save(self):
        self.config = self.collect()
        ok = save_config(self.config, self.config_path)
        self.status.configure(
            text="Gespeichert. 'Speichern & starten' startet die Widgets neu."
            if ok else "Speichern fehlgeschlagen (Rechte?).")
        return ok

    def save_and_start(self):
        self.save()
        try:
            start_widgets(self.config, detach=True,
                          config_path_hint=self.config_path)
            self.status.configure(text="Widgets gestartet – Fenster hier schließen.")
        except Exception as exc:               # pragma: no cover - environment
            self.status.configure(text=f"Start fehlgeschlagen: {exc}")

    def reset(self):
        self.config = normalise(DEFAULTS)
        self.var_style.set(self.config["style"])
        self.var_panel.set(not self.config["transparent"])
        self.var_layer.set(self.config["layer"])
        self.var_size.set(self.config["size"])
        self.var_opacity.set(self.config["opacity"] * 100)
        self.var_interval.set(self.config["interval"])
        self.var_accent.set("")
        self.var_wallpaper.set(True)
        for kind in ORDER:
            self.var_widgets[kind].set(True)
        save_config(self.config, self.config_path)
        self.status.configure(text="Zurückgesetzt.")

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.close()
        return 0


def main(config_path=None):
    try:
        import tkinter  # noqa: F401
    except Exception:
        raise SystemExit(
            "  tkinter fehlt - das Einstellungsfenster braucht es.\n"
            "  Linux: sudo apt install python3-tk\n"
            "  Die Einstellungen lassen sich auch direkt schreiben:\n"
            "  python -m winwidgets desktop --ascii --no-panel --size l")
    return SettingsWindow(config_path).run()


if __name__ == "__main__":
    sys.exit(main())
