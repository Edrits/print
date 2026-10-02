"""ESC/POS byte-sequence builders. Pure functions; every one returns bytes.

Command set confirmed against M-Wham/m08f-printer (MIT licence).
"""
from . import spec


def initialize() -> bytes:
    """ESC @ - reset the printer to its power-on state."""
    return b"\x1b\x40"


def density(level: int) -> bytes:
    """GS ( K - print darkness, 1 (lightest) to 8 (darkest).

    Layout: GS ( K pL pH fn m  with pL=2 pH=0 fn=49.
    """
    level = max(1, min(8, int(level)))
    return b"\x1d\x28\x4b\x02\x00\x31" + bytes([level])


def feed(lines: int) -> bytes:
    """ESC d n - advance n lines."""
    lines = max(0, min(255, int(lines)))
    return b"\x1b\x64" + bytes([lines])


def cut(mode: str) -> bytes:
    """GS V - paper cut. Harmless no-op on units without a cutter."""
    if mode == "full":
        return b"\x1d\x56\x00"
    if mode == "partial":
        return b"\x1d\x56\x01"
    return b""


def raster_block(width_bytes: int, height: int, data: bytes) -> bytes:
    """GS v 0 - raster bit image. Bit set = dot fired (black).

    Layout: GS v 0 m xL xH yL yH [data], m=0 for normal scale.
    """
    if height > 0xFFFF:
        raise ValueError(f"raster block height {height} exceeds 65535")
    expected = width_bytes * height
    if len(data) != expected:
        raise ValueError(f"raster payload is {len(data)} bytes, expected {expected}")
    header = b"\x1d\x76\x30\x00" + bytes([
        width_bytes & 0xFF, (width_bytes >> 8) & 0xFF,
        height & 0xFF, (height >> 8) & 0xFF,
    ])
    return header + data


def status_query() -> bytes:
    """DLE EOT 1 - real-time status request."""
    return b"\x10\x04\x01"
