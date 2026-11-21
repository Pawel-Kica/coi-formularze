"""File bytes -> normalized text of a blank form. The pipeline's first stage.

    from app.extract import extract, Document
    doc = extract(data, filename)   # doc.text, doc.method, doc.warnings

Formats: PDF (text layer, OCR for scanned pages), DOCX, legacy DOC (own binary parser),
ODS (and ODT), XLSX, images (JPG/JPEG/PNG and anything else Pillow opens) via OCR.
The format is sniffed from the bytes; the extension is only a tiebreaker (real files
lie: some `.DOC` here are DOCX). Everything stays in memory.
Same form in any format -> nearly the same text (see normalize.py).
"""

import io
import zipfile
from dataclasses import dataclass, field

from PIL import Image, UnidentifiedImageError

from app.extract.doc import read_doc
from app.extract.docx import read_docx
from app.extract.normalize import normalize
from app.extract.ocr import OcrError, default_engine, ocr_image
from app.extract.pdf import read_pdf
from app.extract.sheets import read_odf, read_xlsx


class UnsupportedFile(ValueError):
    """The file is not a supported form format or cannot be read."""


@dataclass
class Document:
    text: str
    method: str  # "pdf-text", "pdf-ocr", "docx", "doc", "ods", "odt", "xlsx", "ocr", "vlm"
    warnings: list[str] = field(default_factory=list)


OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def extract(data: bytes, filename: str = "", ocr_engine: str | None = None) -> Document:
    """Bytes of one file -> Document. Raises UnsupportedFile for anything we cannot read, and
    OcrError when the OCR engine itself is unavailable (a server problem, not a bad file).
    `ocr_engine` ("tesseract" | "vlm") overrides env OCR_ENGINE for scans."""
    engine = ocr_engine or default_engine()
    warnings: list[str] = []
    try:
        raw, method = _read(data, filename, warnings, engine)
    except (UnsupportedFile, OcrError):
        raise
    except Exception as e:  # corrupt files surface as library-specific errors
        raise UnsupportedFile(f"cannot read {filename or 'file'}: {e.__class__.__name__}: {e}") from e
    text = normalize(raw)
    if not text.strip():
        warnings.append("no text found")
    return Document(text=text, method=method, warnings=warnings)


def _read(data: bytes, filename: str, warnings: list[str], engine: str) -> tuple[str, str]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not data:
        raise UnsupportedFile("empty file")
    if data[:5] == b"%PDF-" or (ext == "pdf" and b"%PDF-" in data[:1024]):
        text, ocr = read_pdf(data, warnings, engine)
        return text, "pdf-ocr" if ocr else "pdf-text"
    if data[:4] == b"PK\x03\x04":
        return _read_zip(data)
    if data[:8] == OLE_MAGIC:
        return read_doc(data), "doc"  # raises DocError (a ValueError) for non-Word OLE files
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise UnsupportedFile(f"unsupported file type ({ext or 'no extension'})") from e
    return ocr_image(img, engine), "vlm" if engine == "vlm" else "ocr"


def _read_zip(data: bytes) -> tuple[str, str]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    if "word/document.xml" in names:
        return read_docx(zf), "docx"
    if "xl/workbook.xml" in names:
        return read_xlsx(data), "xlsx"
    if "content.xml" in names:
        mime = zf.read("mimetype").decode("ascii", "replace").strip() if "mimetype" in names else ""
        return read_odf(zf), "odt" if mime.endswith(".text") else "ods"
    raise UnsupportedFile("zip archive that is not DOCX, XLSX or ODS")
