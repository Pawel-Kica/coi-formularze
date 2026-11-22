"""`/api/forms`: one blank form in, the COI e-service JSON out, plus the checks and helpers the editor uses."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.errors import conversion_error
from app.api.security import current_user
from app.api.upload import FILE_BODY, read_single_file, validate_upload
from app.coi.builder import blank
from app.coi.problems import explain
from app.coi.rules import check
from app.core.config import settings
from app.extract import extract
from app.services import pipeline

router = APIRouter(prefix="/api/forms", tags=["forms"], dependencies=[Depends(current_user)])


@router.post("/convert", openapi_extra=FILE_BODY, response_description="Konfiguracja e-usługi COI gotowa do importu")
async def convert(request: Request, evidence: bool = False) -> JSONResponse:
    """One blank form (multipart field `file`) in, the COI JSON out, nothing wrapped around it. `?evidence=1` (the
    editor) returns `{"coi", "evidence": {element id: {"source": "paper" | "model" | "code", "quote", "page", ...}}}`
    instead: where each element came from."""
    filename, data = await read_single_file(request, settings.max_file_size_bytes)
    validate_upload(filename, data)
    try:  # through the module, so tests can monkeypatch it
        if evidence:
            coi, ev = await run_in_threadpool(pipeline.convert_with_evidence, data, filename)
            return JSONResponse({"coi": coi, "evidence": ev})
        coi = await run_in_threadpool(pipeline.convert, data, filename)
    except Exception as error:  # 422 / 502 / 503 / 504 / 500, the same codes a job's result URL gives
        raise conversion_error(error)
    return JSONResponse(coi)


@router.post("/text", openapi_extra=FILE_BODY, response_description="Tekst odczytany z formularza")
async def form_text(request: Request) -> dict:
    """The text of an office form (multipart field `file`): `{"text", "method"}`. No model call. The upload
    page shows it as the original of formats a browser cannot display (DOC, DOCX, ODS, XLSX, XLS) when their
    LibreOffice PDF fails. A PDF gives its text layer; images have no text to read here (422)."""
    filename, data = await read_single_file(request, settings.max_file_size_bytes)
    validate_upload(filename, data)
    try:
        doc = await run_in_threadpool(extract, data, filename)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Nie udało się odczytać formularza: {error}")
    return {"text": doc.text, "method": doc.method}


@router.post("/check", response_description="Problemy z regułami COI, pusta lista = gotowe do importu")
async def check_form(coi: Annotated[dict, Body()]) -> dict:
    """A COI JSON (e.g. after the clerk's edits) checked with the same rules as every convert result:
    `{"problems": [{"id": <element id or null>, "message": <Polish>, "detail": <rules line>}]}`."""
    return {"problems": explain(coi, await run_in_threadpool(check, coi))}


@router.get("/blank", response_description="Pusty formularz do zbudowania w edytorze")
async def blank_form() -> dict:
    """A form to build by hand, no file and no model: `{"coi", "evidence"}` like `convert?evidence=1`, with the
    standard COI pages (Dane, Kontakt i zgody, finish) around one page with one text field."""
    coi, evidence = blank()
    return {"coi": coi, "evidence": evidence}
