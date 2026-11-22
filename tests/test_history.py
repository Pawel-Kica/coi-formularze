"""Upload page history in SQLite (app/history.py): API round trip and the script path, on a temp DB."""

import json

import pytest
from fastapi.testclient import TestClient

from app import history
from app.api import app
from app.config import settings

PDF = b"%PDF-1.7\n%test\n"
COI = {"id": "form", "pages": []}


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "history_db", tmp_path / "history.sqlite3")
    monkeypatch.setattr(settings, "auth_enabled", False)


@pytest.fixture
def client():
    return TestClient(app)


def test_round_trip(client):
    record = {"created": 1000, "status": "queued", "result": None, "error": ""}
    r = client.post("/api/history", data={"record": json.dumps(record)}, files={"file": ("wniosek.pdf", PDF, "application/pdf")})
    assert r.status_code == 200
    item_id = r.json()["id"]

    # PUT replaces the whole state (any page field, e.g. checking), keeps the file and created
    state = {"status": "done", "error": "", "result": COI, "evidence": {"a": {"source": "paper"}}, "problems": [], "checking": True}
    r = client.put(f"/api/history/{item_id}", json=state)
    assert r.json() == {"id": item_id}

    [rec] = client.get("/api/history").json()
    assert rec == {"id": item_id, "file_name": "wniosek.pdf", "file_type": "application/pdf", "file_size": len(PDF), "created": 1000, **state}
    r = client.get(f"/api/history/{item_id}/file")
    assert r.content == PDF and r.headers["content-type"] == "application/pdf"


def test_newest_first_delete_and_clear(client):
    old = history.add("a.pdf", "application/pdf", PDF, {"created": 1})
    new = history.add("b.pdf", "application/pdf", PDF, {"created": 2})
    assert [r["id"] for r in client.get("/api/history").json()] == [new, old]

    assert client.delete(f"/api/history/{new}").status_code == 204
    assert [r["id"] for r in client.get("/api/history").json()] == [old]
    assert client.delete("/api/history").status_code == 204
    assert client.get("/api/history").json() == []


def test_missing_and_bad_input(client):
    assert client.put("/api/history/99", json={"status": "done"}).status_code == 404
    assert client.get("/api/history/99/file").status_code == 404
    r = client.post("/api/history", data={"record": "not json"}, files={"file": ("a.pdf", PDF, "application/pdf")})
    assert r.status_code == 400


def test_upload_checked_like_convert(client):
    record = {"record": json.dumps({"created": 1})}
    assert client.post("/api/history", data=record, files={"file": ("a.png", PDF)}).status_code == 415
    assert client.post("/api/history", data=record, files={"file": ("a;b.pdf", PDF)}).status_code == 400
    assert client.get("/api/history").json() == []


def test_coi_json_saved_without_paper(client):
    """A form started from a COI JSON or the blank form: its history file is the JSON, other JSON is refused."""
    record = {"record": json.dumps({"created": 1, "status": "done"})}
    ok = client.post("/api/history", data=record, files={"file": ("Nowy formularz.json", b'{"title": "x", "tabs": []}', "application/json")})
    assert ok.status_code == 200
    for bad in (b'{"a": 1}', b"[1]", b"not json"):
        assert client.post("/api/history", data=record, files={"file": ("a.json", bad, "application/json")}).status_code == 415
    good = b'{"tabs": []}'
    assert client.post("/api/history", data=record, files={"file": ("a;b.json", good, "application/json")}).status_code == 400
    assert [r["file_name"] for r in client.get("/api/history").json()] == ["Nowy formularz.json"]


def test_history_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "history_enabled", False)
    assert client.get("/api/auth/config").json()["history"] is False
    record = {"record": json.dumps({"created": 1})}
    assert client.post("/api/history", data=record, files={"file": ("a.pdf", PDF)}).status_code == 404
    assert client.get("/api/history").status_code == 404
    assert client.get("/api/history/1/file").status_code == 404


def test_needs_login_when_auth_is_on(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    assert client.get("/api/history").status_code == 401
