"""The FastAPI application: the REST API (`app.api`) plus the frontend's static pages (FRONTEND_DIR).

Run from `backend/`: `uv run uvicorn app.main:app --reload`. Docs at /docs, upload page at /.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, JSONResponse

from app.api import router
from app.core.config import settings

app = FastAPI(
    title="COI Cyfryzacja Formularzy",
    description="Pusty formularz (PDF, DOC, DOCX, ODS, XLSX, XLS, JPG, PNG) na konfigurację e-usługi COI (JSON).",
    version="0.1.0",
)
app.include_router(router)

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
