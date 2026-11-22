"""Stacked page images (Pillow + numpy): `split_pages` cuts a tall image of n stacked A4 pages into n pages,
overlapping at each cut. Used before transcription (`app.llm.transcribe`) and for "Źródło" (`app.document.sources`).
"""

import numpy as np
from PIL import Image

A4 = 297 / 210
OVERLAP = 0.04  # share of a page height each cut page also takes from its neighbour (~2 lines of text)


def split_pages(img: Image.Image) -> list[Image.Image]:
    """A tall image that is n A4 pages stacked (a common way to 'scan' a multi-page form into one
    file) -> n page images, cut at the whitest row near each expected page boundary. Each page
    reaches OVERLAP past its cuts (its edge at the whitest row of the last half of that), so a line
    a cut runs through is whole on one side: pages that are not quite A4 (cropped margins) shift the
    cuts into the text. A line read twice may give the model a field twice; `app.grounding.repair` keeps one
    when the citizen could not tell the two apart."""
    w, h = img.size
    n = round(h / (w * A4))
    if n < 2:
        return [img]
    rows = np.asarray(img.convert("L").resize((1, h), Image.Resampling.BOX)).ravel().tolist()  # mean per row
    page_h = h / n
    cuts = [0]
    for k in range(1, n):
        target = int(k * page_h)
        lo, hi = max(cuts[-1] + 1, target - int(page_h * 0.05)), min(h - 1, target + int(page_h * 0.05))
        cuts.append(max(range(lo, hi), key=lambda y: (rows[y], -abs(y - target))) if hi > lo else target)
    cuts.append(h)
    pad = int(page_h * OVERLAP)
    white = lambda lo, hi: max(range(max(0, lo), min(h, hi) + 1), key=lambda y: rows[min(y, h - 1)])  # whitest row
    return [img.crop((0, white(a - pad, a - pad // 2) if a else 0, w, white(b + pad // 2, b + pad) if b < h else h))
            for a, b in zip(cuts, cuts[1:])]
