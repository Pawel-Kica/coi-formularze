"""Benchmark scoring: our output vs a reference, by fuzzy matching of labels.

    score_coi(ours, ref)   COI JSON vs a reference COI JSON (forms 1 and 5):
                           recall of tabs, sections, fields, dependencies.
    score_plan(ours, gold) a model's Plan vs a hand-written gold Plan (7 forms):
                           field recall/precision, types, options, conditions,
                           applicant/addressee agreement.

CLI:
    uv run python bench/score.py coi  <ours.json> <ref.json>  [--json]
    uv run python bench/score.py plan <ours.json> <gold.json> [--json]

Both return a dict; `report["summary"]` is a short readable text.
"""

import json
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.plan import Plan
from app.rules import query_atoms
from app.text import ascii_fold, strip_html

THRESHOLD = 75  # minimum similarity (0-100) for two labels to be "the same"
_STOP = {"z", "w", "i", "o", "na", "do", "ze", "plik", "pliku", "sekcja", "oraz", "a"}


def norm(text: str) -> str:
    """Label -> comparable form: no HTML, ascii, lowercase, no punctuation or filler words.
    TERYT_DOWODOWY and TERYT_4G_DOWODOWY are treated as the same dictionary."""
    t = ascii_fold(strip_html(text or "")).lower().replace("_", " ")
    t = re.sub(r"\bteryt\s+(4g\s+)?dowodowy\b", "teryt", t)
    words = [w for w in re.findall(r"[a-z0-9]+", t) if w not in _STOP]
    return " ".join(words)


def sim(a: str, b: str) -> float:
    """Similarity 0-100 of two labels (exact after normalization = 100)."""
    na, nb = norm(a), norm(b)
    if not na and not nb:
        return 100.0
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    return 0.5 * fuzz.ratio(na, nb) + 0.5 * fuzz.token_set_ratio(na, nb)


def pair(refs: list, ours: list, score) -> list[tuple[int, int, float]]:
    """Greedy one-to-one matching, best pairs first; returns (ref index, our index, score)."""
    cands = sorted(
        ((score(r, o), i, j) for i, r in enumerate(refs) for j, o in enumerate(ours)),
        key=lambda c: (-c[0], c[1], c[2]),
    )
    used_r, used_o, out = set(), set(), []
    for s, i, j in cands:
        if s >= THRESHOLD and i not in used_r and j not in used_o:
            used_r.add(i)
            used_o.add(j)
            out.append((i, j, s))
    return sorted(out)


def _overlap(ref_labels: list[str], our_labels: list[str]) -> float:
    """Share (0-100) of ref labels that have a fuzzy match among ours."""
    if not ref_labels:
        return 0.0
    return 100.0 * len(pair(ref_labels, our_labels, sim)) / len(ref_labels)


def _recall(found: int, total: int) -> float:
    return round(found / total, 4) if total else 1.0


# --- COI JSON vs reference ---------------------------------------------------------


def _field_label(f: dict) -> str:
    """Title, or for untitled blocks (TEXT_BLOCK, CONSENT_SECTION) their text."""
    if (f.get("title") or "").strip():
        return f["title"]
    if f.get("type") == "CONSENT_SECTION":
        consents = (f.get("consentSectionConfig") or {}).get("consents") or []
        return " ".join(strip_html(c.get("description", "")) for c in consents)[:300] or "zgoda"
    return strip_html(f.get("description", ""))[:300] or f.get("type", "")


def _flatten(coi: dict) -> dict:
    """{tabs: [{title, sections: [{title, field, fields: [field]}]}], by_tech: {(tab, sec, fld): field}}"""
    tabs, by_tech = [], {}
    for t in coi.get("tabs", []):
        secs = []
        for q in t.get("questions", []):
            s = q["field"]
            kids = [c["field"] for c in s.get("repeatingSectionGrid") or []]
            secs.append({"title": _field_label(s), "field": s, "fields": kids})
            for k in kids:
                by_tech[(t["title"], s.get("techName"), k.get("techName"))] = k
        tabs.append({"title": t.get("title", ""), "sections": secs})
    return {"tabs": tabs, "by_tech": by_tech}


def _signature(f: dict) -> list[tuple[str, bool]]:
    """Options and negations of a field's conditions (to tell same-label fields apart)."""
    return sorted((norm(a[0]), a.negated) for st in f.get("states") or [] for a in query_atoms(st.get("query", ""))[0])


def _deps(flat: dict) -> list[dict]:
    """Every (element with states) x (condition atom) as a dependency."""
    out = []
    for ti, t in enumerate(flat["tabs"]):
        for si, s in enumerate(t["sections"]):
            elems = [(None, s["field"])] + list(enumerate(s["fields"]))
            for fi, f in elems:
                for st in f.get("states") or []:
                    for a in query_atoms(st.get("query", ""))[0]:
                        opt, tab, sec, fld = a
                        trig = flat["by_tech"].get((tab, sec, fld))
                        out.append({
                            "target": (ti, si, fi),
                            "trigger": _field_label(trig) if trig else fld,
                            "option": opt,
                            "negated": a.negated,
                            "label": f"{t['title']} / {s['title']}"
                                     + (f" / {_field_label(f)}" if fi is not None else "")
                                     + f" <- {'NOT ' if a.negated else ''}"
                                     + f"{_field_label(trig) if trig else fld} = {opt}",
                        })  # fmt: skip
    return out


def score_coi(ours: dict, ref: dict) -> dict:
    """Element recall of `ours` against reference COI JSON `ref`.

    Tabs match by title (or, failing that, by their sections), sections by title within
    matched tabs (or by their fields), fields by label + type within matched sections.
    Ids, order, grid, validation and descriptions are ignored."""
    o, r = _flatten(ours), _flatten(ref)
    kinds = ("tabs", "sections", "fields", "dependencies")
    found = dict.fromkeys(kinds, 0)
    total = dict.fromkeys(kinds, 0)
    missing = {k: [] for k in kinds}
    extra = {k: [] for k in kinds}
    weak, type_mismatch = [], []
    sec_map: dict[tuple, tuple] = {}  # ref (ti, si) -> ours (ti, si)
    field_map: dict[tuple, tuple] = {}  # ref (ti, si, fi) -> ours

    def tab_score(rt, ot):
        content = _overlap([s["title"] for s in rt["sections"]], [s["title"] for s in ot["sections"]])
        return max(sim(rt["title"], ot["title"]), 0.9 * content)

    def sec_score(rs, os_):
        content = _overlap([_field_label(f) for f in rs["fields"]], [_field_label(f) for f in os_["fields"]])
        return max(sim(rs["title"], os_["title"]), 0.9 * content)

    def field_score(rf, of):
        s = sim(_field_label(rf), _field_label(of))
        if s < THRESHOLD:
            return s
        # Tie-breakers for same-label fields (e.g. Polish vs foreign "Ulica"):
        # type, then the same show/hide signature, then techName similarity.
        s += 5 if rf.get("type") == of.get("type") else 0
        s += 2 if _signature(rf) == _signature(of) else 0
        return s + fuzz.ratio(rf.get("techName", ""), of.get("techName", "")) / 100

    tab_pairs = pair(r["tabs"], o["tabs"], tab_score)
    total["tabs"] = len(r["tabs"])
    found["tabs"] = len(tab_pairs)
    tmap = {i: j for i, j, _ in tab_pairs}
    missing["tabs"] = [t["title"] for i, t in enumerate(r["tabs"]) if i not in tmap]
    extra["tabs"] = [t["title"] for j, t in enumerate(o["tabs"]) if j not in tmap.values()]

    for ti, rt in enumerate(r["tabs"]):
        total["sections"] += len(rt["sections"])
        total["fields"] += sum(len(s["fields"]) for s in rt["sections"])
        oj = tmap.get(ti)
        ot = o["tabs"][oj] if oj is not None else {"sections": []}
        spairs = pair(rt["sections"], ot["sections"], sec_score)
        smap = {i: j for i, j, _ in spairs}
        found["sections"] += len(spairs)
        for si, rs in enumerate(rt["sections"]):
            where = f"{rt['title']} / {rs['title']}"
            if si not in smap:
                missing["sections"].append(where)
                missing["fields"] += [f"{where} / {_field_label(f)} [{f.get('type')}]" for f in rs["fields"]]
                continue
            sec_map[(ti, si)] = (oj, smap[si])
            os_ = ot["sections"][smap[si]]
            fpairs = pair(rs["fields"], os_["fields"], field_score)
            fmap = {i: j for i, j, _ in fpairs}
            left_r = [i for i in range(len(rs["fields"])) if i not in fmap]
            left_o = [j for j in range(len(os_["fields"])) if j not in fmap.values()]
            if (len(left_r) == 1 and len(left_o) == 1
                    and rs["fields"][left_r[0]].get("type") == os_["fields"][left_o[0]].get("type")):
                fmap[left_r[0]] = left_o[0]  # the only leftover pair, same type: same field
                weak.append(f"{where} / {_field_label(rs['fields'][left_r[0]])} ~ "
                            f"{_field_label(os_['fields'][left_o[0]])}")
            for fi, rf in enumerate(rs["fields"]):
                label = f"{where} / {_field_label(rf)} [{rf.get('type')}]"
                if fi not in fmap:
                    missing["fields"].append(label)
                    continue
                of = os_["fields"][fmap[fi]]
                if of.get("type") != rf.get("type"):
                    type_mismatch.append(f"{label} vs ours [{of.get('type')}]")
                    missing["fields"].append(label)
                    continue
                found["fields"] += 1
                field_map[(ti, si, fi)] = (oj, smap[si], fmap[fi])
            extra["fields"] += [f"{where} / {_field_label(os_['fields'][j])} [{os_['fields'][j].get('type')}]"
                                for j in range(len(os_["fields"])) if j not in fmap.values()]
        for j, os_ in enumerate(ot["sections"]):
            if j not in smap.values():
                extra["sections"].append(f"{ot.get('title', '')} / {os_['title']}")

    # Dependencies: same (mapped) target, trigger label, option and negation.
    rdeps, odeps = _deps(r), _deps(o)
    total["dependencies"] = len(rdeps)

    def map_target(t):
        ti, si, fi = t
        if fi is None:
            m = sec_map.get((ti, si))
            return None if m is None else (m[0], m[1], None)
        return field_map.get((ti, si, fi))

    def dep_score(rd, od):
        if map_target(rd["target"]) != od["target"] or rd["negated"] != od["negated"]:
            return 0.0
        return min(sim(rd["trigger"], od["trigger"]), sim(rd["option"], od["option"]))

    dpairs = pair(rdeps, odeps, dep_score)
    found["dependencies"] = len(dpairs)
    dm = {i for i, _, _ in dpairs}
    missing["dependencies"] = [d["label"] for i, d in enumerate(rdeps) if i not in dm]
    dmo = {j for _, j, _ in dpairs}
    extra["dependencies"] = [d["label"] for j, d in enumerate(odeps) if j not in dmo]

    recall = {k: _recall(found[k], total[k]) for k in kinds}
    recall["overall"] = _recall(sum(found.values()), sum(total.values()))
    report = {"recall": recall, "found": found, "total": total, "missing": missing, "extra": extra,
              "weak_matches": weak, "type_mismatch": type_mismatch}
    report["summary"] = _coi_summary(report)
    return report


def _coi_summary(rep: dict) -> str:
    lines = ["recall: " + ", ".join(
        f"{k} {rep['found'][k]}/{rep['total'][k]} ({rep['recall'][k]:.0%})" for k in rep["found"]
    ) + f", overall {rep['recall']['overall']:.0%}"]  # fmt: skip
    singular = {"tabs": "tab", "sections": "section", "fields": "field", "dependencies": "dependency"}
    for k, items in rep["missing"].items():
        lines += [f"  missing {singular[k]}: {x}" for x in items]
    for k, items in rep["extra"].items():
        lines += [f"  extra {singular[k]}: {x}" for x in items]
    lines += [f"  weak match: {x}" for x in rep["weak_matches"]]
    lines += [f"  type mismatch: {x}" for x in rep["type_mismatch"]]
    return "\n".join(lines)


# --- Plan vs gold Plan ------------------------------------------------------------------


def _plan_fields(plan: Plan) -> list[dict]:
    out = []
    for pi, page in enumerate(plan.pages):
        for s in page.sections:
            for f in s.fields:
                out.append({"page": pi, "section": s, "field": f})
    return out


def _options_f1(gold: list[str], ours: list[str]) -> float:
    if not gold and not ours:
        return 1.0
    if not gold or not ours:
        return 0.0
    m = len(pair(gold, ours, sim))
    p, r = m / len(ours), m / len(gold)
    return 0.0 if m == 0 else 2 * p * r / (p + r)


def score_plan(ours: Plan, gold: Plan) -> dict:
    """Compare a model Plan with a gold Plan. Fields are matched across the whole plan
    (label, or the paper quote as a fallback), with a bonus for sitting on the matched
    page, so a different page split does not hide correct fields."""
    from app.builder.standard import is_organ

    page_pairs = pair(gold.pages, ours.pages, lambda g, o: max(
        sim(g.title, o.title),
        0.9 * _overlap([f.label for s in g.sections for f in s.fields],
                       [f.label for s in o.sections for f in s.fields])))  # fmt: skip
    page_map = {i: j for i, j, _ in page_pairs}

    gf, of = _plan_fields(gold), _plan_fields(ours)

    def fscore(g, o):
        s = max(sim(g["field"].label, o["field"].label), 0.9 * sim(g["field"].quote, o["field"].quote))
        return s + (5 if page_map.get(g["page"]) == o["page"] else 0) if s >= THRESHOLD else s

    fpairs = pair(gf, of, fscore)
    fmap = {i: j for i, j, _ in fpairs}
    n = len(fpairs)
    recall, precision = _recall(n, len(gf)), _recall(n, len(of))
    f1 = round(2 * recall * precision / (recall + precision), 4) if recall + precision else 0.0

    type_ok, req_ok, opt_scores, type_diff, opt_diff = 0, 0, [], [], []
    for i, j in fmap.items():
        g, o = gf[i]["field"], of[j]["field"]
        if g.type == o.type:
            type_ok += 1
        else:
            type_diff.append(f"{g.label}: gold {g.type}, ours {o.type}")
        req_ok += g.required == o.required
        if g.options or o.options:
            s = _options_f1(g.options, o.options)
            opt_scores.append(s)
            if s < 1:
                opt_diff.append(f"{g.label}: gold {g.options}, ours {o.options}")

    # Conditions: the target and the trigger map to each other, options overlap, same negation.
    gid = {x["field"].id: k for k, x in enumerate(gf)}
    oid = {x["field"].id: k for k, x in enumerate(of)}
    gsec = [s for p in gold.pages for s in p.sections]
    osec = [s for p in ours.pages for s in p.sections]
    smap = {i: j for i, j, _ in pair(gsec, osec, lambda g, o: sim(g.title, o.title))}

    def conds(plan_fields, sections):
        out = [("field", k, x["field"].show_if) for k, x in enumerate(plan_fields) if x["field"].show_if]
        out += [("section", k, s.show_if) for k, s in enumerate(sections) if s.show_if]
        return out

    gconds, oconds = conds(gf, gsec), conds(of, osec)

    def cscore(g, o):
        kind, k, gc = g
        okind, ok, oc = o
        target = fmap.get(k) if kind == "field" else smap.get(k)
        trig_g, trig_o = gid.get(gc.field), oid.get(oc.field)
        if kind != okind or target != ok or trig_g is None or fmap.get(trig_g) != trig_o or gc.negate != oc.negate:
            return 0.0
        return 100.0 * _options_f1(gc.options, oc.options)

    cpairs = pair(gconds, oconds, cscore)
    cm = {i for i, _, _ in cpairs}

    def cond_label(c, plan_fields, sections):
        kind, k, cond = c
        target = plan_fields[k]["field"].label if kind == "field" else sections[k].title
        return f"{target} <- {cond.field} in {cond.options}{' (negated)' if cond.negate else ''}"

    a, b = gold.applicant, ours.applicant
    applicant = {k: getattr(a, k) == getattr(b, k) for k in ("person", "entity", "pesel", "proxy")}
    applicant["entity_ids"] = set(a.entity_ids) == set(b.entity_ids)
    addressee_ok = sim(gold.addressee, ours.addressee) >= 85 and is_organ(gold.addressee) == is_organ(ours.addressee)
    st_pairs = pair([s.text for s in gold.statements], [s.text for s in ours.statements], sim)

    metrics = {
        "fields_recall": recall,
        "fields_precision": precision,
        "fields_f1": f1,
        "type_agreement": _recall(type_ok, n),
        "required_agreement": _recall(req_ok, n),
        "options_agreement": round(sum(opt_scores) / len(opt_scores), 4) if opt_scores else 1.0,
        "conditions_recall": _recall(len(cpairs), len(gconds)),
        "conditions_precision": _recall(len(cpairs), len(oconds)),
        "pages_recall": _recall(len(page_pairs), len(gold.pages)),
        "statements_recall": _recall(len(st_pairs), len(gold.statements)),
        "applicant_agreement": _recall(sum(applicant.values()), len(applicant)),
        "addressee_ok": addressee_ok,
        "title_similarity": round(sim(gold.title, ours.title) / 100, 4),
    }
    metrics["score"] = round(sum(metrics[k] for k in (
        "fields_f1", "type_agreement", "options_agreement", "conditions_recall", "applicant_agreement",
    )) / 5, 4)  # fmt: skip
    report = {
        "metrics": metrics,
        "applicant": applicant,
        "missing_fields": [gf[i]["field"].label for i in range(len(gf)) if i not in fmap],
        "extra_fields": [of[j]["field"].label for j in range(len(of)) if j not in fmap.values()],
        "type_diff": type_diff,
        "options_diff": opt_diff,
        "missing_conditions": [cond_label(c, gf, gsec) for i, c in enumerate(gconds) if i not in cm],
        "addressee": {"gold": gold.addressee, "ours": ours.addressee},
    }
    report["summary"] = _plan_summary(report)
    return report


def _plan_summary(rep: dict) -> str:
    m = rep["metrics"]
    lines = [(
        f"score {m['score']:.0%} | fields R {m['fields_recall']:.0%} P {m['fields_precision']:.0%} "
        f"F1 {m['fields_f1']:.0%} | types {m['type_agreement']:.0%} | options {m['options_agreement']:.0%} "
        f"| conditions R {m['conditions_recall']:.0%} | required {m['required_agreement']:.0%} "
        f"| pages {m['pages_recall']:.0%} | statements {m['statements_recall']:.0%} "
        f"| applicant {m['applicant_agreement']:.0%} | addressee {'ok' if m['addressee_ok'] else 'WRONG'}"
    )]
    lines += [f"  applicant.{k} differs" for k, ok in rep["applicant"].items() if not ok]
    if not m["addressee_ok"]:
        lines.append(f"  addressee: gold {rep['addressee']['gold']!r}, ours {rep['addressee']['ours']!r}")
    lines += [f"  missing field: {x}" for x in rep["missing_fields"]]
    lines += [f"  extra field: {x}" for x in rep["extra_fields"]]
    lines += [f"  type: {x}" for x in rep["type_diff"]]
    lines += [f"  options: {x}" for x in rep["options_diff"]]
    lines += [f"  missing condition: {x}" for x in rep["missing_conditions"]]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--json"]
    if len(args) != 3 or args[0] not in ("coi", "plan"):
        print(__doc__)
        return 2
    kind, ours_path, ref_path = args
    if kind == "coi":
        rep = score_coi(json.loads(Path(ours_path).read_text()), json.loads(Path(ref_path).read_text()))
    else:
        rep = score_plan(Plan.model_validate_json(Path(ours_path).read_text()),
                         Plan.model_validate_json(Path(ref_path).read_text()))
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str) if "--json" in argv else rep["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
