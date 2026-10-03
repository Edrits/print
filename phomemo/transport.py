"""The only layer that touches the serial port.

The M08F enumerates as an STM32 virtual COM port (0483:5740). On macOS that
is /dev/cu.usbmodem*; on Linux /dev/ttyACM*. The printer must be in "solid
red" mode (hold power ~3s) before it shows up at all.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

from . import spec


class PrinterNotFound(Exception):
    pass


def discover() -> list[str]:
    """Device paths that look like an M08F, best match first."""
    from serial.tools import list_ports

    exact, probable = [], []
    for p in list_ports.comports():
        if p.vid == spec.USB_VID and p.pid == spec.USB_PID:
            exact.append(p.device)
        elif "usbmodem" in p.device or "ttyACM" in p.device:
            probable.append(p.device)
    return exact + probable


def autodetect() -> str:
    found = discover()
    if not found:
        raise PrinterNotFound(
            "No M08F found.\n"
            "  1. Connect it over USB.\n"
            "  2. Hold the power button ~3s until the light is SOLID RED.\n"
            "     (Blinking blue is Bluetooth mode - USB will not enumerate.)\n"
            "  3. Re-run, or pass --device /dev/cu.usbmodemXXXX explicitly."
        )
    return found[0]


class SerialPort:
    def __init__(self, ser):
        self._ser = ser

    def send(self, data: bytes) -> None:
        self._ser.write(data)
        self._ser.flush()

    def close(self) -> None:
        self._ser.close()


class FilePort:
    """Dry-run sink: captures the job instead of printing it."""

    def __init__(self, path: Path | None):
        self.path = path
        self.buffer = bytearray()

    def send(self, data: bytes) -> None:
        self.buffer += data

    def close(self) -> None:
        if self.path is not None:
            self.path.write_bytes(bytes(self.buffer))
            print(f"Wrote {len(self.buffer):,} bytes to {self.path}", file=sys.stderr)


@contextmanager
def open_port(device: str | None = None, baud: int = spec.BAUD,
              dry_run_path: Path | None = None, dry_run: bool = False,
              ble: str | None = None, ble_rate: int | None = None,
              ble_eject: bool = False, ble_require_paper: bool = True,
              ble_isolate: bool = False):
    """Yield something with .send(bytes). Never raises on dry runs.

    ble: None for USB; "auto" to find the printer by name; or a name/address.
    ble_eject: after the job, feed until the printer runs the sheet out.
    """
    if dry_run or dry_run_path is not None:
        port = FilePort(dry_run_path)
        try:
            yield port
        finally:
            port.close()
        return

    if ble is not None:
        from .ble import DEFAULT_RATE, BlePort
        port = BlePort(None if ble == "auto" else ble, ble_rate or DEFAULT_RATE,
                       eject=ble_eject, require_paper=ble_require_paper, isolate=ble_isolate)
        try:
            yield port
        finally:
            port.close()
        return

    import serial

    target = device or autodetect()
    try:
        ser = serial.Serial(target, baud, timeout=2)
    except (FileNotFoundError, OSError) as e:
        raise PrinterNotFound(
            f"Could not open {target}: {e}\n"
            "Is the printer in solid-red USB mode?"
        ) from e
    port = SerialPort(ser)
    try:
        yield port
    finally:
        port.close()
