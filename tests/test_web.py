"""`phomemo ui`: the HTTP API, driven the way the browser drives it."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from phomemo import job, render, web

REPO = Path(__file__).parent.parent


@pytest.fixture
def server(tmp_path):
    out = tmp_path / "job.bin"
    app = web.App(dry_run=out)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (web.Handler,), {"app": app}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", out
    httpd.shutdown()
    httpd.server_close()


def call(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_upload_render_print_matches_cli_bytes(server):
    base, out = server
    sample = REPO / "samples" / "notes.md"
    status, body = call(f"{base}/api/files", "POST", sample.read_bytes(),
                        {"X-Filename": "notes.md"})
    assert status == 201
    uid = json.loads(body)["id"]

    status, body = call(f"{base}/api/files/{uid}/render", "POST", {"media": "a4"})
    info = json.loads(body)
    assert info["kind"] == "markdown" and info["pages"] and info["error"] is None

    status, png = call(f"{base}/api/files/{uid}/pages/0")
    assert status == 200 and png.startswith(b"\x89PNG")

    status, _ = call(f"{base}/api/print", "POST", {"files": [uid], "copies": 2, "density": 6})
    assert status == 202
    for _ in range(100):
        state = json.loads(call(f"{base}/api/job")[1])["state"]
        if state != "printing":
            break
        time.sleep(0.1)
    assert state == "done"

    pages = render.render(str(sample), render.RenderOptions(media="a4"))
    assert out.read_bytes() == job.build(pages, job.JobOptions(density=6, copies=2), media="a4")


def test_bad_file_reports_error_instead_of_failing(server):
    base, _ = server
    _, body = call(f"{base}/api/files", "POST", b"\xff\xfe\xfa not a document",
                   {"X-Filename": "mystery.bin"})
    uid = json.loads(body)["id"]
    _, body = call(f"{base}/api/files/{uid}/render", "POST", {})
    info = json.loads(body)
    assert info["error"] and info["pages"] == []


def test_cross_origin_writes_are_refused(server):
    base, _ = server
    status, _ = call(f"{base}/api/job/cancel", "POST", {}, {"Origin": "http://evil.example"})
    assert status == 403


def test_print_with_nothing_ready_is_rejected(server):
    base, _ = server
    status, _ = call(f"{base}/api/print", "POST", {"files": ["nope"]})
    assert status == 400


def test_render_error_names_the_file_not_the_temp_copy(server):
    base, _ = server
    _, body = call(f"{base}/api/files", "POST", b"%PDF-broken", {"X-Filename": "broken.pdf"})
    uid = json.loads(body)["id"]
    info = json.loads(call(f"{base}/api/files/{uid}/render", "POST", {})[1])
    assert info["error"] and "phomemo-ui-" not in info["error"] and uid not in info["error"]


def test_dry_run_never_goes_over_bluetooth(server):
    # The browser asks for Bluetooth by default; a dry run writes the file and
    # reports page progress like USB instead of pretending to send over the air.
    base, out = server
    sample = REPO / "samples" / "notes.md"
    uid = json.loads(call(f"{base}/api/files", "POST", sample.read_bytes(),
                          {"X-Filename": "notes.md"})[1])["id"]
    call(f"{base}/api/files/{uid}/render", "POST", {"media": "a4"})
    _, body = call(f"{base}/api/print", "POST", {"files": [uid], "via": "ble"})
    assert json.loads(body)["via"] == "dry"
    for _ in range(100):
        info = json.loads(call(f"{base}/api/job")[1])
        if info["state"] != "printing":
            break
        time.sleep(0.1)
    assert info["state"] == "done" and info["page"] == info["total"] and out.stat().st_size
