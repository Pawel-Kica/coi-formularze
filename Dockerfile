# syntax=docker/dockerfile:1
# COI Cyfryzacja Formularzy API. Official python Alpine image (all dependency wheels
# exist for musl), dependencies from uv.lock, Tesseract OCR with Polish data, non-root.
ARG PYTHON_VERSION=3.14.10
ARG ALPINE_VERSION=3.24

# --- build: install locked dependencies into /app/.venv ---
FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS build
COPY --from=astral/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

# --- runtime: venv + code, no build tools, no secrets ---
FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION}
RUN apk add --no-cache tesseract-ocr tesseract-ocr-data-pol \
    && adduser -D -H -u 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
