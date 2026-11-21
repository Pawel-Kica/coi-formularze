"""DOCX -> raw text: paragraphs, tables, text boxes, list numbers, footnotes, page headers and
footers, in document order.

Reads the OOXML parts straight from the zip with the stdlib, no python-docx: we need
things python-docx hides (text boxes, `mc:AlternateContent`, auto-numbering, checkbox fields).
"""

import re
import zipfile
import xml.etree.ElementTree as ET

from app.extract.layout import Box, boxes_to_lines

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
V = "{urn:schemas-microsoft-com:vml}"

EMU_PER_PT = 12700
# Wingdings / Symbol code points Word uses for checkboxes in <w:sym>.
SYM_BOX = {"F06F": "☐", "F0A8": "☐", "F071": "☐", "F0FE": "☒", "F078": "☒", "F0FD": "☒", "F06E": "■"}


def read_docx(zf: zipfile.ZipFile) -> str:
    """Whole document as text lines, in the order a PDF prints them: page headers, body,
    footnotes, page footers (form 7 has its stamp duty and bank account in the footer)."""
    doc = _Doc(zf)
    lines = doc.margins("header")
    lines += doc.blocks(doc.part("word/document.xml").find(W + "body"))
    for name in ("word/footnotes.xml", "word/endnotes.xml"):
        root = doc.part(name)
        if root is None:
            continue
        doc.note_no = 0
        for note in root:
            if note.get(W + "type") in (None, "normal"):
                doc.note_no += 1
                lines += doc.blocks(note)
    return "\n".join(lines + doc.margins("footer"))


class _Doc:
    def __init__(self, zf: zipfile.ZipFile):
        self.zf = zf
        self.numbering = _Numbering(self.part("word/numbering.xml"))
        self.note_no = 0  # number of the footnote being read, for its <w:footnoteRef/> mark

    def part(self, name: str):
        try:
            return ET.fromstring(self.zf.read(name))
        except KeyError:
            return None

    def margins(self, kind: str) -> list[str]:
        """Lines of every `kind` ("header" | "footer") part, each line once (the first-page, even
        and default variants usually repeat the same text)."""
        names = sorted(n for n in self.zf.namelist() if re.fullmatch(f"word/{kind}\\d*\\.xml", n))
        lines = [line for n in names for line in self.blocks(self.part(n)) if line.strip()]
        return list(dict.fromkeys(lines))

    def blocks(self, parent) -> list[str]:
        """Block-level children (paragraphs, tables, content controls) -> lines."""
        lines: list[str] = []
        for el in parent:
            if el.tag == W + "p":
                lines += self.paragraph(el)
            elif el.tag == W + "tbl":
                lines += self.table(el)
            elif el.tag in (W + "sdt", W + "customXml", W + "smartTag"):
                content = el.find(W + "sdtContent")
                lines += self.blocks(content if content is not None else el)
            elif el.tag == MC + "AlternateContent":
                choice = el.find(MC + "Choice")
                if choice is not None:
                    lines += self.blocks(choice)
        return lines

    def table(self, tbl) -> list[str]:
        """One line per row, cells joined with ' | ', merged cells once, nested tables flattened."""
        out = []
        for tr in tbl.findall(W + "tr"):
            cells = []
            for tc in tr.findall(W + "tc"):
                vmerge = tc.find(f"{W}tcPr/{W}vMerge")
                if vmerge is not None and vmerge.get(W + "val") in (None, "continue"):
                    continue  # continuation of a vertically merged cell: text already emitted above
                text = " ".join(line for line in self.blocks(tc) if line.strip())
                if text.strip():
                    cells.append(text.strip())
            if cells:
                out.append(" | ".join(cells))
        return out

    def paragraph(self, p) -> list[str]:
        """Paragraph text (with list number) plus any text boxes anchored in it, sorted by position."""
        parts: list[str] = []
        boxes: list[Box] = []
        inline_boxes: list[str] = []
        self._runs(p, parts, boxes, inline_boxes, None)
        text = "".join(parts)
        num = self.numbering.label(p)
        if num and text.strip():
            text = f"{num} {text}"
        lines = [text] if text.strip() else []
        lines += inline_boxes
        if boxes:
            lines += boxes_to_lines(boxes, tol=4 * EMU_PER_PT)
        return lines

    def _runs(self, el, parts, boxes, inline_boxes, pos):
        """Depth-first walk collecting visible text; text boxes go to `boxes` with their anchor position."""
        for ch in el:
            tag = ch.tag
            if tag == W + "t":
                parts.append(ch.text or "")
            elif tag in (W + "tab", W + "ptab"):
                parts.append(" ")
            elif tag in (W + "br", W + "cr"):
                parts.append("\n")
            elif tag == W + "noBreakHyphen":
                parts.append("-")
            elif tag == W + "sym":
                parts.append(SYM_BOX.get((ch.get(W + "char") or "").upper(), ""))
            elif tag == W + "fldChar":
                box = ch.find(f"{W}ffData/{W}checkBox")
                if box is not None:
                    checked = box.find(W + "checked")
                    if checked is None:
                        checked = box.find(W + "default")
                    on = checked is not None and checked.get(W + "val", "1") not in ("0", "false")
                    parts.append("☒" if on else "☐")
            elif tag in (W + "instrText", W + "delText", W + "del", MC + "Fallback", W + "rPr", W + "pPr"):
                continue
            elif tag == W + "footnoteReference" or tag == W + "endnoteReference":
                if ch.get(W + "customMarkFollows") not in ("1", "true"):
                    parts.append(ch.get(W + "id", ""))
            elif tag == W + "footnoteRef" or tag == W + "endnoteRef":
                parts.append(str(self.note_no))
            elif tag == WP + "anchor":
                self._runs(ch, parts, boxes, inline_boxes, _anchor_pos(ch))
            elif tag in (V + "shape", V + "rect", V + "roundrect") and pos is None:
                self._runs(ch, parts, boxes, inline_boxes, _vml_pos(ch))
            elif tag == W + "txbxContent":
                text = "\n".join(line for line in self.blocks(ch) if line.strip())
                if pos is None:
                    inline_boxes.append(text)
                else:
                    boxes.append(Box(pos[0], pos[1], pos[2], text))
            else:
                self._runs(ch, parts, boxes, inline_boxes, pos)

def _anchor_pos(anchor):
    def off(tag):
        el = anchor.find(f"{WP}{tag}/{WP}posOffset")
        return float(el.text) if el is not None and el.text else 0.0

    ext = anchor.find(WP + "extent")
    h = float(ext.get("cy", 0)) if ext is not None else 0.0
    return (off("positionH"), off("positionV"), h)


def _vml_pos(shape):
    style = dict(
        kv.split(":", 1) for kv in (shape.get("style") or "").replace(" ", "").split(";") if ":" in kv
    )

    def pt(key):
        m = re.match(r"(-?[\d.]+)(pt|in)?", style.get(key, ""))
        if not m:
            return 0.0
        return float(m.group(1)) * (72 if m.group(2) == "in" else 1) * EMU_PER_PT

    if "margin-top" not in style and "top" not in style:
        return None
    return (pt("margin-left") or pt("left"), pt("margin-top") or pt("top"), pt("height"))


class _Numbering:
    """Auto list numbers ("1.", "a)", "II.") as Word renders them; bullets render as nothing."""

    def __init__(self, root):
        self.levels: dict[tuple[str, str], tuple[str, str, int]] = {}
        self.counters: dict[str, list[int]] = {}
        if root is None:
            return
        abstract = {}
        for a in root.findall(W + "abstractNum"):
            lv = {}
            for lvl in a.findall(W + "lvl"):
                fmt = lvl.find(W + "numFmt")
                txt = lvl.find(W + "lvlText")
                start = lvl.find(W + "start")
                lv[lvl.get(W + "ilvl")] = (
                    fmt.get(W + "val") if fmt is not None else "decimal",
                    txt.get(W + "val") if txt is not None else "",
                    int(start.get(W + "val")) if start is not None else 1,
                )
            abstract[a.get(W + "abstractNumId")] = lv
        for n in root.findall(W + "num"):
            aid = n.find(W + "abstractNumId")
            for ilvl, spec in abstract.get(aid.get(W + "val") if aid is not None else "", {}).items():
                self.levels[(n.get(W + "numId"), ilvl)] = spec

    def label(self, p) -> str:
        np = p.find(f"{W}pPr/{W}numPr")
        if np is None:
            return ""
        num_id = np.find(W + "numId")
        ilvl = np.find(W + "ilvl")
        num_id = num_id.get(W + "val") if num_id is not None else None
        level = int(ilvl.get(W + "val")) if ilvl is not None else 0
        if not num_id or num_id == "0" or (num_id, str(level)) not in self.levels:
            return ""
        fmt, text, start = self.levels[(num_id, str(level))]
        counters = self.counters.setdefault(num_id, [0] * 9)
        counters[level] = counters[level] + 1 if counters[level] else start
        for deeper in range(level + 1, 9):
            counters[deeper] = 0
        if fmt in ("bullet", "none"):
            return ""

        def render(m):
            i = int(m.group(1)) - 1
            spec = self.levels.get((num_id, str(i)))
            value = counters[i] or (spec[2] if spec else 1)
            return format_number(value, spec[0] if spec else "decimal")

        return re.sub(r"%(\d)", render, text).strip()


def format_number(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        s = chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)
        return s.upper() if fmt == "upperLetter" else s
    if fmt in ("lowerRoman", "upperRoman"):
        vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
        out = ""
        for v, sym in vals:
            while n >= v:
                out, n = out + sym, n - v
        return out.lower() if fmt == "lowerRoman" else out
    return str(n)

