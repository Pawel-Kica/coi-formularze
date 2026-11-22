"""README.md is the jury's documentation: every REST route is described there and every in-page link lands."""

import re
import unicodedata
from pathlib import Path

from app.api import app

README = (Path(__file__).parent.parent / "README.md").read_text()


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped (letters with accents kept), spaces to '-'."""
    text = re.sub(r"[`*]", "", heading).strip().lower()
    return "".join(c if c.isalnum() or c in "-_" else "-" if c == " " else "" for c in unicodedata.normalize("NFC", text))


def test_every_api_route_is_in_the_readme():
    paths = {p for p in app.openapi()["paths"] if p.startswith("/api")}  # the routes /docs shows (dev-only /review is not)
    assert len(paths) > 15
    missing = sorted(p for p in paths if p.replace("{item_id}", "{id}").replace("{job_id}", "{id}") not in README)
    assert not missing, f"describe these routes in README.md, section REST API: {missing}"


def test_every_anchor_link_lands_on_a_heading():
    anchors = {slug(h) for h in re.findall(r"^#{1,6} (.+)$", README, re.M)}
    links = set(re.findall(r"\]\(#([^)]+)\)", README))
    assert links and not links - anchors, f"links without a heading: {sorted(links - anchors)}"
