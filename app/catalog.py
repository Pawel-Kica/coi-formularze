"""Dictionary catalog: the dictionaries the office keeps, in the same SQLite file as the history (HISTORY_DB).

The COI dictionaries (Kraje, TERYT, Numery kierunkowe) are listed read-only: COI keeps their values. Ours come from
the "Dodaj" button next to a bonus II proposal (or from an admin) and hold {name, code, sources, items}. A form field
references one by `dictionaryName` = its code, without a copy of the values (like Kraje), so the preview reads the
values from here and a value added on the "Słowniki" page shows up without touching the form. Never calls a model.

    GET    /api/dictionaries               every dictionary, COI ones first
    POST   /api/dictionaries               new one {name, code?, source?, items?}; a taken code gets a "_2" suffix
    GET    /api/dictionaries/{code}        one dictionary
    PUT    /api/dictionaries/{code}        replace {name?, items?} (the "Słowniki" page)
    POST   /api/dictionaries/{code}/items  merge {items, source?} without duplicates ("Dodaj" to an existing one)
    DELETE /api/dictionaries/{code}        remove one of ours (COI ones: 403)
"""

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.config import settings
from app.dictionaries import _code
from app.quotes import normalize
from app.security import current_user

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


def _merge(items: list[dict], new) -> list[dict]:
    """Items + new values (strings or {nazwa, kod?}), same name (case, spaces, accents aside) only once."""
    if not isinstance(new, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "items musi być listą.")
    out = [dict(i) for i in items]
    seen = {normalize(i["nazwa"]) for i in out}
    taken = {i["kod"] for i in out}
    for v in new:
        name = (v.get("nazwa") if isinstance(v, dict) else v) or ""
        name = " ".join(str(name).split())
        if not name or normalize(name) in seen:
            continue
        seen.add(normalize(name))
        kod = v.get("kod") if isinstance(v, dict) and v.get("kod") and v["kod"] not in taken else _code(name, taken)
        taken.add(kod)
        out.append({"kod": kod, "nazwa": name})
    return out


def _source(s) -> list[dict]:
    if not isinstance(s, dict) or not (s.get("file") or s.get("field")):
        return []
    return [{"file": str(s.get("file") or ""), "field": str(s.get("field") or "")}]


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


router = APIRouter(prefix="/api/dictionaries", tags=["dictionaries"], dependencies=[Depends(current_user)])


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Nie ma takiego słownika.")


def _ours(code: str) -> dict:
    if code in _BUILTIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Słownik COI jest tylko do odczytu.")
    d = get(code)
    if not d:
        raise _missing()
    return d


@router.get("")
def list_dictionaries() -> list[dict]:
    """COI dictionaries (read-only, no values here) first, then ours oldest first, with their values."""
    return all_()


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: Annotated[dict, Body()]) -> dict:
    """New dictionary: `{name, code?, source?: {file, field}, items?: [str | {kod, nazwa}]}`. Code from the name
    when missing; a taken code (also a COI one) gets "_2", "_3"... -> the dictionary as stored."""
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Podaj nazwę słownika.")
    with _db() as conn:
        taken = {r["code"] for r in conn.execute("SELECT code FROM dictionaries")} | set(_BUILTIN)
        wanted = str(body.get("code") or "").strip()
        code = wanted if wanted and wanted not in taken else _code(wanted or name, taken)
        conn.execute("INSERT INTO dictionaries (code, name, sources, items, created) VALUES (?, ?, ?, ?, ?)",
                     (code, name, json.dumps(_source(body.get("source")), ensure_ascii=False),
                      json.dumps(_merge([], body.get("items") or []), ensure_ascii=False), int(time.time() * 1000)))
    return get(code)


@router.get("/{code}")
def read(code: str) -> dict:
    d = get(code)
    if not d:
        raise _missing()
    return d


@router.put("/{code}")
def replace(code: str, body: Annotated[dict, Body()]) -> dict:
    """Replace the name and/or the values (`{name?, items?}`); the code and sources stay."""
    d = _ours(code)
    name = " ".join(str(body.get("name") or d["name"]).split())
    items = _merge([], body["items"]) if isinstance(body.get("items"), list) else d["items"]
    with _db() as conn:
        conn.execute("UPDATE dictionaries SET name = ?, items = ? WHERE code = ?", (name, json.dumps(items, ensure_ascii=False), code))
    return get(code)


@router.post("/{code}/items")
def add_items(code: str, body: Annotated[dict, Body()]) -> dict:
    """Merge `{items, source?}` into one of ours: values already there (by name) are skipped, a new source is added."""
    d = _ours(code)
    items = _merge(d["items"], body.get("items") or [])
    sources = d["sources"] + [s for s in _source(body.get("source")) if s not in d["sources"]]
    with _db() as conn:
        conn.execute("UPDATE dictionaries SET items = ?, sources = ? WHERE code = ?",
                     (json.dumps(items, ensure_ascii=False), json.dumps(sources, ensure_ascii=False), code))
    return get(code)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete(code: str) -> None:
    _ours(code)
    with _db() as conn:
        conn.execute("DELETE FROM dictionaries WHERE code = ?", (code,))
