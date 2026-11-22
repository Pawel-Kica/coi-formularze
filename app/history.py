"""Upload page history in a local SQLite file (HISTORY_DB), so every conversion shows up in every browser.

One row per processed file: the original bytes (the page's original pane, "Spróbuj ponownie" and ZIP
names need them), `created` (epoch milliseconds, like the page's `Date.now()`) and `state`, one JSON
with whatever else the page keeps per file: status, error, the COI JSON with the clerk's edits
(`result`), where each element came from (`evidence`), the COI rules problems after the last edit
(`problems`), `checking`. Scripts add finished conversions with `add()` directly
(`bench/history_add.py`), no server needed. Never calls a model.

"Źródło" (`app.sources`) reads a record's file: `POST /{id}/sources` places the quotes of the evidence the page
holds now (edits included) on the paper, `GET /{id}/page/{n}.png` is the page the boxes are drawn on. The paper's
word boxes are cached per record (the file never changes), so only the first request of a scan waits for OCR.

An office file (DOC/DOCX/ODS/XLS/XLSX) is shown as the real document: `GET /{id}/pdf` is its PDF made by LibreOffice
headless once (cached on disk by the file's sha256 next to HISTORY_DB), and "Źródło" reads that PDF too, so office
files get framed crops like a PDF. When LibreOffice fails or is missing, or its PDF lost text of the file
(`app.llm.transcribe.text_lost`, the planner's own test, so both fall back on the same files), the page and "Źródło"
use the text read from the file instead.
"""

import hashlib
import io
import json
import logging
import re
import sqlite3
import subprocess
import threading
import time
import zipfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Response, UploadFile, status

from app import sources
from app.headings import section_headings
from app.config import settings
from app.extract import UnsupportedFile
from app.llm.transcribe import file_text, office_convert, text_lost
from app.security import current_user
from app.upload import check_name, validate_upload

OFFICE_EXTS = ("doc", "docx", "odt", "ods", "xls", "xlsx")
SHEET_EXTS = ("ods", "xls", "xlsx")
_SOFFICE = threading.Lock()  # one LibreOffice at a time: it is heavy and the Mac is at its memory limit
log = logging.getLogger(__name__)

SCHEMA = """CREATE TABLE IF NOT EXISTS uploads (  -- not "history": that name had per-field columns in the first version
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, type TEXT NOT NULL, file BLOB NOT NULL,
    created INTEGER NOT NULL,
    state TEXT NOT NULL
)"""


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """One connection per call (commit on success), the table created on first use."""
    with closing(sqlite3.connect(settings.history_db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(SCHEMA)
        with conn:
            yield conn


def _split(record: dict) -> tuple[int | None, str]:
    """A client record -> (created, the rest as JSON). The rest is whatever the page keeps per file
    (status, error, result, evidence, problems, checking, ...), so a new page field needs no schema change."""
    state = {k: v for k, v in record.items() if k not in ("id", "created", "file", "file_name", "file_type", "file_size")}
    return record.get("created"), json.dumps(state, ensure_ascii=False)


def add(name: str, mime: str, data: bytes, record: dict) -> int:
    """Insert one file with its state (missing `created` = now, `status` = done), returns its id."""
    created, state = _split({"status": "done", **record})
    with _db() as conn:
        return conn.execute(
            "INSERT INTO uploads (name, type, file, created, state) VALUES (?, ?, ?, ?, ?)",
            (name, mime, data, created or int(time.time() * 1000), state),
        ).lastrowid


def records() -> list[dict]:
    """Every record without the file bytes, newest first: {id, file_name, file_type, file_size, created, **state}."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, type, length(file) AS size, created, state FROM uploads ORDER BY created DESC, id DESC"
        ).fetchall()
    return [
        {**json.loads(r["state"]), "id": r["id"], "file_name": r["name"], "file_type": r["type"], "file_size": r["size"], "created": r["created"]}
        for r in rows
    ]


router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(current_user)])


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Nie ma takiego wpisu w historii.")


@router.get("")
def list_history() -> list[dict]:
    """Upload page history, newest first, without file bytes (those come from /api/history/{id}/file)."""
    return records()


@router.get("/{item_id}/file")
def history_file(item_id: int) -> Response:
    """The original uploaded file of one record, with its content type."""
    with _db() as conn:
        row = conn.execute("SELECT type, file FROM uploads WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise _missing()
    return Response(row["file"], media_type=row["type"] or "application/octet-stream")


def _file(item_id: int) -> tuple[str, bytes]:
    with _db() as conn:
        row = conn.execute("SELECT name, file FROM uploads WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise _missing()
    return row["name"], row["file"]


def office_pdf(name: str, data: bytes) -> bytes | None:
    """The PDF of an office file (LibreOffice, cached on disk by sha256), None for other formats or when it fails."""
    if name.lower().rsplit(".", 1)[-1] not in OFFICE_EXTS:
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


def _shown(item_id: int) -> tuple[str, bytes]:
    """The file as the original pane shows it: an office file's PDF when there is one, else the file itself."""
    name, data = _file(item_id)
    pdf = office_pdf(name, data)
    return (f"{name}.pdf", pdf) if pdf else (name, data)


@lru_cache(maxsize=16)
def _paper(db: str, item_id: int) -> sources.Paper:
    """Cached per database and record: ids are never reused (AUTOINCREMENT), and tests swap databases."""
    name, data = _shown(item_id)
    return sources.read_paper(data, name)


@router.get("/{item_id}/pdf")
def history_pdf(item_id: int) -> Response:
    """An office record as PDF (the original pane shows it like a PDF), 404 for other formats, 422 when it fails."""
    name, data = _file(item_id)
    if name.lower().rsplit(".", 1)[-1] not in OFFICE_EXTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ten plik nie jest dokumentem biurowym.")
    pdf = office_pdf(name, data)
    if pdf is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Nie udało się pokazać dokumentu.")
    return Response(pdf, media_type="application/pdf", headers={"Cache-Control": "private, max-age=86400, immutable"})


@router.post("/{item_id}/sources")
def paper_sources(item_id: int, body: Annotated[dict, Body()]) -> dict:
    """Where on the paper each element came from ("Źródło" in the preview). Body: `{"coi", "evidence"}` as the page
    holds them now. -> `{"pages": [{"aspect": height / width}], "sources": {element id: place or null},
    "headings": {paper heading: page}}`, a place
    being `{page, pages, boxes}` (boxes as fractions of the page image; office files through their PDF) or
    `{lines, text}` for an office file LibreOffice could not convert, see `app.sources.locate`."""
    try:
        paper = _paper(str(settings.history_db), item_id)
    except HTTPException:
        raise
    except Exception:  # a broken file: the pane says the place is unknown, the rest of the page works
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Nie udało się odczytać pliku.")
    coi, evidence = body.get("coi"), body.get("evidence") or {}
    if not isinstance(evidence, dict) or (coi is not None and not isinstance(coi, dict)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Oczekuję {coi, evidence}.")
    try:
        found = sources.locate(paper, coi, evidence)
        headings = section_headings(paper, coi, evidence) if coi and paper.aspects else {}
    except (KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pole coi nie jest JSON-em e-usługi COI.")
    # paper heading -> its page: "W dokumencie: ..." of an office file (no page in its evidence) jumps the PDF there
    pages = {h: paper.lines[line].page + 1 for h, line in headings.values() if paper.lines[line].page is not None}
    return {"pages": [{"aspect": round(a, 4)} for a in paper.aspects], "sources": found, "headings": pages}


@lru_cache(maxsize=32)
def _page_png(db: str, item_id: int, n: int) -> bytes | None:
    name, data = _shown(item_id)
    return sources.page_png(data, name, n)


@router.get("/{item_id}/page/{n}.png")
def page_image(item_id: int, n: int) -> Response:
    """Page `n` (1-based) of a record's PDF, JPG/PNG or office file's PDF as a PNG about 1600 px wide, for "Źródło"."""
    try:
        png = _page_png(str(settings.history_db), item_id, n)
    except HTTPException:
        raise
    except Exception:
        png = None
    if png is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ten plik nie ma takiej strony.")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400, immutable"})


@router.post("")
def create(file: UploadFile, record: Annotated[str, Form()]) -> dict:
    """New record: multipart `file` (the original) + `record` (JSON with its state fields) -> {id}."""
    data = file.file.read(settings.max_file_size_bytes + 1)
    if len(data) > settings.max_file_size_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Plik jest za duży.")
    if (file.filename or "").lower().endswith(".json"):  # a form started without paper (upload page: a COI JSON or the blank form)
        check_name(file.filename)  # the same name rules as every upload
        try:
            coi = json.loads(data)
        except ValueError:
            coi = None
        if not isinstance(coi, dict) or not isinstance(coi.get("tabs"), list):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Plik JSON nie jest konfiguracją e-usługi COI.")
    else:
        validate_upload(file.filename or "", data)  # the same name, extension and content checks as convert
    try:
        state = json.loads(record)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pole record musi być JSON-em.")
    if not isinstance(state, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pole record musi być obiektem JSON.")
    return {"id": add(file.filename or "plik", file.content_type or "application/octet-stream", data, state)}


@router.put("/{item_id}")
def update(item_id: int, record: Annotated[dict, Body()]) -> dict:
    """Replace the record's state with the JSON body (the page always sends all of it); the file stays as it was."""
    created, state = _split(record)
    with _db() as conn:
        found = conn.execute(
            "UPDATE uploads SET state = ?, created = coalesce(?, created) WHERE id = ?", (state, created, item_id)
        ).rowcount
    if not found:
        raise _missing()
    return {"id": item_id}


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(item_id: int) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM uploads WHERE id = ?", (item_id,))


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear() -> None:
    with _db() as conn:
        conn.execute("DELETE FROM uploads")
