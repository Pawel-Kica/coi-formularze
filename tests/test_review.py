"""Dev review page (app/review.py): off without REVIEW_RUNS_DIR, behind login, serves only listed files."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import app
from app.config import settings

SECRET = "test-secret-" + "x" * 32


@pytest.fixture
def runs(tmp_path, monkeypatch):
    """runs/r1 over src/ (a.pdf done, b.doc judged źle, c.png not run yet), plus files the API must never serve."""
    monkeypatch.setattr(settings, "jwt_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "jwt_jwks_url", None)
    monkeypatch.setattr(settings, "auth_username", "urzednik")
    monkeypatch.setattr(settings, "auth_password", SecretStr("tajne"))
    (tmp_path / "secret.txt").write_text("tajne")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.pdf").write_bytes(b"%PDF-1.7\n")
    (src / "b.doc").write_bytes(b"\xd0\xcf\x11\xe0")
    (src / "c.png").write_bytes(b"\x89PNG")
    (src / "README.md").write_text("not a form")
    (src / "link.pdf").symlink_to(tmp_path / "secret.txt")
    root = tmp_path / "runs"
    run = root / "r1"
    (run / "judge").mkdir(parents=True)
    (run / "source.txt").write_text(f"{src}\n")
    (run / "a.pdf.coi.json").write_text('{"id": "a"}')
    (run / "a.pdf.txt").write_text("tekst a")
    (run / "a.pdf.plan.json").write_text(json.dumps({"pages": [{"sections": [{"fields": [1, 2, 3]}]}]}))
    (run / "b.doc.txt").write_text("tekst b")
    (run / "b.doc.coi.json").write_text('{"id": "b"}')
    verdicts = [{"file": "b.doc", "verdict": "dobrze"},
                {"file": "b.doc", "verdict": "źle", "category": "brak pól", "reasons": ["brak tabeli"],
                 "paper_elements": 10, "covered": 4, "basis": "original.txt"},
                {"file": "a.pdf", "verdict": "dobrze", "category": None, "reasons": [], "paper_elements": 5, "covered": 5}]
    (run / "judge" / "verdicts.jsonl").write_text("".join(json.dumps(v) + "\n" for v in verdicts) + "{broken\n")
    (root / "no-source").mkdir()  # not a run_dir.py run: not listed
    (root / "escape").symlink_to(src.parent)  # a link out of the runs dir: not listed
    (src.parent / "source.txt").write_text(f"{src}\n")
    monkeypatch.setattr(settings, "review_runs_dir", root)
    return root


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(client, runs):
    token = client.post("/api/auth/token", json={"username": "urzednik", "password": "tajne"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def test_off_without_setting_even_before_login(client, monkeypatch):
    monkeypatch.setattr(settings, "review_runs_dir", None)
    assert client.get("/review").status_code == 404
    assert client.get("/api/review/runs").status_code == 404
    assert client.post("/api/review/runs/r1/human", json={}).status_code == 404
    assert "/api/review/runs" not in client.get("/openapi.json").json()["paths"]


def test_page_public_data_behind_login(client, runs):
    page = client.get("/review")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert client.get("/api/review/runs").status_code == 401
    assert client.get("/api/review/runs/r1/files/a.pdf/source").status_code == 401


def test_runs_and_files_bad_first(client, auth):
    assert client.get("/api/review/runs", headers=auth).json() == [{"name": "r1", "source": "src"}]
    data = client.get("/api/review/runs/r1", headers=auth).json()
    files = {f["file"]: f for f in data["files"]}
    assert [f["file"] for f in data["files"]] == ["b.doc", "a.pdf", "c.png"]  # źle, dobrze, not run yet
    assert files["b.doc"]["verdict"]["category"] == "brak pól"  # last line per file wins
    assert files["a.pdf"]["fields"] == 3 and files["a.pdf"]["ok"] is True
    assert files["c.png"]["ok"] is None and files["c.png"]["verdict"] is None


def test_serves_source_coi_and_text(client, auth):
    source = client.get("/api/review/runs/r1/files/a.pdf/source", headers=auth)
    assert source.content == b"%PDF-1.7\n" and source.headers["content-type"] == "application/pdf"
    assert client.get("/api/review/runs/r1/files/a.pdf/coi", headers=auth).json() == {"id": "a"}
    assert client.get("/api/review/runs/r1/files/a.pdf/text", headers=auth).text == "tekst a"
    assert client.get("/api/review/runs/r1/files/c.png/coi", headers=auth).status_code == 404


@pytest.mark.parametrize("path", [
    "/api/review/runs/r1/files/..%2F..%2Fsecret.txt/source",
    "/api/review/runs/r1/files/..%2Fsecret.txt/source",
    "/api/review/runs/r1/files/link.pdf/source",  # symlink out of the source folder
    "/api/review/runs/r1/files/README.md/source",
    "/api/review/runs/r1/files/a.pdf/plan",  # unknown kind
    "/api/review/runs/..%2F/files/secret.txt/source",
    "/api/review/runs/escape/files/a.pdf/source",
    "/api/review/runs/no-source",
    "/api/review/runs/..",
])
def test_nothing_outside_is_served(client, auth, path):
    response = client.get(path, headers=auth)
    assert response.status_code in (404, 422)
    assert b"tajne" not in response.content


def test_human_review_appends_lines(client, auth, runs):
    first = client.post("/api/review/runs/r1/human", headers=auth, json={"file": "b.doc", "agree": False, "note": " to OK "})
    assert first.status_code == 200
    assert first.json()["verdict"] == "źle" and first.json()["note"] == "to OK"
    client.post("/api/review/runs/r1/human", headers=auth, json={"file": "b.doc", "agree": True})
    lines = [json.loads(line) for line in (runs / "r1" / "judge" / "human.jsonl").read_text().splitlines()]
    assert [(line["file"], line["agree"]) for line in lines] == [("b.doc", False), ("b.doc", True)]
    assert set(lines[0]) == {"file", "agree", "note", "verdict", "time"}
    files = {f["file"]: f for f in client.get("/api/review/runs/r1", headers=auth).json()["files"]}
    assert files["b.doc"]["human"]["agree"] is True
    assert client.post("/api/review/runs/r1/human", headers=auth, json={"file": "../secret.txt", "agree": True}).status_code == 404
    assert client.post("/api/review/runs/escape/human", headers=auth, json={"file": "a.pdf", "agree": True}).status_code == 404
