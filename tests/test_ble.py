"""Bluetooth without a printer: fake bleak clients, and the web UI's BLE path.

Live checks against real hardware are `phomemo ble-scan --probe` and
`phomemo test --ble`; these cover the logic around them.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

bleak = pytest.importorskip("bleak")
from bleak.exc import BleakError  # noqa: E402

from phomemo import ble, job, render, web  # noqa: E402

REPO = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(ble.asyncio, "sleep", lambda s: real_sleep(0))


class FakeClient:
    """Stands in for BleakClient: fails the first `fail` connects."""
    attempts: list = []
    fail = 0

    def __init__(self, dev, services=None, timeout=None):
        self.services_arg, self.disconnected = services, False
        FakeClient.attempts.append(self)

    async def connect(self):
        if len(FakeClient.attempts) <= FakeClient.fail:
            raise BleakError("disconnected")

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def fake_client(monkeypatch):
    FakeClient.attempts, FakeClient.fail = [], 0
    monkeypatch.setattr(bleak, "BleakClient", FakeClient)
    return FakeClient


def test_connect_retries_then_falls_back_to_full_discovery(fake_client):
    fake_client.fail = 2
    client, how = asyncio.run(ble.connect("dev"))
    assert how == "full discovery, try 3"
    assert [a.services_arg for a in fake_client.attempts] == \
        [ble.DATA_SERVICES, ble.DATA_SERVICES, None]
    assert all(a.disconnected for a in fake_client.attempts[:2])   # failed tries cleaned up


def test_connect_gives_up_with_every_reason_and_the_fixes(fake_client):
    fake_client.fail = 99
    with pytest.raises(ConnectionError) as e:
        asyncio.run(ble.connect("dev"))
    msg = str(e.value)
    assert "targeted: disconnected; targeted: disconnected; full: disconnected" in msg
    assert "phone" in msg and "Power-cycle" in msg
    assert len(fake_client.attempts) == 3


def test_bleport_sends_nothing_until_close(monkeypatch):
    sent = []

    async def fake_send(data, target, rate, eject=False, require_paper=True):
        sent.append((data, target, rate))

    monkeypatch.setattr(ble, "send", fake_send)
    port = ble.BlePort("M08F", 12345)
    port.send(b"ab")
    port.send(b"cd")
    assert sent == []
    port.close()
    assert sent == [(b"abcd", "M08F", 12345)]


# --- the web UI over Bluetooth ------------------------------------------------

@pytest.fixture
def ui(monkeypatch):
    app = web.App()          # not a dry run: the real BLE port, with ble.send faked
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (web.Handler,), {"app": app}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def _post(url, body, headers=None):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _print_notes(base):
    uid = _post(f"{base}/api/files", (REPO / "samples" / "notes.md").read_bytes(),
                {"X-Filename": "notes.md"})["id"]
    _post(f"{base}/api/files/{uid}/render", {"media": "a4"})
    started = _post(f"{base}/api/print", {"files": [uid], "via": "ble", "density": 5})
    assert started["via"] == "ble"
    for _ in range(100):
        with urllib.request.urlopen(f"{base}/api/job") as r:
            state = json.loads(r.read())
        if state["state"] != "printing":
            return state
        time.sleep(0.05)
    raise AssertionError("job never finished")


def test_ui_ble_failure_says_nothing_printed(ui, monkeypatch):
    async def fails(data, target, rate, eject=False, require_paper=True):
        raise ConnectionError("Found the printer but couldn't hold a connection (...)")

    monkeypatch.setattr(ble, "send", fails)
    state = _print_notes(ui)
    assert state["state"] == "error"
    assert state["page"] == 0          # it was all buffered: nothing reached the printer
    assert "couldn't hold a connection" in state["error"]


def test_ui_ble_success_sends_exactly_the_cli_job(ui, monkeypatch):
    sent = []

    async def ok(data, target, rate, eject=False, require_paper=True):
        sent.append((data, target))

    monkeypatch.setattr(ble, "send", ok)
    state = _print_notes(ui)
    assert state["state"] == "done" and state["page"] == state["total"]
    pages = render.render(str(REPO / "samples" / "notes.md"), render.RenderOptions(media="a4"))
    assert sent == [(job.build(pages, job.JobOptions(density=5), media="a4"), None)]


def test_ui_ble_check_sends_only_a_reset(ui, monkeypatch):
    sent = []

    async def ok(data, target, rate, eject=False, require_paper=True):
        sent.append(data)

    monkeypatch.setattr(ble, "send", ok)
    started = _post(f"{ui}/api/ble/check", {})
    assert started["kind"] == "check"
    for _ in range(100):
        with urllib.request.urlopen(f"{ui}/api/job") as r:
            state = json.loads(r.read())
        if state["state"] != "printing":
            break
        time.sleep(0.05)
    assert state["state"] == "done"
    assert sent == [b"\x1b\x40"]      # ESC @ only: no raster, no feed, no paper


# --- credit-based flow control (as measured on an M08F) ----------------------

def test_credits_parse_the_m08f_handshake():
    async def go():
        c = ble.Credits()
        for note in (b"\x01\x07", b"\x02\xf4\x00", b"\x1a\x0f\x0c"):
            c.feed(note)
        assert (c.available, c.max_packet) == (7, 244)
        for _ in range(7):
            await c.take(timeout=0.1)
        with pytest.raises(ConnectionError, match="stopped accepting data"):
            await c.take(timeout=0.05)
        c.feed(b"\x01\x01")
        await c.take(timeout=0.1)
    asyncio.run(go())


class _Char:
    def __init__(self, uuid, props):
        self.uuid, self.properties = uuid, props
        self.max_write_without_response_size = 509


class _Svc:
    def __init__(self, *chars):
        self.uuid, self.characteristics = "0000ff00" + ble._BASE, list(chars)


class FakePrinter:
    """Grants 7 credits, then one back per packet after it 'prints' it.
    Records the most packets ever in flight; real firmware drops beyond its buffer."""

    def __init__(self, credits=True, buffer=7):
        self.write = _Char(ble.PREFERRED_WRITE, ["write-without-response", "write"])
        self.notify = _Char("0000ff03" + ble._BASE, ["notify"])
        self.services = [_Svc(self.write, self.notify)]
        self.credits, self.buffer = credits, buffer
        self.received, self.in_flight, self.max_in_flight = bytearray(), 0, 0
        self.cb = None

    async def start_notify(self, char, cb):
        self.cb = cb
        if self.credits:
            cb(char, bytearray(b"\x01" + bytes([self.buffer])))
            cb(char, bytearray(b"\x02\xf4\x00"))

    paper = True          # answer to the 1f 11 11 query; None = no reply

    async def write_gatt_char(self, char, data, response=False):
        if data == ble.PAPER_QUERY:
            if self.credits:
                asyncio.get_running_loop().call_later(0.001, self.cb, self.notify, bytearray(b"\x01\x01"))
            if self.paper is not None:
                self.cb(self.notify, bytearray(b"\x1a\x06" + (b"\x89" if self.paper else b"\x88")))
            return
        self.received += data
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        asyncio.get_running_loop().call_later(0.001, self._printed)

    def _printed(self):
        self.in_flight -= 1
        if self.credits:
            self.cb(self.notify, bytearray(b"\x01\x01"))

    async def disconnect(self):
        pass


def _send_to(monkeypatch, printer, data):
    async def find(target):
        return "dev", "M08F"

    async def connect(dev):
        return printer, "fake"

    monkeypatch.setattr(ble, "find_printer", find)
    monkeypatch.setattr(ble, "connect", connect)
    asyncio.run(ble.send(data, rate=10_000_000))


def test_send_never_outruns_the_printers_credits(monkeypatch):
    printer = FakePrinter(buffer=7)
    data = bytes(range(256)) * 400                     # ~100 KB, ~420 packets
    _send_to(monkeypatch, printer, data)
    assert bytes(printer.received) == data             # nothing lost, nothing reordered
    assert printer.max_in_flight <= 7                  # never more than the printer granted


def test_send_falls_back_to_pacing_without_credits(monkeypatch):
    printer = FakePrinter(credits=False)
    data = b"\x1b\x40" * 1000
    _send_to(monkeypatch, printer, data)
    assert bytes(printer.received) == data


def test_paper_status_from_notifications():
    async def go():
        c = ble.Credits()
        c.feed(b"\x1a\x06\x89")
        assert ble.printer_state["paper"] is True
        c.feed(b"\x1a\x06\x88")
        assert ble.printer_state["paper"] is False
        c.feed(b"\x1a\x05\x98")                   # other status: ignored
        assert ble.printer_state["paper"] is False
    asyncio.run(go())


def test_stall_while_out_of_paper_says_so():
    async def go():
        c = ble.Credits()
        c.feed(b"\x1a\x06\x88")
        with pytest.raises(ConnectionError, match="out of paper"):
            await c.take(timeout=0.05)
        assert ble.printer_state["waiting_since"] is None
    asyncio.run(go())


class SheetPrinter(FakePrinter):
    """Reports paper out (1a 06 88) once `sheet_rows` raster rows have arrived."""

    def __init__(self, sheet_rows):
        super().__init__()
        self.sheet_rows, self.rows, self.reported, self.after_out = sheet_rows, 0, False, 0

    async def write_gatt_char(self, char, data, response=False):
        await super().write_gatt_char(char, data, response)
        if self.reported:
            self.after_out += len(data)
        self.rows += data.count(b"\x1d\x76\x30") * 16     # eject blocks are 16 rows
        if not self.reported and self.rows >= self.sheet_rows:
            self.reported = True
            self.cb(self.notify, bytearray(b"\x1a\x06\x88"))


def test_eject_feeds_until_paper_out_then_stops(monkeypatch):
    printer = SheetPrinter(sheet_rows=150 * 8)          # tail passes the sensor at 150 mm
    async def find(target):
        return "dev", "M08F"
    async def connect(dev):
        return printer, "fake"
    monkeypatch.setattr(ble, "find_printer", find)
    monkeypatch.setattr(ble, "connect", connect)
    asyncio.run(ble.send(b"\x1b\x40", eject=True))
    assert printer.reported
    assert printer.after_out <= 7 * 244 + 3336          # stops within one block + in-flight


def test_eject_gives_up_at_the_cap(monkeypatch):
    printer = SheetPrinter(sheet_rows=10 ** 9)          # never reports paper out
    async def find(target):
        return "dev", "M08F"
    async def connect(dev):
        return printer, "fake"
    monkeypatch.setattr(ble, "find_printer", find)
    monkeypatch.setattr(ble, "connect", connect)
    asyncio.run(ble.send(b"\x1b\x40", eject=True))
    assert printer.rows == ble.EJECT_MAX_MM * 8


def test_no_eject_unless_asked(monkeypatch):
    printer = SheetPrinter(sheet_rows=8)
    async def find(target):
        return "dev", "M08F"
    async def connect(dev):
        return printer, "fake"
    monkeypatch.setattr(ble, "find_printer", find)
    monkeypatch.setattr(ble, "connect", connect)
    asyncio.run(ble.send(b"\x1b\x40"))
    assert printer.rows == 0


def test_packed_notifications_keep_every_credit():
    """A credit packed with a paper report in one notification must still count."""
    async def go():
        c = ble.Credits()
        c.feed(b"\x01\x07")
        c.feed(b"\x01\x01\x1a\x06\x89")
        c.feed(b"\x1a\x06\x89\x01\x01")
        c.feed(b"\x01\x01\x01\x01")
        assert c.available == 7 + 4
        assert ble.printer_state["paper"] is True
        c.feed(b"\x01\x01\x1a\x06\x88")
        assert c.paper_out and c.available == 12
    asyncio.run(go())


def test_eject_stall_means_sheet_out_not_failure(monkeypatch):
    """Sheet leaves without a 1a 06 88: the printer just stops granting credit."""
    printer = SheetPrinter(sheet_rows=10 ** 9)
    printer.stop_after = 40                          # packets, then no more credits
    real = printer._printed

    def printed():
        if len(printer.received) // 244 < printer.stop_after:
            real()
        else:
            printer.in_flight -= 1
    printer._printed = printed
    monkeypatch.setattr(ble, "EJECT_STALL", 0.05)
    async def find(target):
        return "dev", "M08F"
    async def connect(dev):
        return printer, "fake"
    monkeypatch.setattr(ble, "find_printer", find)
    monkeypatch.setattr(ble, "connect", connect)
    asyncio.run(ble.send(b"\x1b\x40", eject=True))   # returns normally, no ConnectionError


def _fake_link(monkeypatch, printer):
    async def find(target):
        return "dev", "M08F"
    async def connect(dev):
        return printer, "fake"
    monkeypatch.setattr(ble, "find_printer", find)
    monkeypatch.setattr(ble, "connect", connect)


def test_no_paper_refuses_before_sending_anything(monkeypatch):
    """Without paper the M08F swallows a whole job and powers off: ask first."""
    printer = FakePrinter()
    printer.paper = False
    _fake_link(monkeypatch, printer)
    with pytest.raises(ConnectionError, match="No paper"):
        asyncio.run(ble.send(b"\x1b\x40" * 500))
    assert printer.received == bytearray()


def test_paper_check_can_be_informational(monkeypatch):
    printer = FakePrinter()
    printer.paper = False
    _fake_link(monkeypatch, printer)
    asyncio.run(ble.send(b"\x1b\x40", require_paper=False))   # the UI's connection check
    assert bytes(printer.received) == b"\x1b\x40"
    assert ble.printer_state["paper"] is False


def test_silent_printer_still_prints(monkeypatch):
    printer = FakePrinter()
    printer.paper = None
    monkeypatch.setattr(ble.asyncio, "wait_for", _instant_timeout)
    _fake_link(monkeypatch, printer)
    asyncio.run(ble.send(b"\x1b\x40"))
    assert bytes(printer.received) == b"\x1b\x40"


async def _instant_timeout(aw, timeout):
    if hasattr(aw, "close"):
        aw.close()
    raise asyncio.TimeoutError


def test_version_reply_is_five_bytes():
    async def go():
        c = ble.Credits()
        c.feed(b"\x1a\x07\x01\x02\x02\x01\x01")      # version, then a credit
        assert c.available == 1
    asyncio.run(go())


def test_late_no_paper_report_stops_the_job(monkeypatch):
    """The paper answer can arrive after the wait: stop as soon as it does."""
    printer = FakePrinter()
    printer.paper = None                              # query unanswered in time...
    monkeypatch.setattr(ble.asyncio, "wait_for", _instant_timeout)
    real = printer.write_gatt_char

    async def late_report(char, data, response=False):
        await real(char, data, response)
        if len(printer.received) == 244:              # ...then 'no paper' after packet 1
            printer.cb(printer.notify, bytearray(b"\x1a\x06\x88"))
    printer.write_gatt_char = late_report
    ble.printer_state["paper"] = None
    _fake_link(monkeypatch, printer)
    with pytest.raises(ConnectionError, match="No paper"):
        asyncio.run(ble.send(bytes(244 * 100)))
    assert len(printer.received) <= 2 * 244


class DroppingPrinter(FakePrinter):
    """Drops the link (bleak's error) on its first `drops` connections, before data."""
    connections = 0

    def __init__(self, drops):
        super().__init__()
        self.drops = drops

    async def write_gatt_char(self, char, data, response=False):
        if DroppingPrinter.connections <= self.drops:
            raise BleakError("Service Discovery has not been performed yet")
        await super().write_gatt_char(char, data, response)


def _counting_link(monkeypatch, printer):
    async def find(target):
        return "dev", "M08F"
    async def connect(dev):
        DroppingPrinter.connections += 1
        return printer, "fake"
    monkeypatch.setattr(ble, "find_printer", find)
    monkeypatch.setattr(ble, "connect", connect)


def test_link_drop_before_data_reconnects(monkeypatch):
    DroppingPrinter.connections = 0
    printer = DroppingPrinter(drops=1)
    _counting_link(monkeypatch, printer)
    asyncio.run(ble.send(b"\x1b\x40" * 300))
    assert DroppingPrinter.connections == 2
    assert bytes(printer.received) == b"\x1b\x40" * 300


def test_link_that_keeps_dropping_gives_a_plain_error(monkeypatch):
    DroppingPrinter.connections = 0
    printer = DroppingPrinter(drops=99)
    _counting_link(monkeypatch, printer)
    with pytest.raises(ConnectionError, match="keeps dropping the Bluetooth connection"):
        asyncio.run(ble.send(b"\x1b\x40"))
    assert DroppingPrinter.connections == ble.LINK_RETRIES + 1


def test_link_drop_mid_job_is_not_retried(monkeypatch):
    """Retrying after data went out would print part of the page twice."""
    printer = FakePrinter()
    real = printer.write_gatt_char

    async def drop_after_some(char, data, response=False):
        if len(printer.received) >= 244 * 10:
            raise BleakError("disconnected")
        await real(char, data, response)
    printer.write_gatt_char = drop_after_some
    _fake_link(monkeypatch, printer)
    with pytest.raises(ConnectionError, match="dropped the Bluetooth connection after"):
        asyncio.run(ble.send(bytes(244 * 50)))
