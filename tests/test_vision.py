"""Vision path (LLM_VISION) without a model: page rendering, page cap, message shape, flag off = text only,
images instead of text (LLM_VISION_TEXT=0), grayscale (LLM_VISION_GRAY=1)."""

import base64
import io
import json
import zipfile

import pypdfium2 as pdfium
import pytest
from PIL import Image

from app.config import settings
from app.llm import client, planner, vision
from app.quotes import THRESHOLD, THRESHOLD_OCR, threshold_for

PASS1 = {"title": "Wniosek o test", "addressee": "", "addressee_quote": "",
         "applicant": {"person": True, "entity": False, "entity_ids": [], "pesel": False, "proxy": True,
                       "registry_extract": False},
         "pages": [{"title": "P", "sections": [{"title": "S", "description": "", "address": False, "repeating": False,
                    "fields": [{"quote": "Imię ____", "label": "Imię", "type": "TEXT_FIELD", "required": True,
                                "options": [], "description": ""}]}]}],
         "statements": []}


@pytest.fixture
def bodies(monkeypatch):
    """Request bodies the planner sends; the stub answers both passes."""
    seen = []

    def post(url, body):
        seen.append(body)
        ans = PASS1 if body["response_format"]["json_schema"]["name"] == "plan" else {"new_questions": [], "conditions": []}

        class R:
            def json(self):
                return {"choices": [{"message": {"content": json.dumps(ans)}, "finish_reason": "stop"}], "usage": {}}
        return R()

    monkeypatch.setattr(client, "_post", post)
    return seen


@pytest.fixture
def vision_on(monkeypatch):
    monkeypatch.setattr(settings, "llm_vision", True)
    monkeypatch.setattr(settings, "llm_vision_max_side", 1400)
    monkeypatch.setattr(settings, "llm_vision_max_pages", 2)
    monkeypatch.setattr(settings, "llm_vision_conditions_side", 1024)


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


def decoded(part: dict) -> Image.Image:
    url = part["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


def test_flag_off_no_images_and_text_only_requests(bodies, monkeypatch):
    monkeypatch.setattr(settings, "llm_vision", False)
    monkeypatch.setattr(settings, "llm_vision_text", False)  # no images: the text stays whatever this says
    assert vision.page_images(a4_pdf(3), "a.pdf") == []
    planner.make_plan("Imię ____", vision.page_images(a4_pdf(1), "a.pdf"))
    assert len(bodies) == 2
    for body in bodies:
        assert set(body) == {"model", "messages", "response_format", "temperature", "max_tokens", "seed"}
        system, user = body["messages"]
        assert isinstance(user["content"], str) and user["content"].startswith("Tekst formularza:")
        assert "# Obrazy stron" not in system["content"]


def test_pdf_pages_capped_and_sized(vision_on):
    pages = vision.page_images(a4_pdf(3), "a.pdf")
    assert len(pages) == 2  # LLM_VISION_MAX_PAGES
    assert all(p.mode == "RGB" and max(p.size) == 1400 for p in pages)


def test_stacked_image_is_cut_into_pages(vision_on):
    tall = Image.new("L", (1654, 4678), 255)  # two A4 pages at 200 dpi, one image
    pages = vision.page_images(png(tall), "scan.png")
    assert len(pages) == 2 and all(max(p.size) <= 1400 for p in pages)
    small = vision.page_images(png(Image.new("RGB", (600, 800), "white")), "photo.png")
    assert [p.size for p in small] == [(600, 800)]  # never upscaled


def test_documents_without_rendering_stay_text_only(vision_on):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    assert vision.page_images(buf.getvalue(), "a.docx") == []
    assert vision.page_images(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512, "a.doc") == []


def test_both_passes_get_images_before_text(bodies, vision_on):
    pages = vision.page_images(a4_pdf(3), "a.pdf")
    planner.make_plan("Imię ____", pages)
    (s1, u1), (s2, u2) = (b["messages"] for b in bodies)
    for system, user, side in ((s1, u1, 1400), (s2, u2, 1024)):
        parts = user["content"]
        assert [p["type"] for p in parts] == ["image_url", "image_url", "text"]
        assert all(max(decoded(p).size) == side for p in parts[:2])
        assert parts[2]["text"].startswith("Powyżej: obrazy stron 1-2 formularza")
        assert "Tekst formularza:\n```\nImię ____\n```" in parts[2]["text"]
        assert "# Obrazy stron" in system["content"]
    assert "Kwadraty lub kółka" in s1["content"] and "wcięte pod opcją" in s2["content"]
    assert bodies[0]["response_format"]["type"] == "json_schema"  # the schema still constrains the answer


def test_conditions_side_zero_keeps_pass2_text_only(bodies, vision_on, monkeypatch):
    monkeypatch.setattr(settings, "llm_vision_conditions_side", 0)
    planner.make_plan("Imię ____", vision.page_images(a4_pdf(1), "a.pdf"))
    assert [p["type"] for p in bodies[0]["messages"][1]["content"]] == ["image_url", "text"]
    assert bodies[0]["messages"][1]["content"][1]["text"].startswith("Powyżej: obraz strony 1 formularza.")
    assert isinstance(bodies[1]["messages"][1]["content"], str)
    assert "# Obrazy stron" not in bodies[1]["messages"][0]["content"]


def test_setup_names_the_vision_settings(vision_on):
    assert vision.setup() | {"model": "m"} == {"model": "m", "vision": True, "max_side": 1400, "max_pages": 2,
                                                "conditions_side": 1024, "text": True, "gray": False}


# ---------- LLM_VISION_TEXT=0: page images instead of our text ----------


@pytest.fixture
def images_only_on(vision_on, monkeypatch):
    monkeypatch.setattr(settings, "llm_vision_text", False)


def test_images_only_both_passes_get_no_text(bodies, images_only_on):
    pages = vision.page_images(a4_pdf(2), "a.pdf")
    assert vision.images_only(pages)
    planner.make_plan("Imię ____", pages)
    (s1, u1), (s2, u2) = (b["messages"] for b in bodies)
    note = "Powyżej: obrazy stron 1-2 formularza, po kolei.\n\n"
    assert [p["type"] for p in u1["content"]] == [p["type"] for p in u2["content"]] == ["image_url", "image_url", "text"]
    assert u1["content"][2]["text"] == note + planner.TASK
    assert u2["content"][2]["text"].startswith(note + "Plan:\n```\n")  # the plan listing, no form text
    for system, user in ((s1, u1), (s2, u2)):
        assert "Tekst formularza" not in user["content"][2]["text"]
        assert "bez wyciągniętego tekstu" in system["content"] and "dalej kopiuj z tekstu" not in system["content"]
    assert "Kwadraty lub kółka" in s1["content"] and "wcięte pod opcją" in s2["content"]


@pytest.mark.parametrize("file", ["pdf", "png"])
def test_images_only_keeps_text_when_pages_were_cut_off(bodies, images_only_on, file):
    data = a4_pdf(3) if file == "pdf" else png(Image.new("L", (1654, 3 * 2339), 255))  # 3 pages, cap 2
    pages = vision.page_images(data, f"a.{file}")
    assert (len(pages), pages.total) == (2, 3) and not vision.images_only(pages)
    planner.make_plan("Imię ____", pages)
    for b in bodies:  # pages 3.. exist only in the text: both passes keep it, with the usual prompts
        assert "Tekst formularza:\n```\nImię ____\n```" in b["messages"][1]["content"][-1]["text"]
        assert "bez wyciągniętego tekstu" not in b["messages"][0]["content"]


def test_images_only_pass2_without_images_keeps_text(bodies, images_only_on, monkeypatch):
    monkeypatch.setattr(settings, "llm_vision_conditions_side", 0)
    planner.make_plan("Imię ____", vision.page_images(a4_pdf(1), "a.pdf"))
    assert "Tekst formularza" not in bodies[0]["messages"][1]["content"][1]["text"]
    assert bodies[1]["messages"][1]["content"].startswith("Tekst formularza:\n```\nImię ____\n```")


def test_documents_without_images_keep_text_in_images_only_mode(images_only_on):
    assert not vision.images_only(vision.page_images(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512, "a.doc"))
    assert not vision.images_only([])


def test_quote_threshold_follows_who_read_the_page():
    assert threshold_for("pdf-text") == THRESHOLD
    assert threshold_for("pdf-ocr") == threshold_for("ocr") == THRESHOLD_OCR
    assert threshold_for("pdf-text", images_only=True) == THRESHOLD_OCR  # the model's own reading


# ---------- LLM_VISION_GRAY ----------


def test_gray_pages_stay_gray_in_the_request(vision_on, monkeypatch):
    monkeypatch.setattr(settings, "llm_vision_gray", True)
    pages = [*vision.page_images(a4_pdf(1), "a.pdf"), *vision.page_images(png(Image.new("RGB", (600, 800), "red")), "p.png")]
    assert [p.mode for p in pages] == ["L", "L"]
    assert decoded({"image_url": {"url": vision.data_url(pages[1], 400)}}).mode == "L"
    assert vision.setup()["gray"] is True


def test_gray_off_sends_rgb_even_for_a_gray_scan(vision_on):
    page = vision.page_images(png(Image.new("L", (600, 800), 255)), "scan.png")[0]
    assert page.mode == "RGB" and decoded({"image_url": {"url": vision.data_url(page, 400)}}).mode == "RGB"
