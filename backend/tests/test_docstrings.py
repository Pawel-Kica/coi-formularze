"""Docstrings stay short: a function or class says what it does in at most 3 lines, the why goes in comments next to
the code it explains. A module docstring is that module's contract and rule list, so it gets more room, not unlimited."""

import ast
from pathlib import Path

MAX_LINES = 3
MAX_MODULE_LINES = 40
APP = Path(__file__).resolve().parents[1] / "app"


def _long_docstrings() -> list[str]:
    long = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Module):
                limit, name = MAX_MODULE_LINES, "<module>"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                limit, name = MAX_LINES, node.name
            else:
                continue
            doc = ast.get_docstring(node)
            if doc and len(doc.splitlines()) > limit:
                long.append(f"{path.relative_to(APP.parent)}:{getattr(node, 'lineno', 1)} {name}: "
                            f"{len(doc.splitlines())} lines, max {limit}")
    return long


def test_docstrings_are_short():
    long = _long_docstrings()
    assert not long, "docstrings too long:\n" + "\n".join(long)
