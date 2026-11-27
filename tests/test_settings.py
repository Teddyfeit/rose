"""Tests for the settings window and the command it writes.

The window itself needs a display, so what is tested here is everything that
has a consequence: the config normalisation, the widget toggles, the command
line that reproduces the config, and - with the stub Tk - that the window builds,
saves, resets and re-reads its own file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import faketk                                    # noqa: E402
from winwidgets import settings as S             # noqa: E402
from winwidgets.desktop import DEFAULTS, load_config   # noqa: E402
from winwidgets.widgets import ORDER             # noqa: E402


@pytest.fixture
def tk():
    faketk.install()
    yield faketk
    faketk.uninstall()


# --------------------------------------------------------------------------- #
# pure config logic
# --------------------------------------------------------------------------- #
def test_normalise_fills_defaults_and_clamps():
    config = S.normalise({"opacity": 4.0, "interval": 0, "size": "xl",
                          "style": "neon", "layer": "space", "hidden": ["cpu", "nope"]})
    assert config["opacity"] == 1.0
    assert config["interval"] == 0.25
    assert config["size"] == "m"
    assert config["style"] == "ascii"
    assert config["layer"] == "desktop"
    assert config["hidden"] == ["cpu"]
    assert config["transparent"] is True


def test_accent_is_validated():
    assert S.normalise({"accent": "#FF4D6D"})["accent"] == "#ff4d6d"
    assert S.normalise({"accent": "ff4d6d"})["accent"] == "#ff4d6d"
    assert S.normalise({"accent": "rot"})["accent"] is None
    assert S.normalise({"accent": "#12345"})["accent"] is None


def test_toggle_widget_keeps_order():
    config = {"hidden": []}
    S.toggle_widget(config, "cpu", False)
    S.toggle_widget(config, "clock", False)
    assert config["hidden"] == ["cpu", "clock"]          # ORDER, not click order
    S.toggle_widget(config, "cpu", True)
    assert config["hidden"] == ["clock"]


def test_launch_arguments_reproduce_the_config():
    config = S.normalise({
        "style": "ascii", "transparent": True, "layer": "desktop", "size": "l",
        "opacity": 0.7, "interval": 2.0, "accent": "#ff4d6d",
        "wallpaper_scheme": False, "hidden": ["battery"],
    })
    args = S.launch_arguments(config)
    assert "--ascii" in args and "--no-panel" in args
    assert "--desktop-layer" in args
    assert args[args.index("--opacity") + 1] == "0.70"
    assert args[args.index("--size") + 1] == "l"
    assert args[args.index("--accent") + 1] == "#ff4d6d"
    assert "--no-wallpaper" in args
    # every visible widget is named, so a hidden one stays hidden
    named = [a for a in args if a in ORDER]
    assert "battery" not in named and "cpu" in named


def test_launch_arguments_are_minimal_when_everything_is_visible():
    config = S.normalise({})
    args = S.launch_arguments(config)
    assert not [a for a in args if a in ORDER]          # default = all
    assert "--flat" not in args and "--ascii" in args


def test_start_widgets_writes_the_config_and_spawns(monkeypatch, tmp_path):
    calls = {}

    def fake_open(args, cwd=None):
        calls["args"] = args
        return "process"

    import winwidgets.terminal as T
    monkeypatch.setattr(T, "open_terminal", fake_open)

    path = tmp_path / "widgets.json"
    config = S.normalise({"size": "s", "layer": "topmost"})
    assert S.start_widgets(config, config_path_hint=path) == "process"
    assert calls["args"][0] == "desktop"
    assert "--topmost" in calls["args"]
    saved = load_config(path)
    assert saved["size"] == "s"


# --------------------------------------------------------------------------- #
# the window (stub Tk)
# --------------------------------------------------------------------------- #
def test_window_builds_and_shows_the_defaults(tk, tmp_path):
    window = S.SettingsWindow(config_path=tmp_path / "widgets.json")
    assert window.var_style.get() == "ascii"
    assert window.var_panel.get() is False         # transparent by default
    assert window.var_layer.get() == "desktop"
    assert all(window.var_widgets[k].get() for k in ORDER)
    window.close()


def test_window_saves_what_was_chosen(tk, tmp_path):
    path = tmp_path / "widgets.json"
    window = S.SettingsWindow(config_path=path)
    window.var_style.set("flat")
    window.var_panel.set(True)
    window.var_layer.set("topmost")
    window.var_size.set("s")
    window.var_opacity.set(60)
    window.var_accent.set("#ff4d6d")
    window.var_widgets["battery"].set(False)
    assert window.save()
    saved = load_config(path)
    assert saved["style"] == "flat"
    assert saved["transparent"] is False
    assert saved["layer"] == "topmost"
    assert saved["size"] == "s"
    assert saved["opacity"] == 0.6
    assert saved["accent"] == "#ff4d6d"
    assert saved["hidden"] == ["battery"]
    window.close()


def test_window_reset_restores_defaults(tk, tmp_path):
    path = tmp_path / "widgets.json"
    window = S.SettingsWindow(config_path=path)
    window.var_style.set("flat")
    window.var_layer.set("topmost")
    window.reset()
    assert window.var_style.get() == DEFAULTS["style"]
    assert window.var_layer.get() == DEFAULTS["layer"]
    assert load_config(path)["style"] == DEFAULTS["style"]
    window.close()


def test_save_and_start_launches_the_widgets(tk, tmp_path, monkeypatch):
    launched = {}
    monkeypatch.setattr(S, "start_widgets",
                        lambda config, **kw: launched.update(config=config))
    window = S.SettingsWindow(config_path=tmp_path / "widgets.json")
    window.var_size.set("l")
    window.save_and_start()
    assert launched["config"]["size"] == "l"
    assert "gestartet" in window.status.cget("text")
    window.close()


def test_settings_subcommand_is_wired(monkeypatch):
    from winwidgets.__main__ import main

    called = {}
    import winwidgets.settings as settings_module
    monkeypatch.setattr(settings_module, "main",
                        lambda: called.setdefault("ok", True) and None)
    assert main(["settings"]) == 0
    assert called["ok"] is True


def test_settings_help():
    from winwidgets.__main__ import main

    assert main(["settings", "--help"]) == 0
