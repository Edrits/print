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
    for _ in range(max(1, opts.copies)):
        for page in pages:
            for wb, h, data in raster.bands(page):
                out += escpos.raster_block(wb, h, data)
    out += escpos.feed(feed)
    out += escpos.cut(opts.cut)
    return bytes(out)
