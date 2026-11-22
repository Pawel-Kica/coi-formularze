"""Office originals shown as the real document (app/history.py `office_pdf`): LibreOffice PDF per record, cached on
disk, sheets one page wide, "Źródło" boxes on that PDF; the text view when LibreOffice fails or loses text."""

import shutil
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient

from app import history
from app.api import app
from app.builder import build_with_evidence
from app.config import settings
from app.extract import UnsupportedFile
from app.llm.transcribe import MAC_SOFFICE
from app.plan import Plan

ROOT = Path(__file__).parent.parent
COI = ROOT / "data" / "coi"
HAS_DATA = next(COI.glob("3.*/*.docx"), None) is not None
HAS_SOFFICE = bool(shutil.which(settings.soffice)) or Path(MAC_SOFFICE).exists()
needs_data = pytest.mark.skipif(not HAS_DATA, reason="data/coi missing")
needs_soffice = pytest.mark.skipif(not (HAS_DATA and HAS_SOFFICE), reason="data/coi or LibreOffice missing")


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "history_db", tmp_path / "history.sqlite3")
    monkeypatch.setattr(settings, "auth_enabled", False)


@pytest.fixture
def client():
    return TestClient(app)


def form3(ext: str) -> tuple[Path, dict, dict]:
    f = next(p for p in COI.glob("3.*/*") if p.suffix.lower() == f".{ext}")
    coi, ev = build_with_evidence(Plan.model_validate_json((ROOT / "gold" / "plans" / "form3.json").read_text()))
    return f, coi, ev


def add(f: Path, coi: dict, ev: dict) -> int:
    return history.add(f.name, "application/octet-stream", f.read_bytes(), {"result": coi, "evidence": ev})


def pages(pdf: bytes) -> int:
    doc = pdfium.PdfDocument(pdf)
    try:
        return len(doc)
    finally:
        doc.close()


@needs_soffice
@pytest.mark.parametrize("ext", ["docx", "xlsx"])
def test_office_record_is_a_pdf_with_framed_sources(client, monkeypatch, ext):
    f, coi, ev = form3(ext)
    item = add(f, coi, ev)
    r = client.get(f"/api/history/{item}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF-") and r.headers["content-type"] == "application/pdf"
    # a sheet one page wide: LibreOffice's default cut form 3 into two column strips
    assert pages(r.content) == 1
    body = client.post(f"/api/history/{item}/sources", json={"coi": coi, "evidence": ev}).json()
    places = body["sources"]
    assert places and all(p and p["page"] == 1 and p["boxes"] for p in places.values())
    assert client.get(f"/api/history/{item}/page/1.png").status_code == 200

    # cached on disk: a new process (cleared caches) does not run LibreOffice again
    def no_soffice(*_):
        raise AssertionError("LibreOffice ran again")
    monkeypatch.setattr(history, "office_convert", no_soffice)
    history._paper.cache_clear()
    assert client.get(f"/api/history/{item}/pdf").content == r.content


@needs_data
def test_failed_conversion_falls_back_to_text_lines(client, monkeypatch):
    calls = []

    def broken(data, name, to):
        calls.append(to)
        raise UnsupportedFile("no LibreOffice here")
    monkeypatch.setattr(history, "office_convert", broken)
    f, coi, ev = form3("docx")
    item = add(f, coi, ev)
    assert client.get(f"/api/history/{item}/pdf").status_code == 422
    places = client.post(f"/api/history/{item}/sources", json={"coi": coi, "evidence": ev}).json()["sources"]
    assert places and all(p and "lines" in p for p in places.values())
    assert client.get(f"/api/history/{item}/pdf").status_code == 422
    assert calls == ["pdf"]  # the failure is remembered, LibreOffice is not asked again


@needs_data
def test_pdf_that_lost_text_is_not_shown(client, monkeypatch):
    """LibreOffice drops the text boxes of some DOCX (form 8: 0.80 of its words): such a PDF is not shown."""
    other = next(COI.glob("1.*/*.pdf")).read_bytes()  # a real PDF without the words of form 3
    monkeypatch.setattr(history, "office_convert", lambda data, name, to: other)
    f, coi, ev = form3("docx")
    item = add(f, coi, ev)
    assert client.get(f"/api/history/{item}/pdf").status_code == 422


def test_pdf_record_has_no_office_pdf(client):
    item = history.add("wniosek.pdf", "application/pdf", b"%PDF-1.7\n", {})
    assert client.get(f"/api/history/{item}/pdf").status_code == 404
    assert client.get("/api/history/999/pdf").status_code == 404
