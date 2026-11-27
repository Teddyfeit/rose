"""Tests for the terminal widget board (``winwidgets``).

They run headless: no terminal, no Windows, no psutil requirement - the board is
rendered into a :class:`Canvas` and inspected as text.
"""

from __future__ import annotations

import re

import pytest

from winwidgets.collect import Proc, Snapshot
from winwidgets.term import Canvas, Theme
from winwidgets.widgets import ORDER, REGISTRY, make

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def fake_snapshot(**over):
    snap = Snapshot(
        t=1700000000.0,
        cpu=37.0,
        cpu_cores=[12.0, 88.0, 40.0, 60.0],
        cores=4,
        freq=3600.0,
        mem_percent=61.0,
        mem_used=9.8,
        mem_total=16.0,
        swap_percent=4.0,
        disks=[("C:\\", 412.0, 931.0, 44.3), ("D:\\", 100.0, 200.0, 50.0)],
        net_down=12.4,
        net_up=1.2,
        net_total=(120.5, 30.25),
        procs=[Proc("chrome.exe", 12.5, 3.1), Proc("python.exe", 4.0, 1.0)],
        proc_count=241,
        battery=76.0,
        plugged=True,
        uptime=3 * 86400 + 4 * 3600 + 5 * 60,
        host="DESKTOP-1",
        os_name="Windows 11",
    )
    for key, value in over.items():
        setattr(snap, key, value)
    return snap


@pytest.fixture
def history():
    return {
        "cpu": [10, 20, 30, 40, 55, 60, 47, 37],
        "mem": [50, 51, 52, 53, 60, 61, 61, 61],
        "down": [0, 3, 10, 40, 70, 90, 50, 20],
        "up": [0, 1, 2, 5, 10, 8, 4, 2],
    }


def test_every_widget_draws_inside_its_card(history):
    """No widget may write over its own border or outside the canvas."""
    for key in ORDER:
        for width in (34, 40, 52, 68):
            widget = REGISTRY[key]()
            h = widget.height(width)
            canvas = Canvas(width + 10, h + 6, Theme(mode="mono"))
            widget.draw(canvas, 3, 2, width, fake_snapshot(), history)
            lines = canvas.text_lines()
            assert len(lines) == h + 6
            for row_i, line in enumerate(lines):
                assert len(line) <= width + 10
                # inside the card: the first column after the border must not be
                # overwritten by content, and the last one is the border
                inside = line[3 + 1:3 + width - 1]
                if row_i == 2:
                    assert line[3] in "\u256d" and line[3 + width - 1] in "\u256e"
                if 3 <= row_i <= 2 + h - 2:
                    assert len(inside) == width - 2


def test_widgets_fit_their_declared_height(history):
    """The last row a widget draws is the card's bottom border - nothing below."""
    for key in ORDER:
        for width in (34, 40, 52, 68):
            widget = REGISTRY[key]()
            h = widget.height(width)
            canvas = Canvas(width + 4, h + 3, Theme(mode="mono"))
            widget.draw(canvas, 2, 0, width, fake_snapshot(), history)
            for row in range(h + 1, h + 3):
                assert canvas.text_lines()[row].strip() == "", (key, width, row)


def test_footer_shows_keys_and_hides_them_on_a_snap_button(history):
    from winwidgets.board import Board

    collector = type("C", (), {"sample": fake_snapshot(), "h_cpu": [], "h_mem": [],
                               "h_down": [], "h_up": []})()
    board = Board(make(), Theme(mode="mono"), collector)
    board.say("hello")
    assert "hello" in board.render(100, 30).text_lines()[-1]


def test_titles_and_hero_numbers_are_visible(history):
    expected = {
        "cpu": "CPU",
        "memory": "MEMORY",
        "disk": "STORAGE",
        "network": "NETWORK",
        "processes": "TOP PROCESSES",
        "clock": "CLOCK",
        "battery": "BATTERY",
    }
    for key, title in expected.items():
        widget = REGISTRY[key]()
        canvas = Canvas(60, widget.height(60) + 4, Theme(mode="mono"))
        widget.draw(canvas, 0, 0, 60, fake_snapshot(), history)
        text = "\n".join(canvas.text_lines())
        assert title in text


def test_percentages_are_shown(history):
    canvas = Canvas(48, 20, Theme(mode="mono"))
    make(["cpu"])[0].draw(canvas, 0, 0, 48, fake_snapshot(), history)
    assert "37%" in "\n".join(canvas.text_lines())

    canvas = Canvas(48, 20, Theme(mode="mono"))
    make(["memory"])[0].draw(canvas, 0, 0, 48, fake_snapshot(), history)
    text = "\n".join(canvas.text_lines())
    assert "61%" in text and "9.8 / 16.0 GiB" in text


def test_battery_without_battery_does_not_crash(history):
    canvas = Canvas(44, 12, Theme(mode="mono"))
    make(["battery"])[0].draw(canvas, 0, 0, 44,
                              fake_snapshot(battery=None, plugged=None), history)
    assert "no battery" in "\n".join(canvas.text_lines())


def test_board_layout_never_overflows():
    from winwidgets.board import Board, plan

    widgets = make()
    for width, height in ((80, 24), (96, 30), (120, 40), (160, 50), (60, 18)):
        placed = plan(widgets, width, height)
        assert placed, (width, height)
        for widget, x, y, w, h in placed:
            assert x >= 0 and y >= 0
            assert x + w <= width, (width, x, w)
            assert y + h <= height, (height, y, h)
            assert w >= 34, (width, w)


def test_board_frame_fits_the_screen():
    from winwidgets.board import Board

    board = Board(make(), Theme(mode="mono"), collector=None)
    board.collector = type("C", (), {"sample": fake_snapshot(),
                                     "h_cpu": [1, 2, 3], "h_mem": [1, 2],
                                     "h_down": [1], "h_up": [1]})()
    for width, height in ((80, 24), (118, 40), (44, 15)):
        canvas = board.render(width, height)
        lines = canvas.text_lines()
        assert len(lines) == height
        assert all(len(line) <= width for line in lines)
        assert "Windows 11" in lines[0]
        assert lines[-1].startswith(("q quit", "\u2191", "q"))


def test_colour_modes_render_the_same_text():
    from winwidgets.board import Board

    collector = type("C", (), {"sample": fake_snapshot(),
                               "h_cpu": [1, 2], "h_mem": [1], "h_down": [1],
                               "h_up": [1]})()
    plain = Board(make(), Theme(mode="mono"), collector).render(100, 30).text_lines()
    for mode in ("truecolor", "256"):
        board = Board(make(), Theme(mode=mode), collector)
        lines = board.render(100, 30).lines()
        assert all("\x1b[" in line for line in lines)
        stripped = [ANSI.sub("", line) for line in lines]
        # styles are pure colour: the characters must be identical
        assert [line.rstrip() for line in stripped] == plain


def test_256_colour_codes_are_in_range():
    theme = Theme(mode="256")
    for style in theme.styles.values():
        code = theme.code(style)
        values = [int(v) for v in re.findall(r"\d+", code.split("[", 1)[1])]
        assert all(0 <= v <= 255 for v in values), code
        for idx in re.findall(r"[34]8;5;(\d+)", code):
            assert 16 <= int(idx) <= 255, code


def test_snapshot_file_has_no_escapes(tmp_path):
    from winwidgets.board import Board

    collector = type("C", (), {"sample": fake_snapshot(), "h_cpu": [], "h_mem": [],
                               "h_down": [], "h_up": []})()
    canvas = Board(make(), Theme(mode="truecolor"), collector).render(150, 30)
    path = canvas.snapshot(tmp_path / "frame.txt")
    text = open(path, encoding="utf-8").read()
    assert "\x1b" not in text
    assert "CPU" in text and "NETWORK" in text     # both fit at 150 columns
    assert text.endswith("\n")


def test_collector_returns_a_usable_snapshot():
    from winwidgets.collect import Collector

    collector = Collector()
    first = collector.tick()
    assert 0.0 <= first.cpu <= 100.0 * 8
    assert first.cores >= 1
    assert first.mem_total > 0
    assert len(collector.h_cpu) == 2          # seeded in __init__ + this tick
    assert collector.history >= 8
    second = collector.tick()
    assert second.t >= first.t
    assert len(collector.h_cpu) == 3


def test_no_arguments_into_a_pipe_prints_a_banner_not_a_live_board():
    """``widgets.bat  > log`` must not stream escape codes into a file."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-m", "winwidgets"], cwd=root,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert "python -m winwidgets" in out.stdout
    assert "\x1b" not in out.stdout


def test_unknown_widget_is_rejected():
    from winwidgets.__main__ import main

    with pytest.raises(SystemExit):
        main(["nope"])


def test_list_and_snapshot_entry_points(tmp_path, capsys):
    from winwidgets.__main__ import main

    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "cpu" in out and "battery" in out

    target = tmp_path / "board.txt"
    assert main(["--snapshot", str(target), "--size", "100x30"]) == 0
    text = target.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 30
