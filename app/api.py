"""REST API: log in for a JWT, send one blank form, get back the COI e-service JSON.

Run: `uv run uvicorn app.api:app --reload`. Docs at /docs, upload page at /.
"""

from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app import history, pipeline, review
from app.dictionaries import propose as propose_dictionaries
from app.config import settings
from app.extract import extract
from app.llm.client import health as llm_health
from app.problems import explain
from app.rules import check
from app.security import authenticate, current_user, issue_token, local_login_enabled
from app.upload import read_single_file, validate_upload

INDEX_HTML = Path(__file__).parent / "static" / "index.html"
PREVIEW_HTML = Path(__file__).parent / "static" / "preview.html"
EDITOR_JS = Path(__file__).parent / "static" / "editor.js"

app = FastAPI(
    title="COI Cyfryzacja Formularzy",
    description="Pusty formularz papierowy (PDF, DOC, DOCX, ODS, XLSX, XLS, JPG, PNG) na konfigurację e-usługi COI (JSON).",
    version="0.1.0",
)
app.include_router(review.router)  # dev-only /review, 404 unless REVIEW_RUNS_DIR is set
app.include_router(history.router)  # /api/history: upload page history in SQLite (HISTORY_DB)


class Credentials(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


def _body(schema: dict, *content_types: str) -> dict:
    """OpenAPI request body for endpoints that read the raw request themselves."""
    return {"requestBody": {"required": True, "content": {ct: {"schema": schema} for ct in content_types}}}


_CREDENTIALS_BODY = _body(Credentials.model_json_schema(), "application/json", "application/x-www-form-urlencoded")
_FILE_BODY = _body(
    {"type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}}},
    "multipart/form-data",
)


@app.get("/api/auth/config", tags=["auth"])
async def auth_config() -> dict:
    """Whether a token is required (AUTH_ENABLED); the pages skip the login form when it is not."""
    return {"enabled": settings.auth_enabled}


@app.post("/api/auth/token", openapi_extra=_CREDENTIALS_BODY, tags=["auth"])
async def login(request: Request) -> Token:
    """Exchange the configured login and password (JSON or form) for a Bearer JWT."""
    if not local_login_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Logowanie lokalne jest wyłączone (brak AUTH_PASSWORD lub tokeny wydaje zewnętrzny serwer tożsamości).",
        )
    is_json = request.headers.get("content-type", "").startswith("application/json")
    try:
        raw = await request.json() if is_json else dict(await request.form())
        credentials = Credentials.model_validate(raw)
    except ValueError:  # bad JSON or missing fields (pydantic ValidationError is a ValueError)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Podaj username i password.")
    if not authenticate(credentials.username, credentials.password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Błędny login lub hasło.", headers={"WWW-Authenticate": "Bearer"}
        )
    return Token(access_token=issue_token(credentials.username), expires_in=settings.jwt_ttl_minutes * 60)


@app.post(
    "/api/forms/convert",
    openapi_extra=_FILE_BODY,
    tags=["forms"],
    response_description="Konfiguracja e-usługi COI gotowa do importu",
)
async def convert(
    request: Request, _user: Annotated[str, Depends(current_user)], evidence: bool = False
) -> JSONResponse:
    """One blank form (multipart field `file`) in, the COI JSON out, nothing wrapped around it.

    `?evidence=1` (the upload page's editor) wraps it instead: `{"coi": <COI JSON>, "evidence":
    {<element id>: {"source": "paper" | "model" | "code", "quote", "page", ...}}}`, where each
    element of the e-service came from (the quote from the paper for fields)."""
    filename, data = await read_single_file(request, settings.max_file_size_bytes)
    validate_upload(filename, data)
    try:
        if evidence:
            coi, ev = await run_in_threadpool(pipeline.convert_with_evidence, data, filename)
            return JSONResponse({"coi": coi, "evidence": ev})
        coi = await run_in_threadpool(pipeline.convert, data, filename)
    except httpx.HTTPStatusError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Model językowy zwrócił błąd.")
    except httpx.TimeoutException, TimeoutError:  # the model is up but too slow: not "niedostępny"
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "Model nie odpowiedział na czas. Spróbuj ponownie.")
    except httpx.TransportError, ConnectionError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model językowy jest niedostępny.")
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Nie udało się odczytać formularza: {error}")
    return JSONResponse(coi)


@app.post("/api/forms/text", openapi_extra=_FILE_BODY, tags=["forms"], response_description="Tekst odczytany z formularza")
async def form_text(request: Request, _user: Annotated[str, Depends(current_user)]) -> dict:
    """The text read from one form (multipart field `file`), the same text the model gets:
    `{"text", "method"}`. No model call. The upload page shows it as the original for formats a
    browser cannot display (DOC, DOCX, ODS, XLSX, XLS)."""
    filename, data = await read_single_file(request, settings.max_file_size_bytes)
    validate_upload(filename, data)
    try:
        doc = await run_in_threadpool(extract, data, filename)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Nie udało się odczytać formularza: {error}")
    return {"text": doc.text, "method": doc.method}


@app.post("/api/forms/check", tags=["forms"], response_description="Problemy z regułami COI, pusta lista = gotowe do importu")
async def check_form(coi: Annotated[dict, Body()], _user: Annotated[str, Depends(current_user)]) -> dict:
    """A COI JSON (e.g. after the clerk's edits) checked with the same rules as every convert result:
    `{"problems": [{"id": <element id or null>, "message": <Polish>, "detail": <rules line>}]}`."""
    return {"problems": explain(coi, await run_in_threadpool(check, coi))}


@app.post(
    "/api/dictionaries/proposals",
    openapi_extra=_FILE_BODY,
    tags=["forms"],
    response_description="Propozycje nowych słowników COI dla tego formularza (osobno od JSON-a e-usługi)",
)
async def dictionary_proposals(request: Request, _user: Annotated[str, Depends(current_user)]) -> JSONResponse:
    """Bonus II: the same upload as /api/forms/convert, back come proposals of new shared dictionaries
    (name, structure kod + nazwa, values or "to fill", the field they fit) plus what code rejected.
    The COI JSON itself never references them; the upload page calls both endpoints in parallel."""
    filename, data = await read_single_file(request, settings.max_file_size_bytes)
    validate_upload(filename, data)
    try:
        result = await run_in_threadpool(propose_dictionaries, data, filename)
    except httpx.HTTPStatusError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Model językowy zwrócił błąd.")
    except httpx.TransportError, ConnectionError, TimeoutError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model językowy jest niedostępny.")
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Nie udało się odczytać formularza: {error}")
    return JSONResponse(result)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Liveness (always 200 while the API runs) plus whether the model is usable:
    Ollama up and LLM_MODEL pulled, or for a remote API whether its key is set."""
    llm = await llm_health()
    return {"status": "ok", "llm": llm, "llm_backend": settings.llm_backend, "llm_model": settings.llm_model}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML)


@app.get("/preview", include_in_schema=False)
async def preview() -> FileResponse:
    """E-service preview: renders a COI JSON the way a citizen sees it (bonus V)."""
    return FileResponse(PREVIEW_HTML)


@app.get("/editor.js", include_in_schema=False)
async def editor_js() -> FileResponse:
    """Edit mode of /preview (the click editor on the upload page)."""
    return FileResponse(EDITOR_JS, media_type="text/javascript")


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, _error: Exception) -> JSONResponse:
    """Keep errors JSON even for bugs; the traceback still goes to the server log."""
    return JSONResponse({"detail": "Błąd wewnętrzny serwera."}, status.HTTP_500_INTERNAL_SERVER_ERROR)
