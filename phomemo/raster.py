"""Turn any PIL image into M08F raster bands.

Thermal paper is strictly 1-bit: a dot is fired or it isn't. So every
image goes through one decision - how to map grey to black/white - and the
right answer depends on content:

  threshold  Text, line art, most PDFs. Crisp edges, no speckle.
  floyd      Photos. Smooth gradients, but can look muddy on thermal.
  atkinson   Photos, the thermal favourite. Diffuses only 3/4 of the
             error, so highlights stay clean and shadows stay punchy.
  hybrid     PDFs (default). Threshold for text, Atkinson inside each
             embedded image's bounding box.
  ordered    Photos and graphics. Regular 4x4 Bayer pattern. Thermal
             heads render clustered dots more reliably than scattered ones.
"""
from __future__ import annotations

from typing import Iterator

from PIL import Image, ImageOps

from . import spec

DITHERS = ("threshold", "atkinson", "floyd", "ordered")

# Bit order from Pillow's mode-"1" packer is MSB-first, matching ESC/POS,
# but Pillow uses 1=white and ESC/POS uses 1=fire. One lookup table fixes it.
_INVERT = bytes(255 - i for i in range(256))

_BAYER4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)


def fit_width(img: Image.Image, width: int = spec.WIDTH_DOTS) -> Image.Image:
    """Scale to exactly `width` dots wide, preserving aspect ratio."""
    if img.width == width:
        return img
    height = max(1, round(img.height * width / img.width))
    return img.resize((width, height), Image.LANCZOS)


def to_grey(img: Image.Image, autocontrast: bool = False, gamma: float = 1.0) -> Image.Image:
    """Flatten alpha onto white, convert to 8-bit grey, optionally tone-map."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, rgba)
    grey = img.convert("L")
    if autocontrast:
        grey = ImageOps.autocontrast(grey, cutoff=1)
    if gamma != 1.0:
        lut = [round(255 * (i / 255) ** gamma) for i in range(256)]
        grey = grey.point(lut)
    return grey


def _atkinson(grey: Image.Image) -> Image.Image:
    w, h = grey.size
    px = list(grey.tobytes())          # ints; error can push them outside 0-255
    for y in range(h):
        row = y * w
        for x in range(w):
            i = row + x
            old = px[i]
            new = 0 if old < 128 else 255
            px[i] = new
            err = (old - new) >> 3     # 1/8 to each of 6 neighbours = 3/4 total
            if err == 0:
                continue
            if x + 1 < w:
                px[i + 1] += err
                if x + 2 < w:
                    px[i + 2] += err
            if y + 1 < h:
                j = i + w
                if x > 0:
                    px[j - 1] += err
                px[j] += err
                if x + 1 < w:
                    px[j + 1] += err
                if y + 2 < h:
                    px[j + w] += err
    out = bytes(0 if v < 128 else 255 for v in px)
    return Image.frombytes("L", (w, h), out).convert("1", dither=Image.NONE)


def _ordered(grey: Image.Image) -> Image.Image:
    w, h = grey.size
    src = grey.tobytes()
    out = bytearray(w * h)
    for y in range(h):
        brow = _BAYER4[y & 3]
        thresholds = [(brow[x & 3] * 16 + 8) for x in range(4)]
        base = y * w
        for x in range(w):
            out[base + x] = 255 if src[base + x] >= thresholds[x & 3] else 0
    return Image.frombytes("L", (w, h), bytes(out)).convert("1", dither=Image.NONE)


def to_1bit(grey: Image.Image, dither: str = "threshold", threshold: int = 160) -> Image.Image:
    """Map an 8-bit grey image to Pillow mode "1" (0=black, 255=white).

    The default threshold of 160 (not 128) is deliberate: anti-aliased text
    edges are mid-grey, and keeping more of them black gives heavier, more
    legible strokes on thermal paper.
    """
    if dither == "threshold":
        return grey.point(lambda v: 255 if v >= threshold else 0).convert("1", dither=Image.NONE)
    if dither == "floyd":
        return grey.convert("1", dither=Image.FLOYDSTEINBERG)
    if dither == "atkinson":
        return _atkinson(grey)
    if dither == "ordered":
        return _ordered(grey)
    raise ValueError(f"unknown dither {dither!r}; choose from {', '.join(DITHERS)}")


def hybrid(grey: Image.Image, boxes, threshold: int = 160, photo_dither: str = "atkinson",
           gamma: float = 0.85) -> Image.Image:
    """Threshold the page for crisp text, then error-diffuse inside each box.

    Boxes are the image placements PyMuPDF reports. Photo regions also get the
    same dot-gain brightening as standalone images.
    """
    page = to_1bit(grey, "threshold", threshold)
    for box in boxes:
        x0, y0, x1, y1 = box
        if x1 - x0 < 8 or y1 - y0 < 8:      # icons, bullets, rules: leave crisp
            continue
        region = to_grey(grey.crop(box), gamma=gamma)
        page.paste(to_1bit(region, photo_dither), (x0, y0))
    return page


def pack(img1: Image.Image) -> bytes:
    """Mode "1" image -> ESC/POS raster payload, 1 bit per dot, MSB first."""
    if img1.mode != "1":
        raise ValueError(f"expected mode '1', got {img1.mode!r}")
    if img1.width % 8:
        raise ValueError(f"width {img1.width} must be a multiple of 8")
    return img1.tobytes().translate(_INVERT)


def bands(img1: Image.Image, max_lines: int = spec.MAX_BLOCK_LINES
          ) -> Iterator[tuple[int, int, bytes]]:
    """Yield (width_bytes, height, payload) bands sized for the firmware buffer."""
    data = pack(img1)
    wb = img1.width // 8
    for y in range(0, img1.height, max_lines):
        h = min(max_lines, img1.height - y)
        yield wb, h, data[y * wb:(y + h) * wb]


def trim_bottom(img1: Image.Image, keep: int = 0) -> Image.Image:
    """Drop trailing all-white rows (saves paper on continuous media)."""
    inverted = ImageOps.invert(img1.convert("L"))
    bbox = inverted.getbbox()
    if bbox is None:
        return img1.crop((0, 0, img1.width, 1))
    return img1.crop((0, 0, img1.width, min(img1.height, bbox[3] + keep)))
