# phomemo-m08f

Print PDFs, Markdown, plain text, CSV tables and photos to a **Phomemo M08F** A4 thermal
printer from a Mac. No vendor app, account or watermark. It can also appear as
a normal printer in every app's print dialog.

## Setup

```bash
git clone https://github.com/Edrits/print.git && cd print
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

Connect the printer over USB, then **hold the power button ~3s until the light
is solid red**. That's USB mode; blinking blue is Bluetooth mode, and the
printer won't show up over USB.

```bash
./.venv/bin/phomemo devices        # should list /dev/cu.usbmodem... <- M08F
```

## First print: the calibration page

```bash
./.venv/bin/phomemo test
```

Use the sheet to tune your printer:

- **Ruler**: 100 to 200 should measure exactly 100 mm.
- **Border**: all four edges should be visible. If one is missing, the
  printable width is narrower than 1664 dots.
- **Dithering strips**: choose your favourite for photos (`--dither`).
- **Stroke weights and type sizes**: the faintest one that prints solidly is
  your floor. If it's too light, raise `--density` (1-8, default 5).

## Printing

```bash
phomemo print notes.md                 # Markdown: headings, lists, tables, code
phomemo print letter.txt --mono        # plain text, monospace
phomemo print ticket.pdf               # PDFs: crisp text, dithered photos
phomemo print expenses.csv             # CSV/TSV: a table; wide ones turn sideways
phomemo print photo.jpg                # photos: fit to one page, auto-rotated
phomemo print a.pdf b.md --copies 2 --density 6
echo "hello" | phomemo print - --type text
phomemo print notes.md --ble           # over Bluetooth instead of USB
```

**One sheet at a time.** The M08F takes in a whole job even after its sheet
has run out, so a multi-sheet job sent in one go prints the first sheet and
loses the rest. On A4 or Letter, `phomemo print` therefore sends each sheet as
its own job and asks you in the terminal to load the next one (Enter prints
it, `q` stops). Continuous paper and `--dry-run` still go as one job.

To check a print without using paper:

```bash
phomemo preview notes.md --open        # PNGs at exactly the printed dots
phomemo print notes.md --dry-run job.bin
```

## In the browser

```bash
phomemo ui
```

Opens a local print screen at http://127.0.0.1:8632. Drop in files (or paste
text), see every sheet before it prints, with page count, metres of paper and
time, and check that the printer is connected. Bluetooth is the default; the
Printer panel switches to USB. Add `--dry-run job.bin` to try it without a
printer. It only listens on this Mac, and only answers to `127.0.0.1` or
`localhost`.

- **Skip pages**: hover a sheet in the preview and click ×, or press Delete in
  the full-size view. Skipped pages stay visible, faded, and don't print.
- **Sheet by sheet**: on A4 or Letter each sheet is its own job. After each one
  the screen waits with **Print sheet N** (also ⌘P) until you've loaded the
  next; the preview outlines the next sheet. Pages you skip mid-run are
  dropped, and a failed sheet can be tried again without reprinting the others.

### As an app in the Dock

```bash
phomemo ui --make-app
```

Builds **Thermal.app** in `~/Applications` (Spotlight finds it; drag it to the
Dock or desktop). Opening it starts the print screen and opens it in your
default browser; clicking it again reopens the page. Quit it from the Dock, or
just close the page: the server stops by itself three minutes later and the app
leaves the Dock. The app only asks macOS to open a localhost link; it has no
control over your browser.

The first Bluetooth print asks "Thermal would like to use Bluetooth": allow it.
If you chose Don't Allow, the Printer panel says Bluetooth is blocked and
offers **Ask again** (macOS asks once more) or **Open settings**.
The app uses this project's `.venv`, so run `--make-app` again if you move the
project (macOS may ask for Bluetooth again). Server log:
`~/Library/Logs/Thermal.log`.

## In every app's print dialog

```bash
phomemo serve
```

Then open **System Settings → Printers & Scanners → Add Printer**. *Phomemo M08F*
appears in the list; add it. Every app can now print to it. `serve` has to be
running while you print. If you change paper, use `--media letter`.

Print dialogs default to US Letter (an ippeveprinter limitation, explained in
`phomemo/ipp.py`). That's harmless: pages are always padded to the sheet that's
physically loaded, so page breaks stay aligned. Letter content scales by about
3% to fill the A4 width.

**Known limitation:** the print dialog sends a whole document as one job and
there's no way to ask you for the next sheet, so only the first sheet of a
multi-page document prints reliably. Use `phomemo ui` or `phomemo print` for
anything longer than a page.

## How it works

```
file ──► render ──────────────────────► 1-bit pages ──► ESC/POS ──► USB serial
         PDF:      PyMuPDF at exactly 1664 dots, AA off           or Bluetooth LE
         Markdown: markdown-it → HTML → PyMuPDF Story → PDF
         CSV:      table HTML → PyMuPDF Story → PDF (sideways if wide)
         image:    EXIF-rotate, fit page, gamma for dot gain
```

Thermal paper is pure black and white, so every grey has to be resolved:

| content | default | why |
|---|---|---|
| Markdown, text, CSV | `threshold` | crisp strokes, no speckle |
| PDF | `hybrid` | threshold for text, Atkinson inside each embedded image |
| photos | `atkinson` | keeps highlights clean and shadows punchy on thermal |

The protocol: init `1B 40`, density `1D 28 4B 02 00 31 n`, raster
`1D 76 30 00 xL xH yL yH` in bands of ≤255 lines × 208 bytes, then feed
`1B 64 n`. The printer is an STM32 USB CDC device (`0483:5740`). Over
Bluetooth the same bytes go to GATT characteristic `ff02`, paced by credits the
printer grants on `ff03`, which also reports paper in and out (details in
`phomemo/ble.py`).

## Status

**Used on a real M08F**, over Bluetooth and USB, from the print screen
(Thermal.app). Over 100 tests (`pytest`) check, without hardware:
- the command bytes match the hardware-verified reference driver
- an independent decoder rebuilds every job **dot for dot** from the byte
  stream, for PDFs, Markdown and photos
- page padding, copies, sheet-by-sheet jobs and skipped pages
- Bluetooth flow control, paper reports, dropped links and stalls, against a
  fake printer
- the print-dialog path end to end: IPP job → `ippeveprinter` → hook → ESC/POS

Print `phomemo test` first to tune density for your paper.

## Credits

The protocol constants come from
[M-Wham/m08f-printer](https://github.com/M-Wham/m08f-printer) (MIT), a Linux
driver for the same printer. This project is a macOS-first rewrite: PyMuPDF
replaces Ghostscript, it renders at native width rather than resampling,
dithers by content type, renders Markdown, and integrates with the print
dialog.
