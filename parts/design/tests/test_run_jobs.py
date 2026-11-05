"""The 45L job runner works end to end (fake mode, no network) and refuses real mode without its settings."""
import io
import json
import runpy
from pathlib import Path

import pytest

RUN = runpy.run_path(str(Path(__file__).parents[1] / "tools" / "run_jobs.py"))["main"]

pytestmark = pytest.mark.usefixtures("fast_finish")


def test_fake_run_records_everything(tmp_path):
    assert RUN(["--out", str(tmp_path), "--fake", "--only", "h01,h07"]) == 0
    s = json.loads((tmp_path / "summary.json").read_text())
    assert s["lane"] == "shark" and [b["project_id"] for b in s["briefs"]] == ["h01-lakeside-hut", "h07-storehouse"]
    assert s["selftest"]["passed"] is False                      # the fake critic cannot qualify ...
    assert all(b["gate"] == "hold" for b in s["briefs"])        # ... so nothing it scored may be built
    assert (tmp_path / "h01-lakeside-hut" / "v1" / "candidates.json").exists() and (tmp_path / "RESULT.md").exists()


def test_real_mode_refuses_without_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("EVE_DEEPSEEK_ENABLED", raising=False)
    assert RUN(["--out", str(tmp_path), "--only", "h01"]) == 2 and not (tmp_path / "summary.json").exists()


def test_resume_keeps_finished_briefs_and_regates_them(tmp_path):
    assert RUN(["--out", str(tmp_path), "--fake", "--only", "h01"]) == 0
    assert RUN(["--out", str(tmp_path), "--fake", "--only", "h07", "--resume", "--skip-selftest"]) == 0
    s = json.loads((tmp_path / "summary.json").read_text())
    assert [b["project_id"] for b in s["briefs"]] == ["h01-lakeside-hut", "h07-storehouse"]
    assert s["briefs"][0].get("regated") and s["resumed_from"]["selftest"]["passed"] is False


def test_resume_rebuilds_rows_for_finished_folders_without_a_summary(tmp_path):
    """45L attempts 5 and 6 stopped before writing summary.json: their finished briefs exist only as folders."""
    assert RUN(["--out", str(tmp_path), "--fake", "--only", "h01", "--skip-selftest"]) == 0
    first = json.loads((tmp_path / "summary.json").read_text())["briefs"][0]
    (tmp_path / "summary.json").unlink()
    assert RUN(["--out", str(tmp_path), "--fake", "--only", "h07", "--resume", "--skip-selftest"]) == 0
    s = json.loads((tmp_path / "summary.json").read_text())
    h01, h07 = s["briefs"]
    assert h01["project_id"] == "h01-lakeside-hut" and h01["from_folder"] and h01["gate"] == "hold"
    assert {k: h01[k] for k in ("status", "design_hash", "candidates", "critique")} == \
        {k: first[k] for k in ("status", "design_hash", "candidates", "critique")}
    assert h07["project_id"] == "h07-storehouse" and "from_folder" not in h07


def test_a_hung_brief_times_out_scores_0_and_the_run_goes_on(tmp_path, monkeypatch):
    """O68 (45L attempt 6, h06 ran for hours on one core): the brief's wall-clock budget stops it with the reason."""
    real, g = RUN.__globals__["generate"], RUN.__globals__

    def generate(b, *a, **k):
        if b["project_id"].startswith("h01"):
            def spin_in_the_planner():
                while True:
                    pass
            spin_in_the_planner()
        # the brief after the hung one must finish inside its budget on a slow host too: no critic, so no preview
        # renders per critique (45L job 20261022-021544-design: with O87w's extra critiques h07 took over 6 s)
        return real(b, a[0], a[1], None, **k)
    monkeypatch.setitem(g, "generate", generate)
    assert RUN(["--out", str(tmp_path), "--fake", "--only", "h01,h07", "--skip-selftest", "--brief-minutes", "0.25"]) == 0
    s = json.loads((tmp_path / "summary.json").read_text())
    h01, h07 = s["briefs"]
    assert h01["status"] == "timeout" and h01["score"] == 0 and h01["gate"] == "none"
    assert "0.25-minute brief budget" in h01["error"] and "spin_in_the_planner" in h01["error"]
    assert h07["status"] != "timeout" and h07["gate"] == "hold" and "timeout" in (tmp_path / "RESULT.md").read_text()


def test_recritique_critiques_the_saved_design_again_without_generating(tmp_path, monkeypatch):
    """O87: attempt 5's h01/h03/h04 were judged by a critic with no passing self-test; their saved designs are rebuilt
    under today's checks, critiqued again and re-gated, with no generation. A design that no longer builds keeps its
    folder and gates nothing."""
    assert RUN(["--out", str(tmp_path), "--fake", "--only", "h01,h07", "--skip-selftest"]) == 0
    h01, h07 = (tmp_path / "h01-lakeside-hut" / "v1", tmp_path / "h07-storehouse" / "v1")
    before = json.loads((h01 / "critique.json").read_text())
    (h07 / "program.eve").write_text("not a program\n", encoding="utf-8")
    kept = (h07 / "critique.json").read_text()
    monkeypatch.setitem(RUN.__globals__, "generate", lambda *a, **k: pytest.fail("recritique must not generate"))
    assert RUN(["--out", str(tmp_path), "--fake", "--only", "h01,h07", "--recritique", "--resume", "--skip-selftest"]) == 0
    a, b = json.loads((tmp_path / "summary.json").read_text())["briefs"]
    c = json.loads((h01 / "critique.json").read_text())
    assert c["design_hash"] == before["design_hash"] == c["recritique_of"]["design_hash"] and c["history"][0]["scores"]
    assert a["candidates"] == 1 and a["gate"] == "hold"          # re-gated: the fake critic still cannot qualify
    assert b["status"] == "no_valid_design" and b["gate"] == "none" and (h07 / "critique.json").read_text() == kept


def test_selftest_probe_writes_one_critique_per_name(tmp_path):
    """O34n: base and wall_hole, one sample each, before any full self-test."""
    assert RUN(["--out", str(tmp_path), "--fake", "--selftest-probe", "base,wall_hole"]) == 0
    p = json.loads((tmp_path / "probe.json").read_text())
    assert set(p["results"]) == {"base", "wall_hole"} and all("scores" in r for r in p["results"].values())
    assert not (tmp_path / "summary.json").exists()


def test_a_spend_cap_mid_brief_stops_the_run(tmp_path, monkeypatch):
    """O87u (Hunter's money rule): the brief that reaches the job cap is needs_review with reason cap, gets no gate,
    and no further brief starts."""
    from eve_design import models
    g = RUN.__globals__

    def generate(b, *a, **k):
        raise models.SpendCapReached("job spend US$0.9990 plus this call's worst case would pass the job cap US$1")
    monkeypatch.setitem(g, "generate", generate)
    assert RUN(["--out", str(tmp_path), "--fake", "--only", "h01,h07", "--skip-selftest"]) == 0
    s = json.loads((tmp_path / "summary.json").read_text())
    assert [b["project_id"] for b in s["briefs"]] == ["h01-lakeside-hut"]
    h01 = s["briefs"][0]
    assert (h01["status"], h01["reason"], h01["gate"]) == ("needs_review", "cap", "none") and "cap:" in h01["error"]
    assert "cap US$2 reached in h01-lakeside-hut" in s["stopped"] and "Stopped: job spend cap" in (tmp_path / "RESULT.md").read_text()


def _paying_models(tmp_path, monkeypatch, critic_script=None):
    """Real DeepSeekClients (fake HTTP, a scratch spend ledger) behind the fake generator and critic: every step is
    one priced provider call, US$0.002 settled, worst case ~US$0.0041 (price 1/2 per Mtok, 2048 max tokens)."""
    import sqlite3
    from eve_design import models
    db = tmp_path / "eve.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE spend (id INTEGER PRIMARY KEY, ts INTEGER, provider TEXT, model TEXT, job TEXT, "
                  "tokens_in INTEGER, tokens_out INTEGER, usd REAL, cached INTEGER)")

    class HTTP:
        calls = 0

        def __call__(self, req, timeout=None):
            HTTP.calls += 1
            body = {"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
            return io.BytesIO(json.dumps(body).encode())
    client = lambda: models.DeepSeekClient("m", enabled=True, key="k", cap=20, price=("1", "2"),   # noqa: E731
                                           ledger=models.SpendLedger(db), opener=HTTP())
    msg = [{"role": "user", "content": "hi"}]

    class Gen(models.FakeGenerator):
        def __init__(self):
            super().__init__()
            self.client = client()

        def concepts(self, *a, **k):
            self.client.chat("concepts", msg)
            return super().concepts(*a, **k)

        def program(self, *a, **k):
            self.client.chat("program", msg)
            return super().program(*a, **k)

    class Critic(models.FakeCritic):
        def __init__(self):
            super().__init__(script=critic_script)
            self.client = client()

        def critique(self, *a, **k):
            self.client.chat("critique", msg)
            return super().critique(*a, **k)
    made = {}
    monkeypatch.setattr(models, "DeepSeekGenerator", lambda: made.setdefault("gen", Gen()))
    monkeypatch.setattr(models, "DeepSeekCritic", lambda: made.setdefault("critic", Critic()))
    return made, HTTP


def test_the_job_cap_spans_both_clients_and_stops_mid_brief(tmp_path, monkeypatch):
    """O87u (Hunter's money rule): one cap over the generator and the critic, checked before every call. Here the
    critic's spend is what makes the next call too dear (O87w: the next concept's critique): the generator's spend
    alone would still leave room for it."""
    made, http = _paying_models(tmp_path, monkeypatch, critic_script=[{"all": 5, "fixes": ["more detail"]}] * 4)
    cap = 0.013
    assert RUN(["--out", str(tmp_path / "run"), "--only", "h01,h07", "--skip-selftest", "--job-cap-usd", str(cap)]) == 0
    s = json.loads((tmp_path / "run" / "summary.json").read_text())
    h01, = s["briefs"]                                            # h07 never started
    assert (h01["project_id"], h01["status"], h01["reason"], h01["gate"]) == ("h01-lakeside-hut", "needs_review", "cap", "none")
    g, c = made["gen"].client.job_usd, made["critic"].client.job_usd
    worst = 4e-6 + 2048 * 2e-6                                    # '"hi"' is 4 chars in, 2048 tokens out
    assert c > 0 and g + worst <= cap < g + c + worst             # the critic's spend counted toward the refusal
    assert s["job_spend_usd"] <= cap and http.calls == round((g + c) / 0.002)   # the refused call was never sent
    assert "cap US$0.013 reached in h01-lakeside-hut" in s["stopped"]


def test_a_cap_in_the_critic_selftest_stops_the_run_before_any_brief(tmp_path, monkeypatch):
    made, http = _paying_models(tmp_path, monkeypatch)
    assert RUN(["--out", str(tmp_path / "run"), "--only", "h01", "--job-cap-usd", "0.001"]) == 0
    s = json.loads((tmp_path / "run" / "summary.json").read_text())
    assert s["briefs"] == [] and http.calls == 0 and s["selftest"]["error"].startswith("cap: ")
    assert "critic self-test" in s["stopped"] and "Stopped: job spend cap" in (tmp_path / "run" / "RESULT.md").read_text()
