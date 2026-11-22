"""Rules problems for the clerk: each `app.rules.check` line -> Polish text + the element it belongs to.

    from app.problems import explain
    explain(coi, check(coi))  # [{"id": "<element id> | None", "message": "Sekcja nie ma tytułu.", "detail": "<rules line>"}]

The rules stay the only judge (`app.rules`); this module only reads their `where` prefix
("tab 'T' / section / field: ...") to find the element id, so the editor can show the problem
next to it, and translates the problems an edit can cause. Anything else keeps its original text.
"""

import re

_TECH = re.compile(r"(?: / ([A-Za-z0-9_]+))?(?: / ([A-Za-z0-9_]+))?")

# (pattern on the rules line, Polish text; {0}, {1} = the pattern's groups). First match wins.
_POLISH = [
    (r"section has no title", "Sekcja nie ma tytułu."),
    (r"(?:RADIO|CHECKBOX|SELECT) needs options or a dictionary", "Pole wyboru nie ma opcji. Dodaj co najmniej jedną."),
    (r"option without a name", "Jedna z opcji nie ma nazwy."),
    (r"option '(.*)' repeated", "Opcja „{0}” występuje dwa razy."),
    (r"page has no fields", "Strona nie ma żadnego pola."),
    (r"^tab title '(.*)' is not unique", "Dwie strony mają tytuł „{0}”. Tytuły stron muszą się różnić."),
    (r"^title is empty", "E-usługa nie ma tytułu."),
    (r"^title longer than 255 characters \((\d+)\)", "Tytuł e-usługi ma {0} znaków, a limit to 255."),
    (r"query refers to unknown tab '(.*)'", "Warunek wskazuje stronę „{0}”, której nie ma."),
    (r"query refers to unknown (?:section|field)", "Warunek zależy od pola, którego już nie ma."),  # the clerk removed a field either way
    (r"query uses option '(.*)' that '.*' does not have", "Warunek używa opcji „{0}”, której to pole już nie ma."),
    (r"query depends on '.*' which is \w+, not a choice field", "Warunek zależy od pola, które nie jest już polem wyboru."),
    (r"query depends on itself", "Warunek zależy od tego samego pola."),
    (r"\$repeatingSectionIndex outside", "Warunek odwołuje się do wiersza innej sekcji powtarzalnej."),
    (r"uses the (?:repeating|plain)-section form", "Warunek nie pasuje do rodzaju sekcji (zwykła albo powtarzalna)."),
    (r"query (?:does not parse|has unsupported parts|has no recognised)|is not true when|evaluates to|fails on a sample",
     "Warunek ma niepoprawną postać. Ustaw go od nowa albo usuń."),
    (r"FINISH page cannot hold input fields", "Na stronie końcowej nie może być pól do wypełnienia."),
    (r"^techName '(.*)' is not unique", "Nazwa techniczna „{0}” powtarza się."),
    (r"dictionary allowed only on", "Słownik można przypisać tylko do pola wyboru."),
    (r"CONSENT_SECTION needs at least one consent", "Sekcja zgód nie ma żadnej zgody."),
    (r"each consent needs a description", "Każda zgoda musi mieć treść."),
    (r"unknown field type '?([^']*)'?", "Nieznany typ pola {0}."),
    (r"unknown validator '(.*)'", "Nieznany walidator {0}."),
    (r"sections cannot be nested", "Sekcji nie można zagnieżdżać."),
    (r"placed directly on the page", "Pole leży poza sekcją."),
]
_POLISH = [(re.compile(p), text) for p, text in _POLISH]


def explain(coi: dict, problems: list[str]) -> list[dict]:
    """Rules lines -> [{"id", "message", "detail"}]; `id` is None when no single element is at fault."""
    return [{"id": locate(coi, line), "message": polish(line), "detail": line} for line in problems]


def polish(line: str) -> str:
    for rx, text in _POLISH:
        if m := rx.search(line):
            return text.format(*m.groups())
    return f"Problem techniczny: {line}"


def locate(coi: dict, line: str) -> str | None:
    """Id of the tab, section or field a rules line names, or None."""
    tabs = [t for t in coi.get("tabs") or [] if isinstance(t, dict)]
    if m := re.match(r"tab title '(.*)' is not unique", line):
        same = [t for t in tabs if t.get("title") == m.group(1)]
        return same[-1].get("id") if same else None
    if m := re.match(r"techName '(.*)' is not unique", line):
        same = [f for t in tabs for f in _elements(t) if f.get("techName") == m.group(1)]
        return same[-1].get("id") if same else None
    if m := re.match(r"id '(.*)' is not unique", line):
        return m.group(1)
    # "tab 'T'..." lines: longest title first, so "Dane" does not catch "Dane 'x'"
    for t in sorted(tabs, key=lambda t: -len(str(t.get("title")))):
        prefix = f"tab '{t.get('title')}'"
        if not line.startswith(prefix):
            continue
        section, field = _TECH.match(line, len(prefix)).groups()
        tech = field or section
        if tech:
            for f in _elements(t):
                if f.get("techName") == tech:
                    return f.get("id")
        return t.get("id")
    return None


def _elements(tab: dict):
    """Sections and their fields of one tab, in order."""
    for q in tab.get("questions") or []:
        s = q.get("field") or {}
        yield s
        for c in s.get("repeatingSectionGrid") or []:
            yield c.get("field") or {}
