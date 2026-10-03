# PagePayout

The signature view: every sheet that will come out of the printer, in feed order, so the person knows what they have coming before a single dot burns. Sheets sit on a `surface-sunken` tray; each is a `.tp-sheet` (always `sheet` white with `sheet-ink` marks, in both themes, because it shows paper).

- Group sheets by file with a `.tp-file-label` above and a `.tp-split` hairline between files.
- Caption each sheet with a mono `03 /14`, numbered across the whole job, not per file.
- The current or hovered page gets `.tp-sheet--active` (heat outline); printed pages get `.tp-sheet--done`.
- **Sheets / Roll** toggle: Sheets shows A4/Letter pages at 210:297; Roll (`media = continuous`) shows `.tp-sheet--continuous` strips trimmed to content length.
- The header line states the total: sheets, media, and paper length in metres.
- Consumer provides: rendered page thumbnails (the real 1664-dot-wide PNGs from `phomemo preview`), grouped by file. Thumbnails are 84px wide; clicking opens the page at full size.
