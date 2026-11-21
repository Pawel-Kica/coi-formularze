"""bench/blind.py: blind A/B kits from two finished runs (both layouts), verdicts, the unblinded report.
No model and no OCR: the runs are fake folders, the originals tiny PDFs."""

import json

import pytest

from bench import blind, run_all
from tests.test_vision import a4_pdf


def coi(field: str) -> dict:
    """A COI JSON with one field; its title tells the runs apart in X.md / Y.md."""
    section = {"type": "SECTION", "title": "Sekcja", "repeatingSectionGrid": [{"field": {"type": "TEXT_FIELD", "title": field}}]}
    return {"title": "Wniosek", "tabs": [{"title": "Strona", "questions": [{"field": section}]}]}


def put(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))


@pytest.fixture
def all_runs(tmp_path, monkeypatch):
    """Two `bench.run_all` runs over data/coi with forms 1-3: form 1 differs, form 2 is the same in
    both, form 3 exists only in the old run."""
    data = tmp_path / "data"
    for form in "123":
        put(data / f"{form}. Wniosek" / "konfiguracja_form_x.json", "{}")  # the COI reference is not an input
        (data / f"{form}. Wniosek" / "wniosek.pdf").write_bytes(a4_pdf(2))
    monkeypatch.setattr(run_all, "COI", data)
    monkeypatch.setattr(blind, "extract", lambda data, name, ocr_engine: type("Doc", (), {"text": f"tekst {name}"}))
    old, new = tmp_path / "old-run", tmp_path / "new-run"
    put(old / "coi" / "1" / "pdf.coi.json", coi("Pole stare"))
    put(new / "coi" / "1" / "pdf.coi.json", coi("Pole nowe"))
    put(old / "coi" / "2" / "pdf.coi.json", coi("Pole wspólne"))
    put(new / "coi" / "2" / "pdf.coi.json", coi("Pole wspólne"))
    put(old / "coi" / "3" / "pdf.coi.json", coi("Tylko stare"))
    return old, new


@pytest.fixture
def dir_runs(tmp_path):
    """Two `bench.run_dir` runs over src/: a.pdf differs (also in the extracted text), b.pdf crashed
    in the new run, c.pdf is not done in the new run."""
    src = tmp_path / "src"
    src.mkdir()
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (src / name).write_bytes(a4_pdf(1))
    old, new = tmp_path / "old-run", tmp_path / "new-run"
    for run, field in ((old, "Pole stare"), (new, "Pole nowe")):
        put(run / "source.txt", f"{src}\n")
        put(run / "a.pdf.coi.json", coi(field))
        put(run / "a.pdf.txt", f"tekst {field}")
    put(old / "b.pdf.coi.json", coi("Pole b"))
    put(old / "b.pdf.txt", "tekst b")
    put(old / "c.pdf.coi.json", coi("Pole c"))
    put(new / "summary.json", {"rows": [{"file": "a.pdf", "ok": True},
                                        {"file": "b.pdf", "ok": False, "error": "ReadTimeout: model"}]})
    return old, new


def kit_text(out) -> str:
    """Everything a judge can read: kit folder names and every text file in them."""
    return "\n".join([p.name for p in out.iterdir()] + [f.read_text() for f in out.glob("*/*") if f.suffix != ".png"])


def test_make_run_all_layout_hides_the_key(all_runs, tmp_path):
    old, new = all_runs
    out = tmp_path / "blind"
    assert blind.make(old, new, out, seed=1) == ["1-pdf", "2-pdf"]  # form 3 is not in both runs
    kit = out / "1-pdf"
    assert sorted(p.name for p in kit.iterdir()) == ["X.md", "Y.md", "extracted.txt", "info.md", "page-01.png", "page-02.png"]
    assert (kit / "extracted.txt").read_text() == "tekst wniosek.pdf"  # run_all saves no text: extracted now
    key = json.loads((out / "key.json").read_text())
    assert (key["a"], key["b"], key["seed"]) == (str(old), str(new), 1)
    sides = key["files"]["1-pdf"]
    assert {sides["X"], sides["Y"]} == {"a", "b"}
    field = {"a": "Pole stare", "b": "Pole nowe"}
    assert field[sides["X"]] in (kit / "X.md").read_text() and field[sides["Y"]] not in (kit / "X.md").read_text()
    assert field[sides["Y"]] in (kit / "Y.md").read_text()
    assert "old-run" not in kit_text(out) and "new-run" not in kit_text(out)  # only key.json names the runs
    assert key["files"]["2-pdf"].get("identical") is True and "identical" not in sides


def test_make_sides_are_seeded_and_stable(all_runs, tmp_path):
    old, new = all_runs
    for form in "456789":
        (run_all.COI / f"{form}. Wniosek").mkdir()
        (run_all.COI / f"{form}. Wniosek" / "wniosek.pdf").write_bytes(a4_pdf(1))
        put(old / "coi" / form / "pdf.coi.json", coi("Pole stare"))
        put(new / "coi" / form / "pdf.coi.json", coi("Pole nowe"))

    def xs(out, **kw):
        blind.make(old, new, out, **kw)
        return {i: f["X"] for i, f in json.loads((out / "key.json").read_text())["files"].items()}

    first = xs(tmp_path / "k1", seed=7)
    assert set(first.values()) == {"a", "b"}  # a real shuffle, not "X is always the old run"
    assert xs(tmp_path / "k2", seed=7) == first
    assert xs(tmp_path / "k3", seed=7, only=["4/pdf"]) == {"4-pdf": first["4-pdf"]}  # --only does not move the coin
    assert xs(tmp_path / "k3", seed=99) == first  # re-make widens the kit and keeps its own seed and sides
    assert len({json.dumps(xs(tmp_path / f"s{s}", seed=s)) for s in range(8)}) > 1
    with pytest.raises(SystemExit):
        blind.make(new, old, tmp_path / "k1")  # other runs: a new --out


def test_make_run_dir_layout(dir_runs, tmp_path):
    old, new = dir_runs
    out = tmp_path / "blind"
    assert blind.make(old, new, out, seed=3) == ["a.pdf", "b.pdf"]  # c.pdf has no result in the new run yet
    key = json.loads((out / "key.json").read_text())["files"]
    a = out / "a.pdf"
    assert (a / "page-01.png").exists() and not (a / "extracted.txt").exists()
    field = {"a": "Pole stare", "b": "Pole nowe"}
    for side in "XY":  # each side gets the text its own run saved
        assert (a / f"extracted-{side}.txt").read_text() == f"tekst {field[key['a.pdf'][side]]}"
        assert field[key["a.pdf"][side]] in (a / f"{side}.md").read_text()
    crashed = "X" if key["b.pdf"]["X"] == "b" else "Y"  # the new run crashed on b.pdf: still a kit
    assert "BRAK WYNIKU" in (out / "b.pdf" / f"{crashed}.md").read_text()
    assert "ReadTimeout" in (out / "b.pdf" / f"{crashed}.md").read_text()
    assert "Pole b" in (out / "b.pdf" / f"{'Y' if crashed == 'X' else 'X'}.md").read_text()
    assert (out / "b.pdf" / "extracted.txt").read_text() == "tekst b"
    assert "old-run" not in kit_text(out) and "new-run" not in kit_text(out)


def test_make_only_and_unknown_layout(dir_runs, tmp_path):
    old, new = dir_runs
    assert blind.make(old, new, tmp_path / "blind", only=["b.p"]) == ["b.pdf"]
    with pytest.raises(SystemExit):
        blind.make(old, tmp_path, tmp_path / "blind2")


def test_add_validates_and_appends(dir_runs, tmp_path, capsys):
    out = tmp_path / "blind"
    blind.make(*dir_runs, out, seed=3)
    assert blind.add(out, "a.pdf", "X", 1, 4, "Y gubi tabelę") == 0
    assert blind.add(out, "a.pdf", "tie", 2, 2, "to samo") == 0
    assert blind.add(out, "nope.pdf", "X", 0, 0, "x") == 1  # no such kit
    assert blind.add(out, "a.pdf", "Z", 0, 0, "x") == 1
    assert blind.add(out, "a.pdf", "X", -1, 0, "x") == 1
    assert blind.add(out, "a.pdf", "X", 0, 0, " ") == 1
    assert "rejected" in capsys.readouterr().err
    lines = [json.loads(line) for line in (out / "verdicts.jsonl").read_text().splitlines()]
    assert lines == [{"id": "a.pdf", "verdict": "X", "defects_x": 1, "defects_y": 4, "reasons": "Y gubi tabelę"},
                     {"id": "a.pdf", "verdict": "tie", "defects_x": 2, "defects_y": 2, "reasons": "to samo"}]


def test_report_unblinds_and_sorts_regressions_first(tmp_path):
    out = tmp_path / "blind"
    files = {"small-regress": {"X": "a", "Y": "b"}, "big-regress": {"X": "b", "Y": "a"}, "better": {"X": "b", "Y": "a"},
             "tie": {"X": "a", "Y": "b"}, "same": {"X": "a", "Y": "b", "identical": True}, "waiting": {"X": "a", "Y": "b"}}
    put(out / "key.json", {"a": "/runs/old", "b": "/runs/new", "seed": 1, "files": files})
    verdicts = [{"id": "small-regress", "verdict": "X", "defects_x": 2, "defects_y": 3, "reasons": "Y bez warunku"},
                {"id": "big-regress", "verdict": "X", "defects_x": 0, "defects_y": 0, "reasons": "pierwsza ocena"},
                {"id": "big-regress", "verdict": "Y", "defects_x": 9, "defects_y": 1, "reasons": "X gubi stronę | 2"},
                {"id": "better", "verdict": "X", "defects_x": 1, "defects_y": 5, "reasons": "X ma tabelę"},
                {"id": "tie", "verdict": "tie", "defects_x": 2, "defects_y": 2, "reasons": "to samo"}]
    put(out / "verdicts.jsonl", "".join(json.dumps(v) + "\n" for v in verdicts))
    rows = blind.unblind(out)
    assert [(r["id"], r["winner"], r.get("defects_a"), r.get("defects_b")) for r in rows] == [
        ("big-regress", "A", 1, 9),  # X was the new run: Y won = A won; the last verdict counts
        ("small-regress", "A", 2, 3),
        ("same", "tie", None, None),  # identical COI JSON: a tie nobody judged
        ("tie", "tie", 2, 2),
        ("better", "B", 5, 1),
        ("waiting", None, None, None)]
    text = blind.report(out)
    assert "Wygrane: A 2, B 1, remisy 2" in text
    assert "A 10, B 15 (B - A = +5)" in text
    assert "z werdyktem sędziego: 4, identyczne w obu przebiegach (remis bez sędziego): 1, bez werdyktu: 1" in text
    table = [line for line in text.splitlines() if line.startswith("| ")][1:]
    assert table[0] == "| big-regress | A | 1 | 9 | +8 | B | X gubi stronę / 2 |"
    assert table[-1].startswith("| waiting | brak werdyktu |  | |  | A |")
    assert "/runs/old" in text and "/runs/new" in text
