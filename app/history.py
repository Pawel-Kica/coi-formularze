"""Upload page history in a local SQLite file (HISTORY_DB), so every conversion shows up in every browser.

One row per processed file: the original bytes (the page's original pane, "Spróbuj ponownie" and ZIP
names need them), `created` (epoch milliseconds, like the page's `Date.now()`) and `state`, one JSON
with whatever else the page keeps per file: status, error, the COI JSON with the clerk's edits
(`result`), where each element came from (`evidence`), the COI rules problems after the last edit
(`problems`), `checking`. Scripts add finished conversions with `add()` directly
(`bench/history_add.py`), no server needed. Never calls a model.
"""

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Response, UploadFile, status

from app.config import settings
from app.security import current_user

SCHEMA = """CREATE TABLE IF NOT EXISTS history (
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
            "INSERT INTO history (name, type, file, created, state) VALUES (?, ?, ?, ?, ?)",
            (name, mime, data, created or int(time.time() * 1000), state),
        ).lastrowid


def records() -> list[dict]:
    """Every record without the file bytes, newest first: {id, file_name, file_type, file_size, created, **state}."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, type, length(file) AS size, created, state FROM history ORDER BY created DESC, id DESC"
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
        row = conn.execute("SELECT type, file FROM history WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise _missing()
    return Response(row["file"], media_type=row["type"] or "application/octet-stream")


@router.post("")
def create(file: UploadFile, record: Annotated[str, Form()]) -> dict:
    """New record: multipart `file` (the original) + `record` (JSON with its state fields) -> {id}."""
    data = file.file.read(settings.max_file_size_bytes + 1)
    if len(data) > settings.max_file_size_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Plik jest za duży.")
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
            "UPDATE history SET state = ?, created = coalesce(?, created) WHERE id = ?", (state, created, item_id)
        ).rowcount
    if not found:
        raise _missing()
    return {"id": item_id}


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(item_id: int) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM history WHERE id = ?", (item_id,))


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear() -> None:
    with _db() as conn:
        conn.execute("DELETE FROM history")
