"""Bluetooth LE transport (experimental).

Other Phomemo models expose GATT service 0xff00 with write characteristic
0xff02 and notifications on 0xff01/0xff03. The M08F's layout is unconfirmed,
so the write channel is discovered rather than hard-coded: prefer ff02, else
any characteristic that accepts write-without-response.

Bluetooth on macOS needs the *responsible app* to declare Bluetooth usage.
Run this from Terminal.app (and allow the prompt); from an app that doesn't
declare it, macOS kills the process with SIGABRT.

Flow control is credit-based, on notify characteristic ff03 (measured on an
M08F, 2026-10-03): on connect the printer sends `01 07` (7 packets of credit)
and `02 f4 00` (max packet 0x00f4 = 244 bytes), then `01 01` (+1 credit) for
every packet it takes in. While printing it holds credits back until its
buffer has room, so writes must wait for credit: sending at a fixed rate
overflowed the buffer and dropped about a quarter of an A4 calibration page.
Printers that never grant credit fall back to pacing at `rate` bytes/second.

The printer also stops granting credit, sometimes for tens of seconds, while it
pauses mid-page (seen after dense areas; it resumes on its own), so a stall is
waited out rather than treated as a failure.

Status on ff03: `1a 06 89` paper loaded, `1a 06 88` no paper (observed with a
sheet in and with it removed). The printer sends these by itself only while
printing, but answers the query `1f 11 11` at any time, matching its light
(green = loaded, red = none). Without paper it accepts and discards a whole
job, then powers off, so every print asks first. `10 04 01` answers
`1a 07 01 02 02` (probably firmware 1.2.2). Other `1a ..` messages are logged.
"""
from __future__ import annotations

import asyncio
import sys
import time

PRINTER_HINTS = ("M08F", "PHOMEMO", "COLORWING", "AIMO")

# The M08F advertises 0x1812 (HID) and 0xaf30 (vendor). Discovering the HID
# service makes macOS try to pair; if that pairing doesn't complete, macOS
# drops the link mid-discovery. So discover only these candidate data services
# and never touch HID: af30 (advertised by the M08F), ff00 (other Phomemo
# models), ae30 (MX-series printers), 18f0 (common BLE ESC/POS), ISSC and
# Nordic transparent UARTs.
_BASE = "-0000-1000-8000-00805f9b34fb"
DATA_SERVICES = [
    "0000af30" + _BASE, "0000ff00" + _BASE, "0000ae30" + _BASE, "000018f0" + _BASE,
    "49535343-fe7d-4ae5-8fa9-9fafd205e455", "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
]
PREFERRED_WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"
DEFAULT_RATE = 20_000          # bytes/s


def _short(uuid: str) -> str:
    u = uuid.lower()
    return u[4:8] if u.endswith("-0000-1000-8000-00805f9b34fb") else u


def looks_like_printer(name: str | None) -> bool:
    return bool(name) and any(h in name.upper() for h in PRINTER_HINTS)


def pick_write_char(services):
    """Choose the characteristic to stream print data to.

    Order: the known Phomemo ff02; then write-without-response (fast, what
    printers use for bulk data); then plain write.
    """
    chars = [c for s in services for c in s.characteristics]
    for c in chars:
        if c.uuid.lower() == PREFERRED_WRITE:
            return c
    for prop in ("write-without-response", "write"):
        for c in chars:
            if prop in c.properties:
                return c
    return None


async def connect(dev):
    """Connect, discovering only data services; fall back to full discovery.

    Returns (client, strategy). The caller must disconnect the client.
    """
    from bleak import BleakClient
    from bleak.exc import BleakError

    errors = []
    for attempt, services in enumerate((DATA_SERVICES, DATA_SERVICES, None)):
        client = BleakClient(dev, services=services, timeout=15.0)
        try:
            await client.connect()
            return client, ("targeted" if services else "full") + f" discovery, try {attempt + 1}"
        except (BleakError, asyncio.TimeoutError) as e:
            errors.append(f"{'targeted' if services else 'full'}: {e or type(e).__name__}")
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(1.5)
    raise ConnectionError(
        "Found the printer but couldn't hold a connection (" + "; ".join(errors) + ").\n"
        "  * Close the Phomemo app on your phone; it may be holding the printer.\n"
        "  * If macOS shows a connection/pairing request naming M08F, accept it,\n"
        "    then run the command again.\n"
        "  * Power-cycle the printer (normal blue mode).")


def chunks(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield data[i:i + size]


async def _discover(timeout: float):
    """BleakScanner.discover, tolerant of macOS's first-run permission prompt.

    Bleak waits only 1s for CoreBluetooth to report its state. While the
    "would like to use Bluetooth" prompt is on screen the state is "unknown",
    which bleak reports as "Bluetooth device is turned off". Retry while the
    user answers, then translate bleak's errors into actionable ones.
    """
    from bleak import BleakScanner
    from bleak.exc import BleakError

    last = None
    for attempt in range(8):                     # ~16s to answer the prompt
        try:
            return await BleakScanner.discover(timeout=timeout, return_adv=True)
        except BleakError as e:
            last = e
            if "turned off" not in str(e):
                break
            if attempt == 0:
                print("Waiting for Bluetooth (allow the macOS prompt if one appeared)...",
                      file=sys.stderr, flush=True)
            await asyncio.sleep(2.0)
    msg = str(last)
    if "not authorized" in msg or "turned off" in msg:
        raise ConnectionError(
            f"Bluetooth unavailable ({msg}).\n"
            "  * If Bluetooth is on: System Settings > Privacy & Security > Bluetooth,\n"
            "    enable Terminal, then quit and reopen Terminal.\n"
            "  * Otherwise turn Bluetooth on in Control Centre.") from last
    raise ConnectionError(f"Bluetooth error: {msg}") from last


async def _system_connected():
    """Printers macOS itself is already connected to (e.g. after pairing in
    System Settings). Connected peripherals stop advertising, so a scan can't
    see them; CoreBluetooth can still hand them over. Bleak has no API for
    this, so build its BLEDevice the way its scanner does:
    details = (CBPeripheral, CentralManagerDelegate)."""
    from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate
    from bleak.backends.device import BLEDevice
    from bleak.exc import BleakError
    from CoreBluetooth import CBUUID

    for _ in range(8):                      # same first-run prompt race as _discover
        try:
            delegate = CentralManagerDelegate.alloc().init()
            break
        except BleakError as e:
            if "turned off" not in str(e):
                return []
            await asyncio.sleep(2.0)
    else:
        return []
    uuids = [CBUUID.UUIDWithString_(u) for u in DATA_SERVICES + ["1812"]]
    peripherals = delegate.central_manager.retrieveConnectedPeripheralsWithServices_(uuids)
    return [BLEDevice(p.identifier().UUIDString(), p.name(), (p, delegate))
            for p in peripherals or []]


async def find_printer(target: str | None, timeout: float = 10.0):
    for dev in await _system_connected():
        if target and target.lower() in (dev.address.lower(), (dev.name or "").lower()):
            return dev, dev.name
        if not target and looks_like_printer(dev.name):
            return dev, dev.name
    found = await _discover(timeout)
    for dev, adv in found.values():
        name = adv.local_name or dev.name
        if target and target.lower() in (dev.address.lower(), (name or "").lower()):
            return dev, name
        if not target and looks_like_printer(name):
            return dev, name
    return None, None


async def scan(timeout: float = 10.0, probe: bool = False) -> int:
    connected = [d for d in await _system_connected() if looks_like_printer(d.name)]
    for d in connected:
        print(f"  PRINTER  {d.name!r}  already connected to macOS  id={d.address}")
    found = await _discover(timeout)
    rows = sorted(((adv.rssi, adv.local_name or dev.name or "", dev, adv)
                   for dev, adv in found.values()), key=lambda r: -r[0])
    printers = [(0, d.name, d, None) for d in connected] + \
               [r for r in rows if looks_like_printer(r[1])]
    print(f"{len(rows)} BLE devices advertising.")
    for rssi, name, dev, adv in printers:
        if adv is not None:
            print(f"  PRINTER  {name!r}  rssi={rssi}  id={dev.address}  "
                  f"advertises={[_short(u) for u in adv.service_uuids]}")
    if not printers:
        print("  No printer-like names. Named devices nearby:")
        for rssi, name, dev, adv in rows:
            if name:
                print(f"    {name[:32]:32s} rssi={rssi:>4}  id={dev.address}")
        return 1
    if probe:
        rssi, name, dev, adv = printers[0]
        print(f"\nGATT layout of {name!r}:")
        client, how = await connect(dev)
        try:
            print(f"  (connected via {how})")
            for s in client.services:
                print(f"  service {_short(s.uuid)}")
                for c in s.characteristics:
                    print(f"    char {_short(c.uuid):6s} {','.join(c.properties)}"
                          f"  (max write-no-resp {c.max_write_without_response_size})")
            pick = pick_write_char(client.services)
            print(f"\nWould write print data to: {_short(pick.uuid) if pick else 'NOTHING WRITABLE'}")
        finally:
            await client.disconnect()
    return 0


PAPER_QUERY = b"\x1f\x11\x11"   # -> 1a 06 89 (loaded) / 1a 06 88 (none)
# A paper-out report this early in a job means no sheet was loaded; later ones
# are the sheet's tail passing the sensor (~261 mm down an A4 sheet).
NO_PAPER_WINDOW = 70 * 8 * 208    # bytes: ~70 mm of rows
EJECT_MAX_MM = 320            # blank feed cap when ejecting a sheet
EJECT_STALL = 8.0             # s without credit while ejecting = the sheet is out
CREDIT_TIMEOUT = 180.0        # s without credit before giving up; pauses of 30s+ are normal

# What the printer last told us, for the web UI. Updated from notifications.
printer_state = {"paper": None, "paper_at": None, "waiting_since": None}


class Credits:
    """Send credits granted by the printer's ff03 notifications.

    `01 NN` grants NN packets; `02 LL HH` announces the max packet size.
    Anything else (e.g. `1a ..` status) is ignored here.
    """

    def __init__(self):
        self.available = 0
        self.granted = 0             # total ever granted; 0 = printer doesn't do credits
        self.max_packet: int | None = None
        self.paper_out = False       # a `1a 06 88` arrived since this was last cleared
        self.paper_report = asyncio.Event()
        self._event = asyncio.Event()

    # One notification can carry several messages back to back (e.g. a credit
    # and a paper report), so it is parsed as a stream. Lengths by first byte.
    _LENGTHS = {0x01: 2, 0x02: 3, 0x1a: 3}

    def feed(self, payload: bytes) -> list[bytes]:
        """Apply every message in `payload`. Returns the messages that were not
        a plain one-packet credit, for logging; an unknown byte ends parsing."""
        notable, i = [], 0
        while i < len(payload):
            n = self._LENGTHS.get(payload[i])
            if payload[i] == 0x1a and payload[i + 1:i + 2] == b"\x07":
                n = 5                                    # 1a 07 v v v: version
            msg = payload[i:i + n] if n else payload[i:]
            if not n or len(msg) < n:
                notable.append(msg)
                break
            i += n
            if msg[0] == 0x01:
                self.available += msg[1]
                self.granted += msg[1]
                self._event.set()
                if msg[1] != 1:
                    notable.append(msg)
                continue
            notable.append(msg)
            if msg[0] == 0x02:
                self.max_packet = msg[1] | msg[2] << 8
            elif msg[1] == 0x06 and msg[2] in (0x88, 0x89):
                printer_state["paper"] = msg[2] == 0x89
                printer_state["paper_at"] = time.time()
                self.paper_out = msg[2] == 0x88
                self.paper_report.set()
        if len(payload) > 5 or (len(payload) == 3 and payload[0] == 0x01):
            notable.append(b"<packed " + payload.hex().encode() + b">")
        return notable

    async def take(self, timeout: float = CREDIT_TIMEOUT) -> None:
        if self.available <= 0:
            printer_state["waiting_since"] = time.time()
        try:
            while self.available <= 0:
                self._event.clear()
                try:
                    await asyncio.wait_for(self._event.wait(), timeout)
                except asyncio.TimeoutError:
                    if printer_state["paper"] is False:
                        raise ConnectionError(
                            "The printer is out of paper. Load a sheet and print again.") from None
                    raise ConnectionError(
                        f"The printer stopped accepting data for {timeout:.0f}s. "
                        "Check it has paper and the lid is closed.") from None
        finally:
            printer_state["waiting_since"] = None
        self.available -= 1


async def ask_paper(client, char, credits: Credits, flow: bool, timeout: float = 6.0,
                    tries: int = 2):
    """True if a sheet is loaded, False if not, None if the printer doesn't answer.

    Right after connecting the printer can take several seconds to answer (seen
    2026-10-03: a 3 s wait missed it), so settle first, wait longer, ask twice."""
    await asyncio.sleep(1.0)
    for _ in range(tries):
        credits.paper_report.clear()
        if flow:
            await credits.take()
        await client.write_gatt_char(char, PAPER_QUERY, response=False)
        try:
            await asyncio.wait_for(credits.paper_report.wait(), timeout)
            return printer_state["paper"]
        except asyncio.TimeoutError:
            continue
    return None


async def _eject(client, char, credits: Credits, size: int, no_resp: bool) -> float:
    """Run the sheet out. The printer only feeds while it has rows to print and
    holds a sheet once a job ends, but once the paper sensor reports the sheet's
    tail (`1a 06 88`) it ejects the rest by itself. So: feed blank rows, 2 mm at
    a time, until that report, then stop. Returns mm fed."""
    from . import escpos, spec
    rows = 16
    blank = escpos.raster_block(spec.BYTES_PER_LINE, rows, bytes(spec.BYTES_PER_LINE * rows))
    credits.paper_out, mm = False, 0.0
    try:
        while not credits.paper_out and mm < EJECT_MAX_MM:
            for piece in chunks(blank, size):
                await credits.take(timeout=EJECT_STALL)
                await client.write_gatt_char(char, piece, response=not no_resp)
            mm += rows / spec.DOTS_PER_MM
    except ConnectionError:
        # The printer stops taking rows once the sheet has left. It doesn't always
        # send `1a 06 88` first (seen 2026-10-03), so a stall here means done.
        credits.paper_out = True
    return mm


async def send(data: bytes, target: str | None = None, rate: int = DEFAULT_RATE,
               eject: bool = False, require_paper: bool = True) -> None:
    """eject: after the data, feed until the printer runs the sheet out (sheet media).
    require_paper: refuse to send if the printer says no sheet is loaded."""
    dev, name = await find_printer(target)
    if dev is None:
        raise ConnectionError(
            "No Bluetooth printer found. Is it on (normal blue mode, not solid red)? "
            "Run `phomemo ble-scan` to see what's nearby.")
    print(f"Connecting to {name or dev.address}...", file=sys.stderr, flush=True)
    client, how = await connect(dev)
    print(f"  connected ({how})", file=sys.stderr, flush=True)
    try:
        char = pick_write_char(client.services)
        if char is None:
            raise ConnectionError("printer exposes no writable characteristic")
        no_resp = "write-without-response" in char.properties
        size = max(20, min(char.max_write_without_response_size or 20, 244)) if no_resp else 20

        credits = Credits()

        def on_notify(ch, payload: bytearray) -> None:
            for msg in credits.feed(bytes(payload)):   # plain +1 credits are chatter; log the rest
                text = msg.decode() if msg.startswith(b"<") else msg.hex()
                print(f"  printer> {_short(ch.uuid)}: {text}", file=sys.stderr, flush=True)

        for s in client.services:
            for c in s.characteristics:
                if "notify" in c.properties:
                    try:
                        await client.start_notify(c, on_notify)
                    except Exception:
                        pass
        for _ in range(20):                  # the opening grant arrives right after subscribing
            if credits.granted:
                break
            await asyncio.sleep(0.1)
        flow = credits.granted > 0
        if flow and credits.max_packet:
            size = max(20, min(size, credits.max_packet))
        print(f"  flow control: {'printer credits' if flow else f'paced at {rate:,} B/s'}",
              file=sys.stderr, flush=True)
        paper = await ask_paper(client, char, credits, flow)
        print(f"  paper: {'loaded' if paper else 'none' if paper is False else 'unknown (no reply)'}",
              file=sys.stderr, flush=True)
        if paper is False and require_paper:
            raise ConnectionError("No paper: load a sheet (the printer's light turns green) "
                                  "and print again. Nothing was sent.")
        confirmed = paper is True        # a sheet was seen at some point in this job
        credits.paper_out = False

        total, sent, start = len(data), 0, time.monotonic()
        last_report = 0.0
        for piece in chunks(data, size):
            confirmed = confirmed or printer_state["paper"] is True
            if require_paper and credits.paper_out and not confirmed and sent < NO_PAPER_WINDOW:
                # A late answer to the paper query, or the first report of the job:
                # no sheet was ever loaded. Stop before the printer swallows the job.
                raise ConnectionError(
                    f"No paper: the printer reported none after {sent:,} bytes, so printing "
                    "stopped. Load a sheet (the light turns green) and print again.")
            if flow:
                await credits.take()
            await client.write_gatt_char(char, piece, response=not no_resp)
            sent += len(piece)
            if not flow:   # pace: never get ahead of `rate` bytes/second
                ahead = sent / rate - (time.monotonic() - start)
                if ahead > 0:
                    await asyncio.sleep(ahead)
            now = time.monotonic()
            if now - last_report > 1.0 or sent == total:
                last_report = now
                print(f"\r  {sent * 100 // total:3d}%  {sent:,}/{total:,} bytes via "
                      f"{_short(char.uuid)} ({size}-byte chunks)", end="", file=sys.stderr, flush=True)
        print(f"\n  content: {time.monotonic() - start:.1f} s", file=sys.stderr, flush=True)
        if eject and flow:
            t = time.monotonic()
            mm = await _eject(client, char, credits, size, no_resp)
            print(f"  feed-out: {mm:.0f} mm in {time.monotonic() - t:.1f} s"
                  + ("" if credits.paper_out else " (no paper-out report; stopped feeding)"),
                  file=sys.stderr, flush=True)
        await asyncio.sleep(2.0)   # let the last packets drain before disconnecting
    finally:
        await client.disconnect()


class BlePort:
    """Same .send()/.close() shape as the serial port. Buffers, then transmits."""

    def __init__(self, target: str | None, rate: int = DEFAULT_RATE, eject: bool = False,
                 require_paper: bool = True):
        self.target, self.rate, self.buffer = target, rate, bytearray()
        self.eject, self.require_paper = eject, require_paper

    def send(self, data: bytes) -> None:
        self.buffer += data

    def close(self) -> None:
        if self.buffer:
            asyncio.run(send(bytes(self.buffer), self.target, self.rate, eject=self.eject,
                             require_paper=self.require_paper))
