"""Conversion as a job to poll, for clients that should not hold a connection for minutes.

`POST /api/forms/jobs` (the same single-file upload and checks as `/api/forms/convert`) answers 202
with `result_url` and `status_url`. `GET result_url` is 404 while the form is being converted, then
200 with the bare COI JSON, or the sync endpoint's error code and `{"detail"}` if it failed: poll
until it stops being 404. `GET status_url` says `pending`, `done` or `error` (404 = unknown job).

Everything lives in this process's memory: the upload only until its job ends (the conversion's own
temporary OCR/LibreOffice files last as long as in the sync call), the result for JOB_TTL after it
ends; a restart forgets every job. Jobs run on their own pool of JOB_WORKERS threads, at most
MAX_PENDING not finished (running + queued).
"""

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.errors import conversion_error
from app.api.security import current_user
from app.api.upload import FILE_BODY, read_single_file, validate_upload
from app.core.config import settings
from app.services import pipeline

JOB_WORKERS = 2
MAX_PENDING = 20
JOB_TTL = 3600  # seconds a finished job's result stays
RETRY_AFTER = "5"  # seconds, hint on a pending result

@dataclass
class Job:
    finished: float | None = None  # time.monotonic() when it ended; None = still running
    result: dict | None = None
    error: HTTPException | None = field(default=None, repr=False)


_executor = ThreadPoolExecutor(max_workers=JOB_WORKERS, thread_name_prefix="job")
_jobs: dict[str, Job] = {}
_lock = Lock()

router = APIRouter(prefix="/api/forms/jobs", tags=["forms"], dependencies=[Depends(current_user)])


def _run(data: bytes, filename: str, evidence: bool) -> dict:
    """The bare COI JSON, or `{"coi", "evidence"}` like `convert?evidence=1` (the upload page)."""
    if evidence:  # through the module, so tests can monkeypatch it
        coi, ev = pipeline.convert_with_evidence(data, filename)
        return {"coi": coi, "evidence": ev}
    return pipeline.convert(data, filename)


def _finish(job: Job, future: Future) -> None:
    """Done callback: keep the result or the mapped error. Nothing keeps the future, and the error drops its
    traceback (whose frames hold the upload), so the file bytes are freed as soon as the job ends."""
    try:
        job.result = future.result()
    except Exception as error:  # every failure ends the job, so polling never hangs on 404
        job.error = conversion_error(error)
        job.error.__traceback__ = None
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


@router.post("", status_code=status.HTTP_202_ACCEPTED, openapi_extra=FILE_BODY, response_description="Zadanie przyjęte")
async def create_job(request: Request, response: Response, evidence: bool = False) -> dict:
    """One blank form (multipart field `file`), checked like `/api/forms/convert` (400/413/415 right away),
    then converted in the background: `{"id", "status_url", "result_url"}`, `Location` = `result_url`.
    `?evidence=1` makes the result `{"coi", "evidence"}` like `convert?evidence=1` (the upload page)."""
    filename, data = await read_single_file(request, settings.max_file_size_bytes)
    validate_upload(filename, data)
    _expire()
    with _lock:
        if sum(job.finished is None for job in _jobs.values()) >= MAX_PENDING:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Za dużo zadań w kolejce. Spróbuj za chwilę.")
        job_id = uuid.uuid4().hex
        job = _jobs[job_id] = Job()
        future = _executor.submit(_run, data, filename, evidence)
    future.add_done_callback(lambda done: _finish(job, done))
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
