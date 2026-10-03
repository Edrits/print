Thermal is the interface for printing to an A4 thermal printer (Phomemo M08F) from a Mac. It does one job: take files in, show exactly what will come out, and print it. The paper is black and white with no greys, so the interface is too: neutral ink on paper, with one hot accent that means "this burns paper".

## Content fundamentals

- **Plain, short, specific.** "Print 14 pages", not "Submit job". "Printer not connected", not "Device unavailable".
- **Sentence case** everywhere, including buttons and titles. Uppercase only for `overline` labels (SHEETS, PAPER, TIME).
- **Speak to "you", never "we".** "Release to add 3 files", "You have 14 sheets coming".
- **Numbers are the content.** Always say how many pages, how many metres of paper and how long it will take. Use mono figures with units: `14`, `4.16 m`, `~4:40`. Use `~` for estimates.
- **Every problem comes with its fix.** "Plug in USB, then hold power ~3 s until the light is solid red."
- **No emoji, no exclamation marks.**

## Visual foundations

**Color.** Use `paper` for the app background, `surface` for panels and `surface-sunken` for wells (the drop zone, the payout tray). Text is `ink`; secondary text is `ink-muted`. `heat` is the only accent. It is used for the Print button, the active page, the progress bar and the Printing status, and nothing decorative. Text on heat is `on-heat`. `heat-soft` marks a selection. Page thumbnails are always `sheet` with `sheet-ink` marks, in dark theme too, because they show real paper.

**Status.** `ok` (connected, ready), `warn` (Bluetooth, long job) and `danger` (not connected, failed) always come with a word. Not-connected also changes shape: a hollow ring instead of a filled dot.

**Type.** Geist for the interface and Geist Mono for anything counted or measured: page numbers, sizes, metres, device paths. Use `display` once per screen, `title` for panels, `heading` for file names, `body` for copy, `label` for controls, `caption` for hints, `overline` for stat labels, `figure` for the summary numbers and `data` for metadata.

**Space and shape.** Use a 4px base: `space-2` between sheets, `space-3` for row padding, `space-4` for panel padding, `space-6` between sections. Panels are `radius-md` with a 1px `line` border and **no shadow**. Only sheets lift, with `shadow-sheet`, at `radius-xs` because paper is almost square-cornered. Controls are `radius-sm` with a `line-strong` border, which meets 3:1 on every ground.

**Focus.** A 2px solid `focus` ring with a 2px offset on every interactive element.

**Motion.** Keep it minimal. Sheets slide in as they render (150ms ease-out) and the progress bar moves per page. Nothing loops except the Searching dot.

## Layout

The print screen has a top bar (brand and the connection pill), a main column (JobSummary, PagePayout, DropZone, FileQueue), a 300px settings sidebar, and a footer holding the single Print button. **The connection state is visible on every screen**: the pill in the top bar always, and the full card in the sidebar and in the empty state.

## Iconography

Use 16px stroke icons on a 16px box: 1.6 stroke, round caps and joins, `currentColor` (`.tp-icon`). These cover upload, printer, printer-off, USB, Bluetooth, remove and grip. They are drawn for this system; no icon font. Icons never stand alone for state.

## Components

These are CSS-class components in `components/bundle.css` (prefix `tp-`), with no JavaScript dependency: Button, ConnectionStatus, DropZone, FileQueue, PagePayout, JobSummary, PrintProgress, PrintSettings, and the AppShell screen that composes them.
