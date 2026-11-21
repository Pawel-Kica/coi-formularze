"""PDF -> raw text with pypdfium2 (Apache-2.0/BSD): the text layer page by page, and OCR for
pages whose text layer is missing or garbage (scanned PDFs, broken font encodings)."""

import pypdfium2 as pdfium

from app.extract.ocr import ocr_pages

POLISH = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
OCR_DPI = 300


def read_pdf(data: bytes, warnings: list[str], engine: str) -> tuple[str, bool]:
    """Returns (text, used_ocr)."""
    pdf = pdfium.PdfDocument(data)
    try:
        texts: list[str | None] = []
        for page in pdf:
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            textpage.close()
            texts.append(text if _looks_like_text(text) else None)
        bad = [i for i, t in enumerate(texts) if t is None]
        if bad:
            images = [pdf[i].render(scale=OCR_DPI / 72, grayscale=True).to_pil() for i in bad]
            for i, text in zip(bad, ocr_pages(images, engine)):
                texts[i] = text
            warnings.append(f"OCR used for {len(bad)} of {len(texts)} PDF pages without a usable text layer")
        return "\n".join(t or "" for t in texts), bool(bad)
    finally:
        pdf.close()


def _looks_like_text(text: str) -> bool:
    """A usable text layer has real words and few letters outside Latin/Polish (broken
    ToUnicode maps show up as private-use, replacement or random CJK/Cyrillic glyphs)."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 20:
        return False
    odd = sum(1 for c in letters if not (c.isascii() or c in POLISH or 0xC0 <= ord(c) <= 0x17F))
    odd += sum(1 for c in text if c == chr(0xFFFD) or 0xE000 <= ord(c) <= 0xF8FF)
    return odd / len(letters) < 0.05
