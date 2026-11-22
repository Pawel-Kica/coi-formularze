"""Technical rules of a COI JSON ("Reguły techniczne" + the JSON Schema).

Usage: `problems = check(coi)`; an empty list means the JSON is importable.
Each problem is one readable line with a path, e.g. "tab 'Dane' / adres_zamieszkania: ...".

Tolerated reference conventions (both COI reference JSONs do these, so they pass):
- `repeatingSectionGrid` (the official schema misspells it `repetingSectionGrid`;
  the local schema copy below uses the spelling of the references),
- SELECT `defaultValue` as an array (`["Polska"]`, `[]`; the schema says string),
- `validation: {}` instead of `[]` on a few fields,
- an option without `techName`,
- FINISH page holding a SECTION with a TEXT_BLOCK (rules say only INFO_SECTION),
- fields without REQUIRED and registry fields that are not readOnly (not checked).
"""

import re

import jsonata
from jsonschema import Draft202012Validator

FIELD_TYPES = {
    "SECTION", "REPEATING_SECTION", "TEXT_FIELD", "TEXTAREA", "NUMBER", "DATEPICKER",
    "RADIO", "CHECKBOX", "SELECT", "FILE_UPLOADER", "TEXT_BLOCK", "CONSENT_SECTION", "INFO_SECTION",
}  # fmt: skip
SECTIONS = {"SECTION", "REPEATING_SECTION"}
CHOICE = {"RADIO", "CHECKBOX", "SELECT"}
NO_INPUT = {"TEXT_BLOCK", "INFO_SECTION"}  # allowed inside the FINISH page sections
VALIDATORS = {"REQUIRED", "EMAIL", "PESEL", "NIP", "REGON", "ZIP_CODE", "EPUAP", "ADE", "SERIAL_NUMBER_ID_CARD"}
STATE_TYPES = {"SHOW", "READ_ONLY"}
TECHNAME = re.compile(r"^[A-Za-z0-9_]+$")

# Local copy of importowanyPlikJsonSchemat.json with the fixes listed in the module docstring.
_STR = {"type": "string"}
_QUESTION = {
    "type": "object",
    "required": ["x", "y", "rows", "cols", "field"],
    "properties": {
        "x": {"type": "number"}, "y": {"type": "number"},
        "rows": {"type": "number"}, "cols": {"type": "number"},
        "field": {"$ref": "#/$defs/field"},
    },
}  # fmt: skip
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "FormDefinition",
    "type": "object",
    "required": ["title", "tabs", "flow"],
    "$defs": {
        "question": _QUESTION,
        "field": {
            "type": "object",
            "required": ["id", "type", "techName"],
            "properties": {
                "id": _STR, "type": _STR, "techName": _STR, "title": _STR, "description": _STR,
                "options": {"type": "array", "items": {"type": "object", "properties": {
                    "id": _STR, "name": _STR, "techName": _STR}}},
                "validation": {"anyOf": [
                    {"type": "array", "items": _STR},
                    {"type": "object", "maxProperties": 0},  # reference quirk: {}
                ]},
                "dictionaryName": _STR,
                "readOnly": {"type": "boolean"},
                "repeatingSectionGrid": {"type": "array", "items": {"$ref": "#/$defs/question"}},
                "defaultValue": {"anyOf": [_STR, {"type": "array", "items": _STR}]},
                "defaultValueType": _STR,
                "multiLevelTree": {"type": "boolean"},
                "searchEnabled": {"type": "boolean"},
                "consentSectionConfig": {"type": "object", "properties": {
                    "consents": {"type": "array", "items": {"type": "object", "properties": {
                        "description": _STR, "required": {"type": "boolean"}}}},
                    "files": {"type": "array"},
                }},
                "states": {"type": "array", "items": {"type": "object", "required": ["type", "query"],
                                                      "properties": {"type": _STR, "query": _STR}}},
                "questionOrder": {"type": "number"},
                "tooltipMessage": _STR,
            },
        },
    },
    "properties": {
        "title": _STR,
        "tabs": {"type": "array", "items": {
            "type": "object",
            "required": ["id", "title", "type", "questions"],
            "properties": {"id": _STR, "title": _STR, "type": _STR,
                           "questions": {"type": "array", "items": {"$ref": "#/$defs/question"}}},
        }},
        "flow": {"type": "object", "required": ["links"], "properties": {"links": {"type": "array", "items": {
            "type": "object", "required": ["id", "source", "target"],
            "properties": {"id": _STR, "source": _STR, "target": _STR}}}}},
    },
}  # fmt: skip

# JSONata atoms the COI generator understands (see "Warunki jsonaty" in the rules).
_S = r'"((?:[^"\\]|\\.)*)"'  # "option"
_T = r"'((?:[^'\\]|\\.)*)'"  # 'tab title'
_N = r"'([A-Za-z0-9_]+)'"  # 'techName'
_HEAD = rf"{_S}\s*in\s+tabs\[\s*{_T}\s*in\s+title\s*\]\s*\.\s*questions\[\s*{_N}\s*in\s+field\.techName\s*\]\s*\.\s*field"
ATOM_PLAIN = re.compile(
    _HEAD + rf"\s*\.\s*repeatingSectionGrid\[\s*{_N}\s*in\s+field\.techName\s*\]\s*\.\s*field\s*\.\s*__userAnswer"
)
ATOM_REPEATING = re.compile(
    _HEAD + rf"\s*\.\s*__userAnswer\s*(\[\s*\$repeatingSectionIndex\s*\])?\s*\[\s*{_N}\s*in\s+fieldTechName\s*\]"
    r"\s*\.\s*answers"
)
_GLUE = re.compile(r"^(?:\s|\(|\)|\band\b|\bor\b|\$not)*$")


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def _children(f: dict) -> list[dict]:
    return f.get("repeatingSectionGrid") or []


def check(coi: dict) -> list[str]:
    """All technical-rule violations of a COI JSON; empty list = OK."""
    errors = [f"schema: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
              for e in Draft202012Validator(SCHEMA).iter_errors(coi)]
    if errors:
        return errors[:50]  # structure is broken, deeper checks would only add noise

    title = coi["title"]
    if not title.strip():
        errors.append("title is empty")
    if len(title) > 255:
        errors.append(f"title longer than 255 characters ({len(title)})")

    tabs = coi["tabs"]
    if len(tabs) < 2:
        errors.append("form needs at least two tabs")
    types = [t["type"] for t in tabs]
    if types.count("FINISH") != 1:
        errors.append(f"form needs exactly one FINISH tab (has {types.count('FINISH')})")
    elif types[-1] != "FINISH":
        errors.append("FINISH tab must be the last one")
    for t in tabs:
        if t["type"] not in ("MAIN", "FINISH"):
            errors.append(f"tab '{t['title']}': type {t['type']!r} is neither MAIN nor FINISH")
    titles = [t["title"] for t in tabs]
    for dup in sorted({x for x in titles if titles.count(x) > 1}):
        errors.append(f"tab title '{dup}' is not unique (JSONata finds tabs by title)")

    ids: list[str] = []
    techs: list[str] = []
    for t in tabs:
        ids.append(t["id"])
        errors += _check_tab(t, ids, techs)
    for dup in sorted({x for x in techs if techs.count(x) > 1}):
        errors.append(f"techName '{dup}' is not unique")

    errors += _check_flow(coi, ids)
    for dup in sorted({x for x in ids if ids.count(x) > 1}):
        errors.append(f"id '{dup}' is not unique")
    errors += _check_queries(coi)
    return errors


def _check_tab(t: dict, ids: list[str], techs: list[str]) -> list[str]:
    errs = []
    finish = t["type"] == "FINISH"
    where = f"tab '{t['title']}'"
    n_fields = 0
    for q in t["questions"]:
        errs += _check_grid(q, where)
        s = q["field"]
        sw = f"{where} / {s.get('techName')}"
        if s["type"] == "INFO_SECTION":
            errs += _check_field(s, sw, ids, techs)
            n_fields += 1
            continue
        if s["type"] not in SECTIONS:
            errs.append(f"{sw}: {s['type']} placed directly on the page, must be inside a SECTION/REPEATING_SECTION")
        errs += _check_field(s, sw, ids, techs)
        if s["type"] in SECTIONS and not s.get("title", "").strip():
            errs.append(f"{sw}: section has no title")
        if finish and s["type"] == "REPEATING_SECTION":
            errs.append(f"{sw}: FINISH page cannot hold a repeating section")
        for c in _children(s):
            errs += _check_grid(c, sw)
            f = c["field"]
            fw = f"{sw} / {f.get('techName')}"
            n_fields += 1
            if f["type"] in SECTIONS:
                errs.append(f"{fw}: sections cannot be nested")
            if finish and f["type"] not in NO_INPUT:
                errs.append(f"{fw}: FINISH page cannot hold input fields ({f['type']})")
            errs += _check_field(f, fw, ids, techs)
            if _children(f):
                errs.append(f"{fw}: only sections can hold fields")
    if n_fields == 0:
        errs.append(f"{where}: page has no fields")
    return errs


def _check_grid(q: dict, where: str) -> list[str]:
    bad = [k for k in ("x", "y") if q[k] < 0] + [k for k in ("rows", "cols") if q[k] < 1]
    return [f"{where}: grid {k}={q[k]} out of range" for k in bad]


def _check_field(f: dict, where: str, ids: list[str], techs: list[str]) -> list[str]:
    errs = []
    ids.append(f["id"])
    tech = f["techName"]
    techs.append(tech)
    if not TECHNAME.match(tech):
        errs.append(f"{where}: techName {tech!r} must be ascii letters, digits, '_'")
    ftype = f["type"]
    if ftype not in FIELD_TYPES:
        errs.append(f"{where}: unknown field type {ftype!r}")
    if f.get("dictionaryName") and ftype not in CHOICE:
        errs.append(f"{where}: dictionary allowed only on SELECT/RADIO/CHECKBOX, not {ftype}")
    options = f.get("options") or []
    if ftype in CHOICE and not options and not f.get("dictionaryName"):
        errs.append(f"{where}: {ftype} needs options or a dictionary")
    names = [o.get("name") for o in options]
    if any(not n for n in names):
        errs.append(f"{where}: option without a name")
    for dup in sorted({n for n in names if n and names.count(n) > 1}):
        errs.append(f"{where}: option '{dup}' repeated")
    validation = f.get("validation")
    if isinstance(validation, list):
        for v in validation:
            if v not in VALIDATORS:
                errs.append(f"{where}: unknown validator {v!r}")
    if ftype == "CONSENT_SECTION":
        consents = (f.get("consentSectionConfig") or {}).get("consents") or []
        if not consents:
            errs.append(f"{where}: CONSENT_SECTION needs at least one consent")
        for c in consents:
            if not (c.get("description") or "").strip() or not isinstance(c.get("required"), bool):
                errs.append(f"{where}: each consent needs a description and a boolean 'required'")
    if ftype == "INFO_SECTION" and not (f.get("description") or "").strip():
        errs.append(f"{where}: INFO_SECTION needs a description")
    for s in f.get("states") or []:
        if s["type"] not in STATE_TYPES or (ftype in SECTIONS and s["type"] != "SHOW"):
            errs.append(f"{where}: state type {s['type']!r} not allowed on {ftype}")
    return errs


def _check_flow(coi: dict, ids: list[str]) -> list[str]:
    tabs = coi["tabs"]
    tab_ids = [t["id"] for t in tabs]
    errs, nxt = [], {i: set() for i in tab_ids}
    for link in coi["flow"]["links"]:
        ids.append(link["id"])
        if link["source"] not in nxt or link["target"] not in nxt:
            errs.append(f"flow link {link['id']}: source/target is not a tab id")
            continue
        nxt[link["source"]].add(link["target"])
    if not tab_ids:
        return errs
    seen, todo = {tab_ids[0]}, [tab_ids[0]]
    while todo:
        for n in nxt[todo.pop()]:
            if n not in seen:
                seen.add(n)
                todo.append(n)
    for t in tabs:
        if t["id"] not in seen:
            errs.append(f"tab '{t['title']}' is not reachable from the first tab in flow.links")
        if t["type"] == "MAIN" and not nxt[t["id"]]:
            errs.append(f"tab '{t['title']}' has no outgoing flow link")
    return errs


# --- JSONata -------------------------------------------------------------------


def _index(coi: dict) -> dict:
    """{tab title: {section techName: (section field, {field techName: field})}}"""
    out = {}
    for t in coi["tabs"]:
        secs = out.setdefault(t["title"], {})
        for q in t["questions"]:
            s = q["field"]
            secs[s["techName"]] = (s, {c["field"]["techName"]: c["field"] for c in _children(s)})
    return out


def _fake_doc(coi: dict) -> dict:
    """Minimal answers object shaped like the generator's runtime data."""
    return {"tabs": [
        {"title": t["title"], "questions": [
            {"field": {"techName": q["field"]["techName"], "__userAnswer": [],
                       "repeatingSectionGrid": [{"field": {"techName": c["field"]["techName"], "__userAnswer": []}}
                                                for c in _children(q["field"])]}}
            for q in t["questions"]]}
        for t in coi["tabs"]]}  # fmt: skip


def _set_answer(doc: dict, atom, value: list[str]) -> None:
    _opt, tab, sec, *rest = atom
    for t in doc["tabs"]:
        if t["title"] != tab:
            continue
        for q in t["questions"]:
            f = q["field"]
            if f["techName"] != sec:
                continue
            if atom.repeating:
                f["__userAnswer"] = [[{"fieldTechName": rest[-1], "answers": value}]] if value else []
            else:
                for c in f["repeatingSectionGrid"]:
                    if c["field"]["techName"] == rest[-1]:
                        c["field"]["__userAnswer"] = value


class Atom(tuple):
    """(option, tab title, section techName, field techName) of one JSONata condition,
    plus the form used (repeating, same_row) and its text."""

    repeating = False
    same_row = False
    start = 0
    text = ""


def query_atoms(query: str) -> tuple[list[Atom], str]:
    """Conditions found in a JSONata query (in text order) + what is left without them."""
    found = []
    for rx, repeating in ((ATOM_PLAIN, False), (ATOM_REPEATING, True)):
        for m in rx.finditer(query):
            g = m.groups()
            a = Atom((_unescape(g[0]), _unescape(g[1]), g[2], g[-1]))
            a.repeating = repeating
            a.same_row = repeating and bool(g[3])
            a.start, a.text = m.start(), m.group(0)
            found.append(a)
    rest = ATOM_REPEATING.sub(" ", ATOM_PLAIN.sub(" ", query))
    return sorted(found, key=lambda a: a.start), rest


def _check_queries(coi: dict) -> list[str]:
    errs = []
    index = _index(coi)
    doc = _fake_doc(coi)
    for t in coi["tabs"]:
        for q in t["questions"]:
            s = q["field"]
            items = [(s, s)] + [(c["field"], s) for c in _children(s)]
            for f, owner in items:
                for st in f.get("states") or []:
                    where = f"tab '{t['title']}' / {f['techName']}: query"
                    errs += _check_query(st["query"], where, index, doc, f, owner)
    return errs


def _check_query(query: str, where: str, index: dict, doc: dict, target: dict, owner: dict) -> list[str]:
    try:
        expr = jsonata.Jsonata(query)
    except Exception as e:  # noqa: BLE001 - any parse error is a finding
        return [f"{where} does not parse: {e}"]
    atoms, rest = query_atoms(query)
    errs = []
    if not atoms:
        errs.append(f"{where} has no recognised `\"option\" in tabs[...]...__userAnswer` condition")
    if not _GLUE.match(rest):
        errs.append(f"{where} has unsupported parts: {rest.strip()[:80]!r}")
    for a in atoms:
        opt, tab, sec, fld = a
        if tab not in index:
            errs.append(f"{where} refers to unknown tab '{tab}'")
            continue
        if sec not in index[tab]:
            errs.append(f"{where} refers to unknown section '{sec}' on tab '{tab}'")
            continue
        section, fields = index[tab][sec]
        if a.repeating != (section["type"] == "REPEATING_SECTION"):
            kind = "repeating" if a.repeating else "plain"
            errs.append(f"{where} uses the {kind}-section form for {section['type']} '{sec}'")
        if a.same_row and owner is not section:
            errs.append(f"{where} uses $repeatingSectionIndex outside repeating section '{sec}'")
        if fld not in fields:
            errs.append(f"{where} refers to unknown field '{fld}' in section '{sec}'")
            continue
        trig = fields[fld]
        if trig is target:
            errs.append(f"{where} depends on itself")
        if trig["type"] not in CHOICE:
            errs.append(f"{where} depends on '{fld}' which is {trig['type']}, not a choice field")
        elif not trig.get("dictionaryName") and opt not in [o.get("name") for o in trig.get("options") or []]:
            errs.append(f"{where} uses option '{opt}' that '{fld}' does not have")
    if errs:
        return errs
    # Evaluate on a fake answers object: each atom must be true once its option is
    # chosen, and the whole query must give a boolean.
    try:
        expr.assign("repeatingSectionIndex", 0)
        for a in atoms:
            _set_answer(doc, a, [a[0]])
            one = jsonata.Jsonata(a.text)
            one.assign("repeatingSectionIndex", 0)
            if one.evaluate(doc) is not True:
                errs.append(f"{where}: condition on '{a[3]}' = '{a[0]}' is not true when that option is chosen")
            whole = expr.evaluate(doc)
            if whole is not None and not isinstance(whole, bool):
                errs.append(f"{where} evaluates to {type(whole).__name__}, expected a boolean")
            _set_answer(doc, a, [])
    except Exception as e:  # noqa: BLE001
        errs.append(f"{where} fails on a sample answer: {e}")
    return errs
