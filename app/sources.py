"""Where on the paper each element of the e-service came from: "Źródło" in the preview, a framed crop of the page
under the element. Code only, no model.

    paper = read_paper(data, filename)    # once per file (app.history caches it per record: the file never changes)
    locate(paper, coi, evidence)          # on every request (milliseconds): element id -> place on paper
    page_png(data, filename, n)           # the page image the boxes are drawn on

The paper is the file's own lines, each with the boxes of its words: the PDF text layer (pypdfium2), Tesseract on PDF
pages without a usable layer (the check of `app.extract.pdf`) and on JPG/PNG pages (cut by `split_pages`; plain
grayscale, no upscale or deskew, so the boxes fit the served page image), and for DOC/DOCX/ODS/XLSX/XLS the text
`/api/forms/text` shows (no pages, a place is a range of its lines); `app.history` hands in an office file's
LibreOffice PDF instead when it has one, so office files get pages and boxes too. Quotes are placed by `app.quotes.place_quotes`,
the same walk as the paper headings (`app.headings`), so "W dokumencie: ..., str. N" and the crop never disagree.
"""

import io
from dataclasses import dataclass, field
from functools import cached_property

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageOps

from app.extract import extract
from app.extract.pdf import OCR_DPI, PDFIUM, _looks_like_text
from app.extract.scan import split_pages
from app.quotes import _paper_lines, normalize, place_quotes, span_lines

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the page as shown, origin top left
PAGE_WIDTH = 1600  # px of a served page image
IMAGE_EXTS = ("jpg", "jpeg", "jpe", "png")
_COND = "#warunek"  # key suffix of a condition's quote in the walk


@dataclass
class Line:
    """One line of the paper: its text (words joined by one space), page (0-based, None without pages), the words
    with their boxes, and for text formats its index in `text.split("\\n")` of the extracted text."""
    text: str
    page: int | None = None
    words: list[tuple[str, Box]] = field(default_factory=list)
    raw: int | None = None


@dataclass
class Paper:
    """The lines of a file that hold anything a quote can match (non-empty after `normalize`), in reading order;
    `aspects` = height / width of every page as shown ([] for text formats); `raw` = the extracted text's lines."""
    lines: list[Line]
    aspects: list[float] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)

    @cached_property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @classmethod
    def from_text(cls, text: str, page: int | None = None) -> "Paper":
        """A text without boxes: an office document's extracted text, or one page of Markdown."""
        raw = text.split("\n")
        return cls([_line(t, page, raw=i) for i, t in enumerate(raw) if normalize(t)], raw=raw)

    @classmethod
    def from_pages(cls, pages: list[str]) -> "Paper":
        """Page texts without boxes (the Markdown a vision model wrote page by page): lines know their page."""
        return cls([line for n, p in enumerate(pages) for line in cls.from_text(p, n).lines])


def _line(text: str, page: int | None = None, words: list | None = None, raw: int | None = None) -> Line:
    return Line(" ".join(text.splitlines()), page, words or [], raw)  # one paper line, even if it held a \v or


def read_paper(data: bytes, filename: str) -> Paper:
    """The paper of one file (see the module doc). Slow for scans (Tesseract, a few seconds a page)."""
    ext = filename.lower().rsplit(".", 1)[-1]
    if data[:5] == b"%PDF-" or ext == "pdf":
        return _pdf_paper(data)
    if ext in IMAGE_EXTS:
        pages = _image_pages(data)
        lines = [_line(t, n, ws) for n, img in enumerate(pages) for t, ws in _ocr_lines(img)]
        return Paper([ln for ln in lines if normalize(ln.text)], [img.height / img.width for img in pages])
    return Paper.from_text(extract(data, filename).text)


def _image_pages(data: bytes) -> list[Image.Image]:
    """The pages of a JPG/PNG as the transcription sees them: EXIF rotation applied, stacked pages cut."""
    return split_pages(ImageOps.exif_transpose(Image.open(io.BytesIO(data))))


def _pdf_paper(data: bytes) -> Paper:
    with PDFIUM:
        return _pdf_paper_locked(data)


def _pdf_paper_locked(data: bytes) -> Paper:
    pdf = pdfium.PdfDocument(data)
    try:
        lines, aspects = [], []
        for n, page in enumerate(pdf):
            w, h = page.get_size()
            aspects.append(h / w)
            tp = page.get_textpage()
            try:
                words = _pdf_lines(page, tp) if _looks_like_text(tp.get_text_range()) else None
            finally:
                tp.close()
            if words is None:  # a scanned page, or a broken font encoding: read the page image
                words = _ocr_lines(page.render(scale=OCR_DPI / 72, grayscale=True).to_pil())
            lines += [ln for ln in (_line(t, n, ws) for t, ws in words) if normalize(ln.text)]
        return Paper(lines, aspects)
    finally:
        pdf.close()


def _pdf_lines(page, tp) -> list[tuple[str, list[tuple[str, Box]]]]:
    """The text layer as lines (pdfium's own line breaks, the same lines `app.extract` reads) of words with
    boxes normalized to the page as shown: CropBox, y from the top, /Rotate applied."""
    left, bottom, right, top = page.get_cropbox()
    rot = page.get_rotation() % 360

    def norm(b) -> Box:
        x0, y0, x1, y1 = b  # PDF user space: left, bottom, right, top
        u0, u1 = (x0 - left) / (right - left), (x1 - left) / (right - left)
        v0, v1 = (top - y1) / (top - bottom), (top - y0) / (top - bottom)
        return {0: (u0, v0, u1, v1), 90: (1 - v1, u0, 1 - v0, u1),
                180: (1 - u1, 1 - v1, 1 - u0, 1 - v0), 270: (v0, 1 - u1, v1, 1 - u0)}[rot]

    out, words, cur, box = [], [], "", None

    def end_word():
        nonlocal cur, box
        if cur and box:
            words.append((cur, norm(box)))
        cur, box = "", None

    for k in range(tp.count_chars()):
        ch = tp.get_text_range(k, 1)
        if ch in ("\r", "\n"):
            end_word()
            if ch == "\n" and words:
                out.append((" ".join(w for w, _ in words), words))
                words = []
            continue
        if not ch or ch.isspace():
            end_word()
            continue
        cur += ch
        b = tp.get_charbox(k)
        if b[2] > b[0] or b[3] > b[1]:
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    end_word()
    if words:
        out.append((" ".join(w for w, _ in words), words))
    return out


def _ocr_lines(img: Image.Image) -> list[tuple[str, list[tuple[str, Box]]]]:
    """Tesseract on the plain grayscale page: its lines of words with boxes as fractions of the image. psm 11 (sparse
    text: every label of a table cell is its own line) measured on the gold quotes of COI forms 2-9 as PNG: 161/161
    found, 160 at the place the PDF text layer gives; psm 4: 148 found, 139 in place."""
    g = img.convert("L")
    w, h = g.size
    d = pytesseract.image_to_data(g, lang="pol", config="--psm 11", output_type=pytesseract.Output.DICT)
    lines: dict[tuple, list] = {}
    for i, t in enumerate(d["text"]):
        if t.strip():
            x, y = d["left"][i], d["top"][i]
            lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(
                (t.strip(), (x / w, y / h, (x + d["width"][i]) / w, (y + d["height"][i]) / h)))
    return [(" ".join(t for t, _ in ws), ws) for ws in lines.values()]


def quote_items(coi: dict | None, evidence: dict) -> list[tuple[str, str | None, bool]]:
    """The walk of `place_quotes` in form order (COI order, else the evidence's own): every element's paper quote,
    its condition's quote looked up just before it without moving the cursor. Consents are out of scope."""
    ids = [eid for t in coi["tabs"] for eid in [t["id"], *(x for q in t["questions"] for x in (
        q["field"]["id"], *(c["field"]["id"] for c in q["field"].get("repeatingSectionGrid") or [])))]] if coi else list(evidence)
    out = []
    for eid in ids:
        ev = evidence.get(eid) or {}
        if ev.get("condition_quote"):
            out.append((eid + _COND, ev["condition_quote"], False))
        if ev.get("quote"):
            out.append((eid, ev["quote"], True))
    return out


def locate(paper: Paper, coi: dict | None, evidence: dict) -> dict[str, dict | None]:
    """Element id -> where its quote is on paper, for every element with a quote:
    {page (1-based), pages, boxes: one [x0, y0, x1, y1] per text line of the quote, fractions of the page}, or for
    text formats {lines: [first, last] in the extracted text's lines, text: those lines}, or None = not found.
    An element whose SHOW condition has its own quote gets that place too, as `condition` (same shape, or None).
    A section without a quote gets the place of its paper heading (`_heading_places`)."""
    items = quote_items(coi, evidence)
    places = place_quotes(items, paper.text)
    keys = {key for key, _, _ in items}
    out: dict[str, dict | None] = {}
    for key, _, _ in items:
        if key.endswith(_COND):
            continue
        out[key] = where = _where(paper, places[key]) if key in places else None
        if where is not None and key + _COND in keys:
            where["condition"] = _where(paper, places[key + _COND]) if key + _COND in places else None
    if coi:
        out.update(_heading_places(paper, coi, evidence, out))
    return out


def _heading_places(paper: Paper, coi: dict, evidence: dict, done: dict) -> dict[str, dict]:
    """A section without a quote of its own ("Źródło" of its "W dokumencie: ..." line): the place of its paper heading,
    the same line `app.headings` names, so the button shows where the heading came from."""
    from app.headings import section_headings  # app.headings imports this module
    found = section_headings(paper, coi, evidence)
    joined, starts = _paper_lines(paper.text)
    out = {}
    for t in coi["tabs"]:
        for q in t["questions"]:
            sid = q["field"]["id"]
            key = (evidence.get(sid) or {}).get("plan_id")
            if sid in done or key not in found:
                continue
            h, line = found[key]
            end, stop = line, starts[line] + len(normalize(h))  # a heading broken over lines: the lines it takes, no more
            while end + 1 < len(starts) and starts[end + 1] - 1 < stop:
                end += 1
            out[sid] = _where(paper, (starts[line], starts[end + 1] - 1 if end + 1 < len(starts) else len(joined)))
    return out


def _where(paper: Paper, span: tuple[int, int]) -> dict:
    a, b = span_lines(span, paper.text)
    if not paper.aspects:
        first, last = paper.lines[a].raw, paper.lines[b].raw
        if first is None:
            return {"lines": [a, b]}
        return {"lines": [first, last], "text": "\n".join(paper.raw[first:last + 1])}
    _, starts = _paper_lines(paper.text)
    page = paper.lines[a].page
    boxes = []
    for i in range(a, b + 1):
        if paper.lines[i].page == page:
            boxes += _word_boxes(paper.lines[i], span[0] - starts[i], span[1] - starts[i])
    return {"page": page + 1, "pages": len(paper.aspects), "boxes": [[round(v, 4) for v in bx] for bx in _rows(boxes)]}


def _word_boxes(line: Line, a: int, b: int) -> list[Box]:
    """Boxes of the words of `line` that the span [a, b) of its normalized text covers."""
    out, pos, norms = [], 0, []
    for w, box in line.words:
        n = normalize(w)
        if not n:
            continue
        norms.append(n)
        if pos < b and pos + len(n) > a:
            out.append(box)
        pos += len(n) + 1
    if " ".join(norms) != normalize(line.text):  # normalizing word by word drifted: frame the whole line
        return [box for _, box in line.words]
    return out


def _rows(boxes: list[Box]) -> list[Box]:
    """Word boxes -> one box per text line as printed (words whose middles sit in the same band)."""
    rows: list[list[float]] = []
    for x0, y0, x1, y1 in sorted(boxes, key=lambda bx: (bx[1] + bx[3]) / 2):
        mid = (y0 + y1) / 2
        if rows and rows[-1][1] <= mid <= rows[-1][3]:
            r = rows[-1]
            r[:] = [min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1)]
        else:
            rows.append([x0, y0, x1, y1])
    return [tuple(r) for r in rows]


def page_png(data: bytes, filename: str, n: int) -> bytes | None:
    """Page `n` (1-based) as a PNG about PAGE_WIDTH px wide, as the boxes of `locate` see it; None if no such page
    (or a format without pages)."""
    ext = filename.lower().rsplit(".", 1)[-1]
    if data[:5] == b"%PDF-" or ext == "pdf":
        with PDFIUM:
            pdf = pdfium.PdfDocument(data)
            try:
                if not 1 <= n <= len(pdf):
                    return None
                page = pdf[n - 1]
                img = page.render(scale=PAGE_WIDTH / page.get_width()).to_pil()
            finally:
                pdf.close()
    elif ext in IMAGE_EXTS:
        pages = _image_pages(data)
        if not 1 <= n <= len(pages):
            return None
        img = pages[n - 1]
        if img.width > PAGE_WIDTH:
            img = img.resize((PAGE_WIDTH, round(img.height * PAGE_WIDTH / img.width)), Image.Resampling.LANCZOS)
    else:
        return None
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=False)
    return buf.getvalue()
