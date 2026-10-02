"""A calibration sheet. Print this first: it answers every question about
how your particular printer and paper behave, on one page."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import raster, spec

_FONT_DIR = Path("/System/Library/Fonts/Supplemental")


def _font(pt: float, bold: bool = False) -> ImageFont.FreeTypeFont:
    px = max(6, round(pt * spec.DPI / 72))
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    try:
        return ImageFont.truetype(str(_FONT_DIR / name), px)
    except OSError:
        return ImageFont.load_default(size=px)


def build(density: int = 5) -> Image.Image:
    W, H = spec.WIDTH_DOTS, spec.page_height_dots("a4")
    page = Image.new("1", (W, H), 255)
    d = ImageDraw.Draw(page)
    x0, y = 40, 30

    # Printable-area border: if any edge is missing on paper, the true
    # printable width is narrower than 1664 dots.
    d.rectangle((0, 0, W - 1, H - 1), outline=0, width=1)

    # Millimetre ruler. Measure 100 -> 200 with a real ruler to check scale.
    for mm in range(0, int(W / spec.DOTS_PER_MM) + 1):
        x = round(mm * spec.DOTS_PER_MM)
        if x >= W:
            break
        tick = 28 if mm % 10 == 0 else 18 if mm % 5 == 0 else 10
        d.line((x, 0, x, tick), fill=0, width=2 if mm % 10 == 0 else 1)
        if mm % 10 == 0 and 0 < mm < 205:
            d.text((x + 3, 30), str(mm), font=_font(7), fill=0)
    y = 80

    d.text((x0, y), "Phomemo M08F calibration", font=_font(22, bold=True), fill=0)
    y += 80
    info = (f"density {density}   |   {spec.WIDTH_DOTS} dots = "
            f"{spec.WIDTH_DOTS / spec.DOTS_PER_MM:.0f} mm   |   {spec.DPI} dpi   |   "
            f"{dt.date.today().isoformat()}")
    d.text((x0, y), info, font=_font(10), fill=0)
    y += 55
    d.text((x0, y), "Ruler: 100 to 200 should measure exactly 100 mm. "
           "All four border edges should be visible.", font=_font(9), fill=0)
    y += 70

    # Dither comparison on an identical grey ramp.
    d.text((x0, y), "Dithering (same ramp, four methods)", font=_font(13, bold=True), fill=0)
    y += 60
    ramp = Image.linear_gradient("L").rotate(90).resize((W - 2 * x0, 110))
    for method in raster.DITHERS:
        d.text((x0, y), method, font=_font(10, bold=True), fill=0)
        y += 40
        page.paste(raster.to_1bit(ramp, method), (x0, y))
        y += 125

    # Stroke weights: the thinnest one that prints solidly is your minimum.
    y += 15
    d.text((x0, y), "Stroke weight (dots)", font=_font(13, bold=True), fill=0)
    y += 60
    for w in (1, 2, 3, 4, 6, 8):
        d.text((x0, y - 12), f"{w}", font=_font(9), fill=0)
        d.line((x0 + 60, y, W - x0, y), fill=0, width=w)
        y += 32

    # Fine detail: a 1-dot checkerboard reveals head or paper problems.
    y += 25
    d.text((x0, y), "1-dot and 2-dot checkerboards", font=_font(13, bold=True), fill=0)
    y += 60
    for cell, ox in ((1, x0), (2, x0 + 760)):
        for yy in range(0, 120):
            for xx in range(0, 700):
                if ((xx // cell) + (yy // cell)) % 2 == 0:
                    page.putpixel((ox + xx, y + yy), 0)
    y += 150

    # Type sizes, rendered through the real document pipeline (PyMuPDF, AA
    # off, threshold) rather than Pillow. Pillow's hinting snaps stems to 1 or
    # 2 dots unevenly by size, which would misrepresent what documents get.
    d.text((x0, y), "Type sizes (as documents render them)", font=_font(13, bold=True), fill=0)
    y += 60
    page.paste(_type_samples(W - 2 * x0), (x0, y))
    return page


def _type_samples(width: int) -> Image.Image:
    from .render import markdown as md, pdf

    sample = "The quick brown fox jumps over the lazy dog 0123456789"
    rows = [f'<p style="font-size:{pt}pt;margin:0 0 3pt 0">{pt}pt&#160;&#160;{sample}</p>'
            for pt in (6, 7, 8, 9, 10, 11, 12, 14, 18)]
    rows.append(f'<p style="font-size:11pt;font-weight:bold;margin:0 0 3pt 0">'
                f'11pt bold&#160;&#160;{sample}</p>')
    rows.append(f'<p style="font-size:10pt;font-family:monospace;font-weight:bold;margin:0">'
                f'10pt mono bold&#160;&#160;def hello(): return 42</p>')
    grey, _ = next(pdf.pdf_pages(md.html_to_pdf("".join(rows), margin_mm=0)))
    img1 = raster.to_1bit(grey, "threshold")
    from PIL import ImageOps
    bbox = ImageOps.invert(img1.convert("L")).getbbox()
    return img1.crop((0, 0, width, bbox[3] + 4 if bbox else 10))
