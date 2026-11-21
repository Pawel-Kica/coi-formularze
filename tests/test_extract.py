"""app.extract: every format on the real COI forms, normalization rules, bad input."""

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from rapidfuzz import fuzz

from app.extract import Document, OcrError, UnsupportedFile, extract
from app.extract.normalize import normalize

DATA = Path(__file__).resolve().parents[1] / "data" / "coi"
FORMATS = {"pdf", "doc", "docx", "ods", "xlsx", "jpg", "jpeg", "jpe", "png"}
IMAGES = {"jpg", "jpeg", "jpe", "png"}
IMAGE_FORMS = {"1", "5", "7"}  # OCR is ~1-3 s per file; these cover 2-page, small grayscale, .JPE


def _form_files() -> dict[str, dict[str, Path]]:
    out: dict[str, dict[str, Path]] = {}
    if not DATA.exists():
        return out
    for d in sorted(DATA.iterdir()):
        if not (d.is_dir() and d.name[:1].isdigit()):
            continue
        files = out.setdefault(d.name.split(".")[0], {})
        for p in sorted(d.iterdir()):
            ext = p.suffix.lower().lstrip(".")
            if ext in FORMATS and "wizualizacja" not in p.name.lower():
                files.setdefault(ext, p)  # first of a kind (form 1 has two PNGs)
    return out


FILES = _form_files()
CASES = [
    (form, ext)
    for form, files in FILES.items()
    for ext in files
    if ext != "pdf" and (ext not in IMAGES or form in IMAGE_FORMS)
]
needs_data = pytest.mark.skipif(not FILES, reason="data/coi missing")


@pytest.fixture(scope="module")
def pdf_texts() -> dict[str, str]:
    return {f: extract(files["pdf"].read_bytes(), files["pdf"].name).text for f, files in FILES.items()}


@needs_data
@pytest.mark.parametrize("form,ext", CASES)
def test_format_matches_pdf_text(form, ext, pdf_texts):
    path = FILES[form][ext]
    doc = extract(path.read_bytes(), path.name)
    assert isinstance(doc, Document)
    assert len(doc.text) > 300
    # The same form in another format gives nearly the same text (measured 92-100).
    assert fuzz.ratio(pdf_texts[form], doc.text) >= 88, (form, ext)
    expected = {"doc": {"doc", "docx"}, "docx": {"docx"}, "ods": {"ods"}, "xlsx": {"xlsx"}}
    assert doc.method in expected.get(ext, {"ocr"})


@needs_data
@pytest.mark.parametrize("form", sorted(FILES))
def test_pdf_text_layer(form, pdf_texts):
    path = FILES[form]["pdf"]
    doc = extract(path.read_bytes(), path.name)
    assert doc.method == "pdf-text"
    assert "...." not in doc.text and chr(0x2026) not in doc.text  # dot leaders became ____


@needs_data
def test_legacy_doc_equals_docx():
    """The own .doc parser gives exactly what the DOCX of the same form gives (text boxes, tables,
    lists, form 4's page footer)."""
    for form in ("1", "2", "4", "8", "9"):
        doc = extract(FILES[form]["doc"].read_bytes(), FILES[form]["doc"].name)
        docx = extract(FILES[form]["docx"].read_bytes(), FILES[form]["docx"].name)
        assert doc.method == "doc"
        assert doc.text == docx.text, form


@needs_data
def test_docx_named_doc_is_sniffed():
    path = FILES["5"]["doc"]  # a DOCX saved with a .DOC extension
    assert extract(path.read_bytes(), path.name).method == "docx"


@needs_data
def test_docx_footer_is_read():
    """Form 7: the stamp duty and bank account sit in the page footer, which the PDF prints too."""
    path = FILES["7"]["doc"]  # a DOCX saved with a .DOC extension
    text = extract(path.read_bytes(), path.name).text
    assert "Opłata skarbowa - 82 zł." in text  # en dash normalized
    assert "Numer konta: 12 1223 1232 1233 2312 2342 2342" in text


@needs_data
def test_spreadsheet_borders_become_fields():
    """Form 9 xlsx/ods: bordered empty cells are fill-in lines and checkboxes, like on paper."""
    for ext in ("xlsx", "ods"):
        text = extract(FILES["9"][ext].read_bytes(), FILES["9"][ext].name).text
        assert "Imię ____" in text
        assert "[ ] Dane pełnomocnika klubu sportowego" in text


@needs_data
def test_scanned_pdf_goes_through_ocr(pdf_texts):
    """A PDF made of page images (no text layer) is OCRed page by page."""
    img = Image.open(FILES["5"]["png"])
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PDF", resolution=137)
    doc = extract(buf.getvalue(), "scan.pdf")
    assert doc.method == "pdf-ocr"
    assert fuzz.ratio(pdf_texts["5"], doc.text) >= 90


@needs_data
def test_rotated_scan(pdf_texts):
    img = Image.open(FILES["5"]["png"]).rotate(2.5, expand=True, fillcolor="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert fuzz.ratio(pdf_texts["5"], extract(buf.getvalue(), "skew.png").text) >= 90


@needs_data
def test_upside_down_scan(pdf_texts):
    img = Image.open(FILES["3"]["png"]).rotate(180)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert fuzz.ratio(pdf_texts["3"], extract(buf.getvalue(), "flipped.png").text) >= 88


@needs_data
def test_scan_checkboxes_and_fill_lines():
    """Form 9 scan: drawn checkboxes come out as [ ] and dotted lines as ____, like the PDF."""
    path = FILES["9"]["png"]
    text = extract(path.read_bytes(), path.name).text
    assert "[ ] Dane pełnomocnika klubu sportowego" in text
    assert text.count("[ ]") >= 10
    assert "Imię ____" in text


def test_blank_scan_gives_empty_text_and_warning():
    buf = io.BytesIO()
    Image.new("L", (800, 1100), 255).save(buf, format="PNG")
    doc = extract(buf.getvalue(), "blank.png")
    assert doc.text == "" and doc.method == "ocr" and doc.warnings == ["no text found"]


def test_unknown_ocr_engine_is_not_a_file_error():
    buf = io.BytesIO()
    Image.new("L", (200, 200), 255).save(buf, format="PNG")
    with pytest.raises(OcrError):
        extract(buf.getvalue(), "x.png", ocr_engine="nope")


# --- normalization ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Imię ..................", "Imię ____"),
        ("Data ………… r.", "Data ____ r."),
        ("PESEL ____________", "PESEL ____"),
        ("Kwota . . . . . . . zł", "Kwota ____ zł"),
        ("w miejscowości…….…… przy ulicy", "w miejscowości ____ przy ulicy"),
        ("☐ tak  ☒ nie", "[ ] tak [x] nie"),
        (" osoba fizyczna", "[ ] osoba fizyczna"),
        ("[] firma", "[ ] firma"),
        ("• punkt", "punkt"),
        ("„x” w polu – tak", '"x" w polu - tak'),
        ("ulicy¹⁾", "ulicy1)"),
        ("Imię i nazwisko", "Imię i nazwisko"),
        ("Imię", "Imię"),  # decomposed diacritic -> composed
        ("a  \t b c", "a b c"),
        ("a | | b |", "a | b"),
    ],
)
def test_normalize_line(raw, expected):
    assert normalize(raw) == expected


def test_normalize_drops_noise_and_repeats():
    raw = "strona 1/2\nTytuł\n\n   \n-----\n....\n....\nUzasadnienie:\n~~~\n2/6\nKoniec"
    assert normalize(raw) == "Tytuł\n____\nUzasadnienie:\nKoniec"


# --- bad input --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "data,name",
    [
        (b"", "empty.pdf"),
        (b"hello, this is not a form", "x.txt"),
        (b"\x00\x01\x02garbage" * 100, "x.docx"),
        (b"%PDF-1.7\nbroken", "broken.pdf"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600, "broken.doc"),
    ],
)
def test_garbage_is_unsupported(data, name):
    with pytest.raises(UnsupportedFile):
        extract(data, name)


def test_zip_that_is_not_office():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hi")
    with pytest.raises(UnsupportedFile):
        extract(buf.getvalue(), "x.docx")


def test_unsupported_is_value_error():
    assert issubclass(UnsupportedFile, ValueError)
