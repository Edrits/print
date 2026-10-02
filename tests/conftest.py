"""Test fixtures are generated, not checked in: no third-party images, and
the suite runs on any machine."""
import math
import random
import shutil
import sys
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).parent))
REPO = Path(__file__).parent.parent


def _synthetic_photo(path: Path) -> None:
    """Photo-like: smooth gradients, soft shapes, sensor noise, full tonal range."""
    w, h = 1600, 1200
    img = Image.new("L", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            v = 128 + 90 * math.sin(x / 210) * math.cos(y / 170) + 40 * (x / w - 0.5)
            px[x, y] = max(0, min(255, int(v)))
    d = ImageDraw.Draw(img)
    rnd = random.Random(42)
    for _ in range(14):
        cx, cy, r = rnd.randrange(w), rnd.randrange(h), rnd.randrange(60, 220)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rnd.randrange(0, 256))
    img = img.filter(ImageFilter.GaussianBlur(18))
    noise = Image.effect_noise((w, h), 12)
    img = Image.blend(img, noise, 0.08)
    img.convert("RGB").save(path, quality=90)


def _invoice_pdf(path: Path, photo: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 70), "Invoice #2026-0142", fontsize=22, fontname="hebo")
    page.insert_text((50, 95), "Mixed PDF test: text, vector lines and an embedded photo.", fontsize=10)
    for i, w in enumerate((0.25, 0.5, 1, 2)):
        y = 120 + i * 14
        page.draw_line((50, y), (545, y), width=w)
        page.insert_text((50, y + 10), f"{w}pt rule", fontsize=7)
    y = 200
    for row in [("Item", "Qty", "Amount"), ("Thermal paper A4 x200", "2", "19.98"),
                ("Repair labour", "1", "45.00"), ("Total", "", "64.98")]:
        page.insert_text((50, y), row[0], fontsize=11)
        page.insert_text((400, y), row[1], fontsize=11)
        page.insert_text((480, y), row[2], fontsize=11)
        y += 20
    page.draw_rect(fitz.Rect(50, 300, 545, 640), width=1)
    page.insert_image(fitz.Rect(55, 305, 540, 635), filename=str(photo), keep_proportion=True)
    page.insert_text((50, 680), "Small print at 6pt: terms and conditions apply.", fontsize=6)
    doc.save(path)


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("samples")
    _synthetic_photo(d / "photo.jpg")
    _invoice_pdf(d / "invoice.pdf", d / "photo.jpg")
    shutil.copy(REPO / "samples" / "notes.md", d / "notes.md")
    return d
