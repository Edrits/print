# DropZone

The upload target for files to print. Full size (`.tp-drop`) is the empty state; once the queue has files, switch to `.tp-drop--compact` above the queue, and let the whole window accept drops.

- While dragging over: `.tp-drop--active`, title says how many files will be added.
- Accepted: PDF, Markdown, plain text, JPG/PNG photos (the renderer's `pdf`, `markdown`, `text`, `image` kinds). Show them as `.tp-tag`s.
- Also accept pasted text (⌘V) as a text job.
- Consumer provides: the file input, drag handlers, and the accepted list.
