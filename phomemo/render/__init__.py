"""Any supported file -> list of print-ready 1-bit pages.

Every page that leaves here is exactly spec.WIDTH_DOTS wide, Pillow mode
"1", and, on sheet media, exactly one sheet tall. Content type picks the
dither: documents get a hard threshold, photos get error diffusion.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .. import raster, spec
from . import images, markdown, pdf

KINDS = ("pdf", "markdown", "text", "image")

_EXT = {
    ".pdf": "pdf",
    ".md": "markdown", ".markdown": "markdown", ".mdown": "markdown",
    ".txt": "text", ".text": "text", ".log": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".bmp": "image", ".tif": "image", ".tiff": "image", ".webp": "image",
}
_MIME_PREFIX = {
    "application/pdf": "pdf", "text/markdown": "markdown",
    "text/plain": "text", "image/": "image",
}
DEFAULT_DITHER = {"pdf": "hybrid", "markdown": "threshold",
                  "text": "threshold", "image": "atkinson"}


@dataclass
class RenderOptions:
    media: str = "a4"
    dither: str | None = None        # None = pick by content kind
    threshold: int = 160
    font_size: float = 11.0
    margin_mm: float = 12.0
    monospace: bool = False          # plain text only
    fit: str = "page"                # images: page | width
    rotate: str = "auto"             # images: auto | 0 | 90 | 180 | 270
    gamma: float = 0.85              # images: <1 brightens for dot gain
    antialias: bool = False          # PDFs: on helps photo-heavy PDFs


def _sniff(path: str) -> str | None:
    """Identify a file by its first bytes; IPP jobs can arrive untyped."""
    try:
        head = Path(path).read_bytes()[:16]
    except OSError:
        return None
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM")) or head[8:12] == b"WEBP":
        return "image"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return None


def detect_kind(path: str, content_type: str | None = None) -> str:
    ext = Path(path).suffix.lower()
    if ext in _EXT:
        return _EXT[ext]
    ctype = content_type or mimetypes.guess_type(path)[0] or ""
    for prefix, kind in _MIME_PREFIX.items():
        if ctype.startswith(prefix):
            return kind
    sniffed = _sniff(path)
    if sniffed:
        return sniffed
    raise ValueError(f"don't know how to print {path!r} (type {ctype or 'unknown'}); "
                     "pass --type pdf|markdown|text|image")


def _grey_pages(path: str, kind: str, opts: RenderOptions) -> list[tuple[Image.Image, list]]:
    """(grey page, image boxes) pairs. Boxes are only known for PDFs."""
    page_h = spec.page_height_dots(opts.media)
    if kind == "pdf":
        return list(pdf.pdf_pages(path, antialias=opts.antialias))
    if kind in ("markdown", "text"):
        text = Path(path).read_text(encoding="utf-8", errors="replace") if path != "-" \
            else __import__("sys").stdin.read()
        body = markdown.markdown_to_html(text) if kind == "markdown" \
            else markdown.text_to_html(text, monospace=opts.monospace)
        doc = markdown.html_to_pdf(body, page=opts.media, margin_mm=opts.margin_mm,
                                   font_size=opts.font_size)
        return [(g, []) for g, _ in pdf.pdf_pages(doc)]
    if kind == "image":
        img = images.load(path)
        return [(images.prepare(img, page_h, fit=opts.fit, rotate=opts.rotate,
                                gamma=opts.gamma), [])]
    raise ValueError(f"unknown kind {kind!r}")


def paginate(img1: Image.Image, page_h: int | None) -> list[Image.Image]:
    """Sheets: split and pad to exact sheet height, so page N lands on sheet N.
    Continuous: trim trailing white instead."""
    if page_h is None:
        return [raster.trim_bottom(img1, keep=spec.mm_to_dots(4))]
    pages = []
    for top in range(0, img1.height, page_h):
        chunk = img1.crop((0, top, img1.width, min(img1.height, top + page_h)))
        if chunk.height < page_h:
            sheet = Image.new("1", (img1.width, page_h), 255)
            sheet.paste(chunk, (0, 0))
            chunk = sheet
        pages.append(chunk)
    return pages


def render(path: str, opts: RenderOptions | None = None, kind: str | None = None,
           content_type: str | None = None) -> list[Image.Image]:
    opts = opts or RenderOptions()
    kind = kind or detect_kind(path, content_type)
    dither = opts.dither or DEFAULT_DITHER[kind]
    page_h = spec.page_height_dots(opts.media)
    out: list[Image.Image] = []
    for grey, boxes in _grey_pages(path, kind, opts):
        if dither == "hybrid":
            img1 = raster.hybrid(grey, boxes, opts.threshold, gamma=opts.gamma)
        else:
            img1 = raster.to_1bit(grey, dither, opts.threshold)
        out.extend(paginate(img1, page_h))
    return out
