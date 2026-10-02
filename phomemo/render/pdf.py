"""PDF -> 8-bit grey pages, rendered at exactly the printer's width.

Rendering straight to 1664 dots (about 201 dpi for A4) and switching off
anti-aliasing means every glyph is rasterised directly onto the printer's
dot grid. Rendering at 203 dpi and resizing afterwards, as many drivers do,
resamples text that is already sharp and leaves it smeared.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import fitz  # PyMuPDF
from PIL import Image

from .. import spec


@contextmanager
def _aa(level: int):
    """PyMuPDF's anti-aliasing level is process-global; scope the change."""
    before = fitz.TOOLS.show_aa_level()
    fitz.TOOLS.set_aa_level(level)
    try:
        yield
    finally:
        fitz.TOOLS.set_aa_level(before["text"])
        fitz.TOOLS.set_graphics_min_line_width(before["graphics_min_line_width"])


def _exact_width(img: Image.Image, width: int) -> Image.Image:
    """Rounding can give width+-1; pad or crop rather than resample."""
    if img.width == width:
        return img
    canvas = Image.new("L", (width, img.height), 255)
    canvas.paste(img.crop((0, 0, min(width, img.width), img.height)), (0, 0))
    return canvas


def pdf_pages(source: str | bytes, width: int = spec.WIDTH_DOTS,
              antialias: bool = False) -> Iterator[tuple[Image.Image, list[tuple[int, int, int, int]]]]:
    """Yield (grey page, image boxes) where boxes are (x0, y0, x1, y1) in dots.

    The boxes let the caller dither photos while thresholding the text around
    them; no single dither suits a page that has both.
    """
    doc = fitz.open("pdf", source) if isinstance(source, (bytes, bytearray)) else fitz.open(source)
    with doc, _aa(8 if antialias else 0):
        if not antialias:
            # Hairlines thinner than one dot vanish on thermal paper; make
            # every stroke at least ~1 dot wide at this zoom.
            fitz.TOOLS.set_graphics_min_line_width(0.4)
        for page in doc:
            zoom = width / page.rect.width
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                  colorspace=fitz.csGRAY, alpha=False)
            img = _exact_width(Image.frombytes("L", (pix.width, pix.height), pix.samples), width)
            boxes = []
            for info in page.get_image_info():
                r = fitz.Rect(info["bbox"]) & page.rect     # clip to the page
                if r.is_empty:
                    continue
                boxes.append((max(0, int(r.x0 * zoom)), max(0, int(r.y0 * zoom)),
                              min(img.width, int(r.x1 * zoom + 0.999)),
                              min(img.height, int(r.y1 * zoom + 0.999))))
            yield img, boxes
