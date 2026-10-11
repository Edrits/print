"""Markdown and plain text -> paginated PDF via PyMuPDF's Story layout engine.

Thermal-specific CSS rules:
  * No grey backgrounds. At 1 bit, grey becomes solid black or nothing.
    Code blocks and quotes use rules and borders instead.
  * Generous stroke weights and sizes; hairlines and 7pt text fade.
"""
from __future__ import annotations

import csv
import html
import io
import re

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
td.num, th.num {{ text-align: right; }}
p.csv-count {{ font-size: 0.85em; }}
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


_NUMBER = re.compile(r"[-+]?[£$€]?\s?-?[\d,]*\.?\d+%?")


def csv_to_html(text: str) -> str:
    """A CSV (or TSV) as one table. Columns that are empty in every row are
    dropped (exports often carry several), and number columns align right so
    amounts line up. The first row is the header."""
    text = text.lstrip("\ufeff")                      # Excel's byte-order mark
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return "<p>(empty file)</p>"
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    keep = [i for i in range(width) if any(r[i].strip() for r in body)] or list(range(width))
    numeric = {i for i in keep
               if any(r[i].strip() for r in body)
               and all(_NUMBER.fullmatch(r[i].strip()) for r in body if r[i].strip())}

    def cells(row, tag):
        # the last header cell is tagged so csv_to_pdf can find the table's right edge
        return "".join(f'<{tag}{" class=num" if i in numeric else ""}'
                       f'{" id=csv-edge" if tag == "th" and i == keep[-1] else ""}>'
                       f'{html.escape(row[i].strip())}</{tag}>' for i in keep)
    out = ["<table class=csv>", f"<tr>{cells(head, 'th')}</tr>"]
    out += [f"<tr>{cells(r, 'td')}</tr>" for r in body]
    out.append("</table>")
    out.append(f"<p class=csv-count>{len(body)} row{'' if len(body) == 1 else 's'}</p>")
    return "\n".join(out)


# Tables wider than the page: try smaller type, then the sheet turned sideways.
# Below about 7 pt, thermal text fades, so that is the floor.
CSV_LAYOUTS = [(False, 0.85), (False, 0.7), (True, 0.85), (True, 0.7), (True, 0.65)]


def csv_to_pdf(text: str, page: str = "a4", margin_mm: float = 12.0,
               font_size: float = 11.0) -> bytes:
    body = csv_to_html(text)
    layouts = [l for l in CSV_LAYOUTS if not l[0] or spec.PAGE_SIZES_MM[page][1] is not None]
    for landscape, scale in layouts:
        if _table_fits(body, page, margin_mm, font_size * scale, landscape):
            break
    return html_to_pdf(body, page, margin_mm, font_size * scale, landscape=landscape)


def _frame(page: str, margin_mm: float, landscape: bool):
    w_mm, h_mm = spec.PAGE_SIZES_MM[page]
    if h_mm is None:
        h_mm = 2000.0       # continuous: one very long page, trimmed later
    if landscape:
        w_mm, h_mm = h_mm, w_mm
    pt = 72 / 25.4
    rect = fitz.Rect(0, 0, w_mm * pt, h_mm * pt)
    m = margin_mm * pt
    return rect, rect + (m, m, -m, -m)


def _table_fits(body_html: str, page: str, margin_mm: float, font_size: float,
                landscape: bool) -> bool:
    rect, where = _frame(page, margin_mm, landscape)
    story = fitz.Story(html=body_html, user_css=CSS.format(size=font_size))
    story.place(where)
    edge = []
    story.element_positions(lambda p: edge.append(p.rect[2]) if p.id == "csv-edge" else None)
    return not edge or max(edge) <= where.x1 + 0.5


def html_to_pdf(body_html: str, page: str = "a4", margin_mm: float = 12.0,
                font_size: float = 11.0, landscape: bool = False) -> bytes:
    """landscape: lay out across the sheet's length, then turn each page a
    quarter so it still prints on the sheet as it feeds."""
    rect, where = _frame(page, margin_mm, landscape)
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
    if not landscape:
        return buf.getvalue()
    doc = fitz.open("pdf", buf.getvalue())
    for pg in doc:
        pg.set_rotation(90)
    return doc.tobytes()
