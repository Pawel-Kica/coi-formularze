"""Small text helpers shared by the builder, the rules checker and the benchmark."""

import html
import re
import unicodedata

_PL = str.maketrans({"ł": "l", "Ł": "L"})  # NFKD does not decompose ł


def ascii_fold(text: str) -> str:
    """Polish diacritics -> ascii ("Łódź" -> "Lodz")."""
    text = unicodedata.normalize("NFKD", text.translate(_PL))
    return "".join(c for c in text if not unicodedata.combining(c))


def slugify(text: str, fallback: str = "pole", max_len: int = 60) -> str:
    """Label -> COI techName: ascii, lowercase, `_` separated ("Numer PESEL" -> "numer_pesel").
    Long labels are cut at a word boundary to `max_len` characters."""
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_fold(text).lower()).strip("_")
    if len(slug) > max_len:
        cut = slug[: max_len + 1].rsplit("_", 1)[0]
        slug = cut if cut else slug[:max_len]
    return slug or fallback


def html_paragraphs(text: str) -> str:
    """Plain text -> the COI description HTML (one <p> per non-empty line)."""
    paras = [p.strip() for p in (text or "").splitlines() if p.strip()]
    body = "".join(f"<p>{html.escape(p, quote=False)}</p>" for p in paras)
    return f"<html><head></head><body>{body}</body></html>"
