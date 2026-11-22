"""The reading path: any form file -> page images -> Markdown, one vision-model call per page.

    from app.llm.transcribe import transcribe
    doc = transcribe(data, filename)   # Document(text=<Markdown of all pages>, method="vlm-md", pages=<per page>)
    plan = make_plan(doc.text)         # the planner (text only) reads the Markdown

Every format becomes page images with open-source tools only: PDF via pypdfium2 (PDFium, BSD/Apache),
JPG/PNG as they are (a tall image of stacked A4 pages is cut into pages, as for OCR), DOC/DOCX/ODS/
XLS/XLSX first to PDF with LibreOffice headless (MPL-2.0). Each page is shrunk to TRANSCRIBE_SIDE px (1024)
on its long side (tokens grow with pixels; Qwen-family encoders spend ~1 token per 32x32 px) and goes
alone to TRANSCRIBE_MODEL (Qwen3.5 on Eden; the planner reads no images) with `prompts/transcribe.md` as the system prompt.
One page per call keeps every answer short (nothing cut off by max_tokens, nothing skipped in a long
answer); TRANSCRIBE_WORKERS pages run at once, each call capped at TRANSCRIBE_TIMEOUT_S. presence_penalty 1.5
stops Qwen copying a dotted fill-in line forever; runs of dots/underscores are collapsed to `______`. A page
whose answer is cut off or times out loops: it gets one retry with RETRY_FREQUENCY_PENALTY (`transcribe_page`).
Pages are joined in order with a blank line.

`transcribe` keeps the last CACHE_FILES results in memory keyed by (sha256 of the bytes, extension,
TRANSCRIBE_MODEL, TRANSCRIBE_SIDE), and a second request for a file still being read waits for that reading
instead of paying for its own. Failures are never cached (the next request tries again).

The one other way in: an office file is read from its own text (`app.extract`, method "office-text", no model call)
when LibreOffice loses its text (forms 2 and 8 DOCX: floating text boxes render as empty boxes, and the reader then
invents a form for them; `text_lost`)
or cannot convert it at all.
"""

import base64
import hashlib
import io
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from functools import cache
from pathlib import Path

import httpx
import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.extract import Document, UnsupportedFile, extract
from app.extract.pdf import PDFIUM, page_texts
from app.extract.scan import split_pages
from app.llm.client import LLMError, _retry_wait, chat_text

log = logging.getLogger(__name__)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
RENDER_SIDE = 2400  # render big once, then shrink per call (TRANSCRIBE_SIDE)
MAX_PAGES = 30  # a blank form never has more; caps the cost of a wrong upload
PAGE_NOTE = "Strona {n} z {total}. Przepisz tę stronę do Markdown."
# Qwen's own setting for its instruct models; without it Qwen3.5 copies a dotted fill-in line
# forever at temperature 0 (cut off by max_tokens). frequency_penalty breaks the loop too but wrecks
# tables (every "|" counts against it), presence_penalty is a flat, one-time push.
PRESENCE_PENALTY = 1.5
# Only for the retry of a looping page: frequency_penalty breaks any loop (form 5 DOC page 1: 517 tokens, 10 s, whole page)
# but would bend every table of the first try.
RETRY_FREQUENCY_PENALTY = 0.5
FILL_RUN = re.compile(r"[.…_]{4,}")  # a dotted / underscored fill-in line, however the model copied it
CACHE_FILES = 16  # transcriptions kept in memory, most recent first out last
# An office PDF lost text when it holds under MIN_RECALL of the file's own words (measured on 75 office forms: form 2
# DOCX 0.55 and form 8 DOCX 0.80, the rest 0.98 or more) while the file's own text holds at least MIN_OWN of the PDF's
# words (>= 0.977 on all 75), so a garbled own reader (a DOC in the wrong code page) never stands in for the paper.
MIN_RECALL = 0.95
MIN_OWN = 0.9

_cache: OrderedDict[tuple, Document] = OrderedDict()
_inflight: dict[tuple, Future] = {}
_lock = threading.Lock()


@cache
def system_prompt() -> str:
    return (Path(__file__).parent / "prompts" / "transcribe.md").read_text()


def transcribe(data: bytes, filename: str = "") -> Document:
    """Bytes of one form -> Document whose text is the Markdown of all its pages, in order. The same bytes
    read again (or at the same time) reuse one transcription; treat the Document as read-only."""
    key = (hashlib.sha256(data).hexdigest(), Path(filename).suffix.lower(),
           settings.transcribe_model, settings.transcribe_side)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
        running = _inflight.get(key)
        if running is None:
            mine = _inflight[key] = Future()
    if running is not None:
        return running.result()  # the other request's Document, or its exception
    try:
        doc = _transcribe(data, filename)
    except BaseException as e:
        with _lock:
            del _inflight[key]
        mine.set_exception(e)
        raise
    with _lock:
        del _inflight[key]
        _cache[key] = doc
        while len(_cache) > CACHE_FILES:
            _cache.popitem(last=False)
    mine.set_result(doc)
    return doc


def clear_cache() -> None:
    """Forget every kept transcription (tests)."""
    with _lock:
        _cache.clear()


def _transcribe(data: bytes, filename: str) -> Document:
    try:
        pages, office_pdf = _render(data, filename)
    except (UnsupportedFile, subprocess.TimeoutExpired) as e:
        own = file_text(data, filename) if _is_office(data) else ""
        if not own.strip():
            raise
        log.warning("%s: LibreOffice failed (%s), reading the file's own text", filename, e)
        return Document(text=own, method="office-text", pages=[own])
    if not pages:
        raise UnsupportedFile(f"no pages to read in {filename or 'file'}")
    if office_pdf:
        own = file_text(data, filename)
        if text_lost(own, office_pdf):
            log.warning("%s: the LibreOffice PDF lost text of the file, reading the file's own text", filename)
            return Document(text=own, method="office-text", pages=[own])
    with ThreadPoolExecutor(max(1, settings.transcribe_workers)) as pool:
        texts = list(pool.map(lambda i: transcribe_page(pages[i], i + 1, len(pages)), range(len(pages))))
    return Document(text="\n\n".join(texts), method="vlm-md", pages=texts)


def text_lost(own: str, pdf: bytes) -> bool:
    """True when an office file's LibreOffice PDF lost text the file has (MIN_RECALL, MIN_OWN): read or show the
    file's own text instead. `own` = `file_text` of the file (empty = nothing to compare, keep the PDF)."""
    if not own.strip():
        return False
    paper = "\n".join(page_texts(pdf))
    return recall(own, paper) < MIN_RECALL and recall(paper, own) >= MIN_OWN


def recall(file_text: str, paper_text: str) -> float:
    """Share of the words of `file_text` (3+ letters, normalized as quotes are) that `paper_text` holds."""
    from app.quotes import normalize  # local: not needed for PDF and image files

    def words(text: str) -> list[str]:
        return [w for w in normalize(text).split() if len(w) >= 3]

    src, got = words(file_text), set(words(paper_text))
    return sum(w in got for w in src) / len(src) if src else 1.0


def file_text(data: bytes, filename: str) -> str:
    """An office file's own text (`app.extract`), "" when its own reader fails."""
    try:
        return extract(data, filename).text
    except (UnsupportedFile, ValueError) as e:  # the file's own reader failed: the pages are all we have
        log.warning("%s: no own text to check the office PDF against (%s)", filename, e)
        return ""


def _is_office(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04" or data[:8] == OLE_MAGIC  # DOCX/XLSX/ODS/ODT, DOC/XLS


def transcribe_page(img: Image.Image, n: int = 1, total: int = 1, side: int | None = None) -> str:
    """One page image -> its Markdown (fill-in runs collapsed to `______`). One plain try; an answer cut off by
    max_tokens or a read timeout means the page loops (form 5 DOC page 1: an empty table row `| | | …` copied until
    max_tokens; form 5 DOCX: the same loop past TRANSCRIBE_TIMEOUT_S; the same call again looped once more), so it
    gets one retry with RETRY_FREQUENCY_PENALTY and more room (2 HTTP tries). A rate limit, 5xx or broken connection
    is no loop: the same plain call again after the client's backoff (`_plain`)."""
    url = data_url(img, side or settings.transcribe_side)
    note = PAGE_NOTE.format(n=n, total=total)
    call = dict(model=settings.transcribe_model, images=[url], presence_penalty=PRESENCE_PENALTY,
                timeout=settings.transcribe_timeout_s)
    try:
        raw = _plain(note, n, call)
    except (LLMError, httpx.ReadTimeout) as e:
        log.warning("page %d looped (%s), retrying with frequency_penalty", n, e)
        raw = chat_text(system_prompt(), note, max_tokens=16000, frequency_penalty=RETRY_FREQUENCY_PENALTY,
                        tries=2, **call).raw
    return FILL_RUN.sub("______", raw).strip()


def _plain(note: str, n: int, call: dict) -> str:
    """The plain page call: one HTTP try (a looping page would only time out again); after a rate limit, 5xx or
    connection error, the client's wait (Retry-After, else 2 s) and the same call again with 2 tries."""
    try:
        return chat_text(system_prompt(), note, tries=1, **call).raw
    except httpx.ReadTimeout:
        raise
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 429 and e.response.status_code < 500:
            raise
        log.warning("page %d: %s, trying again", n, e)
        time.sleep(_retry_wait(e.response, 1))
    except httpx.TransportError as e:
        log.warning("page %d: %s, trying again", n, e)
        time.sleep(_retry_wait(None, 1))
    return chat_text(system_prompt(), note, tries=2, **call).raw


def data_url(img: Image.Image, side: int | None = None) -> str:
    """`data:image/jpeg;base64,...` of the page (grayscale stays grayscale), long side shrunk to `side` px first
    (never upscaled). JPEG keeps the request small; tokens depend only on the pixel size."""
    if side:
        img = img.convert("L" if img.mode == "L" else "RGB")
        img.thumbnail((side, side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def render_pages(data: bytes, filename: str = "") -> list[Image.Image]:
    """Every page of a PDF, image or office file as an RGB image (long side RENDER_SIDE px at most)."""
    return _render(data, filename)[0]


def _render(data: bytes, filename: str) -> tuple[list[Image.Image], bytes | None]:
    """(page images, the LibreOffice PDF of an office file or None)."""
    if data[:5] == b"%PDF-" or (filename.lower().endswith(".pdf") and b"%PDF-" in data[:1024]):
        return pdf_pages(data), None
    if _is_office(data):  # DOCX/XLSX/ODS/ODT, DOC/XLS
        pdf = office_to_pdf(data, filename)
        return pdf_pages(pdf), pdf
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        raise UnsupportedFile(f"unsupported file type ({filename or 'no name'})") from e
    pages = split_pages(ImageOps.exif_transpose(img).convert("RGB"))
    for p in pages:
        p.thumbnail((RENDER_SIDE, RENDER_SIDE), Image.Resampling.LANCZOS)
    return pages[:MAX_PAGES], None


def pdf_pages(data: bytes) -> list[Image.Image]:
    """The first MAX_PAGES pages of a PDF as RGB images, under PDFIUM (pdfium is not thread-safe)."""
    with PDFIUM:
        pdf = pdfium.PdfDocument(data)
        try:
            out = []
            for i in range(min(len(pdf), MAX_PAGES)):
                page = pdf[i]
                scale = min(RENDER_SIDE / max(page.get_size()), 300 / 72)  # never above 300 dpi
                out.append(page.render(scale=scale).to_pil().convert("RGB"))
            return out
        finally:
            pdf.close()


def office_to_pdf(data: bytes, filename: str) -> bytes:
    """DOC/DOCX/ODS/ODT/XLS/XLSX bytes -> PDF bytes with LibreOffice headless."""
    return office_convert(data, filename, "pdf")


def office_convert(data: bytes, filename: str, to: str) -> bytes:
    """Office file bytes -> bytes of format `to` ("pdf", "ods", ...) with LibreOffice headless, in a temp dir with
    its own LibreOffice profile (parallel conversions do not lock each other out)."""
    soffice = shutil.which(settings.soffice) or (MAC_SOFFICE if Path(MAC_SOFFICE).exists() else None)
    if not soffice:
        raise UnsupportedFile("LibreOffice (soffice) is not installed: office files cannot become page images")
    suffix = Path(filename).suffix.lower() or ".bin"
    with tempfile.TemporaryDirectory(prefix="coi-lo-") as tmp:
        src = Path(tmp) / "in" / f"form{suffix}"  # own folder: an ODS converted to ODS must not overwrite itself
        src.parent.mkdir()
        src.write_bytes(data)
        cmd = [soffice, f"-env:UserInstallation=file://{tmp}/profile", "--headless", "--norestore",
               "--convert-to", to, "--outdir", tmp, str(src)]
        subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        out = Path(tmp) / f"form.{to}"
        if not out.exists():
            raise UnsupportedFile(f"LibreOffice could not convert {filename or 'file'} to {to.upper()}")
        return out.read_bytes()
