# Button

Actions. `.tp-btn` is the default (bordered, `surface`); add `--primary`, `--ghost`, `--lg`, `--icon`.

- Exactly **one** `--primary` per screen, and it is Print. Its label carries the count: "Print 14 pages", "Print 2 copies · 28 pages".
- `--ghost` for low-stakes row actions (Remove, Preview). Icon-only buttons need `aria-label`.
- Disabled Print when the queue is empty or the printer is not connected; say why in the footer, not a tooltip.
- Consumer provides: label, optional 16px stroke icon (`.tp-icon`), optional shortcut in `.tp-kbd`.
