"""PDF -> raw text with pypdfium2 (Apache-2.0/BSD): the text layer page by page. Pages whose layer is missing or
garbage (scans, broken font encodings, including fonts without a Unicode map whose layer is a shifted cipher) are left
out: the pipeline reads every page as an image (`app.llm.transcribe`)."""

import re
import threading

import pypdfium2 as pdfium

POLISH = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
VOWELS = set("aeiouyąęó")
# pdfium is not thread-safe (parallel calls crash the process): every pdfium call in `app` runs under this one lock
# (here and `app.llm.transcribe`). Reentrant, so a locked caller may call another locked reader.
PDFIUM = threading.RLock()
# What a font without a Unicode map gives instead of words: control characters, and letters glued
# together by symbols real words never hold inside (gov.pl "Zgłoszenie pobytu" -> `=JÊRV]HQLHSRE\WX`).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_GLUED = re.compile(r"[^\W\d_][;|=\[\]\\<>{}^~][^\W\d_]")


def read_pdf(data: bytes, warnings: list[str]) -> str:
    """The text layer of every page that has a usable one."""
    texts = page_texts(data)
    good = [t for t in texts if _looks_like_text(t)]
    if len(good) < len(texts):
        warnings.append(f"{len(texts) - len(good)} of {len(texts)} PDF pages have no usable text layer, left out")
    return "\n".join(good)


def page_texts(data: bytes) -> list[str]:
    """The raw text layer of every page (empty for a page without one), under PDFIUM."""
    with PDFIUM:
        pdf = pdfium.PdfDocument(data)
        try:
            texts = []
            for page in pdf:
                textpage = page.get_textpage()
                texts.append(textpage.get_text_range())
                textpage.close()
            return texts
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
    return odd / len(letters) < 0.05 and not _garbled(text, letters)


def _garbled(text: str, letters: list[str]) -> bool:
    """A shifted-cipher text layer (fonts without a ToUnicode map) is mostly ASCII, so the check
    above lets it through. Two signs: letters in impossible tokens (see _CONTROL, _GLUED), and,
    for a cipher of letters only, too few vowels. Measured on 122 good pages (9 COI packs, 25 BIP
    and 30 Drive forms): impossible-token share <= 0.025, vowels >= 0.38; the garbled gov.pl
    pages: share 0.19 (half-garbled page) and 0.80."""
    tokens = [t for t in text.split() if any(c.isalpha() for c in t)]
    bad = [t for t in tokens if _CONTROL.search(t) or _GLUED.search(t)]
    if len(bad) >= 3 and sum(c.isalpha() for t in bad for c in t) / len(letters) >= 0.08:
        return True
    latin = [c.lower() for c in letters if c.isascii() or c in POLISH]
    return len(latin) >= 200 and sum(c in VOWELS for c in latin) / len(latin) < 0.30
