"""Turn positioned text boxes (Word frames, shapes) into reading-order lines.

Converted forms (PDF -> DOC/DOCX) often hold every line in its own absolutely
positioned text box, stored in arbitrary order. Sorting boxes top-to-bottom and
joining boxes that sit on the same line left-to-right gives the same reading
order a PDF text layer has.
"""

from dataclasses import dataclass


@dataclass
class Box:
    x: float  # left edge, any unit, same for all boxes in one group
    y: float  # top edge
    h: float  # height (0 if unknown)
    text: str


def boxes_to_lines(boxes: list[Box], tol: float) -> list[str]:
    """Group boxes whose vertical centres are within `tol` into one line, read left to right."""
    rows: list[list[Box]] = []
    for b in sorted(boxes, key=lambda b: (b.y + b.h / 2, b.x)):
        if not b.text.strip():
            continue
        c = b.y + b.h / 2
        if rows and abs(c - (rows[-1][0].y + rows[-1][0].h / 2)) <= tol:
            rows[-1].append(b)
        else:
            rows.append([b])
    lines = []
    for row in rows:
        texts = [b.text.strip() for b in sorted(row, key=lambda b: b.x)]
        if any("\n" in t for t in texts):
            for t in texts:  # multi-line boxes side by side: read each box whole, left to right
                lines += t.split("\n")
        else:
            lines.append(" ".join(texts))
    return lines
