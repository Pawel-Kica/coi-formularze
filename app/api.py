"""REST API: send one blank form, get back the COI e-service JSON.

Run: `uv run uvicorn app.api:app --reload`. Docs at /docs, upload page at /.
"""

from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from app import pipeline
from app.config import settings
from app.errors import conversion_error
from app.llm.client import health as llm_health
from app.rules import check
from app.upload import read_single_file, validate_upload

INDEX_HTML = Path(__file__).parent / "static" / "index.html"

app = FastAPI(
    title="COI Cyfryzacja Formularzy",
    description="Pusty formularz (PDF, DOC, DOCX, ODS, XLSX, XLS, JPG, PNG) na konfigurację e-usługi COI (JSON).",
    version="0.1.0",
)

_FILE_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}}}}}}}


@app.post(
    "/api/forms/convert",
    openapi_extra=_FILE_BODY,
    tags=["forms"],
    response_description="Konfiguracja e-usługi COI gotowa do importu",
)
async def convert(request: Request, evidence: bool = False) -> JSONResponse:
    """One blank form (multipart field `file`) in, the COI JSON out, nothing wrapped around it.

    `?evidence=1` wraps it instead: `{"coi": <COI JSON>, "evidence": {<element id>: {"source": "paper" | "model" |
    "code", "quote", ...}}}`, where each element of the e-service came from (the quote from the paper for fields)."""
    filename, data = await read_single_file(request, settings.max_file_size_bytes)
    validate_upload(filename, data)
    try:
        if evidence:
            coi, ev = await run_in_threadpool(pipeline.convert_with_evidence, data, filename)
            return JSONResponse({"coi": coi, "evidence": ev})
        coi = await run_in_threadpool(pipeline.convert, data, filename)
    except Exception as error:  # 422 / 502 / 503 / 504 / 500
        raise conversion_error(error)
    return JSONResponse(coi)


@app.post("/api/forms/check", tags=["forms"], response_description="Problemy z regułami COI, pusta lista = gotowe do importu")
async def check_form(coi: Annotated[dict, Body()]) -> dict:
    """A COI JSON (e.g. edited by hand) checked with the same rules as every convert result: `{"problems": [...]}`."""
    return {"problems": await run_in_threadpool(check, coi)}


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Liveness plus whether the models are usable: EDENAI_API_KEY set ("ok" | "no_api_key"; Eden is not called)."""
    return {"status": "ok", "llm": llm_health(), "llm_model": settings.llm_model,
            "transcribe_model": settings.transcribe_model}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML)


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, _error: Exception) -> JSONResponse:
    """Keep errors JSON even for bugs; the traceback still goes to the server log."""
    return JSONResponse({"detail": "Błąd wewnętrzny serwera."}, status.HTTP_500_INTERNAL_SERVER_ERROR)
