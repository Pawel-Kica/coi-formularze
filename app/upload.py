"""Receiving one uploaded form: read it into memory under a size cap, then check its
name, extension and real content (magic bytes) before anything parses it.

Files never touch the disk: the multipart body is streamed straight into RAM and
reading stops as soon as it outgrows the limit, whatever Content-Length claims.
"""

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePath

import olefile
from fastapi import HTTPException, Request, status
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpe": "image/jpeg",
    ".png": "image/png",
}
MAX_FILENAME_LENGTH = 250
FORBIDDEN_FILENAME_CHARS = frozenset(";:><\\/|'*%$")
_MULTIPART_OVERHEAD = 64 * 1024  # room for boundaries and part headers on top of the file itself


@dataclass
class _Part:
    disposition: bytes = b""
    data: bytearray = field(default_factory=bytearray)


class _PartCollector:
    """MultipartParser callbacks that keep every part in memory (the body is already capped)."""

    def __init__(self) -> None:
        self.parts: list[_Part] = []
        self._header_name = self._header_value = b""

    def on_part_begin(self) -> None:
        self.parts.append(_Part())

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def on_header_end(self) -> None:
        if self._header_name.lower() == b"content-disposition":
            self.parts[-1].disposition = self._header_value
        self._header_name = self._header_value = b""

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self.parts[-1].data += data[start:end]

    def callbacks(self) -> dict:
        names = ("on_part_begin", "on_header_field", "on_header_value", "on_header_end", "on_part_data")
        return {name: getattr(self, name) for name in names}


def _too_large(max_bytes: int) -> HTTPException:
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        f"Plik jest za duży (limit {max_bytes / 1024 / 1024:g} MB).",
    )


async def read_single_file(request: Request, max_bytes: int) -> tuple[str, bytes]:
    """Stream a multipart/form-data body into memory; return (filename, content) of its one file."""
    content_type, params = parse_options_header(request.headers.get("content-type"))
    if content_type != b"multipart/form-data" or b"boundary" not in params:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Wyślij plik jako multipart/form-data w polu 'file'.")

    body_limit = max_bytes + _MULTIPART_OVERHEAD
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > body_limit:
        raise _too_large(max_bytes)

    collector = _PartCollector()
    parser = MultipartParser(params[b"boundary"], collector.callbacks())
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > body_limit:
                raise _too_large(max_bytes)
            parser.write(chunk)
        parser.finalize()
    except MultipartParseError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uszkodzone żądanie multipart/form-data.")

    files = []
    for part in collector.parts:
        _, options = parse_options_header(part.disposition)
        if options.get(b"filename"):
            files.append((options[b"filename"].decode("utf-8", "replace"), bytes(part.data)))
    if len(files) != 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Wyślij dokładnie jeden plik w polu 'file'.")
    filename, data = files[0]
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    return filename, data


def check_filename(filename: str) -> str:
    """Name rules from the COI spec; returns the lower-cased extension."""
    if len(filename) > MAX_FILENAME_LENGTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Nazwa pliku jest dłuższa niż {MAX_FILENAME_LENGTH} znaków.")
    if bad := sorted(FORBIDDEN_FILENAME_CHARS.intersection(filename)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Nazwa pliku zawiera niedozwolone znaki: {' '.join(bad)}")
    extension = PurePath(filename).suffix.lower()
    if extension not in MIME_BY_EXTENSION:
        allowed = ", ".join(sorted(MIME_BY_EXTENSION))
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Nieobsługiwany format pliku. Dozwolone: {allowed}"
        )
    return extension


def sniff_mime(data: bytes) -> str | None:
    """MIME type from the file's actual bytes, or None if it is none of the supported formats."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(olefile.MAGIC):
        return _sniff_ole(data)
    if data.startswith(b"PK\x03\x04"):
        return _sniff_zip(data)
    if b"%PDF-" in data[:1024]:  # readers accept the header anywhere in the first 1 KB
        return "application/pdf"
    return None


def _sniff_ole(data: bytes) -> str | None:
    """Legacy .doc: an OLE2 compound file holding a WordDocument stream."""
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:  # BytesIO: never treated as a path
            return "application/msword" if ole.exists("WordDocument") else None
    except Exception:  # noqa: BLE001 - corrupt input raises assorted errors: treat it as unknown
        return None


def _sniff_zip(data: bytes) -> str | None:
    """Office Open XML (.docx, .xlsx) or OpenDocument spreadsheet (.ods)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if "mimetype" in names:
                mimetype = archive.read("mimetype").decode("ascii", "replace").strip()
                return mimetype if mimetype == MIME_BY_EXTENSION[".ods"] else None
            if "[Content_Types].xml" in names:
                if any(name.startswith("word/") for name in names):
                    return MIME_BY_EXTENSION[".docx"]
                if any(name.startswith("xl/") for name in names):
                    return MIME_BY_EXTENSION[".xlsx"]
    except Exception:  # noqa: BLE001 - corrupt input raises assorted errors: treat it as unknown
        return None
    return None


def validate_upload(filename: str, data: bytes) -> str:
    """Check one uploaded form (name, extension, content matches extension); return its MIME type."""
    extension = check_filename(filename)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Plik jest pusty.")
    expected = MIME_BY_EXTENSION[extension]
    if sniff_mime(data) != expected:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Zawartość pliku nie pasuje do rozszerzenia {extension} (oczekiwano {expected}).",
        )
    return expected
