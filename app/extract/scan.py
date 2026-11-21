"""Image operations for scanned forms (Pillow + numpy, no OpenCV).

- `split_pages`: a tall image of n stacked A4 pages -> n pages, overlapping at each cut.
- `prepare`: grayscale, upscale small scans, fix 90/180/270 rotation, deskew.
- `is_noisy` / `is_blurry`: cheap detectors; ocr.py then also tries a median filter / sharpening.
- `find_rules`: horizontal rules: dotted fill-in leaders, underlines, table borders. They wreck
  tesseract's layout analysis (whole lines of labels next to dots vanish), so we erase them
  before OCR and put dotted ones back into the text as `____` (what a PDF text layer has).
- `lines_with_rules`: tesseract words -> lines, with `____` re-inserted and boxes as `[ ]`.
"""

import re
from dataclasses import dataclass

import numpy as np
import pytesseract
from PIL import Image

A4 = 297 / 210
OVERLAP = 0.04  # share of a page height each cut page also takes from its neighbour (~2 lines of text)


@dataclass
class Rule:
    x0: int
    x1: int
    y: int  # vertical centre
    kind: str  # "dotted" (dot/dash leader), "solid" (underline, table border), "other" (e.g. bars of capitals)


def split_pages(img: Image.Image) -> list[Image.Image]:
    """A tall image that is n A4 pages stacked (a common way to 'scan' a multi-page form into one
    file) -> n page images, cut at the whitest row near each expected page boundary. Each page
    reaches OVERLAP past its cuts, so a line a cut runs through is whole on one side: pages that
    are not quite A4 (cropped margins) shift the cuts into the text. A line read twice is fine,
    `app.repair` keeps one of two identical fields."""
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
    return [img.crop((0, max(0, a - pad), w, min(h, b + pad))) for a, b in zip(cuts, cuts[1:])]


def prepare(img: Image.Image, target_width: int, orient: bool, deskew: bool) -> Image.Image:
    g = img.convert("L")
    if target_width and g.width < target_width:
        s = target_width / g.width
        g = g.resize((round(g.width * s), round(g.height * s)), Image.Resampling.LANCZOS)
    if orient:
        g = fix_orientation(g)
    if deskew:
        angle = skew_angle(g)
        if abs(angle) >= 0.15:
            g = g.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    return g


def ink_mask(g: Image.Image) -> np.ndarray:
    """Dark pixels, split from the paper by Otsu's threshold (capped so a near-blank page
    does not turn its paper texture into ink)."""
    a = np.asarray(g)
    hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
    w = np.cumsum(hist)
    mu = np.cumsum(hist * np.arange(256))
    total, mu_t = w[-1], mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu_t * w - mu * total) ** 2 / (w * (total - w))
    between = np.nan_to_num(between[:255])
    if not between.any():
        return np.zeros(a.shape, dtype=bool)  # a single grey level: blank page
    return a <= min(int(between.argmax()), 180)


def is_noisy(g: Image.Image) -> bool:
    """Speckle noise: neighbouring pixels differ a lot even on blank paper. On clean scans,
    blurred or JPEG-compressed ones the median difference is 0-2; Gaussian noise gives 4+."""
    a = np.asarray(g, dtype=np.int16)
    return float(np.median(np.abs(np.diff(a, axis=1)))) >= 4


def is_blurry(g: Image.Image) -> bool:
    """Soft edges: even the strongest steps between neighbouring pixels are small (a sharp scan
    has ink-to-paper steps of 150-250 grey levels; a blurred one 20-40)."""
    a = np.asarray(g, dtype=np.int16)
    return float(np.percentile(np.abs(np.diff(a, axis=1)), 99.9)) < 80


def fix_orientation(g: Image.Image) -> Image.Image:
    """Fix 90/180/270 degree rotation. Tesseract OSD proposes; when it is unsure (or proposes a
    turn) a quick OCR of each candidate decides, so a noisy upright page is never flipped."""
    small = g.copy()
    small.thumbnail((1600, 1600))
    try:
        osd = pytesseract.image_to_osd(small, config="--psm 0 -c min_characters_to_try=10")
        rot = int(re.search(r"Rotate: (\d+)", osd).group(1))
        conf = float(re.search(r"Orientation confidence: ([\d.]+)", osd).group(1))
    except (pytesseract.TesseractError, AttributeError, ValueError):
        rot, conf = 0, 0.0  # too little text for OSD
    if rot == 0 and conf >= 2.0:
        return g
    candidates = {0, rot, 180}
    best = max(sorted(candidates), key=lambda r: ocr_score(small.rotate(-r, expand=True) if r else small))
    return g.rotate(-best, expand=True, fillcolor=255) if best else g


def ocr_score(g: Image.Image, config: str = "--psm 4") -> float:
    """How much confident text tesseract reads: sum over words of confidence x length."""
    data = pytesseract.image_to_data(g, lang="pol", config=config, output_type=pytesseract.Output.DICT)
    return word_score(data)


def word_score(data: dict) -> float:
    return sum(max(0.0, float(c)) / 100 * len(t.strip()) for c, t in zip(data["conf"], data["text"]))


def skew_angle(g: Image.Image, limit: float = 5.0) -> float:
    """Small-angle deskew: the rotation (degrees, counter-clockwise) that makes text rows
    sharpest, i.e. maximizes the variance of ink per row. Coarse 0.5 step, then 0.1."""
    small = g.copy()
    small.thumbnail((1200, 1200))
    ink = Image.fromarray((ink_mask(small) * 255).astype(np.uint8))

    def sharpness(angle: float) -> float:
        rows = np.asarray(ink.rotate(angle, resample=Image.Resampling.NEAREST), dtype=np.float32).sum(axis=1)
        return float(rows.var())

    best = max((a / 2 for a in range(int(-limit * 2), int(limit * 2) + 1)), key=sharpness)
    return max((best + a / 10 for a in range(-4, 5)), key=sharpness)


# --- horizontal rules ------------------------------------------------------------------
def _window_sum(m: np.ndarray, k: int, axis: int) -> np.ndarray:
    """Centered sliding-window sum of length k (odd) along `axis`, same shape as `m`."""
    pads = [(0, 0), (0, 0)]
    pads[axis] = (k // 2 + 1, k - 1 - k // 2)
    c = np.cumsum(np.pad(m.astype(np.int32), pads), axis=axis)
    n = m.shape[axis]
    return c[:, k : k + n] - c[:, :n] if axis == 1 else c[k : k + n, :] - c[:n, :]


# Odd window lengths only: an even window is off-centre and shifts erosion against dilation.
def _erode(m, k, axis):
    k |= 1
    return _window_sum(m, k, axis) == k


def _dilate(m, k, axis):
    return _window_sum(m, k | 1, axis) > 0


def find_rules(g: Image.Image) -> tuple[np.ndarray, list[Rule]]:
    """-> (pixel mask of horizontal rules, rules). Close small horizontal gaps (dots become a
    bar), keep what is thin vertically and long horizontally. Letters stay: they are tall."""
    ink = ink_mask(g)
    mm = g.width / 210  # pixels per mm, assuming an A4-wide page
    gap, thin, length = max(3, round(1.3 * mm)), max(3, round(0.7 * mm)), max(20, round(8 * mm))
    closed = _erode(_dilate(ink, gap, 1), gap, 1)
    tall = _dilate(_erode(closed, thin + 1, 0), thin + 1, 0)
    mask = _dilate(_erode(closed & ~tall, length, 1), length, 1)
    rules = _segments(mask, ink, mm)
    for r in rules:
        if r.kind == "other":  # not a rule: keep those pixels for tesseract
            mask[max(0, r.y - 2 * thin) : r.y + 2 * thin + 1, r.x0 : r.x1] = False
    return mask, rules


def _segments(mask: np.ndarray, ink: np.ndarray, mm: float) -> list[Rule]:
    """Connected horizontal runs of the mask (merged over adjacent rows) -> rules, classified by
    the ink runs along them: a leader is many short dots, a line is one solid run; anything
    else (tops of capitals bridged by the closing) is "other"."""
    rows = np.nonzero(mask.any(axis=1))[0]
    open_: list[list[int]] = []  # [x0, x1, y0, y1]
    done: list[list[int]] = []
    for y in rows:
        r = np.concatenate(([0], mask[y].view(np.int8), [0]))
        d = np.diff(r)
        runs = list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))
        still = []
        for seg in open_:
            if seg[3] < y - 1:
                done.append(seg)
            else:
                still.append(seg)
        open_ = still
        for x0, x1 in runs:
            for seg in open_:
                if x0 < seg[1] and x1 > seg[0]:
                    seg[0], seg[1], seg[3] = min(seg[0], x0), max(seg[1], x1), y
                    break
            else:
                open_.append([int(x0), int(x1), int(y), int(y)])
    done += open_
    rules = []
    for x0, x1, y0, y1 in done:
        cols = ink[y0 : y1 + 1, x0:x1].any(axis=0)
        d = np.diff(np.concatenate(([0], cols.view(np.int8), [0])))
        runs = np.nonzero(d == -1)[0] - np.nonzero(d == 1)[0]
        if cols.mean() >= 0.9:
            kind = "solid"
        elif len(runs) >= 6 and np.median(runs) <= max(2.0, 0.8 * mm):
            kind = "dotted"
        else:
            kind = "other"
        rules.append(Rule(int(x0), int(x1), (y0 + y1) // 2, kind))
    return rules


def erase(g: Image.Image, mask: np.ndarray) -> Image.Image:
    a = np.asarray(g).copy()
    a[_dilate(mask, 3, 0) & (a < 200)] = 255
    return Image.fromarray(a)


# --- text lines from tesseract words + rules ---------------------------------------------
def lines_with_rules(data: dict, rules: list[Rule], ink: np.ndarray | None = None, fill: str = "____") -> list[str]:
    """tesseract image_to_data words -> text lines in tesseract's reading order, with each dotted
    rule put back as `fill`: inside the text line on its baseline, else as its own line.
    With the page's `ink` mask, short "words" that are really empty checkboxes become `[ ]`."""
    lines: dict[tuple, dict] = {}
    order: list[tuple] = []
    for i, text in enumerate(data["text"]):
        if data["level"][i] != 5 or not text.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        if key not in lines:
            lines[key] = {"words": [], "top": y, "bottom": y + h, "left": x, "right": x + w}
            order.append(key)
        ln = lines[key]
        if ink is not None and len(text.strip()) <= 3 and is_checkbox(ink, x, y, w, h):
            text = "[ ]"
        ln["words"].append((x, text))
        ln["top"], ln["bottom"] = min(ln["top"], y), max(ln["bottom"], y + h)
        ln["left"], ln["right"] = min(ln["left"], x), max(ln["right"], x + w)
    out = [lines[k] for k in order]
    loose = []
    for r in rules:
        if r.kind != "dotted":
            continue
        host = None
        for ln in out:
            h = ln["bottom"] - ln["top"]
            if ln["top"] <= r.y <= ln["bottom"] + 0.4 * h:
                if host is None or abs(r.y - ln["bottom"]) < abs(r.y - host["bottom"]):
                    host = ln
        if host is not None and _near(host, r):
            host["words"].append((r.x0, fill))
        else:
            loose.append(r)
    for r in sorted(loose, key=lambda r: r.y):
        new = {"words": [(r.x0, fill)], "top": r.y, "bottom": r.y, "left": r.x0, "right": r.x1}
        above = [i for i, ln in enumerate(out) if ln["top"] <= r.y and _overlap(ln, r)]
        below = [i for i, ln in enumerate(out) if ln["top"] > r.y and _overlap(ln, r)]
        idx = max(above) + 1 if above else (min(below) if below else len(out))
        out.insert(idx, new)
    return [" ".join(t for _, t in sorted(ln["words"], key=lambda w: w[0])) for ln in out]


def is_checkbox(ink: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
    """A square 1.5-8 mm box with all four sides inked and an empty inside (tesseract reads such
    boxes as 'm', 'O', '0', 'D', '[]', ...)."""
    mm = ink.shape[1] / 210
    if not (1.5 * mm <= h <= 8 * mm and 0.7 <= w / max(h, 1) <= 1.4):
        return False
    pad = max(1, round(0.3 * mm))
    box = ink[max(0, y - pad) : y + h + pad, max(0, x - pad) : x + w + pad]
    rows, cols = box.mean(axis=1), box.mean(axis=0)
    k = max(2, len(rows) // 4)
    sides = [rows[:k].max(), rows[-k:].max(), cols[:k].max(), cols[-k:].max()]
    inner = box[len(rows) // 4 : -(len(rows) // 4) or None, len(cols) // 4 : -(len(cols) // 4) or None]
    return min(sides) >= 0.7 and inner.size > 0 and inner.mean() < 0.12


def _overlap(ln: dict, r: Rule) -> bool:
    return ln["left"] < r.x1 and r.x0 < ln["right"]


def _near(ln: dict, r: Rule) -> bool:
    """A rule belongs to a text line when it starts or ends within a few line-heights of it."""
    h = max(1, ln["bottom"] - ln["top"])
    return r.x0 <= ln["right"] + 8 * h and r.x1 >= ln["left"] - 8 * h

