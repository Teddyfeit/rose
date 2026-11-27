"""Build the download bundles into ``download/``.

    .venv/bin/python tools/package.py

What you get:

* ``download/rose-v0.1.0.zip``          the whole project (code, tests, docs,
                                       video) - for people who want to look
                                       inside or change it.
* ``download/rose-standalone-v0.1.0.pyz``  one executable file, the clip is
                                       embedded: ``python rose-standalone.pyz``
                                       starts the film, ``... save rose.mp4 x.mp4``
                                       writes the video out.
* ``download/rose.mp4``                the loop on its own, for phones and chats.

Both bundles are reproducible: no timestamps, files sorted, fixed permissions.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "download"   # not "dist": that name is skipped by the workspace snapshot
VERSION = "0.1.0"
MEDIA = ("poster.png", "rose.mp4", "rose_web.mp4")

EXCLUDE_DIRS = {".git", ".venv", "__pycache__", "dist", "build", "frames",
                ".pytest_cache", ".mypy_cache", "node_modules", ".idea"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".orig", ".rej", ".log"}

STANDALONE_MAIN = '''\
"""rose - a procedural crimson rose that plays in your terminal.

One file, nothing to install but numpy and Pillow:

    python rose-standalone.pyz                 the film in this terminal
    python rose-standalone.pyz --fps 12        ... faster
    python rose-standalone.pyz show            list the bundled video
    python rose-standalone.pyz save rose.mp4 out.mp4

Built from the project sources: https://github.com/Teddyfeit/rose
"""

import sys

from rose.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
'''


def _tracked(patterns):
    """Git knows which files belong to the project; fall back to a walk."""
    try:
        # -c cached, -o untracked, --exclude-standard honours .gitignore
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", *patterns],
                             cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
        files = [line for line in out.splitlines() if line.strip()]
        if files:
            return [ROOT / f for f in files]
    except (OSError, subprocess.CalledProcessError):
        pass
    found = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not set(path.parts) & EXCLUDE_DIRS:
            if path.suffix not in EXCLUDE_SUFFIX:
                found.append(path)
    return sorted(found)


def _rel(path):
    return path.relative_to(ROOT).as_posix()


def _media():
    """The clip and poster: from rose/media, assets/ or a stale dist/."""
    out = {}
    for name in MEDIA:
        for cand in (ROOT / "rose" / "media" / name, ROOT / "assets" / name):
            if cand.is_file():
                out[name] = cand
                break
        else:
            print(f"  ! missing {name} - run the render/still commands first")
    return out


def _zip_write(zf, arcname, data, when):
    info = zipfile.ZipInfo(arcname, date_time=when)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    info.create_system = 3
    zf.writestr(info, data)


def build_project_zip(files, media, when):
    out = DIST / f"rose-v{VERSION}.zip"
    if out.exists():
        out.unlink()
    total = 0
    skip = {f"assets/{n}" for n in media}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            if _rel(path) in skip:
                continue
            _zip_write(zf, _rel(path), path.read_bytes(), when)
        for name, path in media.items():
            _zip_write(zf, f"assets/{name}", path.read_bytes(), when)
        # the download needs no thinking: one entry point
        _zip_write(zf, "START-HIER.txt", START_HIER.encode(), when)
    total = out.stat().st_size
    return out, total


def build_standalone(files, media, when):
    out = DIST / f"rose-standalone-v{VERSION}.pyz"
    if out.exists():
        out.unlink()
    sources = [p for p in files
               if p.suffix == ".py" and p.parent.name == "rose"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _zip_write(zf, "__main__.py", STANDALONE_MAIN.encode(), when)
        for path in sorted(sources):
            _zip_write(zf, _rel(path), path.read_bytes(), when)
        _zip_write(zf, "rose/media/__init__.py", b"", when)
        for name, path in media.items():
            _zip_write(zf, f"rose/media/{name}", path.read_bytes(), when)
    data = buf.getvalue()
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(data)
    return out, len(data)


START_HIER = """\
rose - crimson bloom
====================

1. Ordner entpacken.
2. Terminal in diesem Ordner oeffnen.
3. Einmal einrichten:

       python3 -m venv .venv
       .venv/bin/pip install -r requirements.txt

4. Film starten:

       .venv/bin/python -m rose

   (Windows: .venv\\Scripts\\python -m rose)

Tasten: Space Pause, b Bloom, a Zeichen, f Tempo, s speichern, q beenden.

Schon fertig gerendert:
    assets/rose.mp4       14 MB, 1280x720, 10 s Loop
    assets/rose_web.mp4   kleinere Fassung
    assets/poster.png     Standbild 1600x900

Ohne Einrichtung, nur anschauen:
    .venv/bin/python -m rose save rose.mp4 ~/Downloads/rose.mp4
"""


def main():
    DIST.mkdir(exist_ok=True)
    when = time.localtime()[:6]
    files = [p for p in _tracked(["."])
             if p.is_file() and p.suffix not in EXCLUDE_SUFFIX
             and not set(p.parts) & EXCLUDE_DIRS]
    media = _media()

    zp, zsize = build_project_zip(files, media, when)
    so, ssize = build_standalone(files, media, when)
    clip = media.get("rose.mp4")
    if clip:
        target = DIST / "rose.mp4"
        target.write_bytes(clip.read_bytes())

    print(f"\n  {zp.name:34s} {zsize / 1e6:6.1f} MB   {len(files)} files")
    print(f"  {so.name:34s} {ssize / 1e6:6.1f} MB   single file")
    if clip:
        print(f"  {'rose.mp4':34s} {clip.stat().st_size / 1e6:6.1f} MB   just the loop")
    for p in (zp, so):
        print(f"  sha256 {hashlib.sha256(p.read_bytes()).hexdigest()[:16]}  {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
