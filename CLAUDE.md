# CLAUDE.md

Prints PDFs, Markdown, text, CSV and photos to a **Phomemo M08F** A4 thermal printer from a Mac, over USB or Bluetooth LE. Front ends share one pipeline: the `phomemo` CLI, the print screen (a web page served by `phomemo/web`; in a browser tab via `phomemo ui`, or in its own Mac window via `phomemo app`, which **Thermal.app** runs), and a print-dialog printer (`phomemo serve`, via macOS's `ippeveprinter`).

## Commands

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev,app]"   # setup (app = pywebview)
./.venv/bin/python -m pytest -q                                # all tests, ~20 s, no printer needed
./.venv/bin/phomemo ui --no-open --port 8640 --dry-run /tmp/job.bin   # print screen, no printer
./.venv/bin/phomemo preview file.pdf --open                    # PNGs exactly as printed
./.venv/bin/phomemo print file.md --dry-run job.bin            # ESC/POS bytes to a file
./.venv/bin/phomemo app                                        # the print screen in a native window
./.venv/bin/phomemo app --make-app                             # rebuild ~/Applications/Thermal.app (needs clang)
```

There is no linter config. `pyflakes phomemo tests` should be clean. CI (`.github/workflows/tests.yml`) runs pytest on Python 3.9 and 3.12, Ubuntu and macOS, so keep 3.9 syntax (`from __future__ import annotations` makes `X | None` hints safe; don't use it at runtime).

## Layout

| path | what |
|---|---|
| `phomemo/render/` | any file → list of 1-bit pages, each exactly 1664 dots wide and, on sheets, one sheet tall. `markdown.py` also does plain text and CSV tables via PyMuPDF Story |
| `phomemo/raster.py` | grey → 1-bit (threshold, atkinson, floyd, ordered, hybrid) and raster bands |
| `phomemo/escpos.py`, `job.py` | ESC/POS bytes; `job.one_per_sheet` and `job.sequence` order and trim pages |
| `phomemo/transport.py` | the only code that opens a port: USB serial, Bluetooth (`BlePort`) or dry-run file |
| `phomemo/ble.py` | Bluetooth LE: discovery, credit flow control, paper reports, eject. `ble_job.py` runs one job in a child process |
| `phomemo/web/` | `phomemo ui`: stdlib HTTP server (`__init__.py`) and a no-build plain-JS page (`static/app.js`) |
| `phomemo/desktop.py` | `phomemo app`: pywebview window + the web server in-process, gated by a per-launch secret cookie |
| `phomemo/macapp.py` | builds Thermal.app: a clang-compiled launcher that embeds this venv's Python and runs `phomemo.desktop` in-process (one Dock icon, Thermal owns Bluetooth permission). Also the backup **Thermal Web.app** (`make_web_app`): an AppleScript applet that starts `phomemo ui` on 8632 and opens the browser |
| `phomemo/ipp.py` | `phomemo serve` and the per-job hook `phomemo-ipp-job` |
| `tests/escpos_decode.py` | independent decoder: rebuilds the printed image from job bytes |

## Printer facts that shape the code

Measured on hardware. Read the comments in `ble.py` and `job.py` before changing these paths.

- **One job per sheet.** The paper sensor sits ahead of the head. Once a sheet's tail passes it (~261 mm down A4) the printer stops. It then *accepts and discards* any further data, so a multi-sheet stream prints sheet 1 and loses the rest. Everything that prints on A4/Letter therefore sends each sheet as its own job and waits for the person to load the next one:
  - the web UI: `App._run_sheet`, `next_sheet`, and the job state `waiting`
  - the CLI: `cli._ask_next_sheet`
  - not the IPP path: it can't ask, which is a documented limitation.

  Continuous paper and dry runs stay one job.
- **Trim the last page.** The last page of a sheet job is trimmed to its content (`END_MARGIN_MM`). Sending blank rows for the zone past the sensor leaves the sheet stuck halfway.
- **Bluetooth uses credit-based flow control** on notify characteristic `ff03` (`01 NN` grants packets). Writing without credit overflows the printer and drops data.
- **Stalls of 30 s or more are normal** (cooling after dense areas), so `Credits.take` waits up to 180 s. While waiting it checks the link every 2 s, so a reset printer fails fast.
- **Paper reports:** `1a 06 89` means paper loaded, `1a 06 88` means none or the sheet's tail passed the sensor. The paper query `1f 11 11` gets no answer for ~10–15 s after connecting.
- **macOS Bluetooth permission** belongs to the *responsible app*: Terminal, or Thermal.app, which has its own bundle id and usage string. CoreBluetooth can abort a process, so the web server runs each Bluetooth job in a child process (`ble.run_isolated`) from its main thread.
- **USB mode** is the light held solid red (hold power ~3 s). Blinking blue is Bluetooth mode, and the USB device won't enumerate.

## Web UI conventions

- **Design:** follow the **Thermal** design system (`static/thermal.css`, `tp-` classes; app-only additions go in `app.css`). Neutral ink on paper, and the one accent `--heat` is reserved for print actions and progress. Panels are flat. Use sentence case, no emoji, and mono numbers with units.
- **Server:** stdlib only, and it binds 127.0.0.1. Requests must carry a local `Host` (DNS-rebinding guard), and writes must be same-origin. In Thermal.app (`App(token=…)`) every request also needs the `thermal` cookie, which the window gets from its first load of `/?key=…`. Uploads live in memory and a temp dir; a page left open re-uploads after a server restart (the `instance` id changes).
- **Native window:** `draw()` uses `requestAnimationFrame`, which WebKit pauses while the window is hidden. To check the window from a script, call `window.show()` first. pywebview's `evaluate_js` doesn't wait for promises, so use synchronous JS.
- **Page rendering:** `app.js` redraws regions only when their markup changes (`region()`), so polling never steals focus. Keep that pattern.
- **Print requests:** `/api/print` takes `files: [id | {id, pages: [index…]}]`. `/api/job/next` takes the same `files`, so pages skipped mid-run are dropped. `Job.info()` reports `next` and `printed` as `[file id, page index]` pairs for the preview highlight.

## Working on this machine

- **Thermal.app** is one process, `~/Applications/Thermal.app/Contents/MacOS/Thermal`, running `phomemo.desktop` from this repo's `.venv` (editable install), with its server on 127.0.0.1:8634. Its log is `~/Library/Logs/Thermal.log`, and Bluetooth jobs log their progress there.
- **Thermal Web.app** (the backup, bundle id `…thermal.web`) runs `phomemo ui --from-app --port 8632`. Its API is open to curl, so check `curl -s http://127.0.0.1:8632/api/job` before restarting it. Its log is `~/Library/Logs/Thermal Web.log`.
- **Restarting Thermal:** Python changes need a relaunch to take effect; only C launcher or Info.plist changes need `--make-app`. You can't `curl` its API without the secret, so check the log or ask before quitting it. If a print is running or waiting for the next sheet, quitting drops it. To quit: `osascript -e 'quit app "Thermal"'`, then `open ~/Applications/Thermal.app`.
- **Testing without paper:** never print on the real printer to test unless asked. Use `--dry-run`, or the fake-printer server pattern: monkeypatch `web.transport.open_port` on a separate port. The tests' `_sheet_app` helper does the same.
- **Git:** commit messages explain the why in plain prose. The owner pushes to `main`.
