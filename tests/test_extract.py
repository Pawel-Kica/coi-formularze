"""app.extract: every text format on the real COI forms (office text, PDF text layer), normalization rules, bad input."""

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from rapidfuzz import fuzz

from app.extract import Document, UnsupportedFile, extract
from app.extract.normalize import normalize

DATA = Path(__file__).resolve().parents[1] / "data" / "coi"
FORMATS = {"pdf", "doc", "docx", "ods", "xlsx", "png"}  # png: only for the scan-in-a-PDF test


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
CASES = [(form, ext) for form, files in FILES.items() for ext in files if ext not in ("pdf", "png")]
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
    assert doc.method in expected[ext]


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


DRIVE = Path.home() / "hacknation-2026" / "resources" / "drive-formularze"  # black-box forms, outside the repo
needs_drive = pytest.mark.skipif(not DRIVE.is_dir(), reason="drive-formularze missing")


@needs_drive
def test_legacy_xls():
    """Old Excel (BIFF, an OLE file like .doc): the same row and border rules as XLSX."""
    path = DRIVE / "dolnoslaski-uw__Protokol_strat_-_v2021.xls"
    doc = extract(path.read_bytes(), path.name)
    assert doc.method == "xls"
    assert "PROTOKÓŁ" in doc.text and "Komisja w składzie:\n1. ____\n2. ____" in doc.text
    assert "Lp. | Miejscowość | Rodzaj | Wielkość zniszczeń | Opis zniszczeń | Uwagi | Wartość [zł]\n____" in doc.text
    path = DRIVE / "dolnoslaski-uw__Informacja_o_stratach.xls"
    text = extract(path.read_bytes(), path.name).text
    assert text.startswith("Informacja o stratach\nLp | Nazwa jednostki samorządowej | Drogi | Mosty")


GOOD_PAGE = (
    "Wniosek o wydanie zaświadczenia\nImię i nazwisko ____\nAdres zamieszkania: ul. ____ nr ____\n"
    "Kontakt: urzad@gmina.pl, https://bip.gmina.pl/index.php?id=5\nPodpis wnioskodawcy ____\n"
)
# Real text layer lines of drive form 01 (gov.pl fonts without a ToUnicode map).
CIPHER = (
    "=JÊRV]HQLHSRE\\WXstałego / Permanentu;vb7;m1;u;]bv|u-ঞom=oul\n"
    "1. 'DQHRVRE\\\x0fNWµUHMGRW\\F]\\]JÊRV]HQLH\x12;|-bѴvo=|_;r;uvom|o_ol|_;-rrѴb1-ঞomu;Ѵ-|;v\n"
    "Wypełnij, jHÐli QLHPDV]QXPHUX3(6(/: / &RPSlHWH\x0fLI\\RXGRQRWKDYHD3(6(/QXPEHU:\n"
)


def test_garbled_text_layer_is_not_text():
    from app.extract.pdf import _looks_like_text

    assert _looks_like_text(GOOD_PAGE * 3)  # e-mail and URL symbols do not count as garbage
    assert not _looks_like_text(GOOD_PAGE * 3 + CIPHER)  # a half-garbled page is left out too
    caesar = "".join(chr((ord(c) - 94) % 26 + 97) if "a" <= c <= "z" else c for c in GOOD_PAGE * 8)
    assert not _looks_like_text(caesar)  # a cipher of letters only: too few vowels


@needs_drive
def test_garbled_pdf_pages_are_left_out():
    """Drive 01: pages 1-2 have a cipher text layer, page 3 is a scan; none has text to give (the pipeline reads
    page images, "Źródło" OCRs them)."""
    path = DRIVE / "01_zgloszenie_pobytu_stalego_gov.pdf"
    doc = extract(path.read_bytes(), path.name)
    assert doc.method == "pdf-text" and "3 of 3" in doc.warnings[0] and "JÊRV" not in doc.text


@needs_data
def test_scanned_pdf_has_no_text():
    """A PDF made of page images (no text layer): empty text and warnings, no error."""
    buf = io.BytesIO()
    Image.open(FILES["5"]["png"]).convert("RGB").save(buf, format="PDF", resolution=137)
    doc = extract(buf.getvalue(), "scan.pdf")
    assert doc.text == "" and doc.warnings == ["1 of 1 PDF pages have no usable text layer, left out", "no text found"]


def test_image_is_unsupported():
    buf = io.BytesIO()
    Image.new("L", (200, 200), 255).save(buf, format="PNG")
    with pytest.raises(UnsupportedFile, match="vision model"):
        extract(buf.getvalue(), "x.png")


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
