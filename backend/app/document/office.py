"""Office originals shown as the real document: DOC/DOCX/ODT/ODS/XLS/XLSX -> PDF by LibreOffice headless, once.

The PDF is cached on disk by the file's sha256 next to HISTORY_DB. When LibreOffice fails or is missing, or its PDF
lost text of the file (`app.llm.transcribe.text_lost`, the planner's own test, so both fall back on the same files),
there is no PDF and callers use the text read from the file instead.
"""

import hashlib
import io
import logging
import re
import subprocess
import threading
import zipfile

from app.core.config import settings
from app.extract import UnsupportedFile
from app.llm.transcribe import file_text, office_convert, text_lost

OFFICE_EXTS = ("doc", "docx", "odt", "ods", "xls", "xlsx")
SHEET_EXTS = ("ods", "xls", "xlsx")
_SOFFICE = threading.Lock()  # one LibreOffice at a time: it is heavy
log = logging.getLogger(__name__)


def is_office(name: str) -> bool:
    return name.lower().rsplit(".", 1)[-1] in OFFICE_EXTS


def office_pdf(name: str, data: bytes) -> bytes | None:
    """The PDF of an office file (LibreOffice, cached on disk by sha256), None for other formats or when it fails."""
    if not is_office(name):
        return None
    sha = hashlib.sha256(data).hexdigest()
    cached = settings.history_db.with_name(f"{settings.history_db.stem}-pdf") / f"{sha}.pdf"
    failed = cached.with_suffix(".failed")  # LibreOffice failed or lost text once: don't wait for it again
    with _SOFFICE:  # also makes a second request wait for the first conversion instead of starting its own
        if cached.exists():
            return cached.read_bytes()
        if failed.exists():
            return None
        cached.parent.mkdir(parents=True, exist_ok=True)
        try:
            pdf = _to_pdf(data, name)
            lost = text_lost(file_text(data, name), pdf)  # the planner's own test: both fall back on the same files
        except (UnsupportedFile, OSError, subprocess.SubprocessError) as e:
            log.warning("office file %s not shown as PDF: %s", name, e)
            failed.write_text(str(e))
            return None
        if lost:
            log.warning("office file %s not shown as PDF: LibreOffice lost text of the file", name)
            failed.write_text("text lost")
            return None
        cached.write_bytes(pdf)
        return pdf


def _to_pdf(data: bytes, name: str) -> bytes:
    """LibreOffice PDF of an office file. A sheet is printed one page wide (LibreOffice's default cuts a form wider
    than A4 into column strips on separate pages): any sheet -> ODS, its page styles get "fit to 1 page wide"."""
    ext = name.lower().rsplit(".", 1)[-1]
    if ext in SHEET_EXTS:
        data, name = _fit_width(data if ext == "ods" else office_convert(data, name, "ods")), "form.ods"
    return office_convert(data, name, "pdf")


def _fit_width(ods: bytes) -> bytes:
    """The ODS with every page style scaled to 1 page wide, any number of pages tall."""
    def fit(m: re.Match) -> str:
        tag = re.sub(r'\s(?:style|loext):scale-to(?:-pages|-X|-Y)?="[^"]*"', "", m.group(0))
        return tag.replace("<style:page-layout-properties", '<style:page-layout-properties loext:scale-to-X="1" loext:scale-to-Y="0"', 1)
    src, out = zipfile.ZipFile(io.BytesIO(ods)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():  # same order and compression: "mimetype" stays first and stored
            body = src.read(item.filename)
            if item.filename == "styles.xml":
                if b"xmlns:loext=" not in body:
                    body = body.replace(b"<office:document-styles ", b'<office:document-styles xmlns:loext="urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0" ', 1)
                body = re.sub(r"<style:page-layout-properties[^>]*>", fit, body.decode()).encode()
            dst.writestr(item, body, compress_type=item.compress_type)
    return out.getvalue()
