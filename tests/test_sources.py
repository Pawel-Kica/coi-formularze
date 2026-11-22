"""Where on the paper each element came from (app/sources.py, "Źródło"): PDF text layer, scans via Tesseract, office
text lines; the shared walk resolves repeated labels by form order; the history endpoints."""

import io
import json
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient

from app import history
from app.api import app
from app.builder import build_with_evidence
from app.config import settings
from app.plan import Plan
from app.quotes import normalize
from app.sources import Paper, locate, page_png, read_paper

ROOT = Path(__file__).parent.parent
COI = ROOT / "data" / "coi"
FORM8 = next(COI.glob("8.*/*.pdf"), None)
needs_data = pytest.mark.skipif(FORM8 is None, reason="data/coi missing")
REAL = json.loads((ROOT / "tests" / "fixtures" / "sources-f8-images.json").read_text())  # history record of form 8 (read from page images)


def gold(n: int) -> tuple[dict, dict]:
    return build_with_evidence(Plan.model_validate_json((ROOT / "gold" / "plans" / f"form{n}.json").read_text()))


def form_file(n: int, ext: str) -> Path:
    return next(p for p in COI.glob(f"{n}.*/*") if p.suffix.lower() == f".{ext}")


def found(paper: Paper, coi: dict, ev: dict) -> tuple[int, int]:
    places = locate(paper, coi, ev)
    return sum(v is not None for v in places.values()), len(places)


@needs_data
def test_form8_pdf_every_gold_quote_found_with_boxes_on_the_page():
    coi, ev = gold(8)
    places = locate(read_paper(FORM8.read_bytes(), FORM8.name), coi, ev)
    assert places and all(places.values())
    for p in places.values():
        assert 1 <= p["page"] <= p["pages"] == 6 and p["boxes"]
        assert all(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 for x0, y0, x1, y1 in p["boxes"])


def test_repeated_label_resolves_by_form_order():
    """'Numer lokalu' printed in B.3 and D.1: each field gets the occurrence after the previous quote."""
    paper = Paper.from_text("B.3 ADRES\n14. Numer lokalu\n15. Kod pocztowy\nD.1 ADRES NIERUCHOMOŚCI\n29. Numer domu\n30. Numer lokalu")
    ev = {"a": {"quote": "Numer lokalu"}, "b": {"quote": "Kod pocztowy"}, "c": {"quote": "Numer domu"}, "d": {"quote": "Numer lokalu"}}
    places = locate(paper, None, ev)
    assert [places[k]["lines"][0] for k in "abcd"] == [1, 2, 4, 5]


@needs_data
def test_real_images_result_all_quotes_placed():
    places = locate(read_paper(FORM8.read_bytes(), FORM8.name), REAL["coi"], REAL["evidence"])
    assert len(places) >= 50 and all(places.values())


@pytest.mark.slow
@needs_data
@pytest.mark.parametrize("n", [7, 8])
def test_png_scan_quotes_found(n):
    coi, ev = gold(n)
    f = form_file(n, "png")
    ok, total = found(read_paper(f.read_bytes(), f.name), coi, ev)
    assert ok >= 0.9 * total


@needs_data
def test_docx_places_are_text_lines():
    coi, ev = gold(8)
    f = form_file(8, "docx")
    paper = read_paper(f.read_bytes(), f.name)
    places = locate(paper, coi, ev)
    assert places and all(places.values())
    for eid, p in places.items():
        a, b = p["lines"]
        assert p["text"] == "\n".join(paper.raw[a:b + 1])
        quote = ev[eid]["quote"]
        if quote:
            assert normalize(quote).split()[0] in normalize(p["text"])
        else:  # a section without a quote is placed at its paper heading (6043615)
            assert p["text"].strip()


@needs_data
def test_rotated_pdf_boxes_follow_the_page_as_shown():
    """A page with /Rotate 90: the box still frames ink on the served image."""
    pdf = pdfium.PdfDocument(FORM8.read_bytes())
    pdf[0].set_rotation(90)
    buf = io.BytesIO()
    pdf.save(buf)
    data = buf.getvalue()
    paper = read_paper(data, "x.pdf")
    line = next(ln for ln in paper.lines if ln.page == 0 and len(ln.words) >= 3)
    from PIL import Image
    img = Image.open(io.BytesIO(page_png(data, "x.pdf", 1))).convert("L")
    assert img.width > img.height  # shown in landscape
    x0, y0, x1, y1 = line.words[0][1]
    crop = img.crop((int(x0 * img.width), int(y0 * img.height), int(x1 * img.width) + 1, int(y1 * img.height) + 1))
    assert crop.getextrema()[0] < 128  # ink inside the box


def test_not_found_is_null_and_no_quote_is_left_out():
    paper = Paper.from_text("Imię\nNazwisko\nRasa psa: ....")
    ev = {"a": {"quote": "Rasa psa"}, "b": {"quote": "Kolor sierści"}, "c": {"quote": None}}
    assert locate(paper, None, ev) == {"a": {"lines": [2, 2], "text": "Rasa psa: ...."}, "b": None}


@needs_data
def test_history_endpoints(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "history_db", tmp_path / "history.sqlite3")
    monkeypatch.setattr(settings, "auth_enabled", False)
    item = history.add(FORM8.name, "application/pdf", FORM8.read_bytes(), {"result": REAL["coi"], "evidence": REAL["evidence"]})
    client = TestClient(app)
    r = client.post(f"/api/history/{item}/sources", json={"coi": REAL["coi"], "evidence": REAL["evidence"]})
    assert r.status_code == 200
    body = r.json()
    assert len(body["pages"]) == 6 and all(body["sources"].values())
    r = client.get(f"/api/history/{item}/page/2.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "max-age" in r.headers["cache-control"]
    assert client.get(f"/api/history/{item}/page/9.png").status_code == 404
    assert client.post("/api/history/999/sources", json={}).status_code == 404
