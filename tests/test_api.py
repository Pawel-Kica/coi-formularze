"""API layer: auth, upload validation, error mapping. The pipeline itself is monkeypatched."""

import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import pipeline
from app.api import app
from app.config import settings
from app.upload import MIME_BY_EXTENSION, sniff_mime, validate_upload

SECRET = "test-secret-" + "x" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n%test\n"
FORM_1 = next((Path(__file__).parent.parent / "data/coi").glob("1. *"), Path("missing"))  # reference form 1
COI = {"id": "form", "pages": []}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "jwt_jwks_url", None)
    monkeypatch.setattr(settings, "auth_username", "urzednik")
    monkeypatch.setattr(settings, "auth_password", SecretStr("tajne"))
    monkeypatch.setattr(settings, "max_file_size_mb", 1)
    monkeypatch.setattr(pipeline, "convert", lambda data, filename: COI)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(client):
    token = client.post("/api/auth/token", json={"username": "urzednik", "password": "tajne"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def upload(client, auth, name, content):
    return client.post("/api/forms/convert", headers=auth, files={"file": (name, content)})


def zipped(*entries: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


# --- auth ---


def test_token_issued_for_json_and_form(client):
    for kwargs in (
        {"json": {"username": "urzednik", "password": "tajne"}},
        {"data": {"username": "urzednik", "password": "tajne"}},
    ):
        response = client.post("/api/auth/token", **kwargs)
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer" and body["expires_in"] == settings.jwt_ttl_minutes * 60
        claims = jwt.decode(body["access_token"], SECRET, algorithms=["HS256"], audience=settings.jwt_audience)
        assert claims["sub"] == "urzednik" and claims["iss"] == settings.jwt_issuer


def test_wrong_password(client):
    response = client.post("/api/auth/token", json={"username": "urzednik", "password": "zle"})
    assert response.status_code == 401


def test_login_disabled_without_password(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_password", None)
    assert client.post("/api/auth/token", json={"username": "urzednik", "password": ""}).status_code == 503


def test_missing_token(client):
    assert client.get("/api/auth/config").json() == {"enabled": True, "history": True}
    assert upload(client, {}, "a.pdf", PDF).status_code == 401


def test_open_mode_needs_no_token(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    assert client.get("/api/auth/config").json() == {"enabled": False, "history": True}
    response = upload(client, {}, "a.pdf", PDF)
    assert response.status_code == 200 and response.json() == COI
    assert upload(client, {"Authorization": "Bearer garbage"}, "a.pdf", PDF).status_code == 200


@pytest.mark.parametrize(
    "claims, key",
    [
        ({"exp": datetime.now(UTC) - timedelta(minutes=1)}, SECRET),  # expired
        ({}, "another-secret-" + "y" * 32),  # wrong signature
        ({"aud": "someone-else"}, SECRET),  # wrong audience
    ],
)
def test_bad_tokens_rejected(client, claims, key):
    base = {
        "sub": "urzednik",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    token = jwt.encode(base | claims, key, algorithm="HS256")
    response = upload(client, {"Authorization": f"Bearer {token}"}, "a.pdf", PDF)
    assert response.status_code == 401


# --- convert ---


def test_convert_returns_pipeline_json_as_is(client, auth, monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "convert", lambda data, filename: seen.update(name=filename, data=data) or COI)
    response = upload(client, auth, "Wniosek o zwrot opłaty.PDF", PDF)
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
def test_bad_filename(client, auth, name):
    assert upload(client, auth, name, PDF).status_code == 400


def test_filename_at_limit_ok(client, auth):
    assert upload(client, auth, "a" * 246 + ".pdf", PDF).status_code == 200


@pytest.mark.parametrize("name", ["formularz.txt", "formularz.exe", "formularz"])
def test_unsupported_extension(client, auth, name):
    assert upload(client, auth, name, PDF).status_code == 415


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
def test_content_must_match_extension(client, auth, name, content):
    assert upload(client, auth, name, content).status_code == 415


DRIVE = Path.home() / "hacknation-2026/resources/drive-formularze"  # black-box forms, outside the repo


@pytest.mark.skipif(not DRIVE.is_dir(), reason="drive-formularze missing")
def test_xls_told_apart_from_doc(client, auth):
    """.xls and .doc share the OLE2 magic bytes; the stream inside (Workbook vs WordDocument) decides."""
    xls = (DRIVE / "dolnoslaski-uw__Protokol_strat_-_v2021.xls").read_bytes()
    doc = (DRIVE / "04_usuniecie_drzewa_buczkowice.doc").read_bytes()
    assert validate_upload("PROTOKOL.XLS", xls) == MIME_BY_EXTENSION[".xls"] == "application/vnd.ms-excel"
    assert sniff_mime(doc) == MIME_BY_EXTENSION[".doc"]
    assert upload(client, auth, "protokol.xls", xls).status_code == 200
    assert upload(client, auth, "protokol.doc", xls).status_code == 415
    assert upload(client, auth, "wniosek.xls", doc).status_code == 415


def test_doc_and_docx_are_one_format():
    """COI forms 5 and 7 ship a .DOC that is really a DOCX: the spec's "DOC (.doc .docx)" lets it in."""
    docx = zipped(("[Content_Types].xml", b""), ("word/document.xml", b""))
    assert validate_upload("WNIOSEK.DOC", docx) == MIME_BY_EXTENSION[".docx"]


def test_too_large(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "max_file_size_mb", 0.01)  # ~10 KB
    response = upload(client, auth, "duzy.pdf", PDF + b"0" * 20_000)
    assert response.status_code == 413


def test_too_large_without_content_length(client, auth, monkeypatch):
    """A chunked body has no Content-Length; the cap still holds while streaming."""
    monkeypatch.setattr(settings, "max_file_size_mb", 0.01)
    head = b'--b\r\nContent-Disposition: form-data; name="file"; filename="duzy.pdf"\r\n\r\n' + PDF
    chunks = iter([head] + [b"0" * 50_000] * 4 + [b"\r\n--b--\r\n"])
    headers = auth | {"Content-Type": "multipart/form-data; boundary=b"}
    response = client.post("/api/forms/convert", headers=headers, content=chunks)
    assert response.status_code == 413


def test_exactly_one_file(client, auth):
    files = [("file", ("a.pdf", PDF)), ("file", ("b.pdf", PDF))]
    assert client.post("/api/forms/convert", headers=auth, files=files).status_code == 400
    assert client.post("/api/forms/convert", headers=auth, data={"x": "1"}).status_code == 400


@pytest.mark.parametrize(
    "error, code",
    [
        (ValueError("brak tekstu"), 422),
        (httpx.ConnectError("refused"), 503),
        (httpx.ReadTimeout("slow model"), 504),
        (httpx.HTTPStatusError("500", request=httpx.Request("POST", "http://x"), response=httpx.Response(500)), 502),
    ],
)
def test_pipeline_errors_mapped(client, auth, monkeypatch, error, code):
    def fail(data, filename):
        raise error

    monkeypatch.setattr(pipeline, "convert", fail)
    response = upload(client, auth, "a.pdf", PDF)
    assert response.status_code == code and "detail" in response.json()


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


def test_form_text_reads_a_real_docx_without_the_model(client, auth):
    sample = next(iter(sorted((FORM_1.parent / "2. Wniosek o zwrot opłaty skarbowej - formularze do cyfryzacji").glob("*.docx"))), None)
    if sample is None:
        pytest.skip("data/coi not present")
    response = client.post("/api/forms/text", headers=auth, files={"file": (sample.name, sample.read_bytes())})
    assert response.status_code == 200
    assert response.json()["method"] == "docx"
    assert "Wniosek o zwrot opłaty skarbowej" in response.json()["text"]


def test_form_text_needs_a_token_and_a_valid_upload(client, auth):
    assert client.post("/api/forms/text", files={"file": ("a.png", PNG)}).status_code == 401
    assert client.post("/api/forms/text", headers=auth, files={"file": ("a.exe", b"MZ")}).status_code == 415
    broken = client.post("/api/forms/text", headers=auth, files={"file": ("a.pdf", PDF)})
    assert broken.status_code == 422
    assert broken.json()["detail"].startswith("Nie udało się odczytać formularza")


def test_upload_page(client):
    response = client.get("/")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
