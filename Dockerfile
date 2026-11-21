# syntax=docker/dockerfile:1
# COI Cyfryzacja Formularzy API. Only the official python Alpine image (every locked dependency
# has a musl wheel), dependencies from uv.lock, Tesseract OCR with Polish and OSD data, non-root.
# Build arg LIBREOFFICE=1 adds LibreOffice (DOC/DOCX/ODS/XLS/XLSX -> PDF), needed only by
# PIPELINE=images; the default image reads those formats with Python libraries and stays light.
ARG PYTHON_VERSION=3.14.10
ARG ALPINE_VERSION=3.24

# --- build: install locked dependencies into /app/.venv ---
FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS build
ARG UV_VERSION=0.13.9
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN --mount=type=cache,target=/root/.cache/pip pip install "uv==${UV_VERSION}"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

# --- runtime: venv + code, no build tools, no secrets ---
FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION}
ARG LIBREOFFICE=0
RUN apk add --no-cache tesseract-ocr tesseract-ocr-data-pol tesseract-ocr-data-osd \
    && if [ "$LIBREOFFICE" = "1" ]; then \
         apk add --no-cache libreoffice-writer libreoffice-calc font-liberation; \
       fi \
    && adduser -D -H -u 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app
# Code is root-owned and read-only for `app`. Every write goes to /tmp: Tesseract and LibreOffice
# temp files, LibreOffice/fontconfig caches (HOME) and the upload page history (HISTORY_DB).
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOME=/tmp HISTORY_DB=/tmp/history.sqlite3
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
