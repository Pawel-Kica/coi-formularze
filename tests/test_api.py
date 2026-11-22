"""API layer: upload validation, error mapping. The pipeline itself is monkeypatched."""

import io
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.api import app
from app.config import settings
from app.llm.client import MissingKeyError
from app.upload import MIME_BY_EXTENSION, sniff_mime, validate_upload

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n%test\n"
FORM_1 = next((Path(__file__).parent.parent / "data/coi").glob("1. *"), Path("missing"))  # reference form 1
COI = {"id": "form", "pages": []}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "max_file_size_mb", 1)
    monkeypatch.setattr(pipeline, "convert", lambda data, filename: COI)


@pytest.fixture
def client():
    return TestClient(app)


def upload(client, name, content):
    return client.post("/api/forms/convert", files={"file": (name, content)})


def zipped(*entries: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


# --- convert ---


def test_convert_returns_pipeline_json_as_is(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "convert", lambda data, filename: seen.update(name=filename, data=data) or COI)
    response = upload(client, "Wniosek o zwrot opłaty.PDF", PDF)
    assert response.status_code == 200
    assert response.json() == COI
    assert seen == {"name": "Wniosek o zwrot opłaty.PDF", "data": PDF}


@pytest.mark.parametrize(
    "name",
    [
        "zly;plik.pdf",
        "a:b.pdf",
        "x<y>.pdf",
        "a/b.pdf",
        "c\\d.pdf",
        "a|b.pdf",
        "o'hara.pdf",
        "a*.pdf",
        "100%.pdf",
        "$a.pdf",
        "a" * 247 + ".pdf",
    ],
)
def test_bad_filename(client, name):
    assert upload(client, name, PDF).status_code == 400


def test_filename_at_limit_ok(client):
    assert upload(client, "a" * 246 + ".pdf", PDF).status_code == 200


@pytest.mark.parametrize("name", ["formularz.txt", "formularz.exe", "formularz"])
def test_unsupported_extension(client, name):
    assert upload(client, name, PDF).status_code == 415


@pytest.mark.parametrize(
    "name, content",
    [
        ("skan.pdf", PNG),  # PNG bytes named .pdf
        ("skan.png", PDF),
        ("wniosek.docx", zipped(("[Content_Types].xml", b""), ("xl/workbook.xml", b""))),  # xlsx named docx
        ("arkusz.ods", zipped(("mimetype", b"application/vnd.oasis.opendocument.text"))),  # odt named ods
        ("stary.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600),  # broken OLE
        ("stary.doc", zipped(("[Content_Types].xml", b""), ("xl/workbook.xml", b""))),  # xlsx named doc
        ("arkusz.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600),  # broken OLE
    ],
)
def test_content_must_match_extension(client, name, content):
    assert upload(client, name, content).status_code == 415


DRIVE = Path.home() / "hacknation-2026/resources/drive-formularze"  # black-box forms, outside the repo


@pytest.mark.skipif(not DRIVE.is_dir(), reason="drive-formularze missing")
def test_xls_told_apart_from_doc(client):
    """.xls and .doc share the OLE2 magic bytes; the stream inside (Workbook vs WordDocument) decides."""
    xls = (DRIVE / "dolnoslaski-uw__Protokol_strat_-_v2021.xls").read_bytes()
    doc = (DRIVE / "04_usuniecie_drzewa_buczkowice.doc").read_bytes()
    assert validate_upload("PROTOKOL.XLS", xls) == MIME_BY_EXTENSION[".xls"] == "application/vnd.ms-excel"
    assert sniff_mime(doc) == MIME_BY_EXTENSION[".doc"]
    assert upload(client, "protokol.xls", xls).status_code == 200
    assert upload(client, "protokol.doc", xls).status_code == 415
    assert upload(client, "wniosek.xls", doc).status_code == 415


def test_doc_and_docx_are_one_format():
    """COI forms 5 and 7 ship a .DOC that is really a DOCX: the spec's "DOC (.doc .docx)" lets it in."""
    docx = zipped(("[Content_Types].xml", b""), ("word/document.xml", b""))
    assert validate_upload("WNIOSEK.DOC", docx) == MIME_BY_EXTENSION[".docx"]


def test_too_large(client, monkeypatch):
    monkeypatch.setattr(settings, "max_file_size_mb", 0.01)  # ~10 KB
    response = upload(client, "duzy.pdf", PDF + b"0" * 20_000)
    assert response.status_code == 413


def test_too_large_without_content_length(client, monkeypatch):
    """A chunked body has no Content-Length; the cap still holds while streaming."""
    monkeypatch.setattr(settings, "max_file_size_mb", 0.01)
    head = b'--b\r\nContent-Disposition: form-data; name="file"; filename="duzy.pdf"\r\n\r\n' + PDF
    chunks = iter([head] + [b"0" * 50_000] * 4 + [b"\r\n--b--\r\n"])
    headers = {"Content-Type": "multipart/form-data; boundary=b"}
    response = client.post("/api/forms/convert", headers=headers, content=chunks)
    assert response.status_code == 413


def test_exactly_one_file(client):
    files = [("file", ("a.pdf", PDF)), ("file", ("b.pdf", PDF))]
    assert client.post("/api/forms/convert", files=files).status_code == 400
    assert client.post("/api/forms/convert", data={"x": "1"}).status_code == 400


@pytest.mark.parametrize(
    "error, code",
    [
        (ValueError("brak tekstu"), 422),
        (MissingKeyError("no key"), 503),  # a ValueError too: the server's setup, never "Nie udało się odczytać"
        (httpx.ConnectError("refused"), 503),
        (httpx.ReadTimeout("slow model"), 504),
        (httpx.HTTPStatusError("500", request=httpx.Request("POST", "http://x"), response=httpx.Response(500)), 502),
    ],
)
def test_pipeline_errors_mapped(client, monkeypatch, error, code):
    def fail(data, filename):
        raise error

    monkeypatch.setattr(pipeline, "convert", fail)
    response = upload(client, "a.pdf", PDF)
    assert response.status_code == code and "detail" in response.json()


def png_page() -> bytes:
    """A real one-page PNG the pipeline can turn into a page image."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (200, 280), 255).save(buf, format="PNG")
    return buf.getvalue()


# --- real COI sample files: every format sniffs as its extension ---


@pytest.mark.skipif(not FORM_1.is_dir(), reason="COI sample files not present")
@pytest.mark.parametrize(
    "extension, mime",
    [
        (".pdf", "application/pdf"),
        (".doc", "application/msword"),
        (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (".ods", "application/vnd.oasis.opendocument.spreadsheet"),
        (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".png", "image/png"),
    ],
)
def test_real_samples_sniffed(extension, mime):
    path = next(FORM_1.glob(f"Wniosek o ustalenie numeru porządkowego nieruchomości*{extension}"))
    assert sniff_mime(path.read_bytes()) == mime


# --- health and page ---


def test_health_says_whether_the_key_is_set(client, monkeypatch):
    monkeypatch.setattr(settings, "edenai_api_key", None)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "llm": "no_api_key", "llm_model": settings.llm_model,
                               "transcribe_model": settings.transcribe_model}


def test_upload_page(client):
    response = client.get("/")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]


def test_check_lists_rule_problems(client):
    assert client.post("/api/forms/check", json={"title": "x"}).json()["problems"]
