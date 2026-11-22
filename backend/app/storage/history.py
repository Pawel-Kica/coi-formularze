"""Upload page history in a local SQLite file (HISTORY_DB), so every conversion shows up in every browser.

One row per processed file: the original bytes (the page's original pane, "Spróbuj ponownie" and ZIP
names need them), `created` (epoch milliseconds, like the page's `Date.now()`) and `state`, one JSON
with whatever else the page keeps per file: status, error, the COI JSON with the clerk's edits
(`result`), where each element came from (`evidence`), the COI rules problems after the last edit
(`problems`), `checking`. Scripts add finished conversions with `add()` directly, no server needed.
Never calls a model. The HTTP side is `app.api.history`.
"""

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager

from app.core.config import settings

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


def file(item_id: int) -> tuple[str, str, bytes] | None:
    """(name, content type, bytes) of one record's original file, None when there is no such record."""
    with _db() as conn:
        row = conn.execute("SELECT name, type, file FROM uploads WHERE id = ?", (item_id,)).fetchone()
    return (row["name"], row["type"], row["file"]) if row else None


def update(item_id: int, record: dict) -> bool:
    """Replace the record's state (the file stays as it was); False when there is no such record."""
    created, state = _split(record)
    with _db() as conn:
        return bool(conn.execute(
            "UPDATE uploads SET state = ?, created = coalesce(?, created) WHERE id = ?", (state, created, item_id)
        ).rowcount)


def delete(item_id: int) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM uploads WHERE id = ?", (item_id,))


def clear() -> None:
    with _db() as conn:
        conn.execute("DELETE FROM uploads")
