"""Building blocks of a COI JSON: ids, techNames, fields, sections, tabs, JSONata.

Every object mirrors the key set and key order of the reference JSONs, so the
output looks like what COI's own generator exports.
"""

import itertools
import uuid

from app.coi.builder.texts import EMPTY_HTML
from app.core.text import slugify

NAMESPACE = uuid.UUID("6f1d3c2a-8b7e-5c4d-9a0b-1e2f3a4b5c6d")


class Ctx:
    """Per-build state: unique techNames and deterministic UUIDs (uuid5 of title + key)."""

    def __init__(self, title: str):
        self.title = title
        self.used: set[str] = set()

    def tech(self, label_or_slug: str) -> str:
        """Unique techName for a label: slug, then `_2`, `_3`... on collision."""
        base = slugify(label_or_slug)
        name, n = base, 2
        while name in self.used:
            name, n = f"{base}_{n}", n + 1
        self.used.add(name)
        return name

    def uid(self, key: str) -> str:
        return str(uuid.uuid5(NAMESPACE, f"{self.title}\x1f{key}"))


def option(name: str) -> dict:
    return {"id": name, "name": name, "techName": slugify(name, "opcja")}


def field(
    ctx: Ctx,
    type_: str,
    title: str,
    tech: str,
    *,
    description: str = "",
    options: list[str] = (),
    validation: list[str] = (),
    read_only: bool = False,
    query: str | None = None,
    extra: dict | None = None,
) -> dict:
    """One COI field. `tech` must already be unique (from `ctx.tech`); `extra` holds
    type-specific keys (defaultValue, dictionaryName, consentSectionConfig...)."""
    out = {
        "id": ctx.uid(f"field/{tech}"),
        "type": type_,
        "techName": tech,
        "title": title,
        "description": description,
        "options": [option(o) for o in options],
        "validation": list(validation),
        "readOnly": read_only,
        "repeatingSectionGrid": [],
    }
    out.update(extra or {})
    out["states"] = states(query)
    out["questionOrder"] = 0  # set by section()/tab()
    out["tooltipMessage"] = ""
    return out


def section(ctx: Ctx, title: str, tech: str, fields: list[dict], *, description: str = "",
            query: str | None = None, repeating: bool = False) -> dict:
    """A SECTION / REPEATING_SECTION holding `fields` stacked in one column."""
    out = field(ctx, "REPEATING_SECTION" if repeating else "SECTION", title, tech,
                description=description or EMPTY_HTML, query=query)
    grid = []
    for i, f in enumerate(fields):
        f["questionOrder"] = i
        grid.append({"x": 0, "y": i, "rows": 1, "cols": 3, "field": f})
    out["repeatingSectionGrid"] = grid
    return out


def tab(ctx: Ctx, title: str, sections: list[dict], *, finish: bool = False, key: str = "") -> dict:
    """A page. Sections stack vertically; a section spans its fields + 1 rows (the title)."""
    questions, y = [], 0
    for s in sections:
        rows = len(s["repeatingSectionGrid"]) + 1
        s["questionOrder"] = y
        questions.append({"x": 0, "y": y, "rows": rows, "cols": 3, "field": s})
        y += rows
    return {
        "id": ctx.uid(f"tab/{key or title}"),
        "title": title,
        "type": "FINISH" if finish else "MAIN",
        "questions": questions,
    }


def flow(ctx: Ctx, tabs: list[dict]) -> dict:
    """Linear page flow: each tab links to the next one."""
    return {
        "links": [
            {"id": ctx.uid(f"link/{i}"), "source": a["id"], "target": b["id"]}
            for i, (a, b) in enumerate(itertools.pairwise(tabs))
        ]
    }


# --- JSONata -----------------------------------------------------------------
# A condition is a JSONata string, or None meaning "always shown".


def states(query: str | None) -> list[dict]:
    return [{"type": "SHOW", "query": query}] if query else []


def _q(text: str, quote: str) -> str:
    return text.replace("\\", "\\\\").replace(quote, "\\" + quote)


def answer_path(tab_title: str, section_tech: str, field_tech: str) -> str:
    """Path to the answer of a field inside a plain SECTION."""
    return (
        f"tabs['{_q(tab_title, chr(39))}' in title].questions['{section_tech}' in field.techName]"
        f".field.repeatingSectionGrid['{field_tech}' in field.techName].field.__userAnswer"
    )


def repeating_answer_path(tab_title: str, section_tech: str, field_tech: str, same_row: bool) -> str:
    """Path to the answer of a field inside a REPEATING_SECTION; `same_row` when the
    dependent element sits in the same repeating section (uses $repeatingSectionIndex)."""
    row = "[$repeatingSectionIndex]" if same_row else ""
    return (
        f"tabs['{_q(tab_title, chr(39))}' in title].questions['{section_tech}' in field.techName]"
        f".field.__userAnswer{row}['{field_tech}' in fieldTechName].answers"
    )


def chosen(option_name: str, path: str) -> str:
    """Atom: `("Option" in <path>)`."""
    return f'("{_q(option_name, chr(34))}" in {path})'


def negate(cond: str) -> str:
    return "$not" + _wrap(cond)


def _is_wrapped(s: str) -> bool:
    """True when the whole string is one parenthesised group (string literals skipped)."""
    if not s.startswith("("):
        return False
    depth, i, quote = 0, 0, ""
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0 and i != len(s) - 1:
                return False
        i += 1
    return True


def _wrap(s: str) -> str:
    return s if _is_wrapped(s) else f"({s})"


def any_of(*conds: str | None) -> str | None:
    if not conds or any(c is None for c in conds):
        return None
    return conds[0] if len(conds) == 1 else " or ".join(_wrap(c) for c in conds)


def all_of(*conds: str | None) -> str | None:
    real = [c for c in conds if c is not None]
    if not real:
        return None
    return real[0] if len(real) == 1 else " and ".join(_wrap(c) for c in real)
