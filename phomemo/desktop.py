"""`phomemo app`: the print screen in its own Mac window. Thermal.app runs this.

The page, its server and the printing code are the same as `phomemo ui`; only
the container changes. The window is a native WebKit view (pywebview), so
there is no browser tab, the Dock icon is the window, and closing it quits.

The server still runs, inside this process, because the page talks to it over
HTTP and loads every sheet thumbnail from it. It is private to the window: it
listens on 127.0.0.1 only, and refuses any request without a secret made fresh
at each launch. The window gets that secret as a cookie on its first load
(web.Handler._sign_in); nothing else on the Mac ever sees it.

Needs pywebview: pip install -e ".[app]".
"""
from __future__ import annotations

import os
import secrets
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer

from . import web
from .macapp import LOG

# A fixed port keeps the page's origin the same between launches, and with it
# the settings the page saves (paper, density, Bluetooth or USB). Not 8632, so
# `phomemo ui` in Terminal can run alongside.
PORT = 8634
PAPER = "#f4f4f1"            # Thermal's --paper: the window's colour while the page loads


def _bind(app: web.App, port: int = PORT) -> ThreadingHTTPServer:
    handler = type("ThermalHandler", (web.Handler,), {"app": app})
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:          # taken (a second copy?): any free port; settings start fresh
        return ThreadingHTTPServer(("127.0.0.1", 0), handler)


def _log_to_file() -> None:
    """Opened from the Dock there's no terminal: keep output, including the
    Bluetooth job processes' progress, in Thermal's log."""
    if sys.stderr is not None and sys.stderr.isatty():
        return
    LOG.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    sys.stdout = os.fdopen(1, "w", buffering=1)
    sys.stderr = os.fdopen(2, "w", buffering=1)


def main(device: str | None = None) -> int:
    try:
        import webview
    except ImportError:
        print('Thermal needs pywebview: ./.venv/bin/pip install -e ".[app]"', file=sys.stderr)
        return 2
    _log_to_file()
    os.environ["PHOMEMO_FROM_APP"] = "1"   # Bluetooth errors word their fix for Thermal
    app = web.App(device=device, from_app=True, token=secrets.token_urlsafe(32))
    httpd = _bind(app)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"Thermal window on 127.0.0.1:{port}", file=sys.stderr, flush=True)

    window = webview.create_window(
        "Thermal", f"http://127.0.0.1:{port}/?key={app.token}",
        width=1320, height=860, min_size=(880, 600), background_color=PAPER)

    def closing(window):
        # Ask only while a job is running or waiting for the next sheet:
        # quitting drops it.
        window.confirm_close = app.job.state in ("printing", "waiting")
    window.events.closing += closing
    window.events.loaded += lambda: print("Thermal: page loaded", file=sys.stderr, flush=True)

    try:
        # Cocoa owns the main thread from here until the window closes. Print
        # jobs run on worker threads; Bluetooth ones in their own process.
        webview.start(private_mode=False, localization={
            "global.quitConfirmation": "A print is in progress. Quit anyway? "
                                       "Sheets not yet printed are dropped."})
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(app.dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
