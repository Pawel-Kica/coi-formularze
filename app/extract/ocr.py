"""Scans -> text. Default engine: tesseract (`pol`) after preprocessing (scan.py). Option: a
vision model through Ollama. See bench/extract_report.py --ocr-bench for the numbers.

Engine per call, default from env `OCR_ENGINE` (`tesseract` | `vlm`).
VLM settings: `OCR_VLM_MODEL` (default qwen2.5vl:7b), `LLM_URL` (default http://localhost:11434).
"""

import base64
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
import pytesseract
from PIL import Image, ImageFilter, ImageOps

from app.extract import scan

# Pages are OCRed in parallel, one tesseract process each; keep each one single-threaded.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

VLM_PROMPT = (
    "Transcribe all text in this image of a Polish form exactly, line by line, top to bottom. "
    "Keep Polish characters. Write dotted or underlined fill-in lines as '____' and empty checkboxes as '[ ]'. "
    "Output only the transcription."
)


@dataclass(frozen=True)
class Prep:
    """Tesseract preprocessing switches; defaults are the benchmarked best."""

    min_width: int = 1100  # pages narrower than this (A4 below ~130 dpi) are upscaled ...
    target_width: int = 1654  # ... to this width (200 dpi); tesseract reads small text badly
    orient: bool = True
    deskew: bool = True
    fixes: bool = True  # also try a median filter on noisy pages and sharpening on blurry ones
    rules: bool = True  # erase horizontal rules, re-insert dotted ones as ____
    psm: int = 4  # one column of lines of varying size: reading order closest to a PDF text layer


DEFAULT = Prep()


class OcrError(RuntimeError):
    """The OCR engine itself failed (tesseract missing, model server down): not the file's fault."""


def default_engine() -> str:
    return os.environ.get("OCR_ENGINE", "tesseract").strip().lower() or "tesseract"


def ocr_image(img: Image.Image, engine: str | None = None, prep: Prep = DEFAULT) -> str:
    """One image file (may hold several pages stacked vertically) -> text."""
    img = ImageOps.exif_transpose(img)
    return "\n".join(ocr_pages(scan.split_pages(img), engine, prep))


def ocr_pages(pages: list[Image.Image], engine: str | None = None, prep: Prep = DEFAULT) -> list[str]:
    engine = engine or default_engine()
    try:
        if engine == "vlm":
            return [vlm_page(p) for p in pages]  # sequential: the model server is the bottleneck
        if engine != "tesseract":
            raise OcrError(f"unknown OCR engine {engine!r}")
        with ThreadPoolExecutor(max(1, min(len(pages), os.cpu_count() or 2))) as ex:
            return list(ex.map(lambda p: tesseract_page(p, prep), pages))
    except (httpx.HTTPError, pytesseract.TesseractNotFoundError) as e:
        raise OcrError(f"OCR engine {engine!r} failed: {e}") from e


def tesseract_page(img: Image.Image, prep: Prep = DEFAULT) -> str:
    """Preprocess, then OCR. A noisy page is also read median-filtered and a blurry one also
    sharpened; tesseract's own confidence picks the reading (the fix helps some pages and
    hurts others, so we let the result decide)."""
    width = prep.target_width if img.width < prep.min_width else 0
    g = scan.prepare(img, width, prep.orient, prep.deskew)
    candidates = [g]
    if prep.fixes and scan.is_noisy(g):
        candidates.append(g.filter(ImageFilter.MedianFilter(3)))
    if prep.fixes and scan.is_blurry(g):
        candidates.append(g.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3)))
    return max((_tesseract(c, prep) for c in candidates), key=lambda r: r[1])[0]


def _tesseract(g: Image.Image, prep: Prep) -> tuple[str, float]:
    """-> (text, confidence score = sum over words of confidence x length)."""
    config = f"--psm {prep.psm}"
    rules: list[scan.Rule] = []
    if prep.rules:
        mask, rules = scan.find_rules(g)
        g = scan.erase(g, mask)
    data = pytesseract.image_to_data(g, lang="pol", config=config, output_type=pytesseract.Output.DICT)
    score = scan.word_score(data)
    lines = scan.lines_with_rules(data, rules, scan.ink_mask(g))
    return clean_ocr("\n".join(lines)), score


_DOT_NOISE = re.compile(r"(?<!\w)[.,:;'\"`_\-~|" + "".join(map(chr, (0x2018, 0x2019, 0x201A, 0x201E, 0x201D, 0x2026))) + r"]{4,}")


def clean_ocr(text: str) -> str:
    """Leftover dot leaders read as punctuation runs -> plain dots (normalization makes them ____)."""
    return _DOT_NOISE.sub(" ..... ", text)


def vlm_page(img: Image.Image, model: str | None = None, max_side: int = 1600) -> str:
    """Transcribe one page with a vision model through Ollama /api/chat."""
    model = model or os.environ.get("OCR_VLM_MODEL", "qwen2.5vl:7b")
    url = os.environ.get("LLM_URL", "http://localhost:11434").rstrip("/")
    g = img.convert("L")
    g.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    g.save(buf, format="PNG")
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        # num_predict caps runaway repetition (seen: endless "[ ] [ ]" on digit boxes)
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 3000},
        "messages": [{"role": "user", "content": VLM_PROMPT, "images": [base64.b64encode(buf.getvalue()).decode()]}],
    }
    r = httpx.post(f"{url}/api/chat", json=payload, timeout=900)
    r.raise_for_status()
    return r.json()["message"]["content"]
