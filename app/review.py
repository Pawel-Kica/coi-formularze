"""Dev-only review page "Przegląd": a finished black-box run, judge verdicts, the original next to our e-service.

Off unless REVIEW_RUNS_DIR points at a folder of runs (e.g. bench/runs): unset by default and in
Docker, so for the jury /review and /api/review/* answer 404 and stay out of /docs. Never calls a model.

A run is a `bench/run_dir.py` output folder that has `source.txt` (the source folder it ran over):
`<file>.coi.json`, `.plan.json`, `.txt`, `summary.json`, and the judge's `judge/verdicts.jsonl`
(`bench/judge_report.py`, last line per file wins). The only write: `judge/human.jsonl`, one line
per "zgadzam się / nie zgadzam się" save. Run and file names are looked up in directory listings
(runs dir, the run's source folder), never joined from client input, so nothing outside is served.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.security import current_user

REVIEW_HTML = Path(__file__).parent / "static" / "review.html"
SKIP_NAMES = {"README.md", "source.md", ".DS_Store"}  # same as bench/run_dir.py
MIME = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg", ".png": "image/png"}

User = Annotated[str, Depends(current_user)]


def _not_found(detail: str = "Not Found") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail)


def _enabled() -> None:
    """Router dependency, runs before the login check: with REVIEW_RUNS_DIR unset everything here is a plain 404."""
    if not settings.review_runs_dir:
        raise _not_found()


router = APIRouter(include_in_schema=False, dependencies=[Depends(_enabled)])


def _root() -> Path:
    _enabled()
    root = settings.review_runs_dir.expanduser().resolve()
    if not root.is_dir():
        raise _not_found("REVIEW_RUNS_DIR nie jest folderem.")
    return root


def _runs() -> dict[str, tuple[Path, Path]]:
    """Run name -> (run dir, source folder), newest run first."""
    root = _root()
    runs = []
    for d in root.iterdir():
        if d.is_dir() and (d / "source.txt").is_file() and d.resolve().is_relative_to(root):
            runs.append((d.stat().st_mtime, d, Path((d / "source.txt").read_text().strip()).expanduser().resolve()))
    return {d.name: (d, src) for _, d, src in sorted(runs, reverse=True)}


def _run(run: str) -> tuple[Path, Path]:
    if run not in (runs := _runs()):
        raise _not_found("Nie ma takiego przebiegu.")
    return runs[run]


def _sources(src: Path) -> dict[str, Path]:
    """File name -> path for the files `run_dir.py` ran over; symlinks out of the folder are left out."""
    if not src.is_dir():
        return {}
    return {p.name: p for p in sorted(src.iterdir())
            if p.is_file() and p.name not in SKIP_NAMES and p.resolve().is_relative_to(src)}


def _jsonl(path: Path) -> dict[str, dict]:
    """Last line per file of a JSONL file; broken lines are skipped."""
    out = {}
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
            out[row["file"]] = row
        except ValueError, KeyError, TypeError:
            continue
    return out


def _plan_fields(path: Path) -> int | None:
    """Field count of a saved plan, as `run_dir.py` counts it (used while summary.json is not written yet)."""
    if not path.exists():
        return None
    plan = json.loads(path.read_text())
    return sum(len(s["fields"]) for p in plan["pages"] for s in p["sections"])


def _order(row: dict) -> tuple:
    """źle first, then dobrze (each lowest coverage first), then files without a verdict, then not run yet."""
    v = row["verdict"]
    if v:
        cov = v["covered"] / v["paper_elements"] if v.get("paper_elements") and "covered" in v else 0.0
        return (0 if v.get("verdict") == "źle" else 1, cov, row["file"])
    return (2 if row["ok"] is not None else 3, 0.0, row["file"])


@router.get("/review")
def review_page() -> FileResponse:
    return FileResponse(REVIEW_HTML)


@router.get("/api/review/runs")
def runs(_user: User) -> list[dict]:
    return [{"name": name, "source": src.name} for name, (_, src) in _runs().items()]


@router.get("/api/review/runs/{run}")
def run_files(run: str, _user: User) -> dict:
    """Every source file with its run result (ok, error, seconds, fields), judge verdict and our last review."""
    run_dir, src = _run(run)
    summary = run_dir / "summary.json"
    rows = {r["file"]: r for r in json.loads(summary.read_text())["rows"]} if summary.exists() else {}
    verdicts = _jsonl(run_dir / "judge" / "verdicts.jsonl")
    human = _jsonl(run_dir / "judge" / "human.jsonl")
    files = []
    for name in _sources(src):
        r = rows.get(name, {})
        has_coi = (run_dir / f"{name}.coi.json").exists()
        files.append({
            "file": name,
            "ok": r.get("ok", True if has_coi else None),  # None = not run yet (or still running)
            "error": r.get("error"),
            "s": r.get("s"),
            "fields": r.get("fields", _plan_fields(run_dir / f"{name}.plan.json")),
            "has_coi": has_coi,
            "has_text": (run_dir / f"{name}.txt").exists(),
            "verdict": verdicts.get(name),
            "human": human.get(name),
        })
    return {"run": run, "source": src.name, "files": sorted(files, key=_order)}


@router.get("/api/review/runs/{run}/files/{name}/{kind}")
def run_file(run: str, name: str, kind: Literal["source", "coi", "text"], _user: User) -> FileResponse:
    """The original (`source`), our COI JSON (`coi`) or the extracted text the model saw (`text`)."""
    run_dir, src = _run(run)
    if name not in (sources := _sources(src)):
        raise _not_found("Nie ma takiego pliku.")
    path, media = {
        "source": (sources[name], MIME.get(sources[name].suffix.lower(), "application/octet-stream")),
        "coi": (run_dir / f"{name}.coi.json", "application/json"),
        "text": (run_dir / f"{name}.txt", "text/plain; charset=utf-8"),
    }[kind]
    if not path.is_file():
        raise _not_found("Brak tego pliku w przebiegu.")
    return FileResponse(path, media_type=media, headers={"X-Content-Type-Options": "nosniff"})


class HumanReview(BaseModel):
    file: str
    agree: bool  # with the judge's verdict
    note: str = Field("", max_length=4000)


@router.post("/api/review/runs/{run}/human")
def save_human(run: str, review: HumanReview, _user: User) -> dict:
    """Appends one line to `<run>/judge/human.jsonl`; the judge's verdict at save time goes with it."""
    run_dir, src = _run(run)
    if review.file not in _sources(src):
        raise _not_found("Nie ma takiego pliku.")
    verdict = _jsonl(run_dir / "judge" / "verdicts.jsonl").get(review.file, {}).get("verdict")
    line = review.model_dump() | {"note": review.note.strip(), "verdict": verdict,
                                  "time": datetime.now().astimezone().isoformat(timespec="seconds")}
    (run_dir / "judge").mkdir(exist_ok=True)
    with open(run_dir / "judge" / "human.jsonl", "a") as f:  # one write per line: two reviewers don't interleave
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line
