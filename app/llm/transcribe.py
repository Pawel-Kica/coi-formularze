"""The only reading path: any form file -> page images -> Markdown, one vision-model call per page.

    from app.llm.transcribe import transcribe
    doc = transcribe(data, filename)   # Document(text=<Markdown of all pages>, method="vlm-md", pages=<per page>)
    plan = make_plan(doc.text)         # the planner (text only) reads the Markdown

Every format becomes page images with open-source tools only: PDF via pypdfium2 (PDFium, BSD/Apache),
JPG/PNG as they are (a tall image of stacked A4 pages is cut into pages, as for OCR), DOC/DOCX/ODS/
XLS/XLSX first to PDF with LibreOffice headless (MPL-2.0). Each page is shrunk to TRANSCRIBE_SIDE px (1024)
on its long side (tokens grow with pixels; Qwen-family encoders spend ~1 token per 32x32 px) and goes
alone to TRANSCRIBE_MODEL (Qwen3.5 on Eden; the planner reads no images) with `prompts/transcribe.md` as the system prompt.
One page per call keeps every answer short (nothing cut off by max_tokens, nothing skipped in a long
answer); TRANSCRIBE_WORKERS pages run at once, each call capped at TRANSCRIBE_TIMEOUT_S with one more
try. presence_penalty 1.5 stops Qwen copying a dotted fill-in line forever; runs of dots/underscores
are collapsed to `______`. Pages are joined in order with a blank line.
"""

import base64
import io
import logging
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.extract import Document, UnsupportedFile
from app.extract.scan import split_pages
from app.llm.client import LLMError, chat_text

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
FILL_RUN = re.compile(r"[.…_]{4,}")  # a dotted / underscored fill-in line, however the model copied it


@cache
def system_prompt() -> str:
    return (Path(__file__).parent / "prompts" / "transcribe.md").read_text()


def transcribe(data: bytes, filename: str = "") -> Document:
    """Bytes of one form -> Document whose text is the Markdown of all its pages, in order."""
    pages = render_pages(data, filename)
    if not pages:
        raise UnsupportedFile(f"no pages to read in {filename or 'file'}")
    with ThreadPoolExecutor(max(1, settings.transcribe_workers)) as pool:
        texts = list(pool.map(lambda i: transcribe_page(pages[i], i + 1, len(pages)), range(len(pages))))
    return Document(text="\n\n".join(texts), method="vlm-md", pages=texts)


def transcribe_page(img: Image.Image, n: int = 1, total: int = 1, side: int | None = None) -> str:
    """One page image -> its Markdown (fill-in runs collapsed to `______`). Retried once with more
    room when the answer is empty or cut off."""
    url = data_url(img, side or settings.transcribe_side)
    note = PAGE_NOTE.format(n=n, total=total)
    call = dict(model=settings.transcribe_model, images=[url], presence_penalty=PRESENCE_PENALTY,
                timeout=settings.transcribe_timeout_s, tries=2)
    try:
        raw = chat_text(system_prompt(), note, **call).raw
    except LLMError as e:
        log.warning("page %d transcription failed (%s), retrying", n, e)
        raw = chat_text(system_prompt(), note, max_tokens=16000, **call).raw
    return FILL_RUN.sub("______", raw).strip()


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
    if data[:5] == b"%PDF-" or (filename.lower().endswith(".pdf") and b"%PDF-" in data[:1024]):
        return pdf_pages(data)
    if data[:4] == b"PK\x03\x04" or data[:8] == OLE_MAGIC:  # DOCX/XLSX/ODS/ODT, DOC/XLS
        return pdf_pages(office_to_pdf(data, filename))
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        raise UnsupportedFile(f"unsupported file type ({filename or 'no name'})") from e
    pages = split_pages(ImageOps.exif_transpose(img).convert("RGB"))
    for p in pages:
        p.thumbnail((RENDER_SIDE, RENDER_SIDE), Image.Resampling.LANCZOS)
    return pages[:MAX_PAGES]


def pdf_pages(data: bytes) -> list[Image.Image]:
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
