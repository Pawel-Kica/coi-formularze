"""The FastAPI application: the REST API (`app.api`) plus the frontend's static pages (FRONTEND_DIR).

Run from `backend/`: `uv run uvicorn app.main:app --reload`. Docs at /docs, upload page at /.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, JSONResponse

from app.api import router
from app.core.config import VERSION, settings
from app.core.logging import setup as setup_logging
from app.llm.client import health as llm_health
from app.llm.transcribe import soffice_path

setup_logging(settings.log_level)
log = logging.getLogger(__name__)

app = FastAPI(
    title="COI Cyfryzacja Formularzy",
    description="Pusty formularz (PDF, DOC, DOCX, ODS, XLSX, XLS, JPG, PNG) na konfigurację e-usługi COI (JSON).",
    version=VERSION,
)
app.include_router(router)
log.info("starting %s: planner %s, reader %s, API key %s, auth %s, history %s, LibreOffice %s",
         VERSION, settings.llm_model, settings.transcribe_model, llm_health(), "on" if settings.auth_enabled else "off",
         settings.history_db if settings.history_enabled else "off",
         soffice_path() or "not found (office files are read as their own text)")
if settings.auth_enabled and settings.jwt_secret_random:
    log.warning("JWT_SECRET not set: tokens are signed with a random per-process secret and die with it")

# The frontend's pages, served from the same origin as the API (the pages call /api/... relative to it).
PAGES = {
    "/": ("index.html", "text/html"),  # upload page
    "/preview": ("preview.html", "text/html"),  # e-service preview with the editor sidebar (bonus V)
    "/editor.js": ("editor.js", "text/javascript"),  # the editor sidebar of /preview
    "/slowniki": ("dictionaries.html", "text/html"),  # dictionary catalog
}


def _page(name: str, media_type: str):
    async def page() -> FileResponse:
        return FileResponse(settings.frontend_dir / name, media_type=media_type)
    return page


for path, (name, media_type) in PAGES.items():
    app.add_api_route(path, _page(name, media_type), include_in_schema=False)


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, _error: Exception) -> JSONResponse:
    """Keep errors JSON even for bugs; the traceback still goes to the server log."""
    return JSONResponse({"detail": "Błąd wewnętrzny serwera."}, status.HTTP_500_INTERNAL_SERVER_ERROR)
