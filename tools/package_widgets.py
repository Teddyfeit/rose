"""Build the download bundles for the Windows 11 widgets.

    .venv/bin/python tools/package_widgets.py

What you get in ``download/``:

* ``winwidgets-v0.2.0.zip``       the widget board (code, tests, docs) plus a
                                 ``START-WIDGETS.txt`` and a double-clickable
                                 ``widgets.bat``.
* ``winwidgets-v0.2.0.pyz``       one file: ``python winwidgets.pyz`` starts the
                                 board.  Still needs ``psutil`` (and on Windows
                                 ``colorama`` is *not* needed - the drawing is
                                 plain ANSI).

Both are reproducible: sorted entries, fixed timestamps, fixed permissions.
"""

from __future__ import annotations

import hashlib
import io
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "download"
VERSION = "0.2.0"
PACKAGE = "winwidgets"

STANDALONE_MAIN = '''\
"""Windows 11 widgets, live in this terminal.

    python winwidgets.pyz              the board
    python winwidgets.pyz cpu clock    only these
    python winwidgets.pyz --list       what is available
    python winwidgets.pyz --help

Needs psutil:  python -m pip install psutil
"""

import sys

from winwidgets.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
'''


def _files():
    try:
        import subprocess

        out = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", PACKAGE,
             "tests/test_widgets.py", "tools/widget_shot.py",
             "START-WIDGETS.txt", "widgets.bat", "requirements.txt",
             "pyproject.toml", "docs/widgets.png"],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout
        files = [ROOT / line for line in out.splitlines() if line.strip()]
        if files:
            return [p for p in files if p.is_file() and "__pycache__" not in p.parts]
    except Exception:
        pass
    return sorted((ROOT / PACKAGE).rglob("*.py"))


def _write(zf, arcname, data, when):
    info = zipfile.ZipInfo(arcname, date_time=when)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    info.create_system = 3
    zf.writestr(info, data)


def build_zip(files, when):
    out = DIST / f"winwidgets-v{VERSION}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            _write(zf, path.relative_to(ROOT).as_posix(), path.read_bytes(), when)
    return out


def build_pyz(files, when):
    out = DIST / f"winwidgets-v{VERSION}.pyz"
    sources = sorted(p for p in files if p.suffix == ".py"
                     and p.parent.parent.name in ("", PACKAGE) or p.parent.name == PACKAGE)
    sources = sorted({p for p in sources if PACKAGE in p.parts or p.name == "__main__.py"})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _write(zf, "__main__.py", STANDALONE_MAIN.encode(), when)
        for path in sources:
            if path.parent.name == PACKAGE:
                _write(zf, path.relative_to(ROOT).as_posix(), path.read_bytes(), when)
    data = buf.getvalue()
    out.write_bytes(data)
    return out


def main():
    DIST.mkdir(exist_ok=True)
    when = time.localtime()[:6]
    files = _files()
    if not files:
        raise SystemExit("no files found")
    zp = build_zip(files, when)
    py = build_pyz(files, when)
    for path in (zp, py):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        print(f"  {path.name:28s} {path.stat().st_size / 1e6:5.2f} MB  "
              f"{len(files)} files  sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
