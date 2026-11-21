"""Upload page history in a local SQLite file (HISTORY_DB), so every conversion shows up in every browser.

One row per processed file: the original bytes (the page's original pane, "Spróbuj ponownie" and ZIP
names need them) plus its state: status, error, the COI JSON with the clerk's edits (`result`), where
each element came from (`evidence`) and the COI rules problems after the last edit (`problems`).
`created` is epoch milliseconds, like the page's `Date.now()`. Scripts add finished conversions with
`add()` directly (`bench/history_add.py`), no server needed. Never calls a model.
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

JSON_FIELDS = ("result", "evidence", "problems")
STATE_FIELDS = ("created", "status", "error", *JSON_FIELDS)  # what PUT may change; the file never changes

SCHEMA = """CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, type TEXT NOT NULL, file BLOB NOT NULL,
    created INTEGER NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
    result TEXT, evidence TEXT, problems TEXT
)"""


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """One connection per call (commit on success), the table created on first use."""
    with closing(sqlite3.connect(settings.history_db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(SCHEMA)
        with conn:
            yield conn


def _state(record: dict) -> dict:
    """The known state fields of a client record, JSON fields serialized."""
    out = {k: record[k] for k in STATE_FIELDS if k in record}
    for k in JSON_FIELDS:
        if k in out:
            out[k] = json.dumps(out[k], ensure_ascii=False)
    if out.get("error") is None:
        out.pop("error", None)
    return out


def add(name: str, mime: str, data: bytes, record: dict) -> int:
    """Insert one file with its state (missing `created` = now, `status` = done), returns its id."""
    row = {"created": int(time.time() * 1000), "status": "done", **_state(record), "name": name, "type": mime, "file": data}
    with _db() as conn:
        cols = ", ".join(row)
        return conn.execute(f"INSERT INTO history ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values())).lastrowid


def records() -> list[dict]:
    """Every record without the file bytes, newest first, with file_name, file_type, file_size."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, type, length(file) AS size, created, status, error, result, evidence, problems"
            " FROM history ORDER BY created DESC, id DESC"
        ).fetchall()
    out = []
    for r in rows:
        rec = {"id": r["id"], "file_name": r["name"], "file_type": r["type"], "file_size": r["size"]}
        rec |= {k: r[k] for k in ("created", "status", "error")}
        rec |= {k: json.loads(r[k]) if r[k] is not None else None for k in JSON_FIELDS}
        out.append(rec)
    return out


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
    """Replace the state fields given in the JSON body; the file stays as it was."""
    state = _state(record)
    with _db() as conn:
        if state:
            sets = ", ".join(f"{k} = ?" for k in state)
            found = conn.execute(f"UPDATE history SET {sets} WHERE id = ?", [*state.values(), item_id]).rowcount
        else:
            found = conn.execute("SELECT 1 FROM history WHERE id = ?", (item_id,)).fetchone()
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
