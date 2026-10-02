"""Glue between macOS's built-in `ippeveprinter` and the M08F.

`phomemo serve` runs ippeveprinter, which advertises an IPP printer over
Bonjour. Add it once in System Settings > Printers & Scanners and it appears
in every app's print dialog. For each job, ippeveprinter runs
`phomemo-ipp-job <spoolfile>` with the job's IPP attributes in the
environment (CONTENT_TYPE, IPP_COPIES, IPP_MEDIA, ...).

No root, no kernel extensions, nothing on the sealed system volume.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import job, render, transport

STATE_DIR = Path.home() / "Library" / "Application Support" / "phomemo"

_MEDIA = {"iso_a4_210x297mm": "a4", "na_letter_8.5x11in": "letter"}


def _log(level: str, msg: str) -> None:
    # ippeveprinter reads "LEVEL: message" lines from stderr into the job log.
    print(f"{level}: {msg}", file=sys.stderr, flush=True)


def job_main() -> int:
    """Entry point for ippeveprinter's -c print command."""
    if len(sys.argv) < 2:
        _log("ERROR", "no spool file passed")
        return 1
    spool = sys.argv[1]
    ctype = os.environ.get("CONTENT_TYPE", "application/pdf")
    # Pad pages to the sheet that is physically loaded (set by `serve --media`),
    # not the dialog's paper choice: a Letter layout on A4 sheets must still
    # break on A4 boundaries. The layout itself just scales to full width.
    media = os.environ.get("PHOMEMO_MEDIA", "a4")
    requested = _MEDIA.get(os.environ.get("IPP_MEDIA", ""), media)
    copies = int(os.environ.get("IPP_COPIES", "1") or 1)
    density = int(os.environ.get("PHOMEMO_DENSITY", "5"))
    dry = os.environ.get("PHOMEMO_DRY_RUN")
    device = os.environ.get("PHOMEMO_DEVICE") or None

    _log("INFO", f"{ctype} -> {media}, {copies} copies, density {density}")
    if requested != media:
        _log("INFO", f"laid out for {requested}, printing on {media} sheets")
    try:
        pages = render.render(spool, render.RenderOptions(media=media), content_type=ctype)
        data = job.build(pages, job.JobOptions(density=density, copies=copies), media=media)
        with transport.open_port(device=device,
                                 dry_run_path=Path(dry) if dry else None) as port:
            port.send(data)
    except transport.PrinterNotFound as e:
        _log("ERROR", str(e).splitlines()[0])
        return 1
    except Exception as e:  # report anything else into the job log
        _log("ERROR", f"{type(e).__name__}: {e}")
        return 1
    _log("INFO", f"sent {len(pages)} page(s), {len(data):,} bytes")
    return 0


def serve(name: str = "Phomemo M08F", port: int = 8631, density: int = 5,
          verbose: bool = False, dry_run: Path | None = None,
          device: str | None = None, media: str = "a4") -> int:
    exe = shutil.which("ippeveprinter")
    if not exe:
        print("ippeveprinter not found; it ships with macOS in /usr/bin.", file=sys.stderr)
        return 2
    hook = Path(sys.executable).with_name("phomemo-ipp-job")
    if not hook.exists():
        print(f"{hook} missing; reinstall with: pip install -e .", file=sys.stderr)
        return 2

    spool = STATE_DIR / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PHOMEMO_DENSITY=str(density), PHOMEMO_MEDIA=media)
    if dry_run:
        env["PHOMEMO_DRY_RUN"] = str(dry_run)
    if device:
        env["PHOMEMO_DEVICE"] = device

    # Attributes come from flags, not an -a conf file. ippeveprinter rejects
    # -a combined with any attribute flag, and with -a alone it appends its
    # own document-format-supported (octet-stream, pwg-raster, urf) beside
    # ours: a malformed response that could lead macOS to send AirPrint
    # raster. The cost is that dialogs default to US Letter, which the job
    # hook absorbs by padding to the physically loaded sheet (PHOMEMO_MEDIA).
    # (-P with a PPD would fix the default, but needs ippeveps, which macOS
    # does not ship.)
    cmd = [exe, "-c", str(hook),
           "-f", "application/pdf,image/jpeg,image/png,text/plain",
           "-M", "Phomemo", "-m", "M08F", "-l", "USB",
           "-p", str(port), "-d", str(spool)]
    if verbose:
        cmd.append("-v")
    cmd.append(name)
    print(f"Serving '{name}' on ipp://localhost:{port}/ipp/print  (Ctrl-C to stop)",
          file=sys.stderr, flush=True)
    # exec, not subprocess: stopping `phomemo serve` must stop the printer.
    # A child process would be orphaned and keep advertising over Bonjour.
    os.execve(exe, cmd, env)
