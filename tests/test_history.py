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

    r = client.put(f"/api/history/{item_id}", json={"status": "done", "result": COI, "evidence": {"a": {"source": "paper"}}, "problems": []})
    assert r.json() == {"id": item_id}

    [rec] = client.get("/api/history").json()
    assert rec == {
        "id": item_id, "file_name": "wniosek.pdf", "file_type": "application/pdf", "file_size": len(PDF),
        "created": 1000, "status": "done", "error": "", "result": COI, "evidence": {"a": {"source": "paper"}}, "problems": [],
    }
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


def test_needs_login_when_auth_is_on(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    assert client.get("/api/history").status_code == 401


def test_history_add_script(monkeypatch, tmp_path, capsys):
    from bench import history_add

    original, coi = tmp_path / "Deklaracja.pdf", tmp_path / "coi.json"
    original.write_bytes(PDF)
    coi.write_text(json.dumps(COI))
    monkeypatch.setattr("sys.argv", ["history_add", str(original), str(coi), "--name", "agent run"])
    history_add.main()

    [rec] = history.records()
    assert (rec["file_name"], rec["file_type"], rec["status"], rec["result"]) == ("agent run.pdf", "application/pdf", "done", COI)
