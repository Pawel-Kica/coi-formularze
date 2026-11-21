"""Experiment: does giving the model OCR line bounding boxes make a better e-service?

One scanned page, OCR'd elsewhere (lines with [x1,y1,x2,y2] boxes), three variants on the local model:
  A  plain OCR text        -> planner (2 passes) -> quote filter -> builder
  B  OCR lines with bboxes -> planner (2 passes, + a note on coordinates) -> quote filter -> builder
  C  OCR lines with bboxes -> model writes the subject-matter COI tabs directly (no Plan, no builder);
     the standard pages (Dane, Kontakt i zgody, finish) are taken from B so the preview is complete.
Writes <out>/{A,B,C}.json (+ plans, timings, rules problems) for a manual verdict.

    uv run python -m bench.bbox_test <boxes.json> <fullpage.txt> <out_dir>
"""

import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.builder import build
from app.llm import planner
from app.llm.client import chat_json
from app.quotes import THRESHOLD_OCR, filter_plan
from app.rules import SCHEMA, check

COI = Path.home() / "hacknation-2026/resources/formularze"
RULES_DOCX = COI / "Materiały do zadania dla Uczestników" / (
    "Reguły techniczne konfiguracji formularzy i pól na formularzu - materiał do zadania obligatoryjnego  (2).docx"
)

BBOX_NOTE = (
    "\n\n# Współrzędne linii\n\nTekst formularza to linie z OCR skanu. Każda linia zaczyna się od `[x1,y1,x2,y2]`: "
    "prostokąt tej linii na stronie w pikselach (rozmiar strony podany w pierwszej linii). Użyj ich, żeby zrozumieć "
    "układ: co stoi obok czego w jednym wierszu, wcięcia (podpunkty), tekst wyrównany do prawej (nagłówek, adresat, "
    "miejsce na datę i podpis), co jest podpisem pod czym. W cytatach (`quote`) przepisuj sam tekst, bez współrzędnych."
)


def bbox_text(boxes: dict) -> str:
    head = f"strona {boxes['width']}x{boxes['height']} px"
    # a line may come without a bbox (the OCR's own filler, e.g. the signature dots): text only
    return "\n".join([head] + [f"[{','.join(map(str, ln['bbox']))}] {ln['text']}" if ln.get("bbox") else ln["text"]
                               for ln in boxes["lines"]])


def make_plan(text: str, note: str = ""):
    """planner.make_plan with an optional extra note on both system prompts (no monkeypatching, A and B run in parallel)."""
    src = planner.prepare_text(text)
    user1 = f"Tekst formularza:\n```\n{src}\n```\n\n{planner.TASK}"
    plan = planner.draft_to_plan(
        chat_json(planner._structure_system() + note, user1, planner.STRUCTURE_SCHEMA, name="plan").data
    )
    user2 = f"Tekst formularza:\n```\n{src}\n```\n\nPlan:\n```\n{planner.plan_listing(plan)}\n```"
    r2 = chat_json(planner._conditions_system() + note, user2, planner.conditions_schema(plan),
                   name="conditions", max_tokens=4000)
    return planner.apply_conditions(plan, r2.data)


def run_plan(name: str, text: str, plain: str, note: str, out: Path) -> dict:
    t = time.time()
    plan = make_plan(text, note)
    secs = round(time.time() - t, 1)
    (out / f"{name}.plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    plan, dropped = filter_plan(plan, plain, THRESHOLD_OCR)
    coi = build(plan)
    return {"name": name, "secs": secs, "dropped": dropped, "coi": coi}


def _example_tab(title: str) -> str:
    """One subject-matter tab of a COI reference JSON (forms 1 and 5), compact."""
    for p in COI.glob("*/*.json"):
        if "materiał referencyjny" in p.parent.name:
            d = json.loads(p.read_text(encoding="utf-8"))
            for tb in d["tabs"]:
                if tb["title"] == title:
                    return json.dumps(tb, ensure_ascii=False)
    raise LookupError(title)


def direct_system() -> str:
    from app.extract import extract

    rules = extract(RULES_DOCX.read_bytes(), "r.docx").text
    return (
        "Jesteś projektantem e-usług w Centralnym Ośrodku Informatyki (COI). Dostajesz linie OCR skanu pustego, "
        "papierowego formularza urzędowego; każda linia ma `[x1,y1,x2,y2]` (prostokąt na stronie w pikselach), "
        "kropki `....` to miejsca do wpisania, `□` to pole do zaznaczenia. Zaprojektuj z niego formularz elektroniczny "
        "i zwróć go wprost w formacie importu COI (JSON).\n\n"
        "Zwróć TYLKO strony merytoryczne (`tabs` typu MAIN): o co wnioskodawca prosi, czego sprawa dotyczy, załączniki. "
        "NIE twórz strony z danymi wnioskodawcy (imię, nazwisko, adres zamieszkania, telefon), adresata, pełnomocnika, "
        "zgód RODO, miejsca i daty, podpisu ani strony końcowej: kod dokłada je sam ze Standardów COI. Adres innej rzeczy "
        "(np. nieruchomości) to treść merytoryczna. Pole widoczne tylko po wyborze opcji dostaje `states` typu SHOW z "
        "warunkiem jsonata jak w przykładach. Etykiety prostym językiem, bez numeracji z papieru. `id` dowolne unikalne, "
        "`techName` z liter, cyfr i `_`.\n\n# Reguły techniczne COI\n\n" + rules
        + "\n\n# Przykłady stron merytorycznych z INNYCH formularzy (tylko format)\n\n"
        + _example_tab("Informacje o budynku") + "\n\n" + _example_tab("Informacje o nadpłacie") + "\n"
    )


def run_direct(text: str, out: Path) -> dict:
    schema = {"type": "object", "required": ["tabs"], "$defs": SCHEMA["$defs"],
              "properties": {"tabs": SCHEMA["properties"]["tabs"]}}
    t = time.time()
    r = chat_json(direct_system(), f"Tekst formularza:\n```\n{text}\n```\n\nZaprojektuj strony merytoryczne e-usługi.",
                  schema, name="coi_tabs", max_tokens=12000)
    secs = round(time.time() - t, 1)
    (out / "C.raw.json").write_text(json.dumps(r.data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"name": "C", "secs": secs, "dropped": [], "tabs": r.data["tabs"]}


def assemble(b_coi: dict, tabs: list[dict]) -> dict:
    """B's standard pages around the model's own subject-matter tabs, linear flow."""
    std = b_coi["tabs"]
    contact = next(i for i, t in enumerate(std) if t["title"].startswith("Kontakt"))
    for t in tabs:
        t["type"] = "MAIN"
    all_tabs = [std[0], *tabs, *std[contact:]]
    links = [{"id": str(uuid.uuid4()), "source": a["id"], "target": b["id"]} for a, b in zip(all_tabs, all_tabs[1:])]
    return {"title": b_coi["title"], "tabs": all_tabs, "flow": {"links": links}}


def main(boxes_path: str, fullpage_path: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    boxes = json.loads(Path(boxes_path).read_text(encoding="utf-8"))
    plain = Path(fullpage_path).read_text(encoding="utf-8")
    btext = bbox_text(boxes)
    (out / "input_A.txt").write_text(planner.prepare_text(plain), encoding="utf-8")
    (out / "input_B.txt").write_text(planner.prepare_text(btext), encoding="utf-8")
    with ThreadPoolExecutor(3) as ex:  # Ollama has 2 slots; the third call queues
        fa = ex.submit(run_plan, "A", plain, plain, "", out)
        fb = ex.submit(run_plan, "B", btext, plain, BBOX_NOTE, out)
        fc = ex.submit(run_direct, btext, out)
        res = [fa.result(), fb.result(), fc.result()]
    res[2]["coi"] = assemble(res[1]["coi"], res[2].pop("tabs"))
    summary = {}
    for r in res:
        (out / f"{r['name']}.json").write_text(json.dumps(r["coi"], ensure_ascii=False, indent=2), encoding="utf-8")
        summary[r["name"]] = {"secs": r["secs"], "quote_dropped": r["dropped"], "rules_problems": check(r["coi"])}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:4])
