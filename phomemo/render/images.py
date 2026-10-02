"""Raster images -> 8-bit grey, sized for the page."""
from __future__ import annotations

from PIL import Image, ImageOps

from .. import raster, spec


def load(path: str) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)   # honour phone camera orientation
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)                      # animated GIF/WebP: first frame
    return img


def prepare(img: Image.Image, page_height: int | None, fit: str = "page",
            rotate: str = "auto", autocontrast: bool = True,
            gamma: float = 0.85) -> Image.Image:
    """Return 8-bit grey at exactly spec.WIDTH_DOTS wide.

    gamma < 1 brightens. Thermal dots bleed into their neighbours (dot gain),
    so photos print darker than they look on screen; 0.85 compensates.
    """
    if rotate == "auto":
        # A landscape photo on portrait A4 wastes two-thirds of the sheet.
        if page_height is not None and img.width > img.height * 1.15:
            img = img.rotate(90, expand=True)
    elif rotate in ("90", "180", "270"):
        img = img.rotate(int(rotate), expand=True)

    grey = raster.to_grey(img, autocontrast=autocontrast, gamma=gamma)
    width = spec.WIDTH_DOTS

    if fit == "page" and page_height is not None:
        scale = min(width / grey.width, page_height / grey.height)
        size = (max(1, round(grey.width * scale)), max(1, round(grey.height * scale)))
        grey = grey.resize(size, Image.LANCZOS)
        canvas = Image.new("L", (width, grey.height), 255)
        canvas.paste(grey, ((width - grey.width) // 2, 0))   # centre horizontally
        return canvas
    return raster.fit_width(grey, width)
