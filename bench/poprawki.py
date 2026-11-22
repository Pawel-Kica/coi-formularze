"""Clerk corrections ("liczba poprawek urzędnika"): judge kits, the 2-of-3 count, planted-error calibration.

    uv run python -I bench/poprawki.py pick <run_all run> [--forms 2,3,4,6,7,8,9]   # best file per form
    uv run python -I bench/poprawki.py kit <run_dir> --files 3/pdf,8/pdf [--out <dir>]
    uv run python -I bench/poprawki.py kit <file.coi.json> --form 3 [--out <dir>]
    uv run python -I bench/poprawki.py count <kit_dir>
    uv run python -I bench/poprawki.py plant <form> --out <dir>/form3-planted.coi.json
    uv run python -I bench/poprawki.py calib <clean kit> <planted kit> --key <key.json>

No network, no model. The brief and the recipe (3 judges per file, 2 of 3 count) are in
`bench/JUDGE-POPRAWKI.md`.

`kit` writes one folder per file: `page-NN.png` = the paper (the form's PDF in `data/coi/<N>. .../` for every
format of a `bench.run_all` run, since LibreOffice drops text boxes in forms 2 and 8 DOCX and renders form 9 to
24 pages; the source file itself for a `bench.run_dir` run), `ours.md` = our COI JSON as the clerk sees it,
with each element's techName (the outline of `bench/judge_kit.py`), `BRIEF.md` = the judge brief, and
`elements.json` = techName -> label + section (used by `count`, judges don't need it). A single COI JSON
(approach comparison arms, planted calibration) works too: give `--form`.

`count` reads `judge-1.jsonl` .. `judge-3.jsonl` in every kit folder, matches lines across judges and keeps
the corrections that 2 of 3 judges list; `count.json` in `<kit_dir>`. Matching (`same`): the categories fall in
the same group (type and options are one group, missing and wrong condition another) and the elements are the
same: two techNames must be equal (or one is the other's section); otherwise the word stems (first 5 letters,
diacritics folded, a techName expanded with its label) of the shorter element are >= 60% inside the other.
Pairs are joined best score first, a group holds at most one line per judge.

`plant` builds `app.builder.build(gold/plans/formN.json)` and plants up to 10 known errors (3 deleted fields,
2 flipped types, 1 dropped condition, 1 invented field, 1 broken option list, 1 flipped required, 1 condition
on the wrong option), deterministic per form. Writes the planted COI JSON, `<name>.key.json` and the clean
build next to it (`form<N>-clean.coi.json`). Small forms get fewer errors (form 3 has 6 own elements); the key
says how many.

`calib` = the proof of the judge: found = planted errors matched by a counted correction on the planted kit
(one to one); invented = counted corrections on the planted kit matching neither a planted error nor a
counted correction on the clean kit.
"""

import argparse
import copy
import json
import random
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.builder import build_with_evidence
from app.builder.core import Ctx, field
from app.plan import Plan
from app.rules import check, query_atoms
from bench.blind import run_items
from bench.judge_kit import LEGEND, STANDARD_PAGES, _count, _is_address, _kids, _label, outline, write_images

COI_DIR = ROOT / "data" / "coi"
GOLD = ROOT / "gold" / "plans"
BRIEF = ROOT / "bench" / "JUDGE-POPRAWKI.md"
JUDGES = 3
CATEGORIES = {"missing_field": "brak", "invented_field": "zmyślone", "wrong_type": "typ", "wrong_options": "typ",
              "wrong_condition": "warunek", "missing_condition": "warunek", "wrong_required": "wymagane"}
SIM = 0.6


# --- kit ------------------------------------------------------------------------------


def form_pdf(form: str) -> Path:
    """The paper of a COI form: the PDF in its `data/coi/<N>. ...` folder."""
    d = next(p for p in COI_DIR.iterdir() if p.is_dir() and p.name.split(".")[0] == form)
    return next(f for f in sorted(d.iterdir()) if f.suffix.lower() == ".pdf")


def brief_text() -> str:
    """The judge part of JUDGE-POPRAWKI.md (between the `<!-- brief -->` markers)."""
    s = BRIEF.read_text()
    return s.split("<!-- brief -->")[1].split("<!-- /brief -->")[0].strip() + "\n"


def elements(coi: dict) -> dict[str, dict]:
    """techName -> {label, section} for every section and field (techNames are unique per build)."""
    out = {}
    for t in coi.get("tabs", []):
        for q in t.get("questions", []):
            s = q["field"]
            out[s["techName"]] = {"label": _label(s), "section": None, "page": t["title"]}
            for k in _kids(s):
                out[k["techName"]] = {"label": _label(k), "section": s["techName"], "page": t["title"]}
    return out


def ours_md(kit_id: str, coi: dict) -> str:
    problems = check(coi)
    own, total = _count(coi)
    head = [f"# {kit_id}: nasza e-usługa", "", f"- Tytuł e-usługi: {coi.get('title')}",
            f"- Pola do wypełnienia: {own} na stronach formularza, {total} razem ze stronami standardowymi",
            "- Reguły COI (import): " + ("OK" if not problems else f"{len(problems)} problemów")]
    head += [f"  - {p}" for p in problems[:10]]
    head += ["- W nawiasach `...` id elementu (techName): podawaj je w polu `element`."]
    return "\n".join(head + ["", LEGEND, *outline(coi, ids=True), ""])


def make_kit(out: Path, kit_id: str, coi: dict, paper: Path) -> Path:
    d = out / kit_id
    d.mkdir(parents=True, exist_ok=True)
    note = write_images(paper, d)
    (d / "ours.md").write_text(ours_md(kit_id, coi))
    (d / "BRIEF.md").write_text(brief_text())
    (d / "elements.json").write_text(json.dumps(elements(coi), ensure_ascii=False, indent=1))
    print(f"kit {d}  (papier: {paper.name}, {note})")
    return d


def cmd_kit(a) -> int:
    src = Path(a.source).expanduser().resolve()
    if src.is_file():
        if not a.form:
            raise SystemExit("a single COI JSON needs --form N (whose paper to show)")
        out = Path(a.out or src.parent / "poprawki").expanduser()
        make_kit(out, f"{a.form}-{src.name.removesuffix('.json').removesuffix('.coi')}",
                 json.loads(src.read_text()), form_pdf(a.form))
        return 0
    items = run_items(src)
    want = set(a.files.split(",")) if a.files else None
    out = Path(a.out or src / "poprawki").expanduser()
    for kit_id, it in sorted(items.items()):
        if want and it.key not in want:
            continue
        if not it.coi:
            print(f"skip {kit_id}: no COI JSON ({it.error})")
            continue
        run_all = (src / "coi").is_dir()
        make_kit(out, kit_id, json.loads(it.coi.read_text()), form_pdf(it.key.split("/")[0]) if run_all else it.src)
    return 0


def best_files(run: Path, forms: list[str]) -> dict[str, dict]:
    """Per form of a `bench.run_all` run: the file with the highest gold= among those with 0 COI rule problems
    (`summary.json` rows when the run is done, else scored here from the cached plan and COI JSON)."""
    from bench.score import score_plan

    base = run / "coi"
    rows = {(r["form"], r["ext"]): r for r in json.loads((base / "summary.json").read_text())["rows"]} \
        if (base / "summary.json").exists() else {}
    out = {}
    for form in forms:
        gold = Plan.model_validate_json((GOLD / f"form{form}.json").read_text())
        cands = []
        for coi_path in sorted((base / form).glob("*.coi.json")):
            ext = coi_path.name.removesuffix(".coi.json")
            r = rows.get((form, ext)) or {}
            if "gold" not in r:
                plan = Plan.model_validate_json((base / form / f"{ext}.plan.json").read_text())
                r = {"gold": round(score_plan(plan, gold)["metrics"]["score"], 3),
                     "problems": check(json.loads(coi_path.read_text()))}
            if not r.get("problems"):
                cands.append({"form": form, "ext": ext, "gold": r["gold"]})
        if cands:  # ties: the PDF, then the first format by name
            out[form] = max(cands, key=lambda c: (c["gold"], c["ext"] == "pdf", [-ord(ch) for ch in c["ext"]]))
    return out


def cmd_pick(a) -> int:
    best = best_files(Path(a.run).expanduser(), a.forms.split(","))
    for form, b in best.items():
        print(f"form {form}: {b['ext']} gold={b['gold']}")
    print(",".join(f"{b['form']}/{b['ext']}" for b in best.values()))
    return 0


# --- matching -------------------------------------------------------------------------

STOP = {"czy", "jak", "jaki", "jaka", "jakie", "sie", "dla", "lub", "oraz", "pole", "pola", "jest", "przy", "ktory",
        "ktora", "ktore", "the", "and", "albo", "ten", "tej", "tego", "jego", "jej", "twoj", "twoja", "twoje"}


def fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def stems(text: str) -> set[str]:
    """Word stems: camelCase split, diacritics folded, first 5 letters, words of 3+ letters minus STOP."""
    text = re.sub(r"(?<=[a-ząćęłńóśźż])(?=[A-ZĄĆĘŁŃÓŚŹŻ])", " ", text)
    return {w[:5] for w in re.findall(r"[a-z0-9]+", fold(text)) if len(w) >= 3 and w not in STOP}


def tech_of(element: str, els: dict) -> str | None:
    """The techName a judge cited (bare or in backticks), if it is one of the kit's elements."""
    e = element.strip().strip("`").strip()
    return e if e in els else None


def _bag(line: dict, els: dict) -> set[str]:
    tech = tech_of(line["element"], els)
    extra = " ".join(str(line.get(k) or "") for k in ("label", "quote"))  # planted key entries carry these
    return stems(line["element"] + " " + (els[tech]["label"] if tech else "") + " " + extra)


def same(a: dict, b: dict, els: dict) -> float:
    """0 = different corrections; > 0 = the same one (higher = surer). See the module docstring."""
    if CATEGORIES[a["category"]] != CATEGORIES[b["category"]]:
        return 0.0
    ta, tb = tech_of(a["element"], els), tech_of(b["element"], els)
    if ta and tb:
        if ta == tb:
            return 2.0
        return 1.5 if els[ta]["section"] == tb or els[tb]["section"] == ta else 0.0
    if fold(a["element"]).strip() == fold(b["element"]).strip():
        return 2.0
    sa, sb = _bag(a, els), _bag(b, els)
    if not sa or not sb:
        return 0.0
    s = len(sa & sb) / min(len(sa), len(sb))
    return s if s >= SIM else 0.0


def read_judge(path: Path) -> list[dict]:
    out = []
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("```"):
            continue
        line = json.loads(raw)
        if line.get("category") not in CATEGORIES or not str(line.get("element") or "").strip():
            raise SystemExit(f"{path}:{n}: bad line (element + category from {sorted(CATEGORIES)}): {raw}")
        out.append({"element": str(line["element"]), "category": line["category"], "fix": str(line.get("fix") or "")})
    return out


def cluster(lines: dict[int, list[dict]], els: dict) -> list[list[tuple[int, dict]]]:
    """Lines of several judges -> groups of the same correction, at most one line per judge per group."""
    nodes = [(j, ln) for j, ls in lines.items() for ln in ls]
    group = list(range(len(nodes)))
    judges = [{j} for j, _ in nodes]
    pairs = sorted(((same(nodes[x][1], nodes[y][1], els), x, y) for x in range(len(nodes))
                    for y in range(x + 1, len(nodes)) if nodes[x][0] != nodes[y][0]), reverse=True)

    def root(i):
        while group[i] != i:
            i = group[i]
        return i

    for s, x, y in pairs:
        rx, ry = root(x), root(y)
        if s <= 0 or rx == ry or judges[rx] & judges[ry]:
            continue
        group[ry] = rx
        judges[rx] |= judges[ry]
    out: dict[int, list] = {}
    for i, n in enumerate(nodes):
        out.setdefault(root(i), []).append(n)
    return list(out.values())


def count_dir(d: Path) -> dict:
    """One kit folder -> counted corrections (2 of 3) + the single-judge leftovers."""
    els = json.loads((d / "elements.json").read_text()) if (d / "elements.json").exists() else {}
    lines = {j: read_judge(d / f"judge-{j}.jsonl") for j in range(1, JUDGES + 1) if (d / f"judge-{j}.jsonl").exists()}
    counted, singles = [], []
    for g in cluster(lines, els):
        rep = dict(g[0][1])  # the first judge's wording stands for the group
        rep["judges"] = sorted(j for j, _ in g)
        (counted if len(g) >= 2 else singles).append(rep)
    by_cat: dict[str, int] = {}
    for c in counted:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    return {"judges": sorted(lines), "per_judge": {j: len(ls) for j, ls in lines.items()},
            "counted": len(counted), "by_category": by_cat, "corrections": counted, "singles": singles}


def cmd_count(a) -> int:
    kit = Path(a.kit_dir).expanduser()
    dirs = [kit] if (kit / "ours.md").exists() else sorted(p for p in kit.iterdir() if (p / "ours.md").exists())
    result = {}
    for d in dirs:
        r = count_dir(d)
        result[d.name] = r
        print(f"\n{d.name}: {r['counted']} poprawek (2 z 3; sędziowie {r['judges']}, linie {r['per_judge']})  {r['by_category']}")
        for c in r["corrections"]:
            print(f"  [{c['category']}] {c['element']}: {c['fix']}  (sędziowie {c['judges']})")
    (kit / "count.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"\n-> {kit / 'count.json'}")
    return 0


# --- plant ----------------------------------------------------------------------------

INVENTED = "Numer karty wędkarskiej"
FLIP = {"TEXT_FIELD": "DATEPICKER", "TEXTAREA": "NUMBER", "NUMBER": "DATEPICKER", "DATEPICKER": "NUMBER",
        "FILE_UPLOADER": "TEXT_FIELD"}
CHOICE = ("RADIO", "CHECKBOX", "SELECT")
OPS = ["delete", "delete", "delete", "type", "type", "cond_drop", "invent", "options", "required", "cond_wrong"]


class Planter:
    """Plants errors into a COI JSON one at a time, keeping it importable (0 rule problems)."""

    def __init__(self, coi: dict, evidence: dict, seed: str):
        self.coi, self.ev, self.rng = coi, evidence, random.Random(seed)
        self.key: list[dict] = []
        self.touched: dict[str, set[str]] = {}  # techName -> category groups already planted on it

    # what is where
    def sections(self):
        for t in self.coi["tabs"]:
            if t["title"] in STANDARD_PAGES:
                continue
            for q in t["questions"]:
                s = q["field"]
                if not _is_address(_kids(s)):
                    yield t, s

    def fields(self):
        for t, s in self.sections():
            for c in s["repeatingSectionGrid"]:
                yield t, s, c["field"]

    def atoms(self):
        return [a for t in self.coi["tabs"] for q in t["questions"]
                for f in [q["field"], *_kids(q["field"])] for st in f.get("states") or []
                for a in query_atoms(st.get("query", ""))[0]]

    def triggers(self) -> set[str]:
        return {a[3] for a in self.atoms()}

    def by_tech(self, tech: str) -> dict:
        return next(f for t in self.coi["tabs"] for q in t["questions"]
                    for f in [q["field"], *_kids(q["field"])] if f["techName"] == tech)

    def pick(self, cands: list, group: str):
        """Untouched candidates first, then ones without a planted error of this group."""
        fresh = [c for c in cands if c[-1]["techName"] not in self.touched]
        ok = fresh or [c for c in cands if group not in self.touched.get(c[-1]["techName"], set())]
        return self.rng.choice(ok) if ok else None

    def record(self, el: dict, category: str, fix: str, element: str | None = None):
        self.touched.setdefault(el["techName"], set()).add(CATEGORIES[category])
        ev = self.ev.get(el["id"]) or {}
        self.key.append({"element": element or el["techName"], "category": category, "fix": fix,
                         "label": _label(el), "quote": ev.get("quote") or ev.get("condition_quote") or ""})

    # the errors
    def delete(self) -> bool:
        trig = self.triggers()
        labels = [e["label"] for e in elements(self.coi).values()]
        cands = [(t, s, f) for t, s, f in self.fields() if f["type"] != "TEXT_BLOCK" and f["techName"] not in trig
                 and f["techName"] not in self.touched and len(s["repeatingSectionGrid"]) > 1
                 and labels.count(_label(f)) == 1]  # a label found nowhere else: the key stays unambiguous
        plain = [c for c in cands if not c[2].get("states")]  # leave conditioned fields for the condition errors
        if not cands:
            return False
        t, s, f = self.rng.choice(plain or cands)
        s["repeatingSectionGrid"] = [c for c in s["repeatingSectionGrid"] if c["field"] is not f]
        self.record(f, "missing_field", f"dodać pole „{_label(f)}” ({f['type']})", element=_label(f))
        self.key[-1]["techName"] = f["techName"]
        return True

    def type(self) -> bool:
        trig = self.triggers()
        c = self.pick([(t, s, f) for t, s, f in self.fields() if f["type"] in FLIP and f["techName"] not in trig],
                      "typ")
        if not c:
            return False
        f = c[2]
        old, f["type"] = f["type"], FLIP[f["type"]]
        f["validation"] = [v for v in f.get("validation") or [] if v == "REQUIRED"]
        self.record(f, "wrong_type", f"zmienić typ {f['type']} na {old}")
        return True

    def _conditioned(self):
        """Sections and input fields with a SHOW condition (info texts are not judged, see the brief)."""
        out = [(t, s, s) for t, s in self.sections() if s.get("states")]
        return out + [(t, s, f) for t, s, f in self.fields() if f.get("states") and f["type"] != "TEXT_BLOCK"]

    def cond_drop(self) -> bool:
        c = self.pick(self._conditioned(), "warunek")
        if not c:
            return False
        el = c[2]
        words = el["states"][0]["query"]
        el["states"] = []
        self.record(el, "missing_condition", "dodać warunek pokazania z papieru")
        self.key[-1]["dropped_query"] = words
        return True

    def cond_wrong(self) -> bool:
        cands = []
        for t, s, el in self._conditioned():
            atoms = [a for st in el["states"] for a in query_atoms(st["query"])[0]]
            trig = {a[3] for a in atoms}
            if len(trig) != 1 or any(a.negated for a in atoms):
                continue
            opts = [o["name"] for o in self.by_tech(trig.pop()).get("options") or []]
            other = [o for o in opts if o not in {a[0] for a in atoms}]
            if other:
                cands.append((atoms[0], other, el))
        c = self.pick(cands, "warunek")
        if not c:
            return False
        atom, other, el = c
        new = self.rng.choice(other)
        st = el["states"][0]
        st["query"] = st["query"].replace(atom.text, atom.text.replace(_lit(atom[0]), _lit(new), 1), 1)
        self.record(el, "wrong_condition", f"warunek: „{atom[0]}” zamiast „{new}”")
        return True

    def options(self) -> bool:
        used = {(a[3], a[0]) for a in self.atoms()}
        cands = []
        for t, s, f in self.fields():
            opts = [o["name"] for o in f.get("options") or []]
            free = [o for o in opts if (f["techName"], o) not in used]
            if f["type"] in CHOICE and not f.get("dictionaryName") and len(opts) >= 2 and free:
                cands.append((len(opts) >= 3, free, f))
        c = self.pick([x for x in cands if x[0]] or cands, "typ")
        if not c:
            return False
        _, free, f = c
        gone = self.rng.choice(free)
        f["options"] = [o for o in f["options"] if o["name"] != gone]
        self.record(f, "wrong_options", f"dodać opcję „{gone}”")
        return True

    def required(self) -> bool:
        c = self.pick([(t, s, f) for t, s, f in self.fields()
                       if "REQUIRED" in (f.get("validation") or []) and not f.get("states") and not s.get("states")],
                      "wymagane")
        if not c:
            return False
        f = c[2]
        f["validation"] = [v for v in f["validation"] if v != "REQUIRED"]
        self.record(f, "wrong_required", "ustawić jako wymagane")
        return True

    def invent(self) -> bool:
        secs = [(t, s) for t, s in self.sections() if not s.get("states")]
        if not secs:
            return False
        t, s = self.rng.choice(secs)
        ctx = Ctx(self.coi["title"])
        ctx.used = set(elements(self.coi))
        f = field(ctx, "TEXT_FIELD", INVENTED, ctx.tech(INVENTED), validation=["REQUIRED"])
        s["repeatingSectionGrid"].append({"x": 0, "y": 0, "rows": 1, "cols": 3, "field": f})
        self.record(f, "invented_field", "usunąć pole, papier go nie ma")
        return True

    def relayout(self):
        for t in self.coi["tabs"]:
            y = 0
            for q in t["questions"]:
                s = q["field"]
                for i, c in enumerate(s["repeatingSectionGrid"]):
                    c["y"] = c["field"]["questionOrder"] = i
                q["y"] = s["questionOrder"] = y
                q["rows"] = len(s["repeatingSectionGrid"]) + 1
                y += q["rows"]


def _lit(option: str) -> str:
    """An option name as `app.builder.core.chosen` writes it inside a query."""
    return '"' + option.replace("\\", "\\\\").replace('"', '\\"') + '"'


def plant(form: str) -> tuple[dict, dict, list[dict]]:
    """Clean build of the gold plan, the planted copy and the key (planted errors in order)."""
    plan = Plan.model_validate_json((GOLD / f"form{form}.json").read_text())
    clean, evidence = build_with_evidence(plan)
    p = Planter(copy.deepcopy(clean), evidence, f"poprawki-{form}")
    for op in OPS:
        if not getattr(p, op)():
            print(f"form {form}: no target left for {op}, skipped")
    p.relayout()
    if problems := check(p.coi):
        raise SystemExit(f"planted form {form} breaks the COI rules: {problems[:3]}")
    return clean, p.coi, p.key


def cmd_plant(a) -> int:
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    clean, planted, key = plant(a.form)
    stem = out.name.removesuffix(".json").removesuffix(".coi")
    (out.parent / f"form{a.form}-clean.coi.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1))
    out.write_text(json.dumps(planted, ensure_ascii=False, indent=1))
    (out.parent / f"{stem}.key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1))
    print(f"form {a.form}: {len(key)} planted errors -> {out}, {out.parent / f'{stem}.key.json'}; "
          f"clean build -> {out.parent / f'form{a.form}-clean.coi.json'}")
    for k in key:
        print(f"  [{k['category']}] {k['element']}: {k['fix']}")
    return 0


# --- calibration ----------------------------------------------------------------------


def one_to_one(xs: list[dict], ys: list[dict], els: dict) -> list[tuple[int, int]]:
    """Best-first one-to-one matches between two lists of corrections."""
    pairs = sorted(((same(x, y, els), i, j) for i, x in enumerate(xs) for j, y in enumerate(ys)), reverse=True)
    used_x, used_y, out = set(), set(), []
    for s, i, j in pairs:
        if s > 0 and i not in used_x and j not in used_y:
            used_x.add(i)
            used_y.add(j)
            out.append((i, j))
    return out


def calib(clean_dir: Path, planted_dir: Path, key: list[dict]) -> dict:
    els = {**json.loads((clean_dir / "elements.json").read_text()),
           **json.loads((planted_dir / "elements.json").read_text())}
    clean, planted = count_dir(clean_dir)["corrections"], count_dir(planted_dir)["corrections"]
    hits = one_to_one(key, planted, els)
    hit_k, hit_p = {i for i, _ in hits}, {j for _, j in hits}
    rest = [c for j, c in enumerate(planted) if j not in hit_p]
    known = {j for j, c in enumerate(rest) if any(same(c, k, els) for k in clean)}
    invented = [c for j, c in enumerate(rest) if j not in known]
    return {"planted": len(key), "found": len(hits), "invented": len(invented),
            "missed": [k for i, k in enumerate(key) if i not in hit_k], "invented_list": invented,
            "clean_counted": len(clean), "planted_counted": len(planted),
            "pass": len(hits) >= 0.8 * len(key) and len(invented) <= 1}


def cmd_calib(a) -> int:
    r = calib(Path(a.clean).expanduser(), Path(a.planted).expanduser(), json.loads(Path(a.key).read_text()))
    print(f"found {r['found']}/{r['planted']}, invented {r['invented']} "
          f"(counted: clean {r['clean_counted']}, planted {r['planted_counted']}) -> {'PASS' if r['pass'] else 'FAIL'}")
    for k in r["missed"]:
        print(f"  missed [{k['category']}] {k['element']}: {k['fix']}")
    for c in r["invented_list"]:
        print(f"  invented [{c['category']}] {c['element']}: {c['fix']}  (sędziowie {c['judges']})")
    Path(a.planted, "calib.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("kit")
    k.add_argument("source", help="a bench.run_all / bench.run_dir run, or one COI JSON")
    k.add_argument("--files", help="run_all: 3/pdf,8/pdf; run_dir: file names")
    k.add_argument("--form", help="the form whose paper goes with a single COI JSON")
    k.add_argument("--out")
    c = sub.add_parser("count")
    c.add_argument("kit_dir")
    p = sub.add_parser("plant")
    p.add_argument("form")
    p.add_argument("--out", required=True)
    pk = sub.add_parser("pick", help="best file per form of a run_all run (gold=, 0 rule problems) -> --files")
    pk.add_argument("run")
    pk.add_argument("--forms", default="2,3,4,6,7,8,9")
    cb = sub.add_parser("calib")
    cb.add_argument("clean")
    cb.add_argument("planted")
    cb.add_argument("--key", required=True)
    a = ap.parse_args()
    return {"kit": cmd_kit, "count": cmd_count, "plant": cmd_plant, "pick": cmd_pick, "calib": cmd_calib}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
