"""Put a finished conversion into the upload page history (HISTORY_DB), so it shows up at http://localhost:8010.

Writes the SQLite file directly, no server or model needed:
    uv run python -m bench.history_add <original file> <coi.json> [--evidence ev.json] [--name "label"]
The record gets status "done" and created = now; the page lists it after a reload.
"""

import argparse
import json
import mimetypes
from pathlib import Path

from app import history
from app.config import settings
from app.upload import MIME_BY_EXTENSION


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("original", type=Path, help="the original form (PDF, DOC, DOCX, ODS, XLSX, XLS, JPG, PNG)")
    parser.add_argument("coi", type=Path, help="the COI JSON made from it")
    parser.add_argument("--evidence", type=Path, help="evidence map {element id: {source, quote, ...}}")
    parser.add_argument("--name", help="name shown in the history (default: the original's name; its extension is kept)")
    args = parser.parse_args()

    original = args.original
    mime = MIME_BY_EXTENSION.get(original.suffix.lower()) or mimetypes.guess_type(original.name)[0] or "application/octet-stream"
    record = {
        "status": "done",
        "error": "",
        "result": json.loads(args.coi.read_text()),
        "evidence": json.loads(args.evidence.read_text()) if args.evidence else None,
        "problems": [],
    }
    name = args.name or original.name
    if not name.lower().endswith(original.suffix.lower()):  # the page picks the original's viewer by extension
        name += original.suffix
    item_id = history.add(name, mime, original.read_bytes(), record)
    print(f"history id {item_id} -> {settings.history_db}")


if __name__ == "__main__":
    main()
