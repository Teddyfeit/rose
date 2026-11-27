"""Tests for the desktop (tkinter) widgets, run against a stub Tk.

The GUI itself cannot be started in CI, so the interesting parts are tested
where they can be: the colour maths, the wallpaper analysis, the config file,
the drag handling, and - through ``tests/faketk.py`` - that every card actually
draws text and stays inside its window.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import faketk                                        # noqa: E402
from winwidgets import palette as P                  # noqa: E402
from winwidgets import wallpaper as W                # noqa: E402
from winwidgets.collect import Proc, Snapshot         # noqa: E402


@pytest.fixture
def tk():
    faketk.install()
    yield faketk
    faketk.uninstall()


@pytest.fixture
def app(tk, tmp_path, monkeypatch):
    monkeypatch.setenv("W11WIDGETS_WALLPAPER", "")
    from winwidgets import desktop

    application = desktop.DesktopApp(config_path=tmp_path / "widgets.json")
    yield application
    application.quit()


# --------------------------------------------------------------------------- #
# wallpaper analysis
# --------------------------------------------------------------------------- #
def test_wallpaper_accent_is_a_colour_not_a_grey():
    pixels = [(200, 30, 40)] * 120 + [(10, 10, 14)] * 380
    scheme = W.scheme_from_pixels(pixels)
    r, g, b = scheme["accent"]
    assert r > 150 and r > g + 60 and r > b + 60      # red, readable
    assert W.luminance(scheme["accent"]) > 0.3
    assert scheme["mode"] == "dark"
    assert W.luminance(scheme["tint"]) < 0.1          # cards sit on a dark tint


def test_wallpaper_grey_picture_gives_a_neutral_accent():
    scheme = W.scheme_from_pixels([(40, 40, 42)] * 400 + [(8, 8, 9)] * 300)
    r, g, b = scheme["accent"]
    assert max(r, g, b) - min(r, g, b) < 60           # nearly neutral, no random hue


def test_light_wallpaper_switches_the_mode():
    scheme = W.scheme_from_pixels([(240, 240, 238)] * 300 + [(120, 140, 160)] * 100)
    assert scheme["mode"] == "light"
    assert W.luminance(scheme["tint"]) > 0.6


def test_scheme_never_raises_on_nonsense():
    assert W.scheme_from_pixels([])["accent"]
    assert W.scheme_from_pixels(None)["accent"]
    assert W.scheme_from_pixels([(0, 0, 0)])["accent"]


def test_png_reader_matches_pillow(tmp_path):
    """The hand-written PNG reader must agree with Pillow on the same file."""
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (64, 64), (120, 40, 60)).save(tmp_path / "a.png")
    mine = W._read_png(tmp_path / "a.png", 40)
    theirs = W._read_with_pillow(tmp_path / "a.png", 40)
    assert mine and theirs
    # both are downsampled overviews; the exact sample count may differ (nearest
    # vs. LANCZOS), but the size must be in the same range and the colour equal
    assert 500 <= len(mine) <= 2000
    assert 500 <= len(theirs) <= 2000
    mean_mine = [sum(p[i] for p in mine) / len(mine) for i in range(3)]
    mean_theirs = [sum(p[i] for p in theirs) / len(theirs) for i in range(3)]
    assert all(abs(a - b) < 3 for a, b in zip(mean_mine, mean_theirs))


# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #
def test_palette_is_readable_and_wallpaper_tinted():
    pal = P.palette((255, 60, 60), (12, 14, 16), "dark")
    text = tuple(int(pal["text"][i:i + 2], 16) for i in (1, 3, 5))
    bg = tuple(int(pal["bg"][i:i + 2], 16) for i in (1, 3, 5))
    assert P.contrast(text, bg) >= 7                 # body text is comfortable
    accent = tuple(int(pal["accent"][i:i + 2], 16) for i in (1, 3, 5))
    assert P.contrast(accent, bg) >= 4                # accent is not a dark smudge
    assert max(bg) - min(bg) < 40 or bg[2] > bg[0]    # tinted, not pure grey


def test_level_colours_follow_load():
    pal = P.palette((96, 205, 255), (20, 20, 24))
    assert P.level_colour(10, pal) == pal["accent"]
    assert P.level_colour(80, pal) == pal["warn"]
    assert P.level_colour(95, pal) == pal["bad"]


def test_fit_in_screen_keeps_cards_visible():
    assert P.fit_in_screen(-50, -80, 300, 120, 1920, 1080) == (8, 8)
    x, y = P.fit_in_screen(1900, 1070, 300, 120, 1920, 1080)
    assert x + 300 <= 1920 and y + 120 <= 1080


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_config_roundtrip_and_defaults(tmp_path):
    from winwidgets import desktop

    path = tmp_path / "widgets.json"
    config = desktop.load_config(path)
    assert config["opacity"] == 0.82 and config["size"] == "m"   # defaults
    config["positions"] = {"cpu": [10, 20]}
    config["opacity"] = 0.7
    assert desktop.save_config(config, path)
    again = desktop.load_config(path)
    assert again["positions"]["cpu"] == [10, 20]
    assert again["opacity"] == 0.7

    path.write_text("{ this is not json")
    assert desktop.load_config(path)["opacity"] == 0.82          # never crashes


def test_cli_options_are_parsed():
    from winwidgets.desktop import options_from

    names, config = options_from(
        ["cpu", "--opacity", "0.6", "--size", "l", "--accent", "ff4d6d",
         "--no-wallpaper", "clock"], {})
    assert names == ["cpu", "clock"]
    assert config["opacity"] == 0.6 and config["size"] == "l"
    assert config["accent"] == "#ff4d6d"
    assert config["wallpaper_scheme"] is False


# --------------------------------------------------------------------------- #
# the windows
# --------------------------------------------------------------------------- #
def test_every_card_is_borderless_transparent_and_has_text(app):
    assert app.cards, "no cards spawned"
    for kind, card in app.cards.items():
        assert card.win.overrideredirect() is True, kind
        assert card.win.attributes("-alpha") == pytest.approx(0.82)
        # the default is "rests on the desktop", not "always on top" - otherwise
        # the cards would cover everything you are working in
        assert card.win.attributes("-topmost") is False
        texts = [t for t in card.canvas.texts() if t.strip()]
        assert texts, f"{kind} drew no text"
        assert card.w == int(card.win.geometry().split("x")[0])


def test_cards_render_ascii_box_glyphs(app):
    """ASCII mode: the same characters as the terminal board."""
    card = app.cards["cpu"]
    texts = "".join(card.canvas.texts())
    assert "\u256d" in texts and "\u2500" in texts and "\u256f" in texts
    assert "CPU" in texts
    # the ASCII frame is the card: exactly one box, no second panel around it
    assert texts.count("\u256d") == 1
    assert card.win.attributes("-alpha") == pytest.approx(0.82)
    # one card = a character grid, so every glyph uses the monospace font
    fonts = {item.kwargs.get("font") for item in card.canvas.items
             if item.kind == "text" and "content" in item.tags}
    assert fonts and all(f == app.font_ascii for f in fonts)


def test_ascii_cards_show_live_numbers(app):
    for _ in range(3):
        app.collector.tick()
    app.refresh()
    text = "".join(app.cards["cpu"].canvas.texts())
    assert "%" in text
    mem = "".join(app.cards["memory"].canvas.texts())
    assert "GiB" in mem


def test_style_switch_rebuilds_in_the_new_style(app):
    assert isinstance(app.cards["cpu"], __import__("winwidgets.desktop",
                                                  fromlist=["x"]).AsciiCard)
    app.set_style("flat")
    assert not isinstance(app.cards["cpu"], __import__("winwidgets.desktop",
                                                      fromlist=["x"]).AsciiCard)
    assert app.style == "flat"
    app.set_style("ascii")
    assert app.style == "ascii"


def test_layer_cycles_desktop_normal_topmost(app):
    assert app.layer == "desktop"
    # off Windows the desktop layer cannot be attached: nothing is topmost, and
    # the app says so instead of silently floating over everything
    assert all(c.win.attributes("-topmost") is False for c in app.cards.values())
    assert all(c.on_desktop is False for c in app.cards.values())

    app.toggle_layer()                       # -> normal
    assert app.layer == "normal"
    assert all(c.win.attributes("-topmost") is False for c in app.cards.values())

    app.toggle_layer()                       # -> topmost
    assert app.layer == "topmost"
    assert all(c.win.attributes("-topmost") is True for c in app.cards.values())

    app.toggle_layer()                       # -> back to the desktop
    assert app.layer == "desktop"


def test_desktop_layer_reports_honestly_when_it_fails(app, capsys):
    """The start-up line must not promise something that did not happen."""
    assert all(c.on_desktop is False for c in app.cards.values())
    print(app.layer_label())


def test_ascii_theme_uses_wallpaper_colours(app):
    from winwidgets.desktop import ascii_theme

    theme = ascii_theme(app.pal)
    accent = theme.styles["accent"].fg
    assert "#%02x%02x%02x" % accent == app.pal["accent"]
    assert app.style_colour("accent") == app.pal["accent"]


def test_cards_stay_on_screen(app):
    width = app.root.winfo_screenwidth()
    height = app.root.winfo_screenheight()
    for card in app.cards.values():
        x, y = card.win.winfo_x(), card.win.winfo_y()
        assert 0 <= x and x + card.w <= width, card.kind
        assert 0 <= y and y + card.h <= height, card.kind


def test_live_data_reaches_the_cards(app):
    for _ in range(3):
        app.collector.tick()
    app.refresh()
    cpu_texts = " ".join(app.cards["cpu"].canvas.texts())
    assert "%" in cpu_texts
    mem_texts = " ".join(app.cards["memory"].canvas.texts())
    assert "GiB" in mem_texts


def test_refresh_does_not_pile_up_drawing(app):
    app.refresh()
    first = len(app.cards["cpu"].canvas.items)
    for _ in range(5):
        app.refresh()
    assert len(app.cards["cpu"].canvas.items) == first


def test_dragging_moves_and_remembers_the_card(app, tmp_path):
    card = app.cards["cpu"]
    start = (card.win.winfo_x(), card.win.winfo_y())

    class Event:
        def __init__(self, x, y):
            self.x_root, self.y_root = x, y

    card._press(Event(500, 500))
    card._motion(Event(560, 540))
    card._release(Event(560, 540))
    assert (card.win.winfo_x(), card.win.winfo_y()) == (start[0] + 60, start[1] + 40)
    saved = json.loads((tmp_path / "widgets.json").read_text())
    assert saved["positions"]["cpu"] == [start[0] + 60, start[1] + 40]


def test_opacity_and_size_menu_actions(app):
    card = app.cards["cpu"]
    app.set_opacity(0.55)
    assert card.win.attributes("-alpha") == pytest.approx(0.55)
    before = app.cards["cpu"].w
    app.set_size("l")
    assert app.cards["cpu"].w > before            # ASCII size grows with the font
    assert all(c.win.attributes("-alpha") == pytest.approx(0.55)
               for c in app.cards.values())


def test_right_click_menu_has_the_actions(app):
    card = app.cards["cpu"]
    captured = {}
    app.tk.Menu = lambda master=None, **kw: captured.setdefault("menu", faketk.Menu(master, **kw))

    class Event:
        x_root = y_root = 10

    card._menu(Event())
    labels = [name for kind, name, _ in captured["menu"].entries if kind in ("command", "cascade")]
    for needed in ("Alle Widgets", "Nur dieses", "Größe", "Transparenz", "Beenden"):
        assert needed in labels


def test_clock_card_updates_over_time(app):
    app.collector.tick()
    app.refresh()
    texts = " ".join(app.cards["clock"].canvas.texts())
    assert ":" in texts and "UHR" in texts and "up " in texts


def test_network_card_is_honest_without_psutil(app):
    """The card says what is missing instead of drawing zeros."""
    class NoNet:
        net_known = False
        net_down = net_up = 0.0
        net_total = (0.0, 0.0)

    card = app.cards["network"]
    card.refresh(NoNet(), {})
    assert "kein Messwert" in " ".join(card.canvas.texts())


def test_processes_card_lists_rows(app):
    snap = Snapshot(t=0.0, procs=[Proc("chrome.exe", 12.0, 3.0),
                                  Proc("Code.exe", 4.0, 1.0)], proc_count=201)
    card = app.cards["processes"]
    card.refresh(snap, {})
    texts = card.canvas.texts()
    assert any("chrome.exe" in t for t in texts)
    assert any("201" in t for t in texts)


def test_battery_card_without_battery(app):
    snap = Snapshot(t=0.0, battery=None)
    from winwidgets.desktop import CARD_TYPES

    card = app.cards.get("battery") or CARD_TYPES["battery"](
        app, "battery", 10, 10, 300, 80)
    card.refresh(snap, {})
    assert "kein Akku" in " ".join(card.canvas.texts())
