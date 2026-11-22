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
"""

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Response, UploadFile, status

from app import sources
from app.config import settings
from app.security import current_user
from app.upload import validate_upload

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


@lru_cache(maxsize=16)
def _paper(db: str, item_id: int) -> sources.Paper:
    """Cached per database and record: ids are never reused (AUTOINCREMENT), and tests swap databases."""
    name, data = _file(item_id)
    return sources.read_paper(data, name)


@router.post("/{item_id}/sources")
def paper_sources(item_id: int, body: Annotated[dict, Body()]) -> dict:
    """Where on the paper each element came from ("Źródło" in the preview). Body: `{"coi", "evidence"}` as the page
    holds them now. -> `{"pages": [{"aspect": height / width}], "sources": {element id: place or null}}`, a place
    being `{page, pages, boxes}` (boxes as fractions of the page image) or `{lines, text}` for office formats,
    see `app.sources.locate`."""
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
    except (KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pole coi nie jest JSON-em e-usługi COI.")
    return {"pages": [{"aspect": round(a, 4)} for a in paper.aspects], "sources": found}


@lru_cache(maxsize=32)
def _page_png(db: str, item_id: int, n: int) -> bytes | None:
    name, data = _file(item_id)
    return sources.page_png(data, name, n)


@router.get("/{item_id}/page/{n}.png")
def page_image(item_id: int, n: int) -> Response:
    """Page `n` (1-based) of a record's PDF or JPG/PNG as a PNG about 1600 px wide, for "Źródło"."""
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
