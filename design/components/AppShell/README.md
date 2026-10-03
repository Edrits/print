# AppShell

The full print screen: top bar with brand and ConnectionStatus pill; main column with JobSummary → PagePayout → compact DropZone → FileQueue; a 300px settings sidebar with the connection card; footer with what's blocking and the one Print button.

- Empty state: hide the summary and payout, show the full-size DropZone with a `display` headline, and the connection card under it.
- The footer always explains a disabled Print ("Printer not connected", "Add a file to print").
