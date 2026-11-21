"""Extraction benchmarks for app.extract.

Consistency report, every form x every format:

    uv run python bench/extract_report.py [--forms 1,5] [--formats pdf,png] [--engine tesseract|vlm]

Writes each text to bench/extract/<form>/<ext>.txt and prints form x format -> status, chars,
method, seconds, similarity to the same form's PDF text (rapidfuzz ratio, 0-100) and line
recall (share of the PDF's lines found, fuzzy >= 90, anywhere in the other text).

Scan benchmark, tesseract configs vs a vision model on the 9 form images and degraded copies
(rotated, blurred, JPEG q30, noisy, half size, all at once, upside-down) in bench/extract/degraded/:

    uv run python bench/extract_report.py --ocr-bench [--forms 3,5] [--variants orig,rot]
        [--configs default,plain] [--vlm-model qwen2.5vl:7b --vlm-forms 3,5 --vlm-variants orig,noise]

Results accumulate in bench/extract/ocr_bench*.json (one entry per form/variant/engine).
"""

import argparse
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.extract import extract  # noqa: E402
from app.extract.normalize import normalize  # noqa: E402
from app.extract.ocr import Prep, ocr_pages, vlm_page  # noqa: E402
from app.extract.scan import split_pages  # noqa: E402

DATA = ROOT / "data" / "coi"
OUT = ROOT / "bench" / "extract"
FORMATS = {"pdf", "doc", "docx", "ods", "xlsx", "jpg", "jpeg", "jpe", "png"}


def form_files(form_dir: Path) -> dict[str, Path]:
    """Format key -> file. Keys are lowercase extensions; a second file with the same
    extension gets a numbered key (e.g. `png2`)."""
    files: dict[str, Path] = {}
    for p in sorted(form_dir.iterdir()):
        ext = p.suffix.lower().lstrip(".")
        if ext not in FORMATS or "wizualizacja" in p.name.lower():
            continue
        key = ext
        n = 2
        while key in files:
            key, n = f"{ext}{n}", n + 1
        files[key] = p
    return files


def forms() -> list[tuple[str, Path]]:
    dirs = [d for d in DATA.iterdir() if d.is_dir() and d.name[:1].isdigit()]
    return sorted(((d.name.split(".")[0], d) for d in dirs), key=lambda x: int(x[0]))


def line_recall(ref: str, text: str, cutoff: int = 90) -> float:
    """Share of the reference's content lines (>= 8 chars) found, fuzzily (partial ratio >= 90),
    anywhere in `text`. Insensitive to line wrapping, which differs between PDF and Word."""
    ref_lines = [l for l in ref.splitlines() if len(l) >= 8]
    flat = " ".join(text.split())
    if not ref_lines or not flat:
        return 0.0
    hits = sum(1 for l in ref_lines if fuzz.partial_ratio(l, flat, score_cutoff=cutoff))
    return hits / len(ref_lines)


# --- scan benchmark -------------------------------------------------------------------------
DEGRADED = OUT / "degraded"
BENCH_JSON = OUT / "ocr_bench.json"


def _noise(img: Image.Image, sigma: float, seed: int) -> Image.Image:
    a = np.asarray(img.convert("L"), dtype=np.float32)
    a += np.random.default_rng(seed).normal(0, sigma, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _jpeg(img: Image.Image, q: int) -> Image.Image:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=q)
    return Image.open(io.BytesIO(buf.getvalue()))


def _rot(img: Image.Image, deg: float) -> Image.Image:
    return img.convert("RGB").rotate(deg, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")


def _half(img: Image.Image) -> Image.Image:
    return img.resize((img.width // 2, img.height // 2), Image.Resampling.LANCZOS)


VARIANTS = {
    "orig": lambda im, n: im,
    "rot": lambda im, n: _rot(im, 1 + n % 3),  # 1-3 degrees, differs per form
    "blur": lambda im, n: im.filter(ImageFilter.GaussianBlur(2)),
    "jpeg30": lambda im, n: _jpeg(im, 30),
    "noise": lambda im, n: _noise(im, 30, n),
    "half": lambda im, n: _half(im),
    "combo": lambda im, n: _jpeg(_noise(_half(_rot(im, -(1 + n % 3))), 20, n), 40),  # phone-scan worst case
    "upside": lambda im, n: im.rotate(180),
}

CONFIGS = {
    "default": Prep(),
    "plain": Prep(min_width=0, orient=False, deskew=False, rules=False, fixes=False, psm=3),  # tesseract as is
    "no-rules": Prep(rules=False),
    "no-deskew": Prep(deskew=False),
    "no-orient": Prep(orient=False),
    "no-fixes": Prep(fixes=False),
    "no-upscale": Prep(min_width=0),
    "psm3": Prep(psm=3),
}


def degraded_pages(form: str, form_dir: Path, variant: str) -> list[Image.Image]:
    """Pages of the form's PNG scan after `variant`; the degraded file is cached on disk."""
    src = form_files(form_dir).get("png") or form_files(form_dir)["jpg"]
    DEGRADED.mkdir(parents=True, exist_ok=True)
    pages = []
    for i, page in enumerate(split_pages(Image.open(src))):
        path = DEGRADED / f"{form}_p{i + 1}_{variant}.{'jpg' if variant in ('jpeg30', 'combo') else 'png'}"
        if not path.exists():
            out = VARIANTS[variant](page, int(form))
            out.convert("L" if variant in ("noise", "combo") else "RGB").save(path, quality=95)
        pages.append(Image.open(path))
    return pages


def load_results() -> dict:
    """All stored scan-bench results (ocr_bench*.json, so parallel runs can use their own file)."""
    out = {}
    for p in sorted(OUT.glob("ocr_bench*.json")):
        out.update(json.loads(p.read_text()))
    return out


def ocr_bench(args):
    path = OUT / (args.results or BENCH_JSON.name)
    results = json.loads(path.read_text()) if path.exists() else {}
    want_forms = set(args.forms.split(",")) if args.forms else None
    variants = args.variants.split(",") if args.variants else list(VARIANTS)
    configs = args.configs.split(",") if args.configs is not None else list(CONFIGS)
    configs = [c for c in configs if c]
    vlm_forms = set(args.vlm_forms.split(",")) if args.vlm_forms else set()
    vlm_variants = args.vlm_variants.split(",") if args.vlm_variants else variants
    for form, d in forms():
        if want_forms and form not in want_forms:
            continue
        pdf = form_files(d)["pdf"]
        ref = extract(pdf.read_bytes(), pdf.name).text
        for variant in variants:
            pages = degraded_pages(form, d, variant)
            runs = [(c, lambda ps, c=c: ocr_pages(ps, "tesseract", CONFIGS[c])) for c in configs]
            if args.vlm_model and form in vlm_forms and variant in vlm_variants:
                runs.append((f"vlm:{args.vlm_model}", lambda ps: [vlm_page(p, args.vlm_model) for p in ps]))
            for name, run in runs:
                t0 = time.perf_counter()
                text = normalize("\n".join(run(pages)))
                secs = time.perf_counter() - t0
                key = f"{form}|{variant}|{name}"
                results[key] = {
                    "form": form, "variant": variant, "engine": name, "pages": len(pages),
                    "sim": round(fuzz.ratio(ref, text), 1), "lines": round(100 * line_recall(ref, text), 1),
                    "sec": round(secs, 2),
                }
                out = OUT / "ocr" / f"{form}_{variant}_{name.replace(':', '_')}.txt"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8")
                r = results[key]
                print(f"{form:>2} {variant:7} {name:22} sim {r['sim']:5.1f} lines {r['lines']:5.1f} {secs:6.1f}s ({len(pages)} p)", flush=True)
                path.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    print_bench_summary(load_results())


def print_bench_summary(results: dict):
    """Mean sim / line recall / seconds per page by engine x variant, over forms each engine ran."""
    engines = sorted({r["engine"] for r in results.values()})
    variants = [v for v in VARIANTS if any(r["variant"] == v for r in results.values())]
    print(f"\n{'engine':22} " + " ".join(f"{v:>16}" for v in variants) + "   s/page")
    for e in engines:
        cells = []
        secs, pages = 0.0, 0
        for v in variants:
            rs = [r for r in results.values() if r["engine"] == e and r["variant"] == v]
            if not rs:
                cells.append(f"{'-':>16}")
                continue
            secs += sum(r["sec"] for r in rs)
            pages += sum(r["pages"] for r in rs)
            cells.append(f"{sum(r['sim'] for r in rs) / len(rs):6.1f}/{sum(r['lines'] for r in rs) / len(rs):5.1f} n{len(rs):<2}")
        print(f"{e:22} " + " ".join(cells) + f"   {secs / max(pages, 1):6.2f}")
    print("cells: mean similarity to PDF text / mean % of PDF lines recovered, n = forms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", help="comma list of form numbers, default all")
    ap.add_argument("--formats", help="comma list of format keys, default all")
    ap.add_argument("--engine", help="OCR engine for images: tesseract (default) or vlm")
    ap.add_argument("--ocr-bench", action="store_true", help="run the scan benchmark instead")
    ap.add_argument("--variants", help=f"scan bench: comma list of {','.join(VARIANTS)}")
    ap.add_argument("--configs", help=f"scan bench: tesseract configs, comma list of {','.join(CONFIGS)} ('' = none)")
    ap.add_argument("--vlm-model", help="scan bench: also run this Ollama vision model")
    ap.add_argument("--vlm-forms", help="scan bench: forms for the vision model (it is slow)")
    ap.add_argument("--vlm-variants", help="scan bench: variants for the vision model")
    ap.add_argument("--results", help="scan bench: results file name in bench/extract (default ocr_bench.json)")
    ap.add_argument("--summary", action="store_true", help="scan bench: only print the stored summary")
    args = ap.parse_args()
    if args.summary:
        return print_bench_summary(load_results())
    if args.ocr_bench:
        return ocr_bench(args)
    want_forms = set(args.forms.split(",")) if args.forms else None
    want_formats = set(args.formats.split(",")) if args.formats else None

    rows = []
    failures = 0
    for form, d in forms():
        if want_forms and form not in want_forms:
            continue
        files = form_files(d)
        out_dir = OUT / form
        out_dir.mkdir(parents=True, exist_ok=True)
        texts = {}
        for key, path in files.items():
            if want_formats and key not in want_formats and key != "pdf":
                continue
            t0 = time.perf_counter()
            try:
                doc = extract(path.read_bytes(), path.name, ocr_engine=args.engine)
                status, text, method, warn = "ok", doc.text, doc.method, "; ".join(doc.warnings)
            except Exception as e:  # report and keep going
                status, text, method, warn = "ERROR", "", "-", f"{e.__class__.__name__}: {e}"
                failures += 1
            secs = time.perf_counter() - t0
            texts[key] = text
            (out_dir / f"{key}.txt").write_text(text, encoding="utf-8")
            rows.append([form, key, status, len(text), method, secs, warn])
        ref = texts.get("pdf", "")
        for r in rows:
            if r[0] == form:
                t = texts.get(r[1], "")
                r.append(fuzz.ratio(ref, t) if ref and t else 0.0)
                r.append(line_recall(ref, t) * 100 if ref and t else 0.0)

    print(f"{'form':>4} {'fmt':5} {'status':6} {'chars':>6} {'method':9} {'sec':>6} {'sim':>5} {'lines%':>6}  warnings")
    for form, key, status, chars, method, secs, warn, sim, rec in rows:
        print(f"{form:>4} {key:5} {status:6} {chars:6d} {method:9} {secs:6.2f} {sim:5.1f} {rec:6.1f}  {warn[:80]}")
    by_fmt: dict[str, list] = {}
    for r in rows:
        by_fmt.setdefault(r[1].rstrip("0123456789"), []).append(r)
    print("\nper format (mean): sim / line recall / seconds")
    for k, rs in sorted(by_fmt.items()):
        n = len(rs)
        print(f"  {k:5} n={n}  sim {sum(r[7] for r in rs) / n:5.1f}  lines {sum(r[8] for r in rs) / n:5.1f}%  {sum(r[5] for r in rs) / n:6.2f}s")
    print(f"\n{len(rows) - failures}/{len(rows)} ok")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
