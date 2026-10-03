# JobSummary

"What you've got coming": four stats across the top of the print screen. Sheets, Paper (metres), Time, Copies, each an `overline` label, a mono `figure`, and a one-line note.

- Paper = sheets × sheet length (A4 297 mm, Letter 279.4 mm; continuous = the rendered length). Show metres to 2 decimals.
- Time = paper length ÷ ~15 mm/s over USB; Bluetooth is slower, so say so in a `--warn` note.
- Use a `--warn` note when a job is unusually long (more than a fanfold pack, more than ~10 minutes).
- Updates live as files are added, re-ordered, or settings change.
