"""Conversion as a job to poll, for clients that should not hold a connection for minutes.

`POST /api/forms/jobs` (the same single-file upload and checks as `/api/forms/convert`) answers 202
with `result_url` and `status_url`. `GET result_url` is 404 while the form is being converted, then
200 with the bare COI JSON, or the sync endpoint's error code and `{"detail"}` if it failed: poll
until it stops being 404. `GET status_url` says `pending`, `done` or `error` (404 = unknown job).

Everything lives in this process's memory: the file only until its job ends (never on disk), the
result for JOB_TTL after it ends; a restart forgets every job. Jobs run on their own pool of
JOB_WORKERS threads (the local model serves 2 requests at a time), at most MAX_PENDING waiting.
"""

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app import pipeline
from app.config import settings
from app.errors import conversion_error
from app.security import current_user
from app.upload import read_single_file, validate_upload

JOB_WORKERS = 2
MAX_PENDING = 20
JOB_TTL = 3600  # seconds a finished job's result stays
RETRY_AFTER = "5"  # seconds, hint on a pending result

_FILE_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


@dataclass
class Job:
    future: Future
    finished: float | None = None  # time.monotonic() when it ended; None = still running
    result: dict | None = None
    error: HTTPException | None = field(default=None, repr=False)


_executor = ThreadPoolExecutor(max_workers=JOB_WORKERS, thread_name_prefix="job")
_jobs: dict[str, Job] = {}
_lock = Lock()

router = APIRouter(prefix="/api/forms/jobs", tags=["forms"], dependencies=[Depends(current_user)])


def _run(data: bytes, filename: str) -> dict:
    return pipeline.convert(data, filename)  # through the module, so tests can monkeypatch it


def _finish(job: Job, future: Future) -> None:
    """Done callback: keep the result or the mapped error; the file bytes go with the future's call."""
    try:
        job.result = future.result()
    except Exception as error:  # every failure ends the job, so polling never hangs on 404
        job.error = conversion_error(error)
    job.finished = time.monotonic()


def _expire() -> None:
    """Forget jobs that finished more than JOB_TTL ago; running jobs never expire."""
    now = time.monotonic()
    with _lock:
        for job_id in [i for i, j in _jobs.items() if j.finished is not None and now - j.finished > JOB_TTL]:
            del _jobs[job_id]


def _job(job_id: str) -> Job:
    _expire()
    if (job := _jobs.get(job_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nie ma takiego zadania (nieznane albo wygasło).")
    return job


@router.post("", status_code=status.HTTP_202_ACCEPTED, openapi_extra=_FILE_BODY, response_description="Zadanie przyjęte")
async def create_job(request: Request, response: Response) -> dict:
    """One blank form (multipart field `file`), checked like `/api/forms/convert` (400/413/415 right away),
    then converted in the background: `{"id", "status_url", "result_url"}`, `Location` = `result_url`."""
    filename, data = await read_single_file(request, settings.max_file_size_bytes)
    validate_upload(filename, data)
    _expire()
    with _lock:
        if sum(job.finished is None for job in _jobs.values()) >= MAX_PENDING:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Za dużo zadań w kolejce. Spróbuj za chwilę.")
        job_id = uuid.uuid4().hex
        job = Job(_executor.submit(_run, data, filename))
        _jobs[job_id] = job
    job.future.add_done_callback(lambda future: _finish(job, future))
    urls = {"status_url": f"/api/forms/jobs/{job_id}", "result_url": f"/api/forms/jobs/{job_id}/result"}
    response.headers["Location"] = urls["result_url"]
    return {"id": job_id, **urls}


@router.get("/{job_id}", response_description="Stan zadania")
async def job_status(job_id: str) -> dict:
    """`{"id", "status": "pending" | "done" | "error"}`, with an error also `code` and `detail`."""
    job = _job(job_id)
    if job.finished is None:
        return {"id": job_id, "status": "pending"}
    if job.error is not None:
        return {"id": job_id, "status": "error", "code": job.error.status_code, "detail": job.error.detail}
    return {"id": job_id, "status": "done"}


@router.get("/{job_id}/result", response_description="Konfiguracja e-usługi COI gotowa do importu")
async def job_result(job_id: str) -> JSONResponse:
    """404 while the job runs (and for an unknown or expired id), then the bare COI JSON, or the
    error the sync endpoint would give for this file."""
    job = _job(job_id)
    if job.finished is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Wynik jeszcze nie jest gotowy.", headers={"Retry-After": RETRY_AFTER}
        )
    if job.error is not None:
        raise job.error
    return JSONResponse(job.result)
