"""`/api/history`: the upload page history (`app.storage.history`) and "Źródło" on its files.

"Źródło" (`app.document.sources`) reads a record's file: `POST /{id}/sources` places the quotes of the evidence the page
holds now (edits included) on the paper, `GET /{id}/page/{n}.png` is the page the boxes are drawn on. The paper's
word boxes are cached per record (the file never changes), so only the first request of a scan waits for OCR.
An office file is shown as its LibreOffice PDF (`GET /{id}/pdf`, `app.document.office`), and "Źródło" reads that PDF too.
"""

import json
from functools import lru_cache
from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from app.api.security import current_user
from app.api.upload import check_name, validate_upload
from app.core.config import settings
from app.document import sources
from app.document.headings import section_headings
from app.document.office import is_office, office_pdf
from app.storage import history

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(current_user)])


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Nie ma takiego wpisu w historii.")


def _file(item_id: int) -> tuple[str, bytes]:
    found = history.file(item_id)
    if not found:
        raise _missing()
    name, _type, data = found
    return name, data


@router.get("")
def list_history() -> list[dict]:
    """Upload page history, newest first, without file bytes (those come from /api/history/{id}/file)."""
    return history.records()


@router.get("/{item_id}/file")
def history_file(item_id: int) -> Response:
    """The original uploaded file of one record, with its content type."""
    found = history.file(item_id)
    if not found:
        raise _missing()
    _name, mime, data = found
    return Response(data, media_type=mime or "application/octet-stream")


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
    if not is_office(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ten plik nie jest dokumentem biurowym.")
    pdf = office_pdf(name, data)
    if pdf is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Nie udało się pokazać dokumentu.")
    return Response(pdf, media_type="application/pdf", headers={"Cache-Control": "private, max-age=86400, immutable"})


@router.post("/{item_id}/sources")
def paper_sources(item_id: int, body: Annotated[dict, Body()]) -> dict:
    """Where on the paper each element came from ("Źródło"). Body `{"coi", "evidence"}` as the page holds them ->
    `{"pages": [{"aspect": h / w}], "sources": {element id: place or null}, "headings": {paper heading: page}}`; a place
    is `{page, pages, boxes}` or, for an office file without a PDF, `{lines, text}` (`app.document.sources.locate`)."""
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
    return {"id": history.add(file.filename or "plik", file.content_type or "application/octet-stream", data, state)}


@router.put("/{item_id}")
def update(item_id: int, record: Annotated[dict, Body()]) -> dict:
    """Replace the record's state with the JSON body (the page always sends all of it); the file stays as it was."""
    if not history.update(item_id, record):
        raise _missing()
    return {"id": item_id}


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(item_id: int) -> None:
    history.delete(item_id)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear() -> None:
    history.clear()
