"""``winwidgets`` - Windows 11 widgets that live **in your terminal**.

No window, no browser, no webserver.  A single console process paints a small
board of live widgets - CPU, memory, disks, network, processes, clock, battery -
and keeps repainting it in place until you press ``q``.  It is meant to be the
thing you leave open in a Windows Terminal tab (or a second pane, or a
quake-style drop-down) while you work.

Why it is built the way it is:

* **Nothing to install but two small packages** (``psutil`` + ``colorama``).
  The drawing is plain ANSI, so it works in Windows Terminal, VS Code, ConPTY,
  and any Linux terminal.
* **It draws its own box glyphs**, because those are the ones that exist in
  Consolas / Cascadia Mono without a Nerd Font: ``│ ─ ┌ ┐ └ ┘ ├ ┤ ╭ ╮ ╰ ╯ ▐ ▏ ▊ █ ░ ▒ ▓``.
  No patched font is needed for it to look right.
* **24 bit colour** when the terminal has it, 256 colours otherwise, and a
  monochrome mode that still reads well on a dumb pipe (``--plain``).
* **The look follows Windows 11**: 8 px-ish rounded corners (``╭╮╰╯``), the
  system accent colour (read from the registry, override with
  ``W11WIDGETS_ACCENT``), Segoe-like weight, generous padding.

Usage
-----
    python -m winwidgets                 # the whole board
    python -m winwidgets cpu clock       # only these
    python -m winwidgets --list
    python -m winwidgets --help

Keys:  ``q`` quit  ``<space>`` freeze/resume  ``1..9`` toggle a widget
       ``c`` colour mode  ``s`` snapshot to a text file  ``r`` reset history
"""

from __future__ import annotations

__version__ = "0.3.0"

BOARD_TITLE = "Windows 11 \u00b7 widgets"
