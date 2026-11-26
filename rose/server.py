"""A tiny dependency-free HTTP server that streams the rose to a browser.

The page fetches ``/frame?phase=…`` as JPEG and paints it into a <canvas>; the
rendering itself happens in this Python process (numpy, CPU only).
"""

from __future__ import annotations

import io
import json
import threading
import time
from http import server as http_server
from pathlib import Path

import numpy as np
from PIL import Image

from .renderer import RoseRenderer

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rose · crimson bloom</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin:0; height:100%; background:#05060a; overflow:hidden;
    font-family: "Cormorant Garamond", Georgia, "Times New Roman", serif; }
  #stage { position:fixed; inset:0; display:flex; align-items:center; justify-content:center; }
  canvas { display:block; width:100vw; height:100vh; object-fit:contain;
           image-rendering: auto; filter:saturate(1.02); }
  .vig { position:fixed; inset:0; pointer-events:none;
         background:radial-gradient(120% 90% at 50% 48%, transparent 40%, rgba(0,0,0,.55) 100%); }
  .title { position:fixed; left:50%; bottom:6.2vh; transform:translateX(-50%);
           color:#d8b9bd; letter-spacing:.62em; font-size:13px; text-transform:uppercase;
           opacity:.52; text-shadow:0 0 18px rgba(255,40,70,.55); pointer-events:none;
           white-space:nowrap; }
  .bar { position:fixed; left:50%; top:24px; transform:translateX(-50%);
         display:flex; gap:14px; align-items:center; opacity:.25; transition:opacity .4s;
         color:#e4c8cc; font-family: ui-sans-serif, system-ui, sans-serif; font-size:11px;
         letter-spacing:.18em; }
  body:hover .bar { opacity:.85; }
  .bar button { background:rgba(255,255,255,.06); color:#f0d6d9; border:1px solid rgba(255,120,140,.25);
         padding:7px 13px; border-radius:999px; cursor:pointer; letter-spacing:.14em;
         font-size:10px; text-transform:uppercase; backdrop-filter: blur(6px); }
  .bar button:hover { background:rgba(255,60,90,.18); }
  #hint { font-variant-numeric: tabular-nums; }
  .fade { position:fixed; inset:0; background:#05060a; pointer-events:none;
          transition:opacity 1.6s ease; opacity:1; }
</style>
</head>
<body>
  <div id="stage"><canvas id="c"></canvas></div>
  <div class="vig"></div>
  <div class="title">rose &nbsp;·&nbsp; crimson bloom</div>
  <div class="bar">
    <button id="pause">pause</button>
    <button id="replay">replay</button>
    <button id="save">save frame</button>
    <span id="hint">…</span>
  </div>
  <div class="fade" id="fade"></div>
<script>
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d', { alpha:false });
const fade = document.getElementById('fade');
let phase = 0, playing = true, last = performance.now(), raf = null, loopMs = 26000;
let pending = false, currentImage = null;

function sizeCanvas(img) {
  if (canvas.width !== img.width || canvas.height !== img.height) {
    canvas.width = img.width; canvas.height = img.height;
  }
}
const pre = new Map();
function prefetch(p) {
  // warm the cache for the next few frames while the current one is on screen
  if (pre.size > 6) return;
  const k = Math.round(p * 240) % 240;
  if (pre.has(k)) return;
  pre.set(k, true);
  fetch('/frame?phase=' + p.toFixed(5), { cache:'force-cache' })
    .catch(() => {}).finally(() => pre.delete(k));
}
async function fetchFrame(p) {
  const r = await fetch('/frame?phase=' + p.toFixed(5));
  if (!r.ok) throw new Error('render failed');
  const blob = await r.blob();
  const bmp = await createImageBitmap(blob);
  return bmp;
}
async function tick(now) {
  const dt = Math.min(120, now - last); last = now;
  if (playing) phase = (phase + dt / loopMs) % 1;
  if (!pending) {
    pending = true;
    const target = phase;
    prefetch((phase + 0.06) % 1);
    try {
      const bmp = await fetchFrame(target);
      sizeCanvas(bmp);
      ctx.drawImage(bmp, 0, 0);
      fade.style.opacity = 0;
    } catch (e) {
      console.warn(e);
    }
    pending = false;
  }
  raf = requestAnimationFrame(tick);
}
document.getElementById('pause').addEventListener('click', e => {
  playing = !playing; e.target.textContent = playing ? 'pause' : 'play';
});
document.getElementById('save').addEventListener('click', () => {
  const a = document.createElement('a');
  a.download = 'rose-' + Date.now() + '.png';
  a.href = canvas.toDataURL('image/png');
  a.click();
});
document.getElementById('replay').addEventListener('click', () => {
  phase = 0; playing = true;
  document.getElementById('pause').textContent = 'pause';
  fade.style.opacity = 1;
});
document.addEventListener('keydown', e => {
  if (e.code === 'Space') { e.preventDefault(); document.getElementById('pause').click(); }
});
fetch('/info').then(r => r.json()).then(info => {
  loopMs = info.loop * 1000;
  document.getElementById('hint').textContent =
    info.width + '×' + info.height + ' · ' + info.fps + ' fps';
});
raf = requestAnimationFrame(tick);
</script>
</body>
</html>
"""



TERM_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rose · im Terminal</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin:0; height:100%; background:#08080b; overflow:hidden;
    display:flex; align-items:center; justify-content:center;
    font-family: ui-monospace, "SF Mono", "DejaVu Sans Mono", Menlo, monospace; }
  #wrap { position:relative; box-shadow:0 0 90px rgba(255,40,70,.16); border-radius:10px;
          overflow:hidden; border:1px solid #241a20; }
  canvas { display:block; background:#0b0a0e; }
  #bar { display:flex; gap:8px; align-items:center; padding:9px 14px;
         background:#141317; border-bottom:1px solid #241a20; }
  .dot { width:11px; height:11px; border-radius:50%; }
  #title { margin-left:12px; color:#6d5f66; font-size:12px; letter-spacing:.06em; }
  #status { padding:7px 14px; background:#0e0d11; border-top:1px solid #1d1720;
            color:#8e7b82; font-size:12px; white-space:pre; overflow:hidden; }
  #status b { color:#ff4054; font-weight:600; }
</style>
</head>
<body>
<div id="wrap">
  <div id="bar">
    <span class="dot" style="background:#ff5f56"></span>
    <span class="dot" style="background:#ffbd2e"></span>
    <span class="dot" style="background:#27c93f"></span>
    <span id="title">rose tui</span>
  </div>
  <canvas id="c"></canvas>
  <div id="status">rose  0.0%  … f/s</div>
</div>
<script>
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d', { alpha:false });
const statusEl = document.getElementById('status');
let phase = 0, playing = true, last = performance.now(), pending = false, loopMs = 26000;
let COLS = 110, ROWS = 31, SS = 1.5, cw = 8, ch = 16;

function cellSize() {
  // match the browser's monospace cell: 1ch wide, 2ch tall (two half-blocks)
  const probe = document.createElement('canvas').getContext('2d');
  probe.font = '16px ui-monospace, "DejaVu Sans Mono", monospace';
  cw = Math.max(6, Math.round(probe.measureText('M').width));
  ch = cw * 2;
  canvas.width = COLS * cw;
  canvas.height = ROWS * ch;
  canvas.style.width = (COLS * cw) + 'px';
}
async function frame(p) {
  const r = await fetch('/cells?phase=' + p.toFixed(5));
  if (!r.ok) throw new Error('render failed');
  return new Uint8Array(await r.arrayBuffer());
}
function draw(buf) {
  const img = ctx.createImageData(COLS * cw, ROWS * ch);
  const d = img.data;
  const half = ch >> 1;
  for (let y = 0; y < ROWS; y++) {
    for (let x = 0; x < COLS; x++) {
      const i = ((y * COLS + x) * 2) * 3;
      const tr = buf[i], tg = buf[i+1], tb = buf[i+2];
      const br = buf[i+3], bg = buf[i+4], bb = buf[i+5];
      for (let py = 0; py < ch; py++) {
        const top = py < half;
        const dst = ((y * ch + py) * (COLS * cw) + x * cw) * 4;
        const r = top ? tr : br, g = top ? tg : bg, b = top ? tb : bb;
        for (let px = 0; px < cw; px++) {
          d[dst + px*4] = r; d[dst + px*4 + 1] = g; d[dst + px*4 + 2] = b; d[dst + px*4 + 3] = 255;
        }
      }
    }
  }
  ctx.putImageData(img, 0, 0);
  const pct = (phase * 100).toFixed(1).padStart(5);
  statusEl.innerHTML = '<b>rose</b>  ' + pct + '%   ' + COLS + 'x' + (ROWS*2) + ' px' +
    '   halbe bl\u00f6cke \u00b7 24-bit farbe   [space] pause';
}
async function tick(now) {
  const dt = Math.min(200, now - last); last = now;
  if (playing) phase = (phase + dt / loopMs) % 1;
  if (!pending) {
    pending = true;
    try { draw(await frame(phase)); } catch (e) { console.warn(e); }
    pending = false;
  }
  requestAnimationFrame(tick);
}
document.addEventListener('keydown', e => {
  if (e.code === 'Space') { e.preventDefault(); playing = !playing; }
});
fetch('/info').then(r => r.json()).then(info => {
  loopMs = info.loop * 1000;
  COLS = info.cols || COLS; ROWS = info.rows || ROWS;
  document.getElementById('title').textContent = 'rose tui — ' + COLS + 'x' + ROWS + ' zeichen';
  cellSize();
  requestAnimationFrame(tick);
});
</script>
</body>
</html>
"""


class RoseServer:
    def __init__(self, host="0.0.0.0", port=8000, width=854, height=480, seed=7,
                 cols=110, rows=31, city=False):
        self.host, self.port = host, port
        self.width, self.height = width, height
        self.seed = seed
        self.city = bool(city)
        self.renderer = None
        self._lock = threading.Lock()
        self._cache = {}
        self._frame_times = []
        self.fps = 0.0
        self._render_lock = threading.Lock()
        # the terminal view: the same picture the player paints into a console
        self.cols, self.rows = int(cols), int(rows)
        self._player = None
        self._cells = {}
        self.terminal = False

    # -- render worker: the server renders one frame at a time, in a thread
    def _get_frame(self, phase):
        key = round(phase * 240) % 240
        if key in self._cache:
            return self._cache[key]
        # one render at a time: the numpy pipeline is CPU bound
        with self._render_lock:
            if key in self._cache:
                return self._cache[key]
            if self.renderer is None:
                r = RoseRenderer(self.width, self.height, seed=self.seed)
                self.renderer = r if self.city else r.solo()
            t0 = time.perf_counter()
            arr = self.renderer.render(phase)
            dt = time.perf_counter() - t0
            self._frame_times.append(dt)
            if len(self._frame_times) > 20:
                self._frame_times.pop(0)
            self.fps = 1.0 / (sum(self._frame_times) / len(self._frame_times))
            buf = io.BytesIO()
            Image.fromarray(arr).save(buf, format="JPEG", quality=90, subsampling=0)
            data = buf.getvalue()
            with self._lock:
                self._cache[key] = data
                if len(self._cache) > 600:
                    self._cache.pop(next(iter(self._cache)))
            return data

    # -- the terminal view --------------------------------------------------
    def _terminal_player(self):
        if self._player is None:
            from .tui import TerminalRose

            self._player = TerminalRose(cols=self.cols, rows=self.rows,
                                        seed=self.seed, stream=io.StringIO(),
                                        only_rose=not self.city)
        return self._player

    def _get_cells(self, phase):
        """The half-block cells of a frame: 6 bytes per cell (top/bottom RGB)."""
        key = round(phase * 240) % 240
        cached = self._cells.get(key)
        if cached is not None:
            return cached
        from .terminal import cells as cell_grid

        with self._render_lock:
            player = self._terminal_player()
            img = player.current(phase)
            ss = player.artist.supersample
            key = (key, player.renderer.w, player.renderer.h, ss)
            cached = self._cells.get(key)
            if cached is not None:
                return cached
            grid = cell_grid(img, self.cols, self.rows, supersample=ss)
            data = np.clip(np.rint(grid * 255.0), 0, 255).astype(np.uint8).tobytes()
            self._cells[key] = data
            if len(self._cells) > 240:
                self._cells.pop(next(iter(self._cells)))
            return data

    def make_handler(self):
        outer = self

        class Handler(http_server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, data, ctype, code=200):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                from urllib.parse import urlparse, parse_qs

                u = urlparse(self.path)
                if u.path in ("/", "/index.html"):
                    page = TERM_HTML if outer.terminal else INDEX_HTML
                    return self._send(page.encode(), "text/html; charset=utf-8")
                if u.path == "/terminal":
                    return self._send(TERM_HTML.encode(), "text/html; charset=utf-8")
                if u.path == "/cells":
                    q = parse_qs(u.query)
                    try:
                        phase = float(q.get("phase", ["0"])[0]) % 1.0
                    except ValueError:
                        phase = 0.0
                    return self._send(outer._get_cells(phase), "application/octet-stream")
                if u.path == "/info":
                    info = {
                        "width": outer.width,
                        "height": outer.height,
                        "loop": 26.0,
                        "fps": round(outer.fps or 1.0, 1),
                        "cols": outer.cols,
                        "rows": outer.rows,
                    }
                    return self._send(json.dumps(info).encode(), "application/json")
                if u.path == "/frame":
                    q = parse_qs(u.query)
                    try:
                        phase = float(q.get("phase", ["0"])[0]) % 1.0
                    except ValueError:
                        phase = 0.0
                    try:
                        data = outer._get_frame(phase)
                    except Exception as exc:  # pragma: no cover
                        return self._send(str(exc).encode(), "text/plain", 500)
                    return self._send(data, "image/jpeg")
                return self._send(b"not found", "text/plain", 404)

        return Handler

    def serve_forever(self):
        srv = http_server.ThreadingHTTPServer((self.host, self.port), self.make_handler())
        srv.daemon_threads = True
        print(f"  rose  →  http://{self.host}:{self.port}", flush=True)
        srv.serve_forever()
