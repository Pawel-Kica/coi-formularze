"""Render the README diagrams: docs/img/<name>.html -> docs/img/<name>.png with headless Chrome.

    python3 docs/img/render.py            # every *.html here
    python3 docs/img/render.py prosty     # one diagram

Numbers come from liczby.json next to this file: each {{KEY}} in an HTML source is replaced by its value.
A key without a value stays visible on the PNG as a yellow {{KEY}} and is listed at the end, so a diagram
with missing numbers never looks finished. The page size is the source's <meta name="render" content="WxH">.
Stdlib only; needs Google Chrome (CHROME=<path> to override).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SCALE = "1.5"  # sharp on retina screens, PNGs stay under ~1 MB
TODO = '<span style="background:#ffe14d;color:#000;padding:0 4px;border-radius:3px">{{%s}}</span>'


def fill(html: str, numbers: dict[str, str], missing: set[str]) -> str:
    def value(m: re.Match) -> str:
        key = m.group(1)
        if numbers.get(key):
            return numbers[key]
        missing.add(key)
        return TODO % key

    return re.sub(r"\{\{([A-ZĄĆĘŁŃÓŚŹŻ_]+)\}\}", value, html)


def render(src: Path, numbers: dict[str, str], missing: set[str]) -> Path:
    html = src.read_text()
    size = re.search(r'<meta name="render" content="(\d+)x(\d+)">', html)
    if not size:
        sys.exit(f"{src.name}: add <meta name=\"render\" content=\"WIDTHxHEIGHT\">")
    out = src.with_suffix(".png")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / src.name
        page.write_text(fill(html, numbers, missing))
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--force-device-scale-factor={SCALE}",
             f"--window-size={size[1]},{size[2]}", f"--screenshot={out}", page.as_uri()],
            check=True, capture_output=True, timeout=60,
        )
    return out


def main() -> None:
    numbers = {k: str(v) for k, v in json.loads((HERE / "liczby.json").read_text()).items() if not k.startswith("_")}
    names = sys.argv[1:] or sorted(p.stem for p in HERE.glob("*.html"))
    missing: set[str] = set()
    for name in names:
        print(render(HERE / f"{name}.html", numbers, missing).relative_to(HERE.parent.parent))
    if missing:
        print("bez wartości w liczby.json (żółte na PNG):", ", ".join(sorted(missing)))


if __name__ == "__main__":
    main()
