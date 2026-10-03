# PrintProgress

Shown while a job is feeding. A big mono "Page 3 of 14", paper fed and time left, a heat bar ticked per page (`--pages` CSS var), and the payout with done sheets dimmed and the current one outlined.

- Time left is computed from the printer's feed rate (~15 mm/s, so ~20 s per A4 sheet); say "~" because it's an estimate.
- Cancel is always visible and stops after the current band.
- The top-bar ConnectionStatus switches to its Printing state while this is shown.
