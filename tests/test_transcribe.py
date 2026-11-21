"""app/llm/transcribe.py (PIPELINE=images) without a network: chat_text is faked."""

import io
import shutil
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from app import pipeline
from app.config import settings
from app.extract import Document, UnsupportedFile
from app.llm import transcribe as tr
from app.llm.client import ChatResult, LLMError
from tests.test_vision import a4_pdf, png


def fake_chat(calls, delays=None):
    """chat_text stand-in: answers 'page N' from the user note, optionally slower for early pages
    (so parallel answers come back out of order)."""
    lock = threading.Lock()

    def chat(system, user, model=None, max_tokens=8000, images=None, presence_penalty=0.0, timeout=None, tries=3):
        n = int(user.split()[1])
        time.sleep((delays or {}).get(n, 0))
        with lock:
            calls.append({"system": system, "user": user, "model": model, "images": images, "max_tokens": max_tokens})
        return ChatResult(data={}, raw=f"## page {n}\n", seconds=0.0)

    return chat


def test_pdf_pages_in_order_one_call_each(monkeypatch):
    calls = []
    monkeypatch.setattr(tr, "chat_text", fake_chat(calls, {1: 0.2, 2: 0.1}))
    monkeypatch.setattr(settings, "transcribe_workers", 3)
    monkeypatch.setattr(settings, "transcribe_model", "vlm-x")
    doc = tr.transcribe(a4_pdf(3), "form.pdf")
    assert doc.method == "vlm-md"
    assert doc.text == "## page 1\n\n## page 2\n\n## page 3"
    assert len(calls) == 3 and {c["model"] for c in calls} == {"vlm-x"}
    assert calls[0]["system"] == tr.system_prompt() and "Markdown" in calls[0]["system"]
    assert all(len(c["images"]) == 1 and c["images"][0].startswith("data:image/jpeg;base64,") for c in calls)


def test_page_is_shrunk_to_transcribe_side(monkeypatch):
    calls = []
    monkeypatch.setattr(tr, "chat_text", fake_chat(calls))
    monkeypatch.setattr(settings, "transcribe_side", 700)
    tr.transcribe(png(Image.new("RGB", (1240, 1754), "white")), "scan.png")
    import base64
    img = Image.open(io.BytesIO(base64.b64decode(calls[0]["images"][0].split(",", 1)[1])))
    assert max(img.size) == 700


def test_stacked_scan_is_cut_into_pages(monkeypatch):
    calls = []
    monkeypatch.setattr(tr, "chat_text", fake_chat(calls))
    doc = tr.transcribe(png(Image.new("RGB", (600, 2 * 849), "white")), "two.png")
    assert doc.text == "## page 1\n\n## page 2" and sorted(c["user"][:12] for c in calls) == ["Strona 1 z 2", "Strona 2 z 2"]


def test_retry_once_with_more_tokens(monkeypatch):
    calls = []

    def chat(system, user, model=None, max_tokens=8000, images=None, presence_penalty=0.0, timeout=None, tries=3):
        calls.append(max_tokens)
        if len(calls) == 1:
            raise LLMError("cut off")
        return ChatResult(data={}, raw="ok", seconds=0.0)

    monkeypatch.setattr(tr, "chat_text", chat)
    assert tr.transcribe(a4_pdf(1), "f.pdf").text == "ok" and calls == [8000, 16000]


def test_not_an_image_is_unsupported():
    with pytest.raises(UnsupportedFile):
        tr.transcribe(b"hello, not a form", "x.txt")


def test_office_without_soffice_is_unsupported(monkeypatch):
    monkeypatch.setattr(settings, "soffice", "no-such-soffice-binary")
    monkeypatch.setattr(tr, "MAC_SOFFICE", "/nonexistent/soffice")
    with pytest.raises(UnsupportedFile, match="LibreOffice"):
        tr.transcribe(b"PK\x03\x04rest", "f.docx")


@pytest.mark.skipif(not (shutil.which("soffice") or Path(tr.MAC_SOFFICE).exists()), reason="LibreOffice not installed")
def test_docx_goes_through_libreoffice(monkeypatch):
    docx = next(Path("data/coi").glob("*/*.docx"), None) if Path("data/coi").exists() else None
    if docx is None:
        pytest.skip("no DOCX in data/coi")
    calls = []
    monkeypatch.setattr(tr, "chat_text", fake_chat(calls))
    doc = tr.transcribe(docx.read_bytes(), docx.name)
    assert calls and doc.text.startswith("## page 1")


def test_pipeline_switch(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "transcribe", lambda d, f: seen.append("images") or Document("md", "vlm-md"))
    monkeypatch.setattr(pipeline, "extract", lambda d, f: seen.append("text") or Document("txt", "pdf-text"))
    monkeypatch.setattr(settings, "pipeline", "images")
    doc, pages = pipeline.read(b"%PDF-", "f.pdf")
    assert (doc.text, list(pages)) == ("md", [])
    monkeypatch.setattr(settings, "pipeline", "text")
    assert pipeline.read(b"%PDF-", "f.pdf")[0].text == "txt"
    assert seen == ["images", "text"]


def test_fill_in_runs_collapse_and_presence_penalty_sent(monkeypatch):
    seen = {}

    def chat(system, user, model=None, max_tokens=8000, images=None, presence_penalty=0.0, timeout=None, tries=3):
        seen["pp"] = presence_penalty
        return ChatResult(data={}, raw="Inny (jaki?) ....................\n__________\n", seconds=0.0)

    monkeypatch.setattr(tr, "chat_text", chat)
    assert tr.transcribe(a4_pdf(1), "f.pdf").text == "Inny (jaki?) ______\n______"
    assert seen["pp"] == tr.PRESENCE_PENALTY > 0
