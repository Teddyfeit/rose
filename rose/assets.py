"""Bundled media: the poster and the loops that ship with the app.

Three builds have to find the same clip:

* a source checkout        -> ``assets/rose.mp4``
* an installed wheel       -> ``rose/media/rose.mp4``
* the single-file download -> inside the ``.pyz``, where there is no path at
  all and the bytes have to come out of the zip importer.

``read()`` hides that difference; ``find()`` only exists for display.
"""

from __future__ import annotations

import pkgutil
import shutil
from pathlib import Path

MEDIA = ("poster.png", "rose.mp4", "rose_web.mp4")


def _candidates(name):
    here = Path(__file__).resolve().parent
    yield here / "media" / name                 # wheel / checkout
    yield here.parent / "assets" / name         # source checkout
    yield Path.cwd() / "assets" / name
    yield Path.cwd() / name


def find(name):
    """Filesystem path of a bundled file, or None (a .pyz has none)."""
    for path in _candidates(name):
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except (OSError, ValueError):            # pragma: no cover - zip paths
            continue
    return None


def read(name):
    """The bytes of a bundled file, or None when this build has none."""
    path = find(name)
    if path is not None:
        return path.read_bytes()
    try:
        return pkgutil.get_data("rose", f"media/{name}")   # works inside a .pyz
    except (FileNotFoundError, OSError, ImportError, ValueError):
        return None


def extract(name, dest):
    """Write a bundled file to ``dest``. Returns the path."""
    data = read(name)
    if data is None:
        raise FileNotFoundError(
            f"{name} is not part of this build - "
            f"run `rose video {dest}` to render it"
        )
    dest = Path(dest).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = find(name)
    if src is not None and src.resolve() == dest.resolve():
        return dest
    dest.write_bytes(data)
    return dest


def describe():
    """(name, label, megabytes) for everything that is bundled."""
    out = []
    for name in MEDIA:
        data = read(name)
        if data is None:
            continue
        path = find(name)
        label = str(path) if path is not None else "embedded in this file"
        out.append((name, label, len(data) / 1e6))
    return out
