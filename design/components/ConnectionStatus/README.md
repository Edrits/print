# ConnectionStatus

Tells the person, at a glance, whether the printer is connected and how. Two sizes: the compact **pill** (`.tp-status`) lives in the app's top bar on every screen; the **card** (`.tp-conn`) sits at the top of the settings sidebar and in the empty state.

## States

| State | Pill class | Card class | Words |
| --- | --- | --- | --- |
| Connected, USB | `.tp-status` | `.tp-conn` | "Connected" + `USB` |
| Connected, Bluetooth | `.tp-status--warn` | `.tp-conn--ble` | "Connected" + `Bluetooth` (slower; warn tint on the glyph) |
| Searching | — | `.tp-conn--search` | "Looking for printer…" |
| Printing | `.tp-status--busy` | — | "Printing" + `3/14` |
| Not connected | `.tp-status--off` | `.tp-conn--off` | "Not connected" + the fix |

## Rules
- The state is always a **word**; the dot is a second signal, never the only one. The not-connected dot is a hollow `danger` ring so it differs in shape too.
- The not-connected card always says how to fix it: *"Plug in USB, then hold power ~3 s until the light is solid red."* (blinking blue means Bluetooth mode).
- The consumer provides: state, transport (`usb` | `ble`), device name/path, and the action (Test page, Retry, Switch).
- When not connected, the Print button stays enabled only for **Save job file** (dry run); printing is disabled.
