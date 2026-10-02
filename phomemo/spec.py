"""Hardware constants for the Phomemo M08F (also sold as COLORWING / AIMO M08F).

Verified against M-Wham/m08f-printer (MIT) and the vendor spec sheet.
The print head is 1728 dots wide, but 1664 is the width the firmware
accepts cleanly and it divides evenly into 208 bytes per raster line.
"""
from __future__ import annotations


DPI = 203
DOTS_PER_MM = 8.0          # the vendor's own rounding; 203dpi is 7.992
WIDTH_DOTS = 1664          # usable raster width
BYTES_PER_LINE = WIDTH_DOTS // 8   # 208
HEAD_DOTS = 1728           # physical head width, for reference

BAUD = 115200
USB_VID = 0x0483           # STM32 virtual COM port
USB_PID = 0x5740

# GS v 0 can address 65535 lines, but the firmware's buffer is small.
# 255-line bands are what the reference driver uses and they work.
MAX_BLOCK_LINES = 255

# "continuous" is fanfold or any feed where you want no page breaks:
# output is trimmed instead of padded to a sheet length.
PAGE_SIZES_MM = {
    "a4": (210.0, 297.0),
    "letter": (215.9, 279.4),
    "continuous": (210.0, None),
}


def mm_to_dots(mm: float) -> int:
    return int(round(mm * DOTS_PER_MM))


def page_height_dots(page: str) -> int | None:
    """Height in dots, or None for continuous media."""
    h = PAGE_SIZES_MM[page][1]
    return None if h is None else mm_to_dots(h)
