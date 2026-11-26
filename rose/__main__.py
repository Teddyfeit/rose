"""Command line entry point.

    python -m rose                       # the film, inside the terminal
    python -m rose tui --fps 20          # ... at 20 frames per second
    python -m rose serve                 # live preview in the browser
    python -m rose video out.mp4         # loopable clip
    python -m rose still poster.png      # a single hi-res frame
    python -m rose frames out/           # a PNG sequence
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np


def _renderer(args):
    from .renderer import RoseRenderer

    r = RoseRenderer(args.width, args.height, loop_seconds=args.loop, seed=args.seed)
    # the film has exactly one subject: the bloom (``--city`` adds the skyline)
    return r if getattr(args, "city", False) else r.solo()


def _save_png(arr, path):
    from PIL import Image

    Image.fromarray(arr).save(path)


def cmd_still(args):
    r = _renderer(args)
    t0 = time.time()
    img = r.render(args.phase / 100.0)
    _save_png(img, args.out)
    print(f"  {args.out}  ({time.time() - t0:.1f}s, {args.width}x{args.height})")


def cmd_frames(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    r = _renderer(args)
    n = int(round(args.seconds * args.fps))
    t0 = time.time()
    for i in range(n):
        phase = (i / n) if args.looped else (i / args.fps / args.loop)
        arr = r.render(phase % 1.0)
        _save_png(arr, out / f"frame_{i:05d}.png")
        done = i + 1
        eta = (time.time() - t0) / done * (n - done)
        print(f"\r  frame {done}/{n}  eta {eta:5.0f}s", end="", flush=True)
    print()


def cmd_video(args):
    import imageio_ffmpeg

    r = _renderer(args)
    out = Path(args.out)
    n = int(round(args.seconds * args.fps))
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{args.width}x{args.height}", "-r", str(args.fps),
        "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "17",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        for i in range(n):
            phase = (i / n) if args.looped else ((i / args.fps) / args.loop) % 1.0
            arr = r.render(phase)
            proc.stdin.write(arr.tobytes())
            done = i + 1
            eta = (time.time() - t0) / done * (n - done)
            print(f"\r  frame {done}/{n}  eta {eta:5.0f}s", end="", flush=True)
    finally:
        proc.stdin.close()
        proc.wait()
    print(f"\n  {out}  ({n} frames @ {args.fps}fps)")


def cmd_tui(args):
    from . import tui

    return tui.main(args)


def cmd_serve(args):
    from .server import RoseServer

    srv = RoseServer(host=args.host, port=args.port, width=args.width,
                     height=args.height, seed=args.seed, cols=args.cols,
                     rows=args.rows, city=bool(args.city))
    srv.terminal = bool(args.terminal)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")


COMMANDS = ("tui", "serve", "still", "frames", "video")


def _parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--seed", type=int, default=7)
    common.add_argument("--loop", type=float, default=26.0, help="seconds per loop")

    p = argparse.ArgumentParser(prog="rose", description="A procedural anime rose.")
    p.add_argument("--width", type=int, default=854)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--loop", type=float, default=26.0, help="seconds per loop")
    p.add_argument("--city", action="store_true",
                   help="put the ruined skyline back behind the rose")
    sub = p.add_subparsers(dest="cmd")

    from .tui import add_arguments as add_tui_arguments

    s = sub.add_parser("tui", help="the film inside your terminal", parents=[common])
    add_tui_arguments(s)
    s.set_defaults(func=cmd_tui)

    s = sub.add_parser("serve", help="live preview in the browser", parents=[common])
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--terminal", action="store_true",
                   help="show the terminal view (half blocks) instead of the render")
    s.add_argument("--cols", type=int, default=110, help="cells per row (--terminal)")
    s.add_argument("--rows", type=int, default=31, help="rows of cells (--terminal)")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("still", help="render one frame", parents=[common])
    s.add_argument("out", nargs="?", default="poster.png")
    s.add_argument("--phase", type=float, default=12.0, help="0..100")
    s.set_defaults(func=cmd_still)

    s = sub.add_parser("frames", help="render a PNG sequence", parents=[common])
    s.add_argument("out", nargs="?", default="frames")
    s.add_argument("--seconds", type=float, default=6.0)
    s.add_argument("--fps", type=int, default=24)
    s.add_argument("--looped", action=argparse.BooleanOptionalAction, default=True,
                   help="fit exactly one loop into the clip (default: on)")
    s.set_defaults(func=cmd_frames)

    s = sub.add_parser("video", help="render an mp4", parents=[common])
    s.add_argument("out", nargs="?", default="rose.mp4")
    s.add_argument("--seconds", type=float, default=8.0)
    s.add_argument("--fps", type=int, default=24)
    s.add_argument("--looped", action=argparse.BooleanOptionalAction, default=True,
                   help="fit exactly one loop into the clip (default: on)")
    s.set_defaults(func=cmd_video)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # ``rose`` on its own is the terminal player; so is ``rose --fps 12``
    if not any(a in COMMANDS for a in argv) and not set(argv) & {"-h", "--help"}:
        argv = ["tui"] + argv
    args = _parser().parse_args(argv)
    rc = args.func(args)
    return rc or 0


if __name__ == "__main__":
    main()
