# COI Cyfryzacja Formularzy API. Only the official python Debian slim image (Alpine has no headless LibreOffice: its
# package pulls in Qt, GTK, GStreamer and Mesa/LLVM, ~1 GB), dependencies from uv.lock, Tesseract with Polish data
# ("Źródło" on scans), non-root. LibreOffice (DOC/DOCX/ODS/XLS/XLSX -> PDF -> page images for the vision model) is in by
# default, as the -nogui packages; LIBREOFFICE=0 builds a lighter image: PDF and images still become page images, office
# files are read as their own text.
ARG PYTHON_VERSION=3.14.10
ARG DEBIAN_VERSION=trixie

# --- build: install locked dependencies into /app/.venv ---
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION} AS build
ARG UV_VERSION=0.13.9
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN --mount=type=cache,target=/root/.cache/pip pip install "uv==${UV_VERSION}"
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

# --- runtime: venv + code, no build tools, no secrets ---
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION}
ARG LIBREOFFICE=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-pol \
    && if [ "$LIBREOFFICE" = "1" ]; then \
         apt-get install -y --no-install-recommends libreoffice-writer-nogui libreoffice-calc-nogui fonts-liberation; \
       fi \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -M -u 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY backend/app ./app
COPY frontend ./frontend
# Code is root-owned and read-only for `app`. Every write goes to /tmp: Tesseract and LibreOffice
# temp files, LibreOffice/fontconfig caches (HOME) and the upload page history (HISTORY_DB).
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOME=/tmp HISTORY_DB=/tmp/history.sqlite3 FRONTEND_DIR=/app/frontend
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=9)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
