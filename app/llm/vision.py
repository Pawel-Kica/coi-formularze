"""Page images for a vision model (LLM_VISION=1): the planner sees each page next to its text.

    from app.llm.vision import page_images
    pages = page_images(data, filename)   # [] when LLM_VISION is off or the file has no pages to show
    plan = make_plan(doc.text, pages)

PDF pages are rendered with pypdfium2, a JPG/PNG is the image itself (a tall image of stacked
A4 pages is cut into pages, as for OCR). DOC/DOCX/XLS(X)/ODS stay text only: they have no
rendering without LibreOffice. Only the first LLM_VISION_MAX_PAGES pages are returned, at a long
side of LLM_VISION_MAX_SIDE px, in grayscale with LLM_VISION_GRAY=1; `data_url` shrinks them
further per pass and encodes them. With LLM_VISION_TEXT=0 the model gets the images without the
extracted text, see `images_only`.
"""

import base64
import io

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.extract.scan import split_pages

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class Pages(list):
    """What `page_images` returns: the page images, plus `total` = pages in the file (more than
    len() when LLM_VISION_MAX_PAGES cut the rest off)."""

    def __init__(self, images=(), total: int | None = None):
        super().__init__(images)
        self.total = len(self) if total is None else total


def page_images(data: bytes, filename: str = "") -> Pages:
    """First pages of a PDF or image file as RGB (or grayscale) images, long side <= LLM_VISION_MAX_SIDE.
    Empty when vision is off, for other formats, and for anything that does not open (the
    text extraction has already accepted or rejected the file)."""
    if not settings.llm_vision or settings.llm_vision_max_pages <= 0:
        return Pages()
    side, cap = settings.llm_vision_max_side, settings.llm_vision_max_pages
    mode = "L" if settings.llm_vision_gray else "RGB"
    if data[:5] == b"%PDF-" or (filename.lower().endswith(".pdf") and b"%PDF-" in data[:1024]):
        return _pdf_pages(data, side, cap, mode)
    if data[:4] == b"PK\x03\x04" or data[:8] == OLE_MAGIC:  # DOCX/XLSX/ODS, DOC/XLS: text only
        return Pages()
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return Pages()
    pages = split_pages(ImageOps.exif_transpose(img))
    return Pages([_fit(p, side, mode) for p in pages[:cap]], len(pages))


def images_only(pages: list[Image.Image]) -> bool:
    """LLM_VISION_TEXT=0 and every page of the file is among `pages`: the model gets the images and
    no extracted text. A file longer than LLM_VISION_MAX_PAGES keeps the text (its later pages are
    nowhere else). The planner (what to send) and the quote threshold both go by this."""
    return bool(pages) and not settings.llm_vision_text and getattr(pages, "total", len(pages)) <= len(pages)


def _pdf_pages(data: bytes, side: int, cap: int, mode: str) -> Pages:
    pdf = pdfium.PdfDocument(data)
    try:
        out = []
        for i in range(min(len(pdf), cap)):
            page = pdf[i]
            scale = side / max(page.get_size())  # points -> px, long side = `side`
            out.append(page.render(scale=scale).to_pil().convert(mode))
        return Pages(out, len(pdf))
    finally:
        pdf.close()


def _fit(img: Image.Image, side: int, mode: str = "RGB") -> Image.Image:
    """Copy in `mode` (RGB or L) with the long side at most `side` px (never upscaled)."""
    img = img.convert(mode)
    img.thumbnail((side, side), Image.Resampling.LANCZOS)
    return img


def data_url(img: Image.Image, side: int | None = None) -> str:
    """`data:image/jpeg;base64,...` of the page (grayscale stays grayscale), shrunk to `side` px first.
    JPEG keeps the request small; tokens depend only on the pixel size (Qwen3-VL: one per 32x32 px)."""
    if side:
        img = _fit(img, side, "L" if img.mode == "L" else "RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def setup() -> dict:
    """Model and vision settings, for bench logs (tells A/B runs apart)."""
    s = settings
    out = {"model": s.llm_model, "vision": s.llm_vision}
    if s.llm_vision:
        out |= {"max_side": s.llm_vision_max_side, "max_pages": s.llm_vision_max_pages,
                "conditions_side": s.llm_vision_conditions_side, "text": s.llm_vision_text,
                "gray": s.llm_vision_gray}
    return out
