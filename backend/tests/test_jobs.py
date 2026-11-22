"""Jobs (app/api/jobs.py): the result URL is 404 until the conversion ends, then the sync endpoint's answer."""

import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import jobs
from app.core.config import settings
from app.main import app
from app.services import pipeline

PDF = b"%PDF-1.7\n%test\n"
COI = {"id": "form", "pages": []}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "max_file_size_mb", 1)
    monkeypatch.setattr(jobs, "_jobs", {})


@pytest.fixture
def client():
    return TestClient(app)


def submit(client, name="a.pdf", content=PDF):
    return client.post("/api/forms/jobs", files={"file": (name, content)})


def wait_done(client, job_id, timeout=5.0):
    end = time.monotonic() + timeout
    while client.get(f"/api/forms/jobs/{job_id}").json()["status"] == "pending":
        assert time.monotonic() < end, "job never finished"
        time.sleep(0.01)


def test_result_is_404_until_done_then_the_coi_json(client, monkeypatch):
    release = threading.Event()

    def slow(data, filename):
        assert data == PDF and filename == "a.pdf"
        release.wait(5)
        return COI

    monkeypatch.setattr(pipeline, "convert", slow)
    try:
        r = submit(client)
        assert r.status_code == 202
        body = r.json()
        assert body["result_url"] == f"/api/forms/jobs/{body['id']}/result" and r.headers["location"] == body["result_url"]
        pending = client.get(body["result_url"])
        assert pending.status_code == 404 and pending.headers["retry-after"]
        assert client.get(body["status_url"]).json() == {"id": body["id"], "status": "pending"}
    finally:
        release.set()
    wait_done(client, body["id"])
    r = client.get(body["result_url"])
    assert r.status_code == 200 and r.json() == COI
    assert client.get(body["status_url"]).json()["status"] == "done"


@pytest.mark.parametrize(
    "error, code",
    [
        (ValueError("brak tekstu"), 422),
        (httpx.ConnectError("refused"), 503),
        (httpx.ReadTimeout("slow model"), 504),
        (httpx.HTTPStatusError("500", request=httpx.Request("POST", "http://x"), response=httpx.Response(500)), 502),
        (RuntimeError("bug"), 500),  # anything else still ends the job, polling never hangs
    ],
)
def test_failed_job_gives_the_sync_code(client, monkeypatch, error, code):
    def fail(data, filename):
        raise error

    monkeypatch.setattr(pipeline, "convert", fail)
    job_id = submit(client).json()["id"]
    wait_done(client, job_id)
    r = client.get(f"/api/forms/jobs/{job_id}/result")
    assert r.status_code == code and r.json()["detail"]
    assert client.get(f"/api/forms/jobs/{job_id}").json() == {
        "id": job_id, "status": "error", "code": code, "detail": r.json()["detail"]
    }
    if code != 500:  # the sync endpoint answers the same for the same failure
        assert client.post("/api/forms/convert", files={"file": ("a.pdf", PDF)}).status_code == code


def test_bad_upload_rejected_without_a_job(client):
    assert submit(client, "a.png").status_code == 415
    assert submit(client, "a;b.pdf").status_code == 400
    assert submit(client, content=b"%PDF" + b"x" * (1024 * 1024)).status_code == 413
    assert jobs._jobs == {}


def test_unknown_and_expired_jobs_are_404(client, monkeypatch):
    assert client.get("/api/forms/jobs/nope").status_code == 404
    assert client.get("/api/forms/jobs/nope/result").status_code == 404
    monkeypatch.setattr(pipeline, "convert", lambda data, filename: COI)
    job_id = submit(client).json()["id"]
    wait_done(client, job_id)
    monkeypatch.setattr(jobs, "JOB_TTL", -1)  # finished longer ago than the TTL
    assert client.get(f"/api/forms/jobs/{job_id}/result").status_code == 404


def test_running_jobs_never_expire_and_the_queue_is_capped(client, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(pipeline, "convert", lambda data, filename: release.wait(5) and COI)
    monkeypatch.setattr(jobs, "JOB_TTL", -1)
    monkeypatch.setattr(jobs, "MAX_PENDING", 3)
    try:
        ids = [submit(client).json()["id"] for _ in range(3)]
        assert submit(client).status_code == 429
        assert all(client.get(f"/api/forms/jobs/{i}").json()["status"] == "pending" for i in ids)
    finally:
        release.set()


def test_jobs_need_a_token_when_auth_is_on(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    assert submit(client).status_code == 401
    assert client.get("/api/forms/jobs/x/result").status_code == 401


def test_evidence_job_answers_like_convert_with_evidence(client, monkeypatch):
    monkeypatch.setattr(pipeline, "convert_with_evidence", lambda data, filename: (COI, {"form": "quote"}))
    job_id = client.post("/api/forms/jobs?evidence=1", files={"file": ("a.pdf", PDF)}).json()["id"]
    wait_done(client, job_id)
    assert client.get(f"/api/forms/jobs/{job_id}/result").json() == {"coi": COI, "evidence": {"form": "quote"}}
