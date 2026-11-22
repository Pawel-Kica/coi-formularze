"""app/llm/transcribe.py, the only reading path, without a network: chat_text is faked."""

import base64
import io
import shutil
import threading
import time
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from app import pipeline
from app.config import settings
from app.extract import Document, UnsupportedFile
from app.llm import transcribe as tr
from app.llm.client import ChatResult, LLMError


def a4_pdf(pages: int) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(595, 842)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def decoded(url: str) -> Image.Image:
    assert url.startswith("data:image/jpeg;base64,")
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


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
    monkeypatch.setattr(settings, "llm_model", "planner-y")  # the planner reads no images: never its model
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
    assert max(decoded(calls[0]["images"][0]).size) == 700


def test_data_url_keeps_gray_and_never_upscales():
    gray = Image.new("L", (600, 800), 255)
    assert decoded(tr.data_url(gray, 400)).mode == "L" and gray.size == (600, 800)  # the caller's image untouched
    assert decoded(tr.data_url(Image.new("RGB", (300, 400), "red"), 1024)).size == (300, 400)


def test_stacked_scan_is_cut_into_pages(monkeypatch):
    calls = []
    monkeypatch.setattr(tr, "chat_text", fake_chat(calls))
    doc = tr.transcribe(png(Image.new("RGB", (600, 2 * 849), "white")), "two.png")
    assert doc.text == "## page 1\n\n## page 2" and sorted(c["user"][:12] for c in calls) == ["Strona 1 z 2", "Strona 2 z 2"]


def test_cut_off_page_is_retried_once_with_a_frequency_penalty(monkeypatch):
    calls = []

    def chat(system, user, model=None, max_tokens=8000, images=None, presence_penalty=0.0, timeout=None, tries=3,
             frequency_penalty=0.0):
        calls.append(frequency_penalty)
        if len(calls) == 1:
            raise LLMError("cut off")
        return ChatResult(data={}, raw="ok", seconds=0.0)

    monkeypatch.setattr(tr, "chat_text", chat)
    assert tr.transcribe(a4_pdf(1), "f.pdf").text == "ok" and calls == [0.0, tr.RETRY_FREQUENCY_PENALTY]


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


def test_pipeline_always_transcribes(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "transcribe", lambda d, f: seen.append(f) or Document("md", "vlm-md", pages=["md"]))

    def plan(text):
        seen.append(text)
        raise RuntimeError("stop")

    monkeypatch.setattr(pipeline, "make_plan", plan)
    with pytest.raises(RuntimeError, match="stop"):
        pipeline.convert(b"%PDF-", "f.pdf")
    assert seen == ["f.pdf", "md"]


def test_fill_in_runs_collapse_and_presence_penalty_sent(monkeypatch):
    seen = {}

    def chat(system, user, model=None, max_tokens=8000, images=None, presence_penalty=0.0, timeout=None, tries=3):
        seen["pp"] = presence_penalty
        return ChatResult(data={}, raw="Inny (jaki?) ....................\n__________\n", seconds=0.0)

    monkeypatch.setattr(tr, "chat_text", chat)
    assert tr.transcribe(a4_pdf(1), "f.pdf").text == "Inny (jaki?) ______\n______"
    assert seen["pp"] == tr.PRESENCE_PENALTY > 0


def test_same_bytes_reuse_one_transcription(monkeypatch):
    calls = []
    monkeypatch.setattr(tr, "chat_text", fake_chat(calls))
    data = a4_pdf(2)
    first = tr.transcribe(data, "f.pdf")
    assert tr.transcribe(data, "other-name.pdf") is first and len(calls) == 2  # one call per page, once
    monkeypatch.setattr(settings, "transcribe_side", 700)  # another reading setting = another transcription
    tr.transcribe(data, "f.pdf")
    assert len(calls) == 4


def test_concurrent_requests_share_one_transcription(monkeypatch):
    calls = []
    monkeypatch.setattr(tr, "chat_text", fake_chat(calls, {1: 0.3}))
    data, out = a4_pdf(1), []
    threads = [threading.Thread(target=lambda: out.append(tr.transcribe(data, "f.pdf"))) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len(out) == 3 and all(d is out[0] for d in out)


def test_failure_is_not_cached(monkeypatch):
    fails = {"left": 2}  # first try and its retry fail, the next request works

    def chat(*a, **kw):
        if fails["left"]:
            fails["left"] -= 1
            raise LLMError("down")
        return ChatResult(data={}, raw="ok", seconds=0.0)

    monkeypatch.setattr(tr, "chat_text", chat)
    data = a4_pdf(1)
    with pytest.raises(LLMError):
        tr.transcribe(data, "f.pdf")
    assert tr.transcribe(data, "f.pdf").text == "ok"



def test_recall_of_the_file_words_on_the_paper():
    own = "Wniosek o zwrot opłaty\nImię i nazwisko\nInformacje o opłacie skarbowej"
    assert tr.recall(own, own.replace("\n", " ")) == 1.0
    assert tr.recall(own, "Wniosek o zwrot opłaty") < 0.5
    assert tr.recall("", "anything") == 1.0


def test_office_pdf_that_lost_text_is_read_from_the_file(monkeypatch):
    calls = []
    monkeypatch.setattr(tr, "chat_text", fake_chat(calls))
    monkeypatch.setattr(tr, "office_to_pdf", lambda data, name: a4_pdf(1))  # a blank page: LibreOffice lost it all
    monkeypatch.setattr(tr, "_file_text", lambda data, name: "Dane wnioskodawcy\nUzasadnienie wniosku")
    doc = tr.transcribe(b"PK\x03\x04 fake docx", "form.docx")
    assert (doc.text, doc.method, calls) == ("Dane wnioskodawcy\nUzasadnienie wniosku", "office-text", [])


def test_office_pdf_that_holds_the_text_is_read_from_images(monkeypatch):
    calls = []
    monkeypatch.setattr(tr, "chat_text", fake_chat(calls))
    monkeypatch.setattr(tr, "office_to_pdf", lambda data, name: a4_pdf(1))
    monkeypatch.setattr(tr, "_pdf_text", lambda data: "Dane wnioskodawcy Uzasadnienie wniosku")
    monkeypatch.setattr(tr, "_file_text", lambda data, name: "Dane wnioskodawcy\nUzasadnienie wniosku")
    doc = tr.transcribe(b"PK\x03\x04 other docx", "form.docx")
    assert (doc.text, doc.method, len(calls)) == ("## page 1", "vlm-md", 1)
