"""`/api/dictionaries`: the dictionary catalog (`app.storage.catalog`) and bonus II proposals of new dictionaries.

    GET    /api/dictionaries               every dictionary, COI ones first
    POST   /api/dictionaries               new one {name, code?, source?, items?}; a taken code gets a "_2" suffix
    POST   /api/dictionaries/proposals     a form file -> proposals of new dictionaries (calls the model)
    GET    /api/dictionaries/{code}        one dictionary
    PUT    /api/dictionaries/{code}        replace {name?, items?} (the "Słowniki" page)
    POST   /api/dictionaries/{code}/items  merge {items, source?} without duplicates ("Dodaj" to an existing one)
    DELETE /api/dictionaries/{code}        remove one of ours (COI ones: 403)
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.errors import conversion_error
from app.api.security import current_user
from app.api.upload import FILE_BODY, read_single_file, validate_upload
from app.core.config import settings
from app.grounding.quotes import normalize
from app.services import dictionaries
from app.services.dictionaries import _code
from app.storage import catalog

router = APIRouter(prefix="/api/dictionaries", tags=["dictionaries"], dependencies=[Depends(current_user)])


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


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Nie ma takiego słownika.")


def _ours(code: str) -> dict:
    if catalog.is_builtin(code):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Słownik COI jest tylko do odczytu.")
    d = catalog.get(code)
    if not d:
        raise _missing()
    return d


@router.get("")
def list_dictionaries() -> list[dict]:
    """COI dictionaries (read-only, no values here) first, then ours oldest first, with their values."""
    return catalog.all_()


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: Annotated[dict, Body()]) -> dict:
    """New dictionary: `{name, code?, source?: {file, field}, items?: [str | {kod, nazwa}]}`. Code from the name
    when missing; a taken code (also a COI one) gets "_2", "_3"... -> the dictionary as stored."""
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Podaj nazwę słownika.")
    taken = catalog.taken_codes()
    wanted = str(body.get("code") or "").strip()
    code = wanted if wanted and wanted not in taken else _code(wanted or name, taken)
    catalog.insert(code, name, _source(body.get("source")), _merge([], body.get("items") or []))
    return catalog.get(code)


@router.post(
    "/proposals",
    openapi_extra=FILE_BODY,
    response_description="Propozycje nowych słowników COI dla tego formularza (osobno od JSON-a e-usługi)",
)
async def proposals(request: Request) -> JSONResponse:
    """Bonus II: the same upload as /api/forms/convert, back come proposals of new shared dictionaries
    (name, structure kod + nazwa, values or "to fill", the field they fit) plus what code rejected.
    The COI JSON itself never references them; the upload page calls both endpoints in parallel."""
    filename, data = await read_single_file(request, settings.max_file_size_bytes)
    validate_upload(filename, data)
    try:
        result = await run_in_threadpool(dictionaries.propose, data, filename)
    except Exception as error:  # the same codes as /api/forms/convert
        raise conversion_error(error)
    return JSONResponse(result)


@router.get("/{code}")
def read(code: str) -> dict:
    d = catalog.get(code)
    if not d:
        raise _missing()
    return d


@router.put("/{code}")
def replace(code: str, body: Annotated[dict, Body()]) -> dict:
    """Replace the name and/or the values (`{name?, items?}`); the code and sources stay."""
    d = _ours(code)
    name = " ".join(str(body.get("name") or d["name"]).split())
    items = _merge([], body["items"]) if isinstance(body.get("items"), list) else d["items"]
    catalog.update(code, name=name, items=items)
    return catalog.get(code)


@router.post("/{code}/items")
def add_items(code: str, body: Annotated[dict, Body()]) -> dict:
    """Merge `{items, source?}` into one of ours: values already there (by name) are skipped, a new source is added."""
    d = _ours(code)
    items = _merge(d["items"], body.get("items") or [])
    sources = d["sources"] + [s for s in _source(body.get("source")) if s not in d["sources"]]
    catalog.update(code, items=items, sources=sources)
    return catalog.get(code)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete(code: str) -> None:
    _ours(code)
    catalog.delete(code)
