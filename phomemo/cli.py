"""Command-line interface: `phomemo print|preview|test|devices|serve|ui`."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__, ipp, job, raster, render, spec, testpage, transport


def _render_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("rendering")
    g.add_argument("--media", choices=list(spec.PAGE_SIZES_MM), default="a4",
                   help="paper: a4, letter, or continuous (fanfold, no page padding)")
    g.add_argument("--type", choices=render.KINDS, dest="kind",
                   help="override file-type detection")
    g.add_argument("--dither", choices=raster.DITHERS + ("hybrid",),
                   help="default: hybrid for PDFs, threshold for text, atkinson for images")
    g.add_argument("--threshold", type=int, default=160, metavar="0-255",
                   help="cut-off for threshold mode; higher = bolder text (default 160)")
    g.add_argument("--font-size", type=float, default=11.0, metavar="PT",
                   help="Markdown/text body size (default 11)")
    g.add_argument("--margin", type=float, default=12.0, metavar="MM",
                   help="Markdown/text page margin (default 12)")
    g.add_argument("--mono", action="store_true", help="plain text in monospace")
    g.add_argument("--fit", choices=("page", "width"), default="page",
                   help="images: fit on one page, or fill width and span pages")
    g.add_argument("--rotate", choices=("auto", "0", "90", "180", "270"), default="auto",
                   help="images: auto rotates landscape photos onto portrait paper")
    g.add_argument("--gamma", type=float, default=0.85,
                   help="images: <1 brightens to offset thermal dot gain (default 0.85)")
    g.add_argument("--antialias", action="store_true",
                   help="PDFs: smooth rendering; pair with --dither atkinson for photo-heavy PDFs")


def _device_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("printer")
    g.add_argument("--density", type=int, default=5, choices=range(1, 9), metavar="1-8",
                   help="darkness (default 5)")
    g.add_argument("--device", help="serial device; default autodetects the M08F")
    g.add_argument("--dry-run", nargs="?", const="-", metavar="FILE",
                   help="don't print; optionally save the raw ESC/POS job to FILE")
    g.add_argument("--ble", nargs="?", const="auto", metavar="NAME_OR_ID",
                   help="send over Bluetooth LE instead of USB (experimental; run "
                        "from Terminal.app). Optionally name the printer")
    g.add_argument("--ble-rate", type=int, metavar="BYTES_PER_S",
                   help="Bluetooth pacing (default 20000); lower it if output tears")


def _opts(a: argparse.Namespace) -> render.RenderOptions:
    return render.RenderOptions(
        media=a.media, dither=a.dither, threshold=a.threshold, font_size=a.font_size,
        margin_mm=a.margin, monospace=a.mono, fit=a.fit, rotate=a.rotate,
        gamma=a.gamma, antialias=a.antialias)


def _render_all(a: argparse.Namespace) -> list[tuple[str, list]]:
    opts = _opts(a)
    return [(f, render.render(f, opts, kind=a.kind)) for f in a.files]


def _save_previews(rendered, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for src, pages in rendered:
        stem = "stdin" if src == "-" else Path(src).stem
        for i, page in enumerate(pages, 1):
            p = out_dir / f"{stem}-p{i:02d}.png"
            page.save(p)
            paths.append(p)
    return paths


def _send(data: bytes, a: argparse.Namespace) -> None:
    dry = a.dry_run
    target = None if dry in (None, "-") else Path(dry)
    with transport.open_port(device=a.device, dry_run=dry is not None,
                             dry_run_path=target, ble=a.ble, ble_rate=a.ble_rate,
                             ble_eject=spec.page_height_dots(getattr(a, "media", "a4")) is not None
                             ) as port:
        port.send(data)


def cmd_print(a: argparse.Namespace) -> int:
    if a.dry_run is None and not a.device and a.ble is None:
        a.device = transport.autodetect()   # fail before rendering, not after
    rendered = _render_all(a)
    pages = [p for _, ps in rendered for p in ps]
    if a.preview:
        _save_previews(rendered, Path(a.preview))
    data = job.build(pages, job.JobOptions(density=a.density, feed=a.feed, cut=a.cut,
                                           copies=a.copies), media=a.media)
    where = "dry run" if a.dry_run is not None else \
        (f"Bluetooth ({a.ble})" if a.ble is not None else a.device)
    print(f"{len(pages)} page(s), {len(data):,} bytes -> {where}", file=sys.stderr)
    _send(data, a)
    return 0


def cmd_preview(a: argparse.Namespace) -> int:
    paths = _save_previews(_render_all(a), Path(a.out))
    for p in paths:
        print(p)
    if a.open and paths:
        subprocess.run(["open", *map(str, paths)])
    return 0


def cmd_test(a: argparse.Namespace) -> int:
    if a.dry_run is None and not a.device and not a.preview_only and a.ble is None:
        a.device = transport.autodetect()
    page = testpage.build(a.density)
    if a.preview:
        Path(a.preview).parent.mkdir(parents=True, exist_ok=True)
        page.save(a.preview)
        print(a.preview)
        if a.preview_only:
            return 0
    data = job.build([page], job.JobOptions(density=a.density), media="a4")
    print(f"calibration page, {len(data):,} bytes", file=sys.stderr)
    _send(data, a)
    return 0


def cmd_devices(a: argparse.Namespace) -> int:
    from serial.tools import list_ports
    found = False
    for p in list_ports.comports():
        is_m08f = (p.vid, p.pid) == (spec.USB_VID, spec.USB_PID)
        found |= is_m08f
        if is_m08f or a.all:
            ids = f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None else "----:----"
            print(f"{p.device:32s} {ids}  {p.description or ''}{'  <- M08F' if is_m08f else ''}")
    if not found:
        print("No M08F found. Connect it over USB and hold power ~3s until the light "
              "is SOLID RED.", file=sys.stderr)
        return 1
    return 0


def cmd_ble_scan(a: argparse.Namespace) -> int:
    import asyncio
    from . import ble
    return asyncio.run(ble.scan(timeout=a.timeout, probe=a.probe))


def cmd_serve(a: argparse.Namespace) -> int:
    return ipp.serve(name=a.name, port=a.port, density=a.density, verbose=a.verbose,
                     dry_run=Path(a.dry_run) if a.dry_run else None, device=a.device,
                     media=a.media)


def cmd_ui(a: argparse.Namespace) -> int:
    if a.make_app:
        from . import macapp
        app = macapp.make_app(Path(a.app_dir).expanduser(), port=a.port)
        print(f"Built {app}\n"
              "Open it from Applications or Spotlight, or drag it to the Dock or desktop.\n"
              f"Its server log: {macapp.LOG}")
        return 0
    from . import web
    return web.serve(port=a.port, open_browser=not a.no_open, device=a.device,
                     dry_run=Path(a.dry_run) if a.dry_run else None,
                     idle_exit=a.idle_exit, from_app=a.from_app)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="phomemo",
                                description="Print to a Phomemo M08F A4 thermal printer over USB.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("print", help="print files (PDF, Markdown, text, images)")
    pp.add_argument("files", nargs="+", help="files to print; '-' reads text from stdin")
    _render_args(pp)
    _device_args(pp)
    pp.add_argument("--copies", type=int, default=1)
    pp.add_argument("--feed", type=int, help="lines to feed at the end (default: auto)")
    pp.add_argument("--cut", choices=("none", "partial", "full"), default="none")
    pp.add_argument("--preview", metavar="DIR", help="also save page PNGs to DIR")
    pp.set_defaults(func=cmd_print)

    pv = sub.add_parser("preview", help="render to PNGs exactly as they would print")
    pv.add_argument("files", nargs="+")
    _render_args(pv)
    pv.add_argument("-o", "--out", default="preview", help="output directory (default ./preview)")
    pv.add_argument("--open", action="store_true", help="open the PNGs in Preview.app")
    pv.set_defaults(func=cmd_preview)

    pt = sub.add_parser("test", help="print a calibration page")
    _device_args(pt)
    pt.add_argument("--preview", metavar="PNG", help="also save the page as PNG")
    pt.add_argument("--preview-only", action="store_true", help="with --preview: don't print")
    pt.set_defaults(func=cmd_test)

    pd = sub.add_parser("devices", help="find the M08F")
    pd.add_argument("--all", action="store_true", help="list every serial port")
    pd.set_defaults(func=cmd_devices)

    pb = sub.add_parser("ble-scan", help="find the printer over Bluetooth LE (run in Terminal.app)")
    pb.add_argument("--probe", action="store_true",
                    help="connect and list its GATT services (read-only)")
    pb.add_argument("--timeout", type=float, default=10.0)
    pb.set_defaults(func=cmd_ble_scan)

    ps = sub.add_parser("serve", help="appear as a printer in every macOS print dialog")
    ps.add_argument("--name", default="Phomemo M08F")
    ps.add_argument("--port", type=int, default=8631)
    ps.add_argument("--density", type=int, default=5, choices=range(1, 9), metavar="1-8")
    ps.add_argument("--device", help="serial device; default autodetects at each job")
    ps.add_argument("--media", choices=list(spec.PAGE_SIZES_MM), default="a4",
                    help="paper physically loaded; pages pad to this sheet (default a4)")
    ps.add_argument("--dry-run", metavar="FILE", help="write each job to FILE instead of printing")
    ps.add_argument("-v", "--verbose", action="store_true")
    ps.set_defaults(func=cmd_serve)

    pu = sub.add_parser("ui", help="open a local web interface to upload, preview and print")
    pu.add_argument("--port", type=int, default=8632)
    pu.add_argument("--no-open", action="store_true", help="don't open the browser")
    pu.add_argument("--device", help="serial device; default autodetects at each job")
    pu.add_argument("--dry-run", metavar="FILE", help="write each job to FILE instead of printing")
    pu.add_argument("--idle-exit", type=float, metavar="SECONDS",
                    help="stop this long after the last page closes (keep it above 60)")
    pu.add_argument("--make-app", action="store_true",
                    help="build Thermal.app, a Dock icon that starts this and opens the page")
    pu.add_argument("--app-dir", default="~/Applications",
                    help="with --make-app: where to put it (default ~/Applications)")
    pu.add_argument("--from-app", action="store_true", help=argparse.SUPPRESS)
    pu.set_defaults(func=cmd_ui)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.func(a)
    except transport.PrinterNotFound as e:
        print(e, file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError, ConnectionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
