from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from phomemo import escpos, job, raster, render, spec, testpage
from escpos_decode import decode



# -- protocol bytes: must match the hardware-verified reference driver --------

def test_escpos_bytes_match_reference():
    assert escpos.initialize() == b"\x1b\x40"
    assert escpos.density(5) == b"\x1d\x28\x4b\x02\x00\x31\x05"
    assert escpos.density(99) == b"\x1d\x28\x4b\x02\x00\x31\x08"   # clamped
    assert escpos.density(0) == b"\x1d\x28\x4b\x02\x00\x31\x01"
    assert escpos.feed(3) == b"\x1b\x64\x03"
    assert escpos.cut("full") == b"\x1d\x56\x00"
    assert escpos.cut("partial") == b"\x1d\x56\x01"
    assert escpos.cut("none") == b""
    hdr = escpos.raster_block(208, 255, bytes(208 * 255))[:8]
    assert hdr == b"\x1d\x76\x30\x00" + bytes([208, 0, 255, 0])


def test_raster_block_rejects_wrong_payload_size():
    with pytest.raises(ValueError):
        escpos.raster_block(208, 2, bytes(10))


# -- bit packing --------------------------------------------------------------

def test_pack_bit_order_and_polarity():
    img = Image.new("1", (16, 1), 255)
    img.putpixel((0, 0), 0)      # leftmost dot black -> MSB of byte 0
    img.putpixel((15, 0), 0)     # rightmost dot black -> LSB of byte 1
    assert raster.pack(img) == bytes([0x80, 0x01])


def test_pack_rejects_unaligned_width():
    with pytest.raises(ValueError):
        raster.pack(Image.new("1", (12, 1), 255))


def test_bands_respect_firmware_limit():
    img = Image.new("1", (spec.WIDTH_DOTS, 600), 255)
    bands = list(raster.bands(img))
    assert [h for _, h, _ in bands] == [255, 255, 90]
    assert all(wb == spec.BYTES_PER_LINE for wb, _, _ in bands)


# -- pagination ---------------------------------------------------------------

def test_sheet_pages_are_padded_to_exact_sheet_height():
    a4 = spec.page_height_dots("a4")
    tall = Image.new("1", (spec.WIDTH_DOTS, a4 + 100), 0)
    pages = render.paginate(tall, a4)
    assert len(pages) == 2
    assert all(p.size == (spec.WIDTH_DOTS, a4) for p in pages)
    # padding on the last sheet is white
    assert pages[1].getpixel((10, a4 - 1)) == 255


def test_continuous_media_trims_trailing_white():
    img = Image.new("1", (spec.WIDTH_DOTS, 1000), 255)
    img.paste(0, (0, 0, 50, 100))
    (page,) = render.paginate(img, None)
    assert 100 <= page.height <= 100 + spec.mm_to_dots(4)


# -- type detection -----------------------------------------------------------

@pytest.mark.parametrize("name,ctype,kind", [
    ("a.pdf", None, "pdf"), ("a.MD", None, "markdown"), ("a.txt", None, "text"),
    ("a.jpeg", None, "image"), ("job-17", "application/pdf", "pdf"),
    ("job-18", "image/png", "image"), ("job-19", "text/plain", "text"),
])
def test_detect_kind(name, ctype, kind):
    assert render.detect_kind(name, ctype) == kind


def test_detect_kind_sniffs_untyped_spool_files(tmp_path, samples):
    for src, kind in (("invoice.pdf", "pdf"), ("photo.jpg", "image"), ("notes.md", "text")):
        f = tmp_path / f"job-{kind}"            # no extension, like a spool file
        f.write_bytes((samples / src).read_bytes())
        assert render.detect_kind(str(f), "application/octet-stream") == kind


def test_detect_kind_unknown():
    with pytest.raises(ValueError):
        render.detect_kind("mystery.xyz")


# -- full renders -------------------------------------------------------------

@pytest.mark.parametrize("sample", ["notes.md", "invoice.pdf", "photo.jpg"])
def test_every_page_is_print_ready(sample, samples):
    pages = render.render(str(samples / sample))
    assert pages
    for p in pages:
        assert p.mode == "1"
        assert p.size == (spec.WIDTH_DOTS, spec.page_height_dots("a4"))


def test_text_renders_and_paginates(tmp_path):
    f = tmp_path / "long.txt"
    f.write_text("\n\n".join(f"Paragraph {i}. " + "words " * 60 for i in range(80)))
    pages = render.render(str(f))
    assert len(pages) > 1


def _black_fraction(img1, box):
    region = img1.crop(box).convert("L")
    hist = region.histogram()
    return hist[0] / (region.width * region.height)


def test_hybrid_dithers_photo_but_not_text(samples):
    """The invoice photo region must contain a real mix of dots (dithered),
    while hybrid must leave the text identical to a pure threshold render."""
    path = str(samples / "invoice.pdf")
    hyb = render.render(path)[0]
    thr = render.render(path, render.RenderOptions(dither="threshold"))[0]
    photo_box = (200, 900, 1400, 1700)
    text_box = (100, 100, 1500, 800)
    frac = _black_fraction(hyb, photo_box)
    assert 0.05 < frac < 0.95
    assert ImageChops.difference(hyb.crop(text_box).convert("L"),
                                 thr.crop(text_box).convert("L")).getbbox() is None


# -- end to end: the bytes decode back to exactly the pixels we rendered ------

@pytest.mark.parametrize("sample", ["notes.md", "invoice.pdf", "photo.jpg"])
def test_job_round_trip_is_pixel_exact(sample, samples):
    pages = render.render(str(samples / sample))
    data = job.build(pages, job.JobOptions(density=6))
    cmds, img = decode(data)
    assert cmds[0] == ("init",)
    assert cmds[1] == ("density", 6)
    assert cmds[-1] == ("feed", 0)        # sheets are pre-padded, no feed
    assert all(c[1] == spec.BYTES_PER_LINE for c in cmds if c[0] == "raster")
    assert all(c[2] <= spec.MAX_BLOCK_LINES for c in cmds if c[0] == "raster")
    expected = Image.new("1", (spec.WIDTH_DOTS, sum(p.height for p in pages)), 255)
    y = 0
    for p in pages:
        expected.paste(p, (0, y)); y += p.height
    assert ImageChops.difference(img.convert("L"), expected.convert("L")).getbbox() is None


def test_copies_repeat_pages_not_header(samples):
    pages = render.render(str(samples / "notes.md"))
    cmds, img = decode(job.build(pages, job.JobOptions(copies=3)))
    assert [c[0] for c in cmds].count("init") == 1
    sheet = spec.page_height_dots("a4")
    # whole sheets until the last page, which ends at its content (paper sensor)
    assert 2 * sheet < img.height < 3 * sheet


def test_build_rejects_wrong_width():
    with pytest.raises(ValueError):
        job.build([Image.new("1", (800, 10), 255)], job.JobOptions())


def test_calibration_page_is_print_ready():
    page = testpage.build(density=4)
    assert page.mode == "1" and page.width == spec.WIDTH_DOTS


# -- the macOS print-dialog hook ----------------------------------------------

def test_ipp_job_hook_dry_run(tmp_path, samples):
    out = tmp_path / "job.bin"
    hook = Path(sys.executable).with_name("phomemo-ipp-job")
    env = dict(os.environ, CONTENT_TYPE="application/pdf", IPP_COPIES="2",
               IPP_MEDIA="iso_a4_210x297mm", PHOMEMO_DENSITY="7",
               PHOMEMO_DRY_RUN=str(out))
    r = subprocess.run([str(hook), str(samples / "invoice.pdf")], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "INFO:" in r.stderr
    cmds, img = decode(out.read_bytes())
    assert ("density", 7) in cmds
    sheet = spec.page_height_dots("a4")
    assert sheet < img.height <= 2 * sheet


def test_ipp_job_hook_pads_to_loaded_sheet_not_dialog_choice(tmp_path, samples):
    """Dialog says Letter, A4 is loaded: pages must break on A4 boundaries."""
    out = tmp_path / "job.bin"
    hook = Path(sys.executable).with_name("phomemo-ipp-job")
    env = dict(os.environ, CONTENT_TYPE="application/pdf", IPP_MEDIA="na_letter_8.5x11in",
               IPP_COPIES="2", PHOMEMO_MEDIA="a4", PHOMEMO_DRY_RUN=str(out))
    r = subprocess.run([str(hook), str(samples / "invoice.pdf")], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "laid out for letter, printing on a4 sheets" in r.stderr
    _, img = decode(out.read_bytes())
    # copy 1 is padded to a whole A4 sheet (not Letter), so copy 2 starts past it
    assert img.height > spec.page_height_dots("a4")


def test_ipp_job_hook_reports_missing_printer(tmp_path, samples):
    hook = Path(sys.executable).with_name("phomemo-ipp-job")
    env = {k: v for k, v in os.environ.items() if k != "PHOMEMO_DRY_RUN"}
    env["CONTENT_TYPE"] = "application/pdf"
    env["PHOMEMO_DEVICE"] = "/dev/cu.does-not-exist"   # never reach real hardware
    r = subprocess.run([str(hook), str(samples / "invoice.pdf")], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "ERROR:" in r.stderr


# -- Bluetooth LE: decision logic only (no radio in tests) --------------------

class _C:
    def __init__(self, uuid, props):
        self.uuid, self.properties = uuid, props


class _S:
    def __init__(self, *chars):
        self.characteristics = list(chars)


def _u(short):
    return f"0000{short}-0000-1000-8000-00805f9b34fb"


def test_ble_prefers_phomemo_write_characteristic():
    from phomemo import ble
    services = [_S(_C(_u("2a00"), ["read"])),
                _S(_C(_u("ff01"), ["notify"]), _C(_u("ff03"), ["write-without-response"]),
                   _C(_u("ff02"), ["write-without-response", "write"]))]
    assert ble.pick_write_char(services).uuid == _u("ff02")


def test_ble_falls_back_to_any_fast_write_channel():
    from phomemo import ble
    services = [_S(_C(_u("aa01"), ["write"]), _C(_u("aa02"), ["write-without-response"]))]
    assert ble.pick_write_char(services).uuid == _u("aa02")
    assert ble.pick_write_char([_S(_C(_u("bb01"), ["read", "notify"]))]) is None


def test_ble_chunks_cover_stream_exactly():
    from phomemo import ble
    data = bytes(range(256)) * 10
    parts = list(ble.chunks(data, 244))
    assert b"".join(parts) == data and max(map(len, parts)) == 244


@pytest.mark.parametrize("name,hit", [("M08F-1A2B", True), ("Phomemo_M08F", True),
                                      ("AirPods Pro", False), (None, False)])
def test_ble_printer_name_matching(name, hit):
    from phomemo import ble
    assert ble.looks_like_printer(name) is hit


def test_last_sheet_ends_at_content_but_earlier_sheets_stay_whole(samples):
    """The M08F can't print the bottom ~36 mm of a sheet (paper sensor): ending
    the job there would leave the sheet stuck. Only the final page is trimmed."""
    pages = render.render(str(samples / "notes.md"))
    seq = job.sequence(pages, copies=2, media="a4")
    sheet = spec.page_height_dots("a4")
    assert seq[0].height == sheet
    assert seq[-1].height < sheet
    assert job.sequence(pages, copies=1, media="continuous")[-1].height == pages[-1].height
