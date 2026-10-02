"""Markdown and plain text -> paginated PDF via PyMuPDF's Story layout engine.

Thermal-specific CSS rules:
  * No grey backgrounds. At 1 bit, grey becomes solid black or nothing.
    Code blocks and quotes use rules and borders instead.
  * Generous stroke weights and sizes; hairlines and 7pt text fade.
"""
from __future__ import annotations

import html
import io

import fitz
from markdown_it import MarkdownIt

from .. import spec

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(["table", "strikethrough"])

CSS = """
* {{ font-family: sans-serif; }}
body {{ font-size: {size}pt; line-height: 1.35; }}
h1 {{ font-size: 1.9em; font-weight: bold; margin: 0 0 0.4em 0;
      border-bottom: 2pt solid black; padding-bottom: 0.15em; }}
h2 {{ font-size: 1.45em; font-weight: bold; margin: 1.0em 0 0.35em 0; }}
h3 {{ font-size: 1.2em; font-weight: bold; margin: 0.9em 0 0.3em 0; }}
h4, h5, h6 {{ font-size: 1em; font-weight: bold; margin: 0.8em 0 0.2em 0; }}
p {{ margin: 0 0 0.6em 0; }}
ul, ol {{ margin: 0 0 0.6em 0; padding-left: 1.4em; }}
li {{ margin: 0 0 0.15em 0; }}
code {{ font-family: monospace; font-weight: bold; }}
pre {{ font-family: monospace; font-size: 0.88em; margin: 0 0 0.7em 0;
       padding: 0.4em 0 0.4em 0.7em; border-left: 2.5pt solid black; }}
pre code {{ font-weight: bold; }}   /* regular Courier stems are ~2 dots: too faint */
blockquote {{ margin: 0 0 0.6em 0; padding-left: 0.8em;
              border-left: 2.5pt solid black; font-style: italic; }}
hr {{ border: 0; border-top: 1.5pt solid black; margin: 0.8em 0; }}
/* MuPDF ignores border-collapse but honours border-spacing: 0. Cells draw
   only right+bottom edges and the table supplies top+left, so every line in
   the grid is the same weight instead of interior lines being doubled. */
table {{ border-spacing: 0; margin: 0 0 0.7em 0;
         border-top: 1pt solid black; border-left: 1pt solid black; }}
th, td {{ border-right: 1pt solid black; border-bottom: 1pt solid black;
          padding: 0.2em 0.45em; text-align: left; }}
th {{ font-weight: bold; border-bottom: 2pt solid black; }}
a {{ color: black; text-decoration: underline; }}
"""


def markdown_to_html(text: str) -> str:
    return _MD.render(text)


def text_to_html(text: str, monospace: bool = False) -> str:
    """Plain text: keep line breaks, blank lines become paragraph gaps."""
    style = ' style="font-family: monospace"' if monospace else ""
    paras = text.replace("\r\n", "\n").split("\n\n")
    out = []
    for para in paras:
        lines = [html.escape(line) for line in para.split("\n")]
        out.append(f"<p{style}>" + "<br/>".join(lines) + "</p>")
    return "\n".join(out)


def html_to_pdf(body_html: str, page: str = "a4", margin_mm: float = 12.0,
                font_size: float = 11.0) -> bytes:
    w_mm, h_mm = spec.PAGE_SIZES_MM[page]
    if h_mm is None:
        h_mm = 2000.0       # continuous: one very long page, trimmed later
    pt = 72 / 25.4
    rect = fitz.Rect(0, 0, w_mm * pt, h_mm * pt)
    m = margin_mm * pt
    where = rect + (m, m, -m, -m)

    story = fitz.Story(html=body_html, user_css=CSS.format(size=font_size))
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    more = True
    while more:
        device = writer.begin_page(rect)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return buf.getvalue()
