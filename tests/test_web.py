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


def test_idle_exit_waits_for_pages_and_jobs(tmp_path):
    app = web.App(dry_run=tmp_path / "job.bin")
    app.last_seen -= 100
    assert app.idle_for() >= 100
    app.job.state = "printing"          # never stop mid-print
    assert app.idle_for() == 0


def test_requests_count_as_activity(tmp_path):
    app = web.App(dry_run=tmp_path / "job.bin")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (web.Handler,), {"app": app}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        app.last_seen -= 100
        call(f"http://127.0.0.1:{httpd.server_port}/api/status")
        assert app.idle_for() < 5
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_status_says_who_started_the_server(tmp_path):
    assert web.App(dry_run=tmp_path / "j.bin", from_app=True).status()["from_app"] is True


def test_status_reports_bluetooth_permission(tmp_path, monkeypatch):
    from phomemo import ble
    monkeypatch.setattr(ble, "authorization", lambda: "denied")
    assert web.App(dry_run=tmp_path / "j.bin").status()["ble"]["auth"] == "denied"


def test_ask_again_only_from_thermal_app(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web.subprocess, "run", lambda args, **kw: calls.append(args))
    with pytest.raises(RuntimeError):
        web.App(dry_run=tmp_path / "j.bin").ble_ask_again()   # never reset Terminal's
    assert calls == []


def test_ask_again_resets_thermal_then_uses_bluetooth(tmp_path, monkeypatch):
    from phomemo import macapp
    calls = []

    class Done:
        returncode, stderr = 0, ""
    monkeypatch.setattr(web.subprocess, "run", lambda args, **kw: calls.append(args) or Done())
    app = web.App(dry_run=tmp_path / "j.bin", from_app=True)
    monkeypatch.setattr(app, "check_ble", lambda: "checking")
    assert app.ble_ask_again() == "checking"
    assert calls == [["tccutil", "reset", "BluetoothAlways", macapp.BUNDLE_ID]]


def test_settings_opens_the_bluetooth_pane(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web.subprocess, "run", lambda args, **kw: calls.append(args))
    web.App(dry_run=tmp_path / "j.bin").ble_settings()
    assert calls == [["open", web.BLE_SETTINGS]]


def _sheet_app(monkeypatch, fail_on=()):
    """An App printing over a fake Bluetooth port: records one entry per
    connection; raises on the connections numbered in fail_on."""
    from contextlib import contextmanager
    from PIL import Image
    sent = []

    @contextmanager
    def fake_port(**kw):
        buf = bytearray()
        port = type("P", (), {"send": lambda self, d: buf.extend(d)})()
        yield port
        if len(sent) + 1 in fail_on:
            fail_on.remove(len(sent) + 1)
            raise ConnectionError("No paper")
        sent.append((kw, bytes(buf)))

    monkeypatch.setattr(web.transport, "open_port", fake_port)
    app = web.App()
    app.submit = lambda fn: fn()
    page = Image.new("1", (1664, 2376), 1)
    return app, sent, page


def test_bluetooth_sheets_print_one_per_job(monkeypatch):
    app, sent, page = _sheet_app(monkeypatch)
    job = app.start_job([page, page, page], 5, 1, "a4", "ble", ["x.pdf"])
    assert len(sent) == 1 and sent[0][0]["ble_eject"]       # only sheet 1 went out
    assert job.info()["state"] == "waiting" and job.info()["remaining"] == 2
    app.next_sheet()
    assert len(sent) == 2 and job.state == "waiting" and job.page == 2
    app.next_sheet()
    assert len(sent) == 3 and job.state == "done" and job.info()["remaining"] == 0
    with pytest.raises(RuntimeError):
        app.next_sheet()


def test_bluetooth_sheet_error_retries_the_same_sheet(monkeypatch):
    app, sent, page = _sheet_app(monkeypatch, fail_on=[2])
    job = app.start_job([page, page], 5, 1, "a4", "ble", ["x.pdf"])
    app.next_sheet()                                         # sheet 2 fails
    assert job.state == "error" and job.page == 1 and job.info()["remaining"] == 1
    app.next_sheet()                                         # tried again, not sheet 1
    assert job.state == "done" and len(sent) == 2


def test_stop_between_sheets(monkeypatch):
    app, sent, page = _sheet_app(monkeypatch)
    job = app.start_job([page, page], 5, 2, "a4", "ble", ["x.pdf"])
    assert job.total == 4
    app.cancel()
    assert job.state == "cancelled" and job.info()["remaining"] == 0


def test_continuous_bluetooth_is_still_one_job(monkeypatch):
    app, sent, page = _sheet_app(monkeypatch)
    job = app.start_job([page, page], 5, 1, "continuous", "ble", ["x.pdf"])
    assert job.state == "done" and len(sent) == 1 and not job.per_sheet



def test_print_only_the_pages_kept(tmp_path, monkeypatch):
    from PIL import Image
    started = []
    monkeypatch.setattr(web.App, "start_job",
                        lambda self, pages, *a: started.append(pages) or web.Job())
    app = web.App(dry_run=tmp_path / "job.bin")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (web.Handler,), {"app": app}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        uid = json.loads(call(f"{base}/api/files", "POST", b"x", {"X-Filename": "a.pdf"})[1])["id"]
        app.uploads[uid].pages = [Image.new("1", (1664, 10 + i), 1) for i in range(5)]
        status, _ = call(f"{base}/api/print", "POST", {"files": [{"id": uid, "pages": [0, 2, 4, 9]}]})
        assert status == 202
        assert [p.height for p in started[0]] == [10, 12, 14]    # 9 doesn't exist: ignored
        status, _ = call(f"{base}/api/print", "POST", {"files": [uid]})
        assert status == 202 and len(started[1]) == 5             # plain ids still print everything
        status, _ = call(f"{base}/api/print", "POST", {"files": [{"id": uid, "pages": []}]})
        assert status == 400                                       # every page skipped
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_pages_skipped_between_sheets_are_not_printed(monkeypatch):
    app, sent, _ = _sheet_app(monkeypatch)
    from PIL import Image
    pages = [Image.new("1", (1664, 2376), 1) for _ in range(4)]
    for i, p in enumerate(pages):
        p.paste(0, (0, 0, 10 * (i + 1), 10))           # tell the sheets apart by content
    keys = [("f", i) for i in range(4)]
    job = app.start_job(pages, 5, 1, "a4", "ble", ["x.pdf"], keys)
    assert job.page == 1 and job.state == "waiting"
    app.next_sheet({("f", 0), ("f", 1), ("f", 3)})       # page 3 (index 2) skipped mid-run
    app.next_sheet({("f", 0), ("f", 1), ("f", 3)})
    assert job.state == "done" and job.total == 3 and len(sent) == 3
    widths = [jobmod_width(data) for _, data in sent]
    assert widths == [10, 20, 40]                          # index 2 never went out


def test_skipping_every_remaining_page_finishes_the_job(monkeypatch):
    app, sent, page = _sheet_app(monkeypatch)
    job = app.start_job([page, page], 5, 1, "a4", "ble", ["x.pdf"], [("f", 0), ("f", 1)])
    app.next_sheet({("f", 0)})
    assert job.state == "done" and len(sent) == 1


def jobmod_width(data: bytes) -> int:
    """Width in dots of the black mark at the top of a one-sheet job."""
    from PIL import ImageOps
    from escpos_decode import decode
    _, img = decode(data)
    return ImageOps.invert(img.convert("L")).getbbox()[2]


def test_job_names_the_next_page_for_the_preview(monkeypatch):
    app, sent, page = _sheet_app(monkeypatch)
    job = app.start_job([page, page, page], 5, 1, "a4", "ble", ["x.pdf"],
                        [("f", 0), ("f", 1), ("f", 2)])
    assert job.info()["next"] == ["f", 1] and job.info()["printed"] == [["f", 0]]
    app.next_sheet({("f", 0), ("f", 2)})                  # page 2 skipped: page 3 is next
    assert job.state == "done" and job.info()["next"] is None


def test_usb_sheets_also_print_one_per_job(monkeypatch):
    app, sent, page = _sheet_app(monkeypatch)
    monkeypatch.setattr(web.transport, "autodetect", lambda: "/dev/cu.usbmodemTEST")
    job = app.start_job([page, page], 5, 1, "a4", "usb", ["x.pdf"])
    assert job.per_sheet and job.state == "waiting" and len(sent) == 1
    assert sent[0][0] == {"device": "/dev/cu.usbmodemTEST"}     # USB, not Bluetooth
    app.next_sheet()
    assert job.state == "done" and len(sent) == 2


def test_usb_continuous_is_still_one_job(monkeypatch):
    app, sent, page = _sheet_app(monkeypatch)
    monkeypatch.setattr(web.transport, "autodetect", lambda: "/dev/cu.usbmodemTEST")
    job = app.start_job([page, page], 5, 1, "continuous", "usb", ["x.pdf"])
    assert job.state == "done" and not job.per_sheet and job.page == 2
