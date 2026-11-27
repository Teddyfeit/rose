"""``python -m winwidgets`` - the Windows 11 widget board in a terminal.

    python -m winwidgets                  all widgets, live, 1 s refresh
    python -m winwidgets cpu clock        only these
    python -m winwidgets --plain          no colour, one frame (good for pipes)
    python -m winwidgets --snapshot b.txt render one frame to a text file
    python -m winwidgets --list           available widgets
    python -m winwidgets --size 120x44    render at a fixed size

Options
    -i, --interval SEC    refresh interval (default 1.0)
    -p, --plain           monochrome, no escape codes, exits after one frame
    -o, --once N          render N frames and exit
    -s, --size WxH        pretend the terminal is this big
    --snapshot PATH       write one plain-text frame to PATH and exit
    --accent #RRGGBB      override the Windows accent colour
    --list                list the widget keys
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .widgets import ORDER, REGISTRY


def parse_size(text):
    try:
        w, h = text.lower().replace(" ", "").split("x")
        return max(30, int(w)), max(10, int(h))
    except Exception:
        raise argparse.ArgumentTypeError("size must look like 120x40")


def _common_options(parser, suppress=False):
    """The flags every entry point shares (``winwidgets`` and ``winwidgets-top``)."""
    add = parser.add_argument
    kw = {"help": argparse.SUPPRESS} if suppress else {}
    add("-i", "--interval", type=float, default=1.0,
        help=kw.get("help", "refresh interval in seconds (default 1.0)"))
    add("-p", "--plain", action="store_true",
        help=kw.get("help", "no colour, no loop: one frame to stdout"))
    add("-o", "--once", type=int, default=0, metavar="N",
        help=kw.get("help", "render N frames, then exit"))
    add("-s", "--size", type=parse_size, default=None, metavar="WxH",
        help=kw.get("help", "render as if the terminal were this size"))
    add("--snapshot", metavar="PATH", default=None,
        help=kw.get("help", "write one plain-text frame to PATH and exit"))
    add("--accent", metavar="#RRGGBB", default=None,
        help=kw.get("help", "override the Windows accent colour"))


def build_parser(suppress=False):
    kw = {"help": argparse.SUPPRESS} if suppress else {}
    parser = argparse.ArgumentParser(
        prog="winwidgets",
        description="Windows 11 widgets, live in your terminal.",
        epilog="Keys: q quit, space freeze, 1-9 toggle a widget, c colour, "
               "s snapshot, r reset.")
    parser.add_argument("widgets", nargs="*", metavar="WIDGET",
                        help=kw.get("help", "which widgets: " + ", ".join(ORDER)
                                    + "  (default: as many as fit)"))
    parser.add_argument("--list", action="store_true",
                        help=kw.get("help", "list the widgets and exit"))
    parser.add_argument("--version", action="version",
                        version=f"winwidgets {__version__}")
    _common_options(parser, suppress)
    return parser


def select(args):
    import os

    if args.accent:
        os.environ["W11WIDGETS_ACCENT"] = args.accent
    wanted = [w.lower() for w in args.widgets]
    unknown = [w for w in wanted if w not in REGISTRY]
    if unknown:
        raise SystemExit(
            f"unknown widget: {', '.join(unknown)}\n"
            f"available: {', '.join(ORDER)}")
    return wanted or None


BANNER = """
  python -m winwidgets            das Board im Terminal
  python -m winwidgets desktop    die Karten als Fenster auf dem Desktop
  python -m winwidgets cpu clock  nur diese Karten (Terminal)
  python -m winwidgets --list     was es gibt

  Tasten:  q beenden  .  space einfrieren  .  1-7 Karte an/aus
           c Farbmodus  .  s Bild speichern  .  r Verlauf loeschen
"""


DESKTOP_FLAGS = {"--desktop", "desktop", "--fenster", "fenster"}

DESKTOP_HELP = """
  python -m winwidgets desktop [WIDGET ...] [OPTIONEN]

  Die Karten als randlose, halbtransparente Fenster auf dem Desktop.
  Ziehen = verschieben, Rechtsklick = Menue (Groesse, Transparenz, Farben,
  Beenden), Doppelklick = aktualisieren.

  WIDGET     cpu memory disk network clock processes battery
  -o, --opacity  0.25 .. 1.0      Transparenz (Standard 0.82)
  -g, --size     s | m | l        Kartengroesse (Standard m)
      --accent   #rrggbb          Akzentfarbe statt der aus dem Hintergrundbild
      --no-wallpaper              Windows-Akzentfarbe statt Bildfarben
      --flat                      glatte Karten statt ASCII-Zeichen
      --topmost                   immer im Vordergrund
      --normal                    normales Fenster
      --desktop-layer             auf dem Desktop hinter den Fenstern (Standard)
      --interval 1.0              Aktualisierung in Sekunden
      --reset                     gespeicherte Positionen vergessen
      --list                      verfuegbare Widgets
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in DESKTOP_FLAGS:
        rest = [a for a in argv[1:] if a not in DESKTOP_FLAGS]
        if any(a in ("-h", "--help") for a in rest):
            print(DESKTOP_HELP)
            return 0
        if "--list" in rest:
            from .widgets import ORDER

            print("widgets: " + ", ".join(ORDER))
            return 0
        flags_with_value = {"--opacity", "-o", "--size", "-g", "--accent",
                            "--interval", "-i"}
        names, kept, i = [], [], 0
        while i < len(rest):
            arg = rest[i]
            if arg in flags_with_value and i + 1 < len(rest):
                kept.extend([arg, rest[i + 1]])
                i += 2
                continue
            if arg.startswith("-"):
                kept.append(arg)
            else:
                names.append(arg)
            i += 1
        unknown = [k for k in kept if k in ("-p", "--plain", "-s", "--size-x")]
        for flag in unknown:
            print(f"  Hinweis: {flag} gilt nur fuer das Terminal-Board")
        from .desktop import main as desktop_main

        return desktop_main(names or None, argv=[k for k in kept if k not in unknown])
    if argv:
        return _terminal_main(argv)
    return _terminal_main(None)


def _terminal_main(argv=None):
    if argv is None and len(sys.argv) == 1 and not sys.stdout.isatty():
        print(BANNER)
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list:
        from .collect import backend

        print(f"data source: {backend()}")
        print("widgets:")
        for key in ORDER:
            print(f"  {key:10s} {REGISTRY[key].title}")
        print("\nusage: python -m winwidgets [WIDGET ...] [options]")
        from .collect import backend

        if backend() != "psutil":
            print("network needs psutil (pip install psutil); "
                  "everything else works without it")
        return 0

    if args.interval <= 0:
        parser.error("--interval must be positive")

    from .board import build, run

    keys = select(args)

    if args.snapshot:
        board = build(keys, plain=True)
        size = args.size or (120, 40)
        board.collector.tick()
        path = board.render(*size).snapshot(args.snapshot)
        print(f"  frame -> {path}")
        return 0

    if not sys.stdout.isatty() and not args.plain and not args.once:
        # a pipe gets a still frame, not an endless stream of escape codes
        args.plain = True
        args.once = 1

    return run(keys, interval=args.interval, plain=args.plain,
               once=args.once, size=args.size)


def top_main(argv=None):
    """``winwidgets-top``: just CPU + top processes, the smallest useful board."""
    return main(["cpu", "processes", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
