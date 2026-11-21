"""One text shape for every input format, so the same form gives (nearly) the same text.

- Unicode NFKC (Polish diacritics stay composed; superscripts, ligatures, odd spaces folded).
- Fill-in lines (dots, ellipses, underscores, dash runs) -> `____`.
- Checkbox glyphs -> `[ ]` (empty) / `[x]` (ticked).
- Typographic quotes, dashes and bullets folded, whitespace collapsed, one paper line per
  line; empty lines, page numbers and immediately repeated lines dropped.
"""

import re
import unicodedata

FILL = "____"


def _chars(*codes: int) -> str:
    return "".join(map(chr, codes))


# Code points, not literals: several are invisible or private-use (Wingdings/Symbol fonts).
EMPTY_BOX = _chars(0x2610, 0x25A1, 0x25A2, 0x274F, 0x2750, 0x2751, 0x2752, 0x25FB, 0x2B1C, 0x2B1A,
                   0x25AB, 0x25FD, 0xF06F, 0xF0A8, 0xF071, 0xF0A3, 0xF0FF)
TICKED_BOX = _chars(0x2611, 0x2612, 0x25A0, 0x25A3, 0x22A0, 0x2713, 0x2714, 0x2717, 0x2718, 0x2B1B,
                    0x25FC, 0x25AA, 0xF0FE, 0xF0FD, 0xF078, 0xF0FC)
BULLETS = _chars(0x2022, 0x25CF, 0x25E6, 0x2023, 0x2043, 0x2219, 0x00B7, 0x25CB, 0x25D8, 0xF0B7,
                 0xF0A7, 0xF076, 0xF0D8, 0xF0E8)
DASHES = _chars(0x2013, 0x2014, 0x2012, 0x2015, 0x2212, 0x2010, 0x2011)
DOUBLE_QUOTES = _chars(0x201E, 0x201D, 0x201C, 0x00AB, 0x00BB, 0x2033)
SINGLE_QUOTES = _chars(0x2018, 0x2019, 0x201A, 0x0060, 0x00B4, 0x2032)
INVISIBLE = _chars(0x00AD, 0x200B, 0x200C, 0x200D, 0xFEFF, 0x02)
LINE_BREAKS = _chars(0x0D, 0x0B, 0x0C, 0x2028, 0x2029)

_TRANS = str.maketrans(
    {
        **{c: " [ ] " for c in EMPTY_BOX},
        **{c: " [x] " for c in TICKED_BOX},
        **{c: "-" for c in DASHES},
        **{c: '"' for c in DOUBLE_QUOTES},
        **{c: "'" for c in SINGLE_QUOTES},
        **{c: "" for c in INVISIBLE},
        **{c: "\n" for c in LINE_BREAKS},
        "\t": " ",
    }
)

_FILL_RUN = re.compile(r"(?:[._]\s?){3,}|_{2,}|-{4,}")
_FILL_MERGE = re.compile(rf"{FILL}(?:[\s.,:;_-]*{FILL})+")
_BOX = re.compile(r"\[\s*([xX])?\s*\]")
_LEADING_BULLET = re.compile(rf"^[{re.escape(BULLETS)}]\s*")
_SPACES = re.compile(r"\s+")
_PAGE_NUMBER = re.compile(r"^(?:-\s*)?(?:strona|str\.)?\s*\d{1,3}\s*(?:/|z|of)\s*\d{1,3}(?:\s*-)?$", re.IGNORECASE)


def normalize(text: str) -> str:
    """Raw extracted text -> canonical text (see module docstring)."""
    text = text.translate(_TRANS)
    text = unicodedata.normalize("NFKC", text)  # also: ellipsis -> '...', superscript 1) -> '1)'
    out: list[str] = []
    for line in text.split("\n"):
        line = normalize_line(line)
        if line and (not out or line != out[-1]):
            out.append(line)
    return "\n".join(out)


def normalize_line(line: str) -> str:
    line = "".join(c for c in line if c >= " " and not (0xE000 <= ord(c) <= 0xF8FF))
    line = _FILL_RUN.sub(f" {FILL} ", line)
    line = _FILL_MERGE.sub(FILL, line)
    line = _BOX.sub(lambda m: "[x]" if m.group(1) else "[ ]", line)
    line = _LEADING_BULLET.sub("", line.strip())
    line = _SPACES.sub(" ", line).strip()
    line = re.sub(r" ([,;:)])", r"\1", line)  # "dnia ____ ," -> "dnia ____,"
    line = line.replace("( ", "(")
    line = re.sub(r"^(?:\| ?)+|(?: ?\|)+$", "", line).strip()  # dangling cell separators
    line = re.sub(r"(?: ?\| ?){2,}", " | ", line)
    if not any(c.isalnum() for c in line) and FILL not in line and "[" not in line:
        return ""  # pure punctuation, rules, OCR specks
    if _PAGE_NUMBER.match(line):
        return ""  # "strona 1/2", "2/6": printed by PDFs and scans, absent from Word/sheets
    return line
