"""CP-61 / CP-63 / CP-64 on deterministic fakes, plus the DeepSeek adapter's guards (no network: the opener is injected)."""
import io
import json
import re
import sqlite3
import time
from pathlib import Path

import pytest

from eve_design import gamedata, models
from eve_design.generate import generate
from eve_design.models import (DeepSeekClient, DeepSeekCritic, DeepSeekGenerator, FakeCritic, FakeGenerator, ModelError,
                               SpendLedger, agreement, extract_program)

GD = gamedata.load()
HERE = Path(__file__).parent
BRIEFS = json.loads((HERE / "fixtures/briefs/manifest.json").read_text())["briefs"]
CABIN = BRIEFS[1]

pytestmark = pytest.mark.usefixtures("fast_finish")


@pytest.mark.parametrize("brief", BRIEFS, ids=[b["project_id"] for b in BRIEFS])
def test_every_brief_gets_a_valid_design_that_keeps_its_requests(brief, tmp_path):
    r = generate(brief, GD, FakeGenerator(), FakeCritic(), out=tmp_path / "v1")
    assert r["status"] in ("accepted", "needs_review")
    assert 2 <= len(r["concepts"]) <= 3
    v = json.loads((tmp_path / "v1/validation.json").read_text())
    d = json.loads((tmp_path / "v1/design.json").read_text())
    assert v["passed"] and v["design_hash"] == d["hash"] == r["design_hash"]
    types = [room["type"] for room in d["rooms"]]
    assert all(types.count(t) >= brief["rooms"].count(t) for t in brief["rooms"])
    assert set(brief.get("features", [])) <= set(d["provenance"]["commands"])
    for f in ("brief.json", "program.eve", "design.litematic", "bom.json", "critique.json", "candidates.json", "previews/SE.png"):
        assert (tmp_path / "v1" / f).exists(), f
    rep = json.loads((tmp_path / "v1/candidates.json").read_text())
    assert rep["generator"]["version"] == FakeGenerator.version and all("program_hash" in c for c in rep["candidates"])


@pytest.mark.parametrize("fault,expect", [("syntax", "compile: line"), ("unknown_command", "unknown command 'teleport'"),
                                          ("too_many_windows", "no room for window"), ("drop_room", "asks for a bedroom")])
def test_injected_failure_is_repaired_and_recorded(fault, expect):
    r = generate(CABIN, GD, FakeGenerator(faults={fault: 1}), None)
    first = r["candidates"][0]
    assert not first["valid"] and any(expect in e for e in first["errors"]), first["errors"]
    fix = r["candidates"][1]
    assert fix["concept"] == first["concept"] and fix["stage"] == "repair" and fix["valid"]
    assert r["status"] == "not_critiqued" and r["design_hash"]


class DeafGen(FakeGenerator):
    """Ignores feedback: keeps dropping a requested room."""

    def program(self, brief, concept, card, previous=None, feedback=None):
        self.faults["drop_room"] = 1
        return super().program(brief, concept, card)


def test_no_bypass_when_repairs_run_out(tmp_path):
    r = generate(CABIN, GD, DeafGen(), FakeCritic(), out=tmp_path / "v1")
    assert r["status"] == "no_valid_design" and not (tmp_path / "v1/design.json").exists()
    assert len(r["candidates"]) == 9 and not any(c["valid"] for c in r["candidates"])     # 3 concepts x (1 + 2 repairs)
    assert all(any("asks for a bedroom" in e for e in c["errors"]) for c in r["candidates"])


def test_model_unavailable_writes_nothing(tmp_path):
    r = generate(CABIN, GD, FakeGenerator(broken=True), FakeCritic(), out=tmp_path / "v1")
    assert r["status"] == "model_unavailable" and not (tmp_path / "v1").exists()


# --- CP-64 revision loop ------------------------------------------------------------------------------
class VaryingGen(FakeGenerator):
    """Each revision changes the seed and adds the gradient rule so the design changes; `broken_round` returns a
    syntax error once."""

    def __init__(self, broken_round=None):
        super().__init__()
        self.n, self.broken_round = 0, broken_round

    def program(self, brief, concept, card, previous=None, feedback=None):
        src = super().program(brief, concept, card)
        if previous is None:
            return src
        self.n += 1
        if self.n == self.broken_round:
            return src + "b.walls(h=4\n"
        return re.sub(r"seed=\d+", f"seed={self.n}", src).replace("b.roof(", 'b.detail("gradient")\nb.roof(')


def test_still_poor_after_three_rounds_needs_review():
    r = generate(CABIN, GD, VaryingGen(), FakeCritic(script=[{"all": 5}, {"all": 6}, {"all": 5}, {"all": 4}]),
                 n_concepts=1)                                   # the revision loop alone (O87w: no other concepts)
    c = r["critique"]
    assert c["status"] == "needs_review" and c["rounds_used"] == 3 and c["max_rounds"] == 3
    assert c["best_round"] == 1 and min(c["scores"].values()) == 6          # best valid kept with its full scores
    revs = [x for x in r["candidates"] if x.get("stage") == "revision"]
    assert len(revs) == 3 and all(x["valid"] and x["design_hash"] for x in revs)   # every revision recompiled + validated


def test_oscillation_stops_early():
    r = generate(CABIN, GD, FakeGenerator(), FakeCritic(script=[{"all": 5, "fixes": ["make it nicer"]}] * 4), n_concepts=1)
    c = r["critique"]
    assert c["status"] == "needs_review" and c["history"][-1].get("oscillation") and c["rounds_used"] == 1


def test_api_failure_mid_loop_keeps_best():
    r = generate(CABIN, GD, VaryingGen(), FakeCritic(script=[{"all": 6}, None]), n_concepts=1)
    c = r["critique"]
    assert c["status"] == "needs_review" and c["best_round"] == 0 and "critic" in c["history"][-1]["errors"][0]


def test_invalid_revision_is_recorded_and_loop_continues():
    r = generate(CABIN, GD, VaryingGen(broken_round=1), FakeCritic(script=[{"all": 5}, {"all": 8}, {"all": 8}]),
                 n_concepts=1)
    h = r["critique"]["history"]
    assert h[1]["valid"] is False and h[1]["errors"][0].startswith("compile")
    assert r["critique"]["status"] == "accepted" and r["critique"]["best_round"] == 2


def test_accepted_only_when_every_category_reaches_threshold():
    r = generate(CABIN, GD, FakeGenerator(), FakeCritic())
    c = r["critique"]
    assert c["status"] == "accepted" and min(c["scores"].values()) >= 7 and c["history"][0]["fixes"]


# --- DeepSeek adapter guards --------------------------------------------------------------------------
def ledger(tmp_path, spent=0.0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "eve.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE spend (id INTEGER PRIMARY KEY, ts INTEGER, provider TEXT, model TEXT, job TEXT, "
                  "tokens_in INTEGER, tokens_out INTEGER, usd REAL, cached INTEGER)")
        if spent:
            c.execute("INSERT INTO spend (ts, provider, usd) VALUES (?, 'deepseek', ?)", (int(time.time() * 1000), spent))
    return SpendLedger(db)


class FakeHTTP:
    def __init__(self, contents):
        self.contents, self.calls = list(contents), 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        body = {"choices": [{"message": {"content": self.contents.pop(0)}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
        return io.BytesIO(json.dumps(body).encode())


def test_off_until_switched_on_after_cp06(monkeypatch, tmp_path):
    monkeypatch.delenv("EVE_DEEPSEEK_ENABLED", raising=False)
    c = DeepSeekClient("m", key="k", cap=20, price=("1", "2"), ledger=ledger(tmp_path), opener=FakeHTTP(["x"]))
    with pytest.raises(ModelError) as e:
        c.chat("t", [{"role": "user", "content": "hi"}])
    assert e.value.code == "model_disabled" and "EVE_DEEPSEEK_ENABLED=1" in e.value.detail and c.opener.calls == 0


def test_disabled_without_key_cap_or_prices(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    c = DeepSeekClient("deepseek-v4-pro", enabled=True, cap=20, price=("1", "2"), opener=FakeHTTP([]))
    with pytest.raises(ModelError) as e:
        c.chat("t", [{"role": "user", "content": "hi"}])
    assert e.value.code == "model_disabled" and "OPENROUTER_API_KEY" in e.value.detail and c.opener.calls == 0


def test_cap_checked_before_sending(tmp_path):
    http = FakeHTTP(["ok"])
    near = DeepSeekClient("m", enabled=True, key="k", cap=20, price=("1", "2"), ledger=ledger(tmp_path, spent=19.9999), opener=http)
    with pytest.raises(ModelError) as e:
        near.chat("t", [{"role": "user", "content": "x" * 100}], max_tokens=1000)
    assert e.value.code == "budget_exhausted" and http.calls == 0


def test_spend_row_written(tmp_path):
    led = ledger(tmp_path)
    c = DeepSeekClient("m", enabled=True, key="k", cap=20, price=("1", "2"), ledger=led, opener=FakeHTTP(["hello"]))
    assert c.chat("program", [{"role": "user", "content": "hi"}]) == "hello"
    with sqlite3.connect(led.path) as db:
        row = db.execute("SELECT provider, model, job, tokens_in, tokens_out, usd FROM spend").fetchone()
    assert row[:5] == ("openrouter", "m", "design:program", 1000, 500) and row[5] == pytest.approx(0.002)


def test_missing_mind_db_disables(tmp_path):
    c = DeepSeekClient("m", enabled=True, key="k", cap=20, price=("1", "2"), ledger=SpendLedger(tmp_path / "nope.db"), opener=FakeHTTP(["x"]))
    with pytest.raises(ModelError) as e:
        c.chat("t", [{"role": "user", "content": "hi"}])
    assert e.value.code == "no_spend_ledger" and c.opener.calls == 0


def test_program_must_be_one_fenced_block():
    assert extract_program('```eve\nb = Build(style="cozy_cabin")\n```') == 'b = Build(style="cozy_cabin")\n'
    assert extract_program('```eve\nb = Build(style="cozy_cabin")\n') == 'b = Build(style="cozy_cabin")\n'   # left open (h08)
    for bad in ("no fence", "```eve\na\n```\n```eve\nb\n```", "see ```eve"):
        with pytest.raises(ModelError) as e:
            extract_program(bad)
        assert e.value.code == "malformed_output"


def test_deepseek_generator_end_to_end_without_network(tmp_path):
    fake = FakeGenerator()
    program = fake.program(CABIN, fake.concepts(CABIN, {}, 2)[0], {})
    say = "a one-storey cabin with the bedroom at the back, the kitchen by the door and a porch facing south"
    concepts = json.dumps([{"name": "a", "summary": say, "footprint": [11, 9], "storeys": 1, "roof": "gable",
                            "features": ["porch", "chimney"]},
                           {"name": "b", "summary": say, "footprint": [11, 9], "storeys": 1, "roof": "flat",
                            "features": ["porch", "chimney"]}])
    hostile = "```eve\nimport os\n```"
    http = FakeHTTP([concepts, f"```eve\n{program}```", hostile, hostile, hostile])
    gen = DeepSeekGenerator(DeepSeekClient("deepseek-v4-pro", enabled=True, key="k", cap=20, price=("1", "2"), ledger=ledger(tmp_path), opener=http))
    r = generate(CABIN, GD, gen, None, n_concepts=2)
    assert r["status"] == "not_critiqued" and r["generator"]["version"].startswith("deepseek-v4-pro")
    rejected = [c for c in r["candidates"] if c.get("concept") == 1]
    assert len(rejected) == 3 and all(not c["valid"] and "line 1" in c["errors"][0] for c in rejected)
    assert http.calls == 5


def test_image_critic_off_until_cp06(monkeypatch):
    monkeypatch.delenv("EVE_DEEPSEEK_ENABLED", raising=False)
    with pytest.raises(ModelError) as e:
        DeepSeekCritic(DeepSeekClient("deepseek-flash", key="k", cap=20, price=("1", "2"))).critique({}, {}, {})
    assert e.value.code == "model_disabled" and "CP-06" in e.value.detail


def test_calibration_agreement_counts_disagreements():
    scores = {f"d{i}": {"brief": 8, "style": 8 if i % 2 else 5} for i in range(4)}
    out = agreement(scores, {"d0": False, "d1": True, "d2": True, "d3": True, "d9": True})
    assert out["cases"] == 4 and out["agree"] == 3
    assert out["disagreements"] == [{"id": "d2", "critic_accepts": False, "hunter_keeps": True}] and out["missing"] == ["d9"]


def test_openrouter_defaults_and_model_id_required(monkeypatch, tmp_path):
    monkeypatch.delenv("EVE_OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("EVE_DEEPSEEK_MODELS", raising=False)
    gen = DeepSeekGenerator(DeepSeekClient("", enabled=True, key="k", cap=20, price=("1", "2"), ledger=ledger(tmp_path)))
    assert gen.client.base_url == "https://openrouter.ai/api/v1"
    assert "model id" in gen.client.why_disabled()
    seen = {}

    def opener(req, timeout=None):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        return FakeHTTP(["ok"])(req)
    c = DeepSeekClient("deepseek/some-verified-id", enabled=True, key="k", cap=20, price=("1", "2"), ledger=ledger(tmp_path / "b"),
                       opener=opener)
    assert c.chat("t", [{"role": "user", "content": "hi"}]) == "ok"
    assert seen == {"url": "https://openrouter.ai/api/v1/chat/completions", "auth": "Bearer k"}


def test_openrouter_cost_and_config_like_the_mind(monkeypatch, tmp_path):
    monkeypatch.setenv("EVE_DEEPSEEK_MODELS", '{"pro": "deepseek/pro-id", "flash": "deepseek/flash-id"}')
    monkeypatch.setenv("EVE_DEEPSEEK_PRICES", '{"deepseek/pro-id": [1, 2]}')
    monkeypatch.setenv("EVE_DEEPSEEK_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    led = ledger(tmp_path, spent=0)
    gen = DeepSeekGenerator(DeepSeekClient(__import__("eve_design.models").models.configured_model("pro"), cap=20, ledger=led))
    assert gen.client.model == "deepseek/pro-id" and gen.client.why_disabled() is None
    assert DeepSeekCritic().client.model == "deepseek/flash-id" and "EVE_DEEPSEEK_PRICES" in DeepSeekCritic().client.why_disabled()
    sent = {}

    def opener(req, timeout=None):
        sent["body"], sent["title"] = json.loads(req.data), req.get_header("X-title")
        body = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0123}}
        return io.BytesIO(json.dumps(body).encode())
    gen.client.opener = opener
    assert gen.client.chat("program", [{"role": "user", "content": "hi"}]) == "ok"
    assert sent["body"]["usage"] == {"include": True} and sent["title"] == "Eve (Project Sentience)"
    with sqlite3.connect(led.path) as db:
        assert db.execute("SELECT provider, usd FROM spend").fetchone() == ("openrouter", 0.0123)
    with sqlite3.connect(led.path) as db:      # mind's openrouter rows count toward the same cap
        db.execute("INSERT INTO spend (ts, provider, usd) VALUES (?, 'openrouter', 19.99)", (int(time.time() * 1000),))
    with pytest.raises(ModelError) as e:
        gen.client.chat("t", [{"role": "user", "content": "x" * 100}], max_tokens=5000)
    assert e.value.code == "budget_exhausted"


def test_mind_cli_reserve_then_commit_and_refusals():
    """A stand-in for the mind's /cli speaking the agreed shape: reserve before the call, commit usage.cost after."""
    import asyncio
    import threading

    from websockets.asyncio.server import serve

    from eve_design.models import MindCliLedger
    seen, state = [], {"left": 0.01}

    async def handler(ws):
        m = json.loads(await ws.recv())
        seen.append(m)
        a = m["args"]
        if m["token"] != "t" * 16:
            await ws.send(json.dumps({"ok": False, "error": "unauthorized"}))
        elif m["cmd"] == "spend_reserve":
            ok = a["usd_worst"] <= state["left"]
            state["left"] -= a["usd_worst"] if ok else 0
            await ws.send(json.dumps({"ok": True, "reservation_id": 7} if ok else {"ok": False, "error": "budget_exhausted"}))
        else:
            await ws.send(json.dumps({"ok": True, "settled": a["usd"] is not None}))

    ready, stop = threading.Event(), None

    def run():
        nonlocal stop
        async def main():
            nonlocal stop
            stop = asyncio.get_running_loop().create_future()
            async with serve(handler, "127.0.0.1", 0) as srv:
                run.port = srv.sockets[0].getsockname()[1]
                ready.set()
                await stop
        asyncio.run(main())
    th = threading.Thread(target=run, daemon=True)
    th.start()
    ready.wait(5)
    led = MindCliLedger(port=run.port, token="t" * 16)
    http = FakeHTTP(["ok", "unused"])
    c = DeepSeekClient("deepseek/pro", enabled=True, key="k", cap=20, price=("1", "2"), ledger=led, opener=http)
    assert c.chat("program", [{"role": "user", "content": "hi"}]) == "ok"
    assert [m["cmd"] for m in seen] == ["spend_reserve", "spend_commit"]
    assert seen[0]["args"]["job"] == "program" and seen[1]["args"]["reservation_id"] == 7
    with pytest.raises(ModelError) as e:                  # the mind says no -> nothing is sent
        c.chat("program", [{"role": "user", "content": "x" * 5000}], max_tokens=5000)
    assert e.value.code == "budget_exhausted" and http.calls == 1
    stop.get_loop().call_soon_threadsafe(stop.set_result, None)
    th.join(5)
    with pytest.raises(ModelError) as e:                  # mind down -> nothing is sent
        c.chat("program", [{"role": "user", "content": "hi"}])
    assert e.value.code == "mind_unavailable" and http.calls == 1
    assert "EVE_LINK_TOKEN" in MindCliLedger(token="").why_disabled()


def test_parse_json_tolerates_fences_and_prose_and_reports_truncation(tmp_path):
    from eve_design.models import parse_json
    assert parse_json('Here you go:\n```json\n[{"a": 1}, {"b": 2}]\n```\nHope this helps.', "concepts") == [{"a": 1}, {"b": 2}]
    assert parse_json('{"scores": {"x": 1}} trailing', "critique") == {"scores": {"x": 1}}
    with pytest.raises(ModelError) as e:
        parse_json("I cannot do that.", "concepts")
    assert e.value.code == "malformed_output" and "I cannot" in str(e.value)
    def cut(req, timeout=None):
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "[{"}, "finish_reason": "length"}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 2500}}).encode())
    c = DeepSeekClient("deepseek/pro", enabled=True, key="k", cap=20, price=("1", "2"), ledger=ledger(tmp_path), opener=cut)
    with pytest.raises(ModelError) as e:
        c.chat("concepts", [{"role": "user", "content": "hi"}])
    assert e.value.code == "truncated"


class CutOffGen(FakeGenerator):
    """First answer per concept runs out of tokens while reasoning (45L attempt 2), then answers when told."""

    def __init__(self):
        super().__init__()
        self.notes = []

    def program(self, brief, concept, card, previous=None, feedback=None):
        self.notes.append(feedback)
        if feedback is None:
            raise ModelError("truncated", "8000 output tokens hit max_tokens=8000")
        return super().program(brief, concept, card)


def test_truncated_program_is_retried_with_a_note():
    gen = CutOffGen()
    r = generate(CABIN, GD, gen, FakeCritic())
    first = [c for c in r["candidates"] if c.get("concept") == 0 and c.get("stage") != "revision"]
    assert first[0]["errors"] == ["model: truncated: 8000 output tokens hit max_tokens=8000"] and first[1]["valid"]
    assert "ran out of room" in gen.notes[1][0]


class CutOffReviser(VaryingGen):
    """Each revision's first answer is cut off (45L h07 round 3, h08 round 1); told to think briefly, it answers."""

    def __init__(self, deaf=False):
        super().__init__()
        self.deaf, self.notes = deaf, []

    def program(self, brief, concept, card, previous=None, feedback=None):
        if previous is not None:
            self.notes.append(list(feedback or []))
            if self.deaf or models.PROGRAM_TRUNCATED not in (feedback or []):
                raise ModelError("truncated", "16000 output tokens hit max_tokens=16000")
        return super().program(brief, concept, card, previous, feedback)


def test_a_cut_off_revision_gets_one_tighter_try():
    gen = CutOffReviser()
    r = generate(CABIN, GD, gen, FakeCritic(script=[{"all": 5, "fixes": ["add depth"]}, {"all": 8}]), n_concepts=1)
    h = r["critique"]["history"]
    assert h[1] == {"round": 1, "valid": False, "errors": ["generator: truncated: 16000 output tokens hit max_tokens=16000"]}
    assert h[2]["round"] == 1 and h[2]["valid"] and r["critique"]["status"] == "accepted"
    assert gen.notes == [["add depth"], ["add depth", models.PROGRAM_TRUNCATED]]   # the critic's fixes are kept
    gen = CutOffReviser(deaf=True)                                                    # cut off twice: the loop ends
    r = generate(CABIN, GD, gen, FakeCritic(script=[{"all": 5}, {"all": 8}]), n_concepts=1)
    assert len(gen.notes) == 2 and r["critique"]["rounds_used"] == 1 and r["critique"]["status"] == "needs_review"


def test_a_repair_that_repeats_the_program_is_told_so():
    """O87 (45L attempt 7, h02): one concept answered the same failing program three times."""
    from eve_design.generate import PROGRAM_REPEATED
    seen = []

    class Deaf(DeafGen):
        def program(self, brief, concept, card, previous=None, feedback=None):
            seen.append(list(feedback or []))
            return super().program(brief, concept, card)
    generate(CABIN, GD, Deaf(), None, n_concepts=1)
    assert seen[0] == [] and PROGRAM_REPEATED not in seen[1] and seen[2][-1] == PROGRAM_REPEATED


def test_thin_concepts_are_asked_once_more_and_the_reasoning_setting_reaches_them(monkeypatch):
    """O87 (45L attempt 7, h08: a 299-token concepts answer with no thinking, then nine invalid programs)."""
    monkeypatch.setenv("EVE_DEEPSEEK_PROGRAM_REASONING", '{"effort": "medium"}')
    full = "an L-shaped cabin with the kitchen and living room below, two bedrooms upstairs and a porch"
    thin = [{"name": "a", "summary": "a cabin", "footprint": [9, 9], "storeys": 2, "roof": "gable", "features": []}] * 2

    class Client:
        model = "stub-pro"

        def __init__(self, answers):
            self.answers, self.calls = list(answers), []

        def chat(self, job, messages, **kw):
            self.calls.append((messages, kw))
            return json.dumps(self.answers.pop(0))
    c = Client([thin, [dict(x, summary=full) for x in thin]])
    out = models.DeepSeekGenerator(c).concepts(CABIN, {})
    assert [x["summary"] for x in out] == [full, full] and len(c.calls) == 2
    assert c.calls[1][0][-1]["content"] == models.CONCEPTS_THIN and all(kw["reasoning"] == {"effort": "medium"}
                                                                      for _, kw in c.calls)
    c = Client([thin, thin])                                     # still thin: taken, the brief goes on
    assert models.DeepSeekGenerator(c).concepts(CABIN, {}) == thin and len(c.calls) == 2


def test_no_valid_design_says_why(tmp_path):
    r = generate(CABIN, GD, DeafGen(), FakeCritic(), out=tmp_path / "v1")
    assert "asks for a bedroom" in r["error"]


def test_program_calls_have_a_tiered_budget_and_a_cut_off_retries_tighter(monkeypatch):
    # 45L h07: a program call ended exactly at the 16000-token cap; the retry after a cut-off reasons low with less room
    from eve_design import models
    monkeypatch.delenv("EVE_DEEPSEEK_PROGRAM_REASONING", raising=False)

    class Client:
        model, calls = "stub-pro", []

        def chat(self, job, messages, **kw):
            self.calls.append(kw)
            return "```\nhut = Build(style=\"cozy_cabin\", seed=1)\n```"
    gen = models.DeepSeekGenerator(Client())
    gen.program(CABIN, {"name": "x"}, {})
    gen.program(CABIN, {"name": "x"}, {}, feedback=[models.PROGRAM_TRUNCATED])
    first, tight = Client.calls
    assert first["max_tokens"] == 16000 and "reasoning" not in first
    assert tight["max_tokens"] == 8000 and tight["reasoning"] == {"effort": "low"}


def test_the_programs_being_planned_are_on_disk_before_planning(monkeypatch, tmp_path):
    # h06 hung twice in _finish and its program died with the process: now it is in <folder>/finishing first
    import eve_design.generate as G

    def hang(rec, gd, brief):
        raise RuntimeError("stopped while planning")
    monkeypatch.setattr(G, "_finish", hang)
    with pytest.raises(RuntimeError):
        generate(CABIN, GD, FakeGenerator(), FakeCritic(script=[{"all": 8}] * 9), out=tmp_path / "v1")
    saved = list((tmp_path / "v1" / "finishing").glob("*.eve"))
    assert saved and all(p.read_text().strip() for p in saved)


def test_an_unbuildable_design_is_not_retried_under_another_concept(monkeypatch):
    # O68: three concepts gave the same design; each retry of it re-ran a 16-minute scaffold plan that had already failed
    import eve_design.generate as G
    tries = []

    def finish(rec, gd, brief):
        tries.append(rec["design_hash"])
        rec.update(valid=False, errors=["buildable: scaffold planning stopped at its work budget (1 simulated steps)"])
        return False
    monkeypatch.setattr(G, "_finish", finish)
    gen = FakeGenerator()
    one = gen.program(CABIN, gen.concepts(CABIN, {}, 1)[0], {})
    gen.program = lambda *a, **k: one
    r = generate(CABIN, GD, gen, FakeCritic(script=[{"all": 8}] * 9))
    assert r["status"] == "no_buildable_design" and len(tries) == 1 and "work budget" in r["error"]


def test_a_fallback_design_is_critiqued_before_it_is_chosen(monkeypatch, tmp_path):
    """O87 (45L h02): the best-scored design failed buildability and the fallback went out unscored (min score None)."""
    import eve_design.generate as G
    from eve_design import gate
    fast = G._finish

    def finish(rec, gd, brief):                  # the critiqued design cannot be built; the others can
        if rec.get("scores"):
            rec.update(valid=False, errors=["buildable: no stand"])
            return False
        return fast(rec, gd, brief)
    monkeypatch.setattr(G, "_finish", finish)
    r = generate(CABIN, GD, FakeGenerator(), FakeCritic(script=[{"all": 8}, {"all": 7}]), out=tmp_path / "v1")
    c, h = r["critique"], r["critique"]["history"][-1]
    assert h["round"] == "fallback" and h["design_hash"] == r["design_hash"] == c["design_hash"] != c["history"][0]["design_hash"]
    assert c["status"] == "accepted" and set(c["scores"].values()) == {7}
    reasons = gate.decide(tmp_path / "v1", {"critics": ["fake"], "selftest_dir": str(tmp_path)})["reasons"]
    assert not [x for x in reasons if x.startswith(("critique_", "critic_saw"))], reasons   # bound to the folder's previews
    r = generate(CABIN, GD, FakeGenerator(), FakeCritic(script=[{"all": 8}, None]))      # critic down: not accepted
    assert r["critique"]["status"] == "needs_review" and "critic" in r["critique"]["history"][-1]["errors"][0]


def test_critic_reports_the_median_of_three_runs(tmp_path):
    """45L attempt 3: one call per image set drifted 2.0 points on a repeat; the critic now answers with the median."""
    runs = [{c: s for c in ("brief", "style", "proportions", "depth_detail", "palette", "site")} for s in (5, 9, 6)]
    http = FakeHTTP([json.dumps({"scores": r, "fixes": [f"fix {i}"]}) for i, r in enumerate(runs)])
    critic = DeepSeekCritic(DeepSeekClient("deepseek/flash", enabled=True, key="k", cap=20, price=("1", "2"),
                                           ledger=ledger(tmp_path), opener=http))
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    out = critic.critique({}, {}, {"SE": img})
    assert http.calls == 3 and set(out["scores"].values()) == {6.0} and out["fixes"] == ["fix 2"]
    assert critic.version.endswith("+median3")


def test_a_defect_the_critic_lists_caps_its_category():
    from eve_design.models import _critique_shape
    s = {c: 7 for c in ("brief", "style", "proportions", "depth_detail", "palette", "site")}
    out = _critique_shape({"scores": s, "fixes": [], "defects": [{"kind": "hole", "where": "north wall"},
                                                                 {"kind": "floating", "where": "east"}, {"kind": "missing"}]})
    assert out["scores"]["depth_detail"] == 3 and out["scores"]["site"] == 3 and out["scores"]["palette"] == 7
    assert [d["kind"] for d in out["defects"]] == ["hole", "floating"]


def test_a_cut_off_critique_is_a_loud_error(tmp_path):
    """O34h: finish_reason length is an error the self-test reports, never retried or parsed."""
    def opener(req, timeout=None):
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{"}, "finish_reason": "length"}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 16000}}).encode())
    critic = DeepSeekCritic(DeepSeekClient("deepseek/flash", enabled=True, key="k", cap=20, price=("1", "2"),
                                           ledger=ledger(tmp_path), opener=opener))
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    with pytest.raises(ModelError) as e:
        critic.critique({}, {}, {"SE": img})
    assert e.value.code == "truncated"


def test_only_a_defect_most_runs_list_caps_the_median():
    """45L 945f4fe: one run in three listing a stray "hole" swung the base's depth_detail between 6 and 2."""
    from eve_design.models import _critique_shape, median_critique
    s = {c: 6 for c in ("brief", "style", "proportions", "depth_detail", "palette", "site")}
    stray = [_critique_shape({"scores": s, "fixes": [], "defects": [{"kind": "hole"}] if i == 0 else []}) for i in range(3)]
    assert median_critique(stray)["scores"]["depth_detail"] == 6
    real = [_critique_shape({"scores": s, "fixes": [], "defects": [{"kind": "hole"}] if i else []}) for i in range(3)]
    assert median_critique(real)["scores"]["depth_detail"] == 3


def test_every_call_records_its_usage_even_when_cut_off(tmp_path):
    def opener(req, timeout=None):
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{"}, "finish_reason": "length"}],
                                      "provider": "SomeHost",
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 100,
                                                "completion_tokens_details": {"reasoning_tokens": 95}, "cost": 0.01}}).encode())
    c = DeepSeekClient("deepseek/flash", enabled=True, key="k", cap=20, price=("1", "2"), ledger=ledger(tmp_path), opener=opener)
    with pytest.raises(ModelError):
        c.chat("critique", [{"role": "user", "content": "x"}], max_tokens=100)
    assert c.calls == [{"job": "critique", "finish_reason": "length", "prompt_tokens": 10, "completion_tokens": 100,
                        "max_tokens": 100, "usd": 0.01, "reasoning_tokens": 95, "provider": "SomeHost"}]


def test_an_empty_answer_gets_one_logged_retry(tmp_path):
    """O34r: 45L 36c8a7c had empty answers with finish stop (all reasoning); retried once and logged, never twice."""
    good = json.dumps({"defects": [], "scores": {c: 6 for c in ("brief", "style", "proportions", "depth_detail",
                                                                "palette", "site")}, "fixes": []})
    replies = ["", good]

    def opener(req, timeout=None):
        assert json.loads(req.data)["response_format"] == {"type": "json_object"}
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": replies.pop(0)}, "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode())
    critic = DeepSeekCritic(DeepSeekClient("deepseek/flash", enabled=True, key="k", cap=20, price=("1", "2"),
                                           ledger=ledger(tmp_path), opener=opener))
    critic.SAMPLES = 1
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    assert critic.critique({}, {}, {"SE": img})["scores"]["site"] == 6
    assert len(critic.client.calls) == 2 and "invalid" in critic.client.calls[0]


def test_job_cap_is_checked_before_every_call(tmp_path):
    """O87u (Hunter's money rule): run_jobs' job cap is checked before each provider call, with that call's worst case,
    so a brief cannot run past it by its own cost. A refused call is neither reserved nor sent."""
    led, http = ledger(tmp_path), FakeHTTP(["one", "two", "three"])
    c = DeepSeekClient("m", enabled=True, key="k", cap=20, price=("1", "2"), ledger=led, opener=http)
    c.job_cap = models.JobCap(0.0025, [c])          # each FakeHTTP call settles at US$0.002 (1000 in, 500 out)
    msg = [{"role": "user", "content": "hi"}]
    assert c.chat("t", msg, max_tokens=100) == "one"
    with pytest.raises(models.SpendCapReached):     # 0.002 spent + a worst case of 0.002004 would pass 0.0025
        c.chat("t", msg, max_tokens=1000)
    assert not isinstance(models.SpendCapReached("x"), ModelError)   # no step may take it for a failed call
    assert c.chat("t", msg, max_tokens=100) == "two"   # 0.002 + 0.000204 still fits
    with sqlite3.connect(led.path) as db:
        assert db.execute("SELECT count(*) FROM spend").fetchone()[0] == 2 and http.calls == 2


def test_a_spend_cap_mid_brief_stops_it_as_needs_review(tmp_path):
    """O87u: a call the job cap refuses ends the brief where it is: needs_review with reason cap, the candidates so far
    in candidates.json, no design folder."""
    class Capped(FakeGenerator):
        calls = 0

        def program(self, *a, **k):
            Capped.calls += 1
            if Capped.calls == 2:
                raise models.SpendCapReached("job spend would pass the job cap US$1")
            return super().program(*a, **k)
    r = generate(CABIN, GD, Capped(), FakeCritic(), out=tmp_path / "v1")
    assert (r["status"], r["reason"]) == ("needs_review", "cap") and r["error"].startswith("cap: job spend")
    assert len(r["candidates"]) == 1 and r["candidates"][0]["valid"]
    saved = json.loads((tmp_path / "v1" / "candidates.json").read_text())
    assert saved["reason"] == "cap" and not (tmp_path / "v1" / "design.json").exists()


def test_other_concepts_are_critiqued_before_revising():
    """O87w (45L attempt 8: only the first valid concept was ever scored; h10's second valid concept never was). When
    the first valid design is under the bar, the other concepts' valid designs are critiqued (up to 3 in all) and the
    best is revised; when it passes, nothing more is spent."""
    critic = FakeCritic(script=[{"all": 5, "fixes": ["more depth"]}, {"all": 8}])
    r = generate(CABIN, GD, FakeGenerator(), critic)
    crit = r["critique"]
    assert crit["status"] == "accepted" and crit["best_round"] == 0 and critic.calls == 2 and crit["rounds_used"] == 0
    first, second = crit["history"]
    assert second["concept"] == 1 and crit["design_hash"] == second["design_hash"] != first["design_hash"]
    passed = FakeCritic(script=[{"all": 8}])
    assert generate(CABIN, GD, FakeGenerator(), passed)["critique"]["status"] == "accepted" and passed.calls == 1


def test_the_revision_after_a_switch_works_on_that_concept():
    """O87w review: once another concept's design ranks best, revision gets that concept, its program and its fixes;
    a critic that fails on another concept stops the brief's critique there (no revision is paid for)."""
    class Recording(VaryingGen):
        def __init__(self):
            super().__init__()
            self.revisions = []

        def program(self, brief, concept, card, previous=None, feedback=None):
            if previous is not None:
                self.revisions.append((concept["name"], previous, list(feedback or [])))
            return super().program(brief, concept, card, previous, feedback)
    gen = Recording()
    script = [{"all": 5, "fixes": ["a"]}, {"all": 6, "fixes": ["b"]}, {"all": 5}, {"all": 8}]
    r = generate(CABIN, GD, gen, FakeCritic(script=script))
    second = next(c for c in r["candidates"] if c.get("concept") == 1 and c.get("valid"))
    assert gen.revisions == [("concept-2", second["program"], ["b"])]
    assert r["critique"]["status"] == "accepted" and r["critique"]["best_round"] == 1
    gen = Recording()
    r = generate(CABIN, GD, gen, FakeCritic(script=[{"all": 5}, None]))
    assert gen.revisions == [] and r["critique"]["status"] == "needs_review"
    assert r["critique"]["history"][-1]["errors"][0].startswith("critic: model_unavailable")
