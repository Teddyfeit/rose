"""Run the widget board *inside* an existing terminal window.

This is what ``widgets-settings.bat`` uses: it opens a Windows Terminal (or
console) window that lives on the desktop, and keeps drawing the board in it.
It is the terminal board, printed the same way ``python -m winwidgets`` does -
no web page, no Tk, no synthetic background.

Keys (same as the normal board):
    q  beenden · space  einfrieren · 1-7 Karte an/aus · c Farben · s Snapshot
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def terminal_command(args=(), title="Windows 11 widgets"):
    """The command that opens a new console window running ``python -m winwidgets``.

    Windows Terminal when it exists (it is what makes the box glyphs look right),
    otherwise a plain ``cmd`` window.
    """
    exe = sys.executable
    module_args = [exe, "-m", "winwidgets", *args]
    quoted = subprocess.list2cmdline(module_args)
    wt = shutil.which("wt")
    if wt:
        # --title, then the command; opened in the project directory
        return [wt, "-w", "0", "nt", "--title", title,
                "-d", str(Path.cwd()), "cmd", "/k", quoted]
    return ["cmd", "/k", quoted]


def open_terminal(args=(), cwd=None):
    """Start the board in a new console window.  Returns the Popen object."""
    flags = 0
    if os.name == "nt":                      # pragma: no cover - Windows only
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(terminal_command(args), cwd=cwd or str(Path.cwd()),
                            creationflags=flags, shell=False)


def write_launcher(directory):
    """Write a .bat that opens the board in a new window - for the Start menu."""
    path = Path(directory) / "Widgets starten.bat"
    path.write_text("@echo off\r\n"
                    'cd /d "%~dp0"\r\n'
                    'start "" pythonw -m winwidgets %*\r\n',
                    encoding="utf-8")
    return path
