"""Spreadsheets (XLSX via openpyxl, ODS/ODT via content.xml) -> raw text, one sheet row per line.

Forms drawn in a spreadsheet use the grid for layout and cell borders for what paper shows as
dotted lines and boxes, so we read borders too:
- empty cell(s) with a bottom border, or an empty bordered merged area -> fill-in line `____`;
- a lone empty cell with all four borders -> checkbox `[ ]` (a run of them is a comb field -> `____`);
- other empty cells are dropped, merged cells are read once.
Adjacent text cells are joined with ' | '; a row holding a single text cell keeps its line breaks.
"""

import io
import re
import zipfile
import xml.etree.ElementTree as ET

import openpyxl

TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"

ALL = frozenset("lrtb")


def cell_token(text: str, borders: frozenset, span: int):
    """One cell -> ("text", s) | ("fill",) | ("box",) | None (layout-only cell)."""
    if text and text.strip():
        return ("text", text.strip())
    if borders == ALL and span <= 2:
        return ("box",)
    if "b" in borders or (borders == ALL and span > 2):
        return ("fill",)
    return None


def row_lines(tokens: list) -> list[str]:
    """Tokens of one row -> output lines (see module docstring)."""
    tokens = [t for t in tokens if t]
    if not tokens:
        return []
    if len(tokens) == 1 and tokens[0][0] == "text":
        return tokens[0][1].splitlines()
    merged: list = []
    for t in tokens:
        if merged and t[0] in ("fill", "box") and merged[-1][0] in ("fill", "box"):
            merged[-1] = ("fill",)  # a run of boxes or lines is one field
        else:
            merged.append(t)
    parts: list[str] = []
    for i, t in enumerate(merged):
        if t[0] == "text":
            if i and merged[i - 1][0] == "text":
                parts.append("|")
            parts.append(" ".join(t[1].split()))
        else:
            parts.append("____" if t[0] == "fill" else "[ ]")
    return [" ".join(parts)]


# --- XLSX --------------------------------------------------------------------------
def read_xlsx(data: bytes) -> str:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=False)
    lines: list[str] = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        spans = {(r.min_row, r.min_col): r.max_col - r.min_col + 1 for r in ws.merged_cells.ranges}
        for row in ws.iter_rows():
            tokens = []
            for c in row:
                if type(c).__name__ == "MergedCell":
                    continue  # covered part of a merged area
                b = c.border
                borders = frozenset(k[0] for k in ("left", "right", "top", "bottom") if getattr(b, k).style)
                tokens.append(cell_token(_cell_str(c.value), borders, spans.get((c.row, c.column), 1)))
            lines += row_lines(tokens)
    wb.close()
    return "\n".join(lines)


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# --- ODS / ODT ---------------------------------------------------------------------
def read_odf(zf: zipfile.ZipFile) -> str:
    """content.xml: tables row by row, paragraphs/headings/lists as lines."""
    root = ET.fromstring(zf.read("content.xml"))
    borders = _odf_borders(root)
    if "styles.xml" in zf.namelist():
        borders = {**_odf_borders(ET.fromstring(zf.read("styles.xml"))), **borders}
    return "\n".join(_odf_blocks(root.find(OFFICE + "body"), borders))


def _odf_borders(root) -> dict[str, frozenset]:
    """Cell style name -> set of sides with a visible border."""
    out = {}
    for st in root.iter(STYLE + "style"):
        props = st.find(STYLE + "table-cell-properties")
        if props is None:
            continue
        sides = set()
        if _visible(props.get(FO + "border")):
            sides |= ALL
        for side in ("left", "right", "top", "bottom"):
            v = props.get(f"{FO}border-{side}")
            if v is not None:
                sides.discard(side[0])
                if _visible(v):
                    sides.add(side[0])
        out[st.get(STYLE + "name")] = frozenset(sides)
    return out


def _visible(v) -> bool:
    return bool(v) and v != "none" and not re.match(r"^0(\.0+)?(pt|cm|mm|in)?(\s|$)", v)


def _odf_blocks(el, borders) -> list[str]:
    lines: list[str] = []
    for ch in el:
        if ch.tag == TABLE + "table":
            for row in ch.iter(TABLE + "table-row"):
                tokens = []
                for c in row:
                    if c.tag != TABLE + "table-cell":
                        continue  # covered (merged) cells are read once, via their top-left cell
                    text = "\n".join(_odf_blocks(c, borders))
                    span = int(c.get(TABLE + "number-columns-spanned", "1"))
                    if int(c.get(TABLE + "number-columns-repeated", "1")) > 1:
                        span = 99  # a repeated empty cell is a run, never a checkbox
                    tokens.append(cell_token(text, borders.get(c.get(TABLE + "style-name"), frozenset()), span))
                lines += row_lines(tokens)
        elif ch.tag in (TEXT + "p", TEXT + "h"):
            text = _odf_inline(ch)
            if text.strip():
                lines += text.split("\n")
        elif ch.tag == OFFICE + "annotation":
            continue
        else:
            lines += _odf_blocks(ch, borders)
    return lines


def _odf_inline(el) -> str:
    """Text of a paragraph: spans, <text:s c=N/> spaces, tabs, line breaks; notes/annotations skipped."""
    out = [el.text or ""]
    for ch in el:
        tag = ch.tag
        if tag == TEXT + "s":
            out.append(" " * int(ch.get(TEXT + "c", "1")))
        elif tag == TEXT + "tab":
            out.append(" ")
        elif tag == TEXT + "line-break":
            out.append("\n")
        elif tag in (OFFICE + "annotation", TEXT + "note", DRAW + "frame"):
            pass
        else:
            out.append(_odf_inline(ch))
        out.append(ch.tail or "")
    return "".join(out)
