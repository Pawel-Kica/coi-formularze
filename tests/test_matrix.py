"""bench/matrix.py and the run_all bits it relies on, without a model: a fake `ollama` CLI and a fake
child `bench.run_all` check arm order, env, one loaded model at a time, resume and the table."""

import json
import sys
from pathlib import Path

import pytest

from app import pipeline
from app.config import settings
from app.llm import planner
from app.quotes import THRESHOLD_OCR
from bench import matrix, run_all
from tests.test_vision import PASS1, a4_pdf

T30, VL = "qwen3:30b-a3b-instruct-2507-q4_K_M", "qwen3-vl:30b-a3b-instruct"


def test_default_arms():
    arms = matrix.load_arms(matrix.ARMS)
    assert list(arms) == ["t30-text", "vl-text", "vl-img+text", "vl-img", "vl-img-gray"]
    assert [arms[a]["LLM_MODEL"] for a in arms] == [T30, VL, VL, VL, VL]
    assert [matrix.expected_input(e) for e in arms.values()] == ["text", "text", "img+text", "img", "img"]
    assert matrix.load_arms(matrix.ARMS, "vl-img,t30-text").keys() == {"vl-img", "t30-text"}
    with pytest.raises(SystemExit):
        matrix.load_arms(matrix.ARMS, "nope")


class FakeOllama:
    """Stands in for subprocess.run: `ollama ps/stop`, `pgrep`, and the child run_all, which "loads"
    its model and writes a summary. Fails the test if a second model would be loaded."""

    def __init__(self, loaded=(), busy=""):
        self.loaded, self.busy, self.calls, self.runs = list(loaded), busy, [], []

    def __call__(self, cmd, env=None, **kw):
        self.calls.append(cmd)
        out, code = "", 0
        if cmd[:2] == ["ollama", "ps"]:
            out = "NAME  ID  SIZE  PROCESSOR  CONTEXT  UNTIL\n" + "".join(f"{m}  abc  20 GB  100% GPU  32768  4 minutes from now\n" for m in self.loaded)
        elif cmd[:2] == ["ollama", "stop"]:
            self.loaded.remove(cmd[2])
        elif cmd[0] == "pgrep":
            out, code = self.busy, 0 if self.busy else 1
        elif cmd[:3] == [sys.executable, "-m", "bench.run_all"]:
            model, arm_dir = env["LLM_MODEL"], Path(cmd[cmd.index("--out") + 1])
            arg = lambda k: cmd[cmd.index(k) + 1] if k in cmd else None
            if matrix.pending(arm_dir, arg("--forms"), arg("--formats")):  # all cached = no model request
                assert set(self.loaded) <= {model}, f"{model} would load next to {self.loaded}"
                if model not in self.loaded:
                    self.loaded.append(model)
            self.runs.append({"arm": arm_dir.name, "args": cmd[3:], "env": {k: env[k] for k in env if k.startswith("LLM_")}})
            rows = [{"form": "1", "ext": "pdf", "ok": True, "gold": 0.9, "ref": 0.88, "walid": 1.0, "problems": [],
                     "dropped": ["field f3 'X': quote not in text: 'y'"], "s": 50, "input": matrix.expected_input(env)}]
            (arm_dir / "coi").mkdir(parents=True, exist_ok=True)
            (arm_dir / "coi" / "summary.json").write_text(json.dumps({"rows": rows}))
        return type("R", (), {"stdout": out, "returncode": code})()


@pytest.fixture
def fake(monkeypatch):
    f = FakeOllama(loaded=[T30])  # e.g. the dev server's model, idle
    monkeypatch.setattr(matrix.subprocess, "run", f)
    monkeypatch.setattr(matrix, "coi_files", lambda forms, formats: [("1", Path("x/form.pdf"))])
    monkeypatch.setattr(settings, "llm_model", T30)
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    return f


def run_main(monkeypatch, *args) -> int:
    monkeypatch.setattr(sys, "argv", ["matrix", *args])
    return matrix.main()


def test_arms_run_in_order_with_one_model_loaded(fake, monkeypatch, tmp_path):
    out = tmp_path / "m"
    assert run_main(monkeypatch, "--out", str(out), "--forms", "1", "--formats", "pdf") == 0
    assert [r["arm"] for r in fake.runs] == ["t30-text", "vl-text", "vl-img+text", "vl-img", "vl-img-gray"]
    assert all(r["args"] == ["--out", str(out / r["arm"]), "--forms", "1", "--formats", "pdf"] for r in fake.runs)
    env = {r["arm"]: r["env"] for r in fake.runs}
    assert (env["t30-text"]["LLM_VISION"], env["t30-text"]["LLM_MODEL"]) == ("0", T30)
    assert (env["vl-img+text"]["LLM_VISION"], env["vl-img+text"]["LLM_VISION_TEXT"]) == ("1", "1")
    assert (env["vl-img"]["LLM_VISION_TEXT"], env["vl-img"]["LLM_VISION_GRAY"]) == ("0", "0")
    assert env["vl-img-gray"]["LLM_VISION_GRAY"] == "1"
    stops = [c[2] for c in fake.calls if c[:2] == ["ollama", "stop"]]
    assert stops == [T30, VL]  # 30b before the first VL arm, VL at the end (the default model may stay)
    assert fake.loaded == []
    md = (out / "matrix.md").read_text()
    assert "| vl-img | pdf | 0.900 | 0.880 | 1.00 | 0.90 | 0 | 1/1 | 50 | 1/1 |" in md
    assert "differences under ~0.09 on this 1-form mean are noise" in md
    assert json.loads((out / "config.json").read_text())["forms"] == "1"


def test_screen_is_forms_1_4_5_7_pdf_and_png(fake, monkeypatch, tmp_path):
    run_main(monkeypatch, "--out", str(tmp_path / "m"), "--screen", "--arms", "t30-text")
    assert fake.runs[0]["args"][2:] == ["--forms", "1,4,5,7", "--formats", "pdf,png"]


def test_fresh_folders_only_and_resume_skips_done_arms(fake, monkeypatch, tmp_path):
    out = tmp_path / "m"
    run_main(monkeypatch, "--out", str(out), "--arms", "vl-img,t30-text", "--formats", "pdf")
    with pytest.raises(SystemExit):  # an existing folder is never reused without --resume
        run_main(monkeypatch, "--out", str(out))
    for arm in ("vl-img", "t30-text"):  # both arms done: plan + COI saved
        d = out / arm / "coi" / "1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "pdf.plan.json").write_text("{}")
        (d / "pdf.coi.json").write_text("{}")
    fake.calls.clear()
    fake.loaded = [T30]
    assert run_main(monkeypatch, "--resume", str(out)) == 0
    assert [r["arm"] for r in fake.runs[-2:]] == ["vl-img", "t30-text"]  # saved order and arms
    assert not [c for c in fake.calls if c[:2] == ["ollama", "stop"]]  # nothing left to run: no model switch
    assert matrix.pending(out / "vl-img", "1", "pdf") == []


def test_refuses_to_start_next_to_another_bench(fake, monkeypatch, tmp_path):
    fake.busy = "4242 .venv/bin/python -m bench.run_all --out /tmp/coi-baseline\n"
    assert run_main(monkeypatch, "--out", str(tmp_path / "m")) == 2
    assert not fake.runs and not (tmp_path / "m").exists()
    assert run_main(monkeypatch, "--out", str(tmp_path / "m"), "--arms", "t30-text", "--force") == 0
    assert [r["arm"] for r in fake.runs] == ["t30-text"]


def test_unload_raises_when_a_model_stays_loaded(fake, monkeypatch):
    monkeypatch.setattr(matrix, "UNLOAD_WAIT_S", 0)
    monkeypatch.setattr(fake, "loaded", [T30])
    real = fake.__call__

    def sticky(cmd, **kw):  # `ollama stop` does nothing
        return type("R", (), {"stdout": "", "returncode": 0})() if cmd[:2] == ["ollama", "stop"] else real(cmd, **kw)

    monkeypatch.setattr(matrix.subprocess, "run", sticky)
    with pytest.raises(RuntimeError, match="still loaded"):
        matrix.unload_others(VL)


def test_table_marks_fallbacks_errors_and_noise():
    arms = {"vl-img": {"LLM_MODEL": VL, "LLM_VISION": "1", "LLM_VISION_TEXT": "0"}}
    rows = [
        {"form": "1", "ext": "pdf", "ok": True, "gold": 0.9, "ref": 0.8, "walid": 1.0, "problems": [], "s": 40,
         "dropped": ["field f1 'A': quote not in text: 'a'", "standard: field f2 'B' belongs to a generated page"],
         "input": "img"},
        {"form": "9", "ext": "pdf", "ok": True, "gold": 0.5, "problems": ["x"], "s": 400, "dropped": [], "input": "img+text"},
        {"form": "1", "ext": "png", "ok": False, "error": "LLMError: timeout", "s": 1200},
    ]
    md, data = matrix.table({"vl-img": rows}, arms, ["pdf", "png"])
    pdf, png, both = data
    assert (pdf["gold"], pdf["forms"], pdf["rules"], pdf["drop_quote"], pdf["drop_all"], pdf["s"]) == (
        0.7, {"1": "0.90", "9": "0.50*"}, 1, 1, 2, 220)
    assert (png["forms"], png["ok"], png["gold"]) == ({"1": "ERR"}, 0, None)
    assert (both["format"], both["ok"], both["files"]) == ("all", 2, 3)
    assert "on this 2-form mean are noise" in md and "* = the model got other input" in md
    assert matrix.noise(9) == 0.03 and matrix.noise(4) == 0.045


# ---------- run_all: threshold, input kind, meta kept for cached rows ----------


def test_run_file_images_only_uses_ocr_threshold_and_keeps_meta(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "llm_vision", True)
    monkeypatch.setattr(settings, "llm_vision_max_pages", 8)
    monkeypatch.setattr(settings, "llm_vision_text", False)
    seen = {}

    def fake_filter(plan, text, threshold):
        seen["threshold"] = threshold
        return plan, ["field f9 'X': quote not in text: 'y'"]

    monkeypatch.setattr(pipeline, "extract", lambda data, name: type("D", (), {"text": "Imię ____", "method": "pdf-text"})())
    monkeypatch.setattr(run_all, "make_plan", lambda text, pages: planner.draft_to_plan(PASS1))
    monkeypatch.setattr(run_all, "filter_plan", fake_filter)
    monkeypatch.setattr(run_all, "reference", lambda form: None)
    src = tmp_path / "form.pdf"
    src.write_bytes(a4_pdf(2))
    row = run_all.run_file("x", src, tmp_path / "out")
    assert seen["threshold"] == THRESHOLD_OCR
    assert (row["ok"], row["input"], len(row["dropped"])) == (True, "img", 1)
    cached = run_all.run_file("x", src, tmp_path / "out")  # resume: no model, same drops/seconds/input
    assert cached["cached"] and {k: cached[k] for k in ("dropped", "s", "input")} == {k: row[k] for k in ("dropped", "s", "input")}
