"""Page images for a vision model (LLM_VISION=1): the planner sees each page next to its text.

    from app.llm.vision import page_images
    pages = page_images(data, filename)   # [] when LLM_VISION is off or the file has no pages to show
    plan = make_plan(doc.text, pages)

PDF pages are rendered with pypdfium2, a JPG/PNG is the image itself (a tall image of stacked
A4 pages is cut into pages, as for OCR). DOC/DOCX/XLS(X)/ODS stay text only: they have no
rendering without LibreOffice. Only the first LLM_VISION_MAX_PAGES pages are returned, at a long
side of LLM_VISION_MAX_SIDE px; `data_url` shrinks them further per pass and encodes them.
"""

import base64
import io

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.extract.scan import split_pages

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def page_images(data: bytes, filename: str = "") -> list[Image.Image]:
    """First pages of a PDF or image file as RGB images, long side <= LLM_VISION_MAX_SIDE.
    Empty when vision is off, for other formats, and for anything that does not open (the
    text extraction has already accepted or rejected the file)."""
    if not settings.llm_vision or settings.llm_vision_max_pages <= 0:
        return []
    side, cap = settings.llm_vision_max_side, settings.llm_vision_max_pages
    if data[:5] == b"%PDF-" or (filename.lower().endswith(".pdf") and b"%PDF-" in data[:1024]):
        return _pdf_pages(data, side, cap)
    if data[:4] == b"PK\x03\x04" or data[:8] == OLE_MAGIC:  # DOCX/XLSX/ODS, DOC/XLS: text only
        return []
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return []
    return [_fit(p, side) for p in split_pages(ImageOps.exif_transpose(img))[:cap]]


def _pdf_pages(data: bytes, side: int, cap: int) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(data)
    try:
        out = []
        for i in range(min(len(pdf), cap)):
            page = pdf[i]
            scale = side / max(page.get_size())  # points -> px, long side = `side`
            out.append(page.render(scale=scale).to_pil().convert("RGB"))
        return out
    finally:
        pdf.close()


def _fit(img: Image.Image, side: int) -> Image.Image:
    """RGB copy with the long side at most `side` px (never upscaled)."""
    img = img.convert("RGB")
    img.thumbnail((side, side), Image.Resampling.LANCZOS)
    return img


def data_url(img: Image.Image, side: int | None = None) -> str:
    """`data:image/jpeg;base64,...` of the page, shrunk to `side` px first. JPEG keeps the request
    small; tokens depend only on the pixel size (Qwen3-VL: one per 32x32 px)."""
    if side:
        img = _fit(img, side)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def setup() -> dict:
    """Model and vision settings, for bench logs (tells A/B runs apart)."""
    s = settings
    out = {"model": s.llm_model, "vision": s.llm_vision}
    if s.llm_vision:
        out |= {"max_side": s.llm_vision_max_side, "max_pages": s.llm_vision_max_pages,
                "conditions_side": s.llm_vision_conditions_side}
    return out
