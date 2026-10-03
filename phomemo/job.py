"""Assemble print-ready pages into one ESC/POS byte stream."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PIL import Image

from . import escpos, raster, spec


@dataclass
class JobOptions:
    density: int = 5           # 1-8
    feed: int | None = None    # None: 0 for sheets (already padded), 3 for continuous
    cut: str = "none"          # none | partial | full
    copies: int = 1


# The M08F's paper sensor sits ahead of the print head: once a sheet's trailing
# edge passes it (about 261 of 297 mm down an A4 sheet, measured 2026-10-03) the
# printer stops and waits for the next sheet, so the last ~36 mm can't print.
# Blank lines sent for that zone keep the job open and the sheet stuck halfway.
# The final page therefore ends where its content does.
END_MARGIN_MM = 4


def sequence(pages: Sequence[Image.Image], copies: int, media: str):
    """Pages in send order. On sheets, the very last one is trimmed to its content."""
    out = [p for _ in range(max(1, copies)) for p in pages]
    if out and spec.page_height_dots(media) is not None:
        out[-1] = raster.trim_bottom(out[-1], keep=spec.mm_to_dots(END_MARGIN_MM))
    return out


def build(pages: Sequence[Image.Image], opts: JobOptions, media: str = "a4") -> bytes:
    for i, page in enumerate(pages):
        if page.mode != "1" or page.width != spec.WIDTH_DOTS:
            raise ValueError(f"page {i + 1} is {page.mode} {page.width}px; "
                             f"expected mode '1' at {spec.WIDTH_DOTS}px")
    feed = opts.feed if opts.feed is not None else \
        (3 if spec.page_height_dots(media) is None else 0)

    out = bytearray()
    out += escpos.initialize()
    out += escpos.density(opts.density)
    for page in sequence(pages, opts.copies, media):
        for wb, h, data in raster.bands(page):
            out += escpos.raster_block(wb, h, data)
    out += escpos.feed(feed)
    out += escpos.cut(opts.cut)
    return bytes(out)
