"""Dictionary catalog store: the dictionaries the office keeps, in the same SQLite file as the history (HISTORY_DB).

The COI dictionaries (Kraje, TERYT, Numery kierunkowe) are listed read-only: COI keeps their values. Ours come from
the "Dodaj" button next to a bonus II proposal (or from an admin) and hold {name, code, sources, items}. A form field
references one by `dictionaryName` = its code, without a copy of the values (like Kraje), so the preview reads the
values from here and a value added on the "Słowniki" page shows up without touching the form. Never calls a model.
The HTTP side is `app.api.dictionaries`.
"""

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager

from app.core.config import settings

SCHEMA = """CREATE TABLE IF NOT EXISTS dictionaries (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sources TEXT NOT NULL,  -- JSON [{file, field}]: where it came from
    items TEXT NOT NULL,    -- JSON [{kod, nazwa}]
    created INTEGER NOT NULL
)"""

# The Słowniki doc of the task; codes as the builder writes them in `dictionaryName`.
BUILTIN = [
    {"code": "Kraje", "name": "Kraje", "note": "Lista państw. Wartości prowadzi COI."},
    {"code": "TERYT_4G_DOWODOWY", "name": "TERYT: województwo, powiat, gmina, miejscowość",
     "note": "Drzewo adresowe z rejestru TERYT. Wartości prowadzi COI."},
    {"code": "Numery kierunkowe", "name": "Numery kierunkowe", "note": "Prefiksy telefonów, np. +48 dla Polski. Wartości prowadzi COI."},
]
_BUILTIN = {d["code"]: d for d in BUILTIN}


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(settings.history_db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(SCHEMA)
        with conn:
            yield conn


def _builtin(d: dict) -> dict:
    return {**d, "builtin": True, "sources": [], "items": []}


def _row(r: sqlite3.Row) -> dict:
    return {"code": r["code"], "name": r["name"], "builtin": False, "sources": json.loads(r["sources"]),
            "items": json.loads(r["items"]), "created": r["created"]}


def get(code: str) -> dict | None:
    if code in _BUILTIN:
        return _builtin(_BUILTIN[code])
    with _db() as conn:
        r = conn.execute("SELECT * FROM dictionaries WHERE code = ?", (code,)).fetchone()
    return _row(r) if r else None


def all_() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM dictionaries ORDER BY created, code").fetchall()
    return [_builtin(d) for d in BUILTIN] + [_row(r) for r in rows]


def is_builtin(code: str) -> bool:
    return code in _BUILTIN


def taken_codes() -> set[str]:
    """Every code in use, ours and the COI ones."""
    with _db() as conn:
        return {r["code"] for r in conn.execute("SELECT code FROM dictionaries")} | set(_BUILTIN)


def insert(code: str, name: str, sources: list[dict], items: list[dict]) -> None:
    with _db() as conn:
        conn.execute("INSERT INTO dictionaries (code, name, sources, items, created) VALUES (?, ?, ?, ?, ?)",
                     (code, name, json.dumps(sources, ensure_ascii=False), json.dumps(items, ensure_ascii=False),
                      int(time.time() * 1000)))


def update(code: str, *, name: str | None = None, items: list[dict] | None = None,
           sources: list[dict] | None = None) -> None:
    """Set the given columns of one of ours."""
    cols = {k: v if k == "name" else json.dumps(v, ensure_ascii=False)
            for k, v in (("name", name), ("items", items), ("sources", sources)) if v is not None}
    if cols:
        with _db() as conn:
            conn.execute(f"UPDATE dictionaries SET {', '.join(f'{k} = ?' for k in cols)} WHERE code = ?",
                         (*cols.values(), code))


def delete(code: str) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM dictionaries WHERE code = ?", (code,))
