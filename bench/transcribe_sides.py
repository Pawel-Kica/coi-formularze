"""Which page size does the transcription model need? (PIPELINE=images, `app/llm/transcribe.py`)

    uv run python -m bench.transcribe_sides --form 8 --ext pdf --pages 1,2 --sides 1024,1400,1800 [--gray 1400] [--out DIR]

Transcribes the chosen pages of one COI form file at each long side (TRANSCRIBE_MODEL / LLM_MODEL,
real API calls: one per page per side) and scores every transcription by recall of the gold quotes
(`gold/plans/formN.json`, every `quote` string). A quote belongs to a page when it is found (rapidfuzz
partial_ratio >= 90 on `app.quotes.normalize`d text) in that page's own extracted text or in any of its
transcriptions; recall = share of those found in one transcription. Prints side, prompt tokens per page
and recall; saves each Markdown to `<out>/<side>/p<N>.md` and the table to `<out>/sides.json`.
"""

import argparse
import io
import json
import time
from pathlib import Path

from rapidfuzz import fuzz

from app.config import settings
from app.extract import extract
from app.llm import client
from app.llm.transcribe import render_pages, transcribe_page
from app.quotes import normalize
from bench.run_all import COI, GOLD

MATCH = 90


def gold_quotes(form: str) -> list[str]:
    out = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("quote") and isinstance(v, str) and len(normalize(v)) >= 3:
                    out.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json.loads((GOLD / f"form{form}.json").read_text()))
    return list(dict.fromkeys(out))


def found(quote: str, text: str) -> bool:
    return fuzz.partial_ratio(normalize(quote), normalize(text)) >= MATCH


def page_text(img) -> str:
    """Our own extraction of one page image (OCR), the reference reading next to the transcriptions."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return extract(buf.getvalue(), "page.png").text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True)
    ap.add_argument("--ext", required=True, help="file extension in data/coi/<form>.*/, e.g. pdf, jpg")
    ap.add_argument("--pages", default="1", help="1-based page numbers, e.g. 1,2")
    ap.add_argument("--sides", default="1024,1400,1800")
    ap.add_argument("--gray", type=int, default=0, help="also transcribe grayscale at this side")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    folder = next(p for p in COI.iterdir() if p.name.startswith(f"{a.form}."))
    path = next(p for p in folder.iterdir() if p.suffix.lower() == f".{a.ext.lower()}")
    out = Path(a.out or f"bench/runs/sides-f{a.form}-{a.ext}-{time.strftime('%H%M')}")
    pages = render_pages(path.read_bytes(), path.name)
    nums = [int(n) for n in a.pages.split(",")]
    arms = [(f"{s}", s, False) for s in map(int, a.sides.split(","))] + ([(f"{a.gray}g", a.gray, True)] if a.gray else [])
    texts, tokens = {}, {}
    for name, side, gray in arms:
        (out / name).mkdir(parents=True, exist_ok=True)
        for n in nums:
            img = pages[n - 1].convert("L") if gray else pages[n - 1]
            client.CALLS.clear()
            md = transcribe_page(img, n, len(pages), side=side)
            (out / name / f"p{n}.md").write_text(md)
            texts[name, n] = md
            tokens[name, n] = sum(c.get("prompt_tokens") or 0 for c in client.CALLS[-1:])
    quotes = gold_quotes(a.form)
    rows = []
    for n in nums:
        own = page_text(pages[n - 1])
        mine = [q for q in quotes if found(q, own) or any(found(q, texts[name, n]) for name, _, _ in arms)]
        for name, _, _ in arms:
            hit = [q for q in mine if found(q, texts[name, n])]
            rows.append({"side": name, "page": n, "prompt_tokens": tokens[name, n], "quotes": len(mine),
                         "found": len(hit), "missed": [q for q in mine if q not in hit]})
    table = []
    for name, _, _ in arms:
        r = [x for x in rows if x["side"] == name]
        q, f = sum(x["quotes"] for x in r), sum(x["found"] for x in r)
        table.append({"side": name, "tokens_per_page": round(sum(x["prompt_tokens"] for x in r) / len(r)),
                      "recall": round(f / q, 3) if q else None, "found": f, "quotes": q})
        print(f"{name:>6}  tokens/page={table[-1]['tokens_per_page']:5}  recall={table[-1]['recall']}  ({f}/{q})")
    (out / "sides.json").write_text(json.dumps({"model": settings.transcribe_model or settings.llm_model,
                                                "file": path.name, "table": table, "rows": rows},
                                               ensure_ascii=False, indent=1))
    print("saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
