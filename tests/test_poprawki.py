"""bench/poprawki.py: the 2-of-3 count across judges, the planted errors and the calibration math. No model."""

import json

from app.rules import check
from bench import poprawki


def judge(path, lines):
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))


def kit(tmp_path, name, els, judges):
    d = tmp_path / name
    d.mkdir()
    (d / "ours.md").write_text("# kit\n")
    (d / "elements.json").write_text(json.dumps(els))
    for n, lines in judges.items():
        judge(d / f"judge-{n}.jsonl", lines)
    return d


ELS = {"pies": {"label": "Pies", "section": None}, "rasa": {"label": "Rasa", "section": "pies"},
       "plec": {"label": "Płeć", "section": "pies"}, "numer_tatuazu": {"label": "Numer tatuażu", "section": "pies"}}


def test_count_keeps_what_two_judges_list(tmp_path):
    d = kit(tmp_path, "7-pdf", ELS, {
        1: [{"element": "`plec`", "category": "wrong_type", "fix": "RADIO"},
            {"element": "Data urodzenia psa", "category": "missing_field", "fix": "dodać DATEPICKER"},
            {"element": "`rasa`", "category": "wrong_required", "fix": "wymagane"}],
        2: [{"element": "plec", "category": "wrong_options", "fix": "opcje Pies | Suka"},  # same group as wrong_type
            {"element": "data urodzenia", "category": "missing_field", "fix": "dodać datę"}],
        3: [{"element": "`numer_tatuazu`", "category": "missing_condition", "fix": "gdy Tatuaż"},
            {"element": "`rasa`", "category": "invented_field", "fix": "usunąć"}],  # other group than judge 1's rasa
    })
    r = poprawki.count_dir(d)
    assert r["counted"] == 2
    assert {(c["category"], tuple(c["judges"])) for c in r["corrections"]} == {("wrong_type", (1, 2)), ("missing_field", (1, 2))}
    assert len(r["singles"]) == 3


def test_same_needs_one_element_and_one_group():
    a = {"element": "`rasa`", "category": "wrong_type", "fix": ""}
    assert poprawki.same(a, {"element": "`plec`", "category": "wrong_type", "fix": ""}, ELS) == 0
    assert poprawki.same(a, {"element": "`rasa`", "category": "missing_condition", "fix": ""}, ELS) == 0
    assert poprawki.same({"element": "`pies`", "category": "missing_condition", "fix": ""},
                         {"element": "`numer_tatuazu`", "category": "wrong_condition", "fix": ""}, ELS) > 0  # section of it
    assert poprawki.same({"element": "Kopia zaświadczenia o szczepieniu", "category": "missing_field", "fix": ""},
                         {"element": "w załączeniu kopia zaświadczenia", "category": "missing_field", "fix": ""}, {}) > 0


def test_plant_is_importable_deterministic_and_keyed():
    clean, planted, key = poprawki.plant("8")
    assert check(planted) == [] and check(clean) == []
    assert key == poprawki.plant("8")[2]
    cats = [k["category"] for k in key]
    assert len(key) == 10 and cats.count("missing_field") == 3 and cats.count("wrong_type") == 2
    assert {"missing_condition", "wrong_condition", "invented_field", "wrong_options", "wrong_required"} <= set(cats)
    labels = {e["label"] for e in poprawki.elements(planted).values()}
    assert all(k["label"] not in labels for k in key if k["category"] == "missing_field")
    assert poprawki.INVENTED in labels


def test_small_form_gets_fewer_errors_and_no_info_text_targets():
    _, planted, key = poprawki.plant("3")
    assert 0 < len(key) < 10 and check(planted) == []
    info = {t for t, e in poprawki.elements(planted).items() if t.startswith("info_")}
    assert not any(k["element"] in info for k in key)


def test_calib_found_and_invented(tmp_path):
    clean_coi, planted_coi, key = poprawki.plant("7")
    found = [{"element": k["element"], "category": k["category"], "fix": k["fix"]} for k in key[:9]]
    extra = {"element": "`rasa`", "category": "wrong_type", "fix": "?"}  # counted on planted only
    known = {"element": "`plec`", "category": "wrong_required", "fix": "?"}  # counted on both
    clean = kit(tmp_path, "clean", poprawki.elements(clean_coi), {1: [known], 2: [known]})
    planted = kit(tmp_path, "planted", poprawki.elements(planted_coi),
                  {1: found + [extra, known], 2: found + [extra, known], 3: []})
    r = poprawki.calib(clean, planted, key)
    assert (r["found"], r["invented"], r["planted"]) == (9, 1, 10)
    assert r["pass"]
