"""`phomemo ui`: a local web interface for uploading, previewing and printing.

Standard library only. The browser uploads each file as a raw request body,
asks for it to be rendered with the current settings, shows the rendered
pages as thumbnails, and sends the whole queue to the printer. Rendering,
job building and transport are the same code the CLI uses.

Listens on 127.0.0.1 only: anyone who can reach it can print.
"""
from __future__ import annotations

import io
import json
import queue
import re
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

from PIL import Image

from .. import escpos, raster, render, spec, testpage, transport
from .. import job as jobmod

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 200 * 1024 * 1024
THUMB_WIDTH = 240
USB_MM_PER_S = 15.0                      # print head speed
_STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
_DITHERS = (None, "hybrid") + raster.DITHERS


@dataclass
class Upload:
    id: str
    name: str
    path: Path
    kind: str | None = None
    pages: list = field(default_factory=list)
    error: str | None = None
    version: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "size": self.path.stat().st_size, "error": self.error,
                "version": self.version,
                "pages": [{"h": p.height} for p in self.pages]}


@dataclass
class Job:
    state: str = "idle"          # idle | printing | done | error | cancelled
    page: int = 0                # pages fully sent
    total: int = 0
    error: str | None = None
    started: float | None = None
    files: list = field(default_factory=list)
    via: str = "usb"
    phase: str = "sending"       # sending | connecting (BLE: sent in one go at the end)
    kind: str = "print"          # print | check (Bluetooth link test, no paper)
    cancel: threading.Event = field(default_factory=threading.Event)

    def info(self) -> dict:
        return {"state": self.state, "page": self.page, "total": self.total,
                "error": self.error, "started": self.started, "files": self.files,
                "via": self.via, "phase": self.phase, "kind": self.kind}


class App:
    def __init__(self, dry_run: Path | None = None, device: str | None = None):
        self.dir = Path(tempfile.mkdtemp(prefix="phomemo-ui-"))
        self.uploads: dict[str, Upload] = {}
        self.job = Job()
        self.dry_run = dry_run
        self.device = device
        self.lock = threading.Lock()
        # Where print jobs run. serve() swaps in the main thread: CoreBluetooth
        # connects reliably from the process's main thread, and fails its first
        # attempt with CBError 15 ("Failed to encrypt the connection") from a
        # worker thread far more often. Tests keep the default worker thread.
        self.submit = lambda fn: threading.Thread(target=fn, daemon=True).start()

    # --- printer -----------------------------------------------------------

    def status(self) -> dict:
        usb = [self.device] if self.device else transport.discover()
        try:
            from ..ble import printer_state as ble_state
        except Exception:
            ble_state = {}
        return {"usb": usb, "dry_run": str(self.dry_run) if self.dry_run else None,
                "ble": {"paper": ble_state.get("paper"), "paper_at": ble_state.get("paper_at"),
                        "waiting_since": ble_state.get("waiting_since")},
                "now": time.time(),
                "usb_mm_per_s": USB_MM_PER_S,
                "ble_mm_per_s": _ble_mm_per_s(),
                "job": self.job.info()}

    def start_job(self, pages: list[Image.Image], density: int, copies: int,
                  media: str, via: str, files: list[str]) -> Job:
        with self.lock:
            if self.job.state == "printing":
                raise RuntimeError("a job is already printing")
            job = Job(state="printing", total=len(pages) * copies, started=time.time(),
                      files=files, via=via)
            self.job = job
        self.submit(lambda: self._run(job, pages, density, copies, media, via))
        return job

    def check_ble(self) -> Job:
        """Connect over Bluetooth and send only ESC @ (reset): proves the link, uses no paper."""
        with self.lock:
            if self.job.state == "printing":
                raise RuntimeError("a job is already printing")
            job = Job(state="printing", started=time.time(), files=["Bluetooth check"],
                      via="ble", phase="connecting", kind="check")
            self.job = job
        self.submit(lambda: self._check(job))
        return job

    def _check(self, job: Job):
        try:
            with transport.open_port(ble="auto", ble_require_paper=False) as port:
                port.send(escpos.initialize())
            job.state = "done"
        except Exception as e:
            job.state, job.error = "error", str(e).strip()

    def _run(self, job: Job, pages, density: int, copies: int, media: str, via: str):
        feed = 3 if spec.page_height_dots(media) is None else 0
        ble = "auto" if via == "ble" else None
        try:
            device = None
            if self.dry_run is None and ble is None:
                device = self.device or transport.autodetect()
            with transport.open_port(device=device, dry_run_path=self.dry_run,
                                     dry_run=self.dry_run is not None, ble=ble,
                                     ble_eject=spec.page_height_dots(media) is not None) as port:
                port.send(escpos.initialize() + escpos.density(density))
                for page in jobmod.sequence(pages, copies, media):
                    if job.cancel.is_set():
                        job.state = "cancelled"
                        return
                    port.send(b"".join(escpos.raster_block(wb, h, data)
                                       for wb, h, data in raster.bands(page)))
                    if self.dry_run is not None:   # let the UI show progress
                        time.sleep(0.6)
                    if ble is None:   # BLE buffers everything and sends on close
                        job.page += 1
                port.send(escpos.feed(feed) + escpos.cut("none"))
                if ble is not None:
                    job.phase = "connecting"
            job.page, job.state = job.total, "done"
        except Exception as e:   # surfaced to the browser, not the console
            job.state, job.error = "error", str(e).strip()

    # --- files ---------------------------------------------------------------

    def add(self, name: str, body: bytes) -> Upload:
        uid = uuid.uuid4().hex[:12]
        suffix = Path(name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            suffix = ""
        path = self.dir / f"{uid}{suffix}"
        path.write_bytes(body)
        up = Upload(uid, Path(name).name or "untitled", path)
        self.uploads[uid] = up
        return up

    def render(self, up: Upload, opts: dict) -> Upload:
        ro = render.RenderOptions(
            media=opts.get("media") if opts.get("media") in spec.PAGE_SIZES_MM else "a4",
            dither=opts.get("dither") if opts.get("dither") in _DITHERS else None,
            fit=opts.get("fit") if opts.get("fit") in ("page", "width") else "page",
            monospace=bool(opts.get("mono")))
        with up.lock:
            try:
                up.kind = up.kind or render.detect_kind(str(up.path))
                up.pages, up.error = render.render(str(up.path), ro, kind=up.kind), None
            except Exception as e:
                up.pages, up.error = [], _first_line(e)
            up.version += 1
        return up

    def page_png(self, up: Upload, index: int, full: bool) -> bytes:
        page = up.pages[index]
        if not full:
            h = round(page.height * THUMB_WIDTH / page.width)
            page = page.convert("L").resize((THUMB_WIDTH, h), Image.Resampling.BOX)
        buf = io.BytesIO()
        page.save(buf, "PNG", optimize=not full)
        return buf.getvalue()


def _ble_mm_per_s() -> float:
    try:
        from ..ble import DEFAULT_RATE
    except Exception:
        DEFAULT_RATE = 20_000
    return round(DEFAULT_RATE / (spec.BYTES_PER_LINE * spec.DOTS_PER_MM), 2)


def _first_line(e: Exception) -> str:
    text = str(e).strip() or type(e).__name__
    return text.splitlines()[0][:200]


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "phomemo-ui"

    def log_message(self, fmt, *args):   # quiet unless something fails
        if args and str(args[1])[:1] in "45":
            super().log_message(fmt, *args)

    # --- helpers -------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str, cache: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=31536000, immutable" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _error(self, status: int, message: str):
        self._json({"error": message}, status)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("file is larger than 200 MB")
        return self.rfile.read(n)

    def _upload(self, uid: str) -> Upload | None:
        up = self.app.uploads.get(uid)
        if up is None:
            self._error(404, "no such file")
        return up

    def _same_origin(self) -> bool:
        """Refuse writes from other sites the browser happens to have open."""
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        return origin is None or urlparse(origin).netloc == host

    # --- routes --------------------------------------------------------------

    def do_GET(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        if url.path == "/":
            return self._static("index.html")
        if parts[:1] == ["static"] and len(parts) == 2:
            return self._static(parts[1])
        if url.path == "/api/status":
            return self._json(self.app.status())
        if url.path == "/api/job":
            return self._json(self.app.job.info())
        if parts[:2] == ["api", "files"] and len(parts) == 5 and parts[3] == "pages":
            up = self._upload(parts[2])
            if up is None:
                return
            try:
                i = int(parts[4])
                png = self.app.page_png(up, i, parse_qs(url.query).get("size") == ["full"])
            except (ValueError, IndexError):
                return self._error(404, "no such page")
            return self._send(200, png, "image/png", cache=True)
        self._error(404, "not found")

    def do_POST(self):
        if not self._same_origin():
            return self._error(403, "cross-origin request refused")
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        try:
            if parts == ["api", "files"]:
                name = unquote(self.headers.get("X-Filename") or "untitled")
                up = self.app.add(name, self._body())
                return self._json(up.info(), 201)
            if parts[:2] == ["api", "files"] and len(parts) == 4 and parts[3] == "render":
                up = self._upload(parts[2])
                if up is None:
                    return
                opts = json.loads(self._body() or b"{}")
                return self._json(self.app.render(up, opts).info())
            if parts == ["api", "print"]:
                return self._print(json.loads(self._body() or b"{}"))
            if parts == ["api", "test"]:
                req = json.loads(self._body() or b"{}")
                density = _clamp(req.get("density"), 1, 8, 5)
                job = self.app.start_job([testpage.build(density)], density, 1, "a4",
                                         req.get("via", "usb"), ["Calibration page"])
                return self._json(job.info(), 202)
            if parts == ["api", "ble", "check"]:
                return self._json(self.app.check_ble().info(), 202)
            if parts == ["api", "job", "cancel"]:
                self.app.job.cancel.set()
                return self._json(self.app.job.info())
        except RuntimeError as e:
            return self._error(409, str(e))
        except ValueError as e:
            return self._error(400, str(e))
        self._error(404, "not found")

    def do_DELETE(self):
        if not self._same_origin():
            return self._error(403, "cross-origin request refused")
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if parts[:2] == ["api", "files"] and len(parts) == 3:
            up = self.app.uploads.pop(parts[2], None)
            if up:
                up.path.unlink(missing_ok=True)
            return self._json({"ok": True})
        self._error(404, "not found")

    def _print(self, req: dict):
        media = req.get("media") if req.get("media") in spec.PAGE_SIZES_MM else "a4"
        pages, names = [], []
        for uid in req.get("files", []):
            up = self.app.uploads.get(uid)
            if up and up.pages:
                pages.extend(up.pages)
                names.append(up.name)
        if not pages:
            raise ValueError("nothing to print")
        job = self.app.start_job(pages, _clamp(req.get("density"), 1, 8, 5),
                                 _clamp(req.get("copies"), 1, 99, 1), media,
                                 req.get("via", "usb"), names)
        self._json(job.info(), 202)

    def _static(self, name: str):
        path = STATIC / name
        if path.suffix not in _STATIC_TYPES or not path.is_file():
            return self._error(404, "not found")
        self._send(200, path.read_bytes(), _STATIC_TYPES[path.suffix])


def _clamp(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def serve(port: int = 8632, open_browser: bool = True, dry_run: Path | None = None,
          device: str | None = None) -> int:
    app = App(dry_run=dry_run, device=device)
    handler = type("BoundHandler", (Handler,), {"app": app})
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        if e.errno != 48:   # EADDRINUSE on macOS
            raise
        print(f"Port {port} is in use: is `phomemo ui` already running in another window?\n"
              f"Stop it with Ctrl-C there, or pick another port: phomemo ui --port {port + 1}",
              file=sys.stderr)
        return 1
    jobs: queue.Queue = queue.Queue()
    app.submit = jobs.put                 # jobs run on this (the main) thread
    url = f"http://127.0.0.1:{port}/"
    print(f"Phomemo UI at {url}" + (f"  (dry run -> {dry_run})" if dry_run else ""),
          file=sys.stderr)
    print("Ctrl-C to stop.", file=sys.stderr)
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        while True:
            try:
                fn = jobs.get(timeout=0.5)   # short timeout keeps Ctrl-C responsive
            except queue.Empty:
                continue
            fn()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0
