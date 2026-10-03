# FileQueue

The ordered list of uploaded files; order = print order. Each row (`.tp-file`): drag grip, a mini sheet, name + type tag + size and render mode, state, page count.

- States: Ready (`--ok`), Rendering… (`--busy`), Failed (`--error`, with the reason in the meta line). State is always a word.
- Selecting a row (`.tp-file--selected`) scrolls the PagePayout to that file's first page.
- Page count is right-aligned mono (`9 pp`) so the column sums by eye.
- Consumer provides: files with name, kind, size, page count, state, error message.
