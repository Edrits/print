"""Child process for one Bluetooth job (see ble.run_isolated).

    python -m phomemo.ble_job JOB.bin [--target NAME] [--rate N] [--eject] [--no-require-paper]

Progress goes to stderr as usual. stdout carries `STATE {json}` whenever the
printer's reported state changes, and `ERROR message` if the job fails.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import ble


async def _watch(last: dict) -> None:
    while True:
        if ble.printer_state != last:
            last.clear()
            last.update(ble.printer_state)
            print("STATE " + json.dumps(last), flush=True)
        await asyncio.sleep(0.25)


async def _job(data: bytes, a: argparse.Namespace) -> None:
    watcher = asyncio.ensure_future(_watch({}))
    try:
        await ble.send(data, a.target, a.rate, eject=a.eject,
                       require_paper=not a.no_require_paper)
    finally:
        watcher.cancel()
        print("STATE " + json.dumps(ble.printer_state), flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="phomemo.ble_job")
    p.add_argument("job")
    p.add_argument("--target")
    p.add_argument("--rate", type=int, default=ble.DEFAULT_RATE)
    p.add_argument("--eject", action="store_true")
    p.add_argument("--no-require-paper", action="store_true")
    a = p.parse_args(argv)
    try:
        asyncio.run(_job(Path(a.job).read_bytes(), a))
    except Exception as e:
        print("ERROR " + " ".join(str(e).split()), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
