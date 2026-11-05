"""CP-41 — only valid executable plans. The validator fixture is the K part (independent expected verdicts);
the adapter cases prove the schema lock, bounded waits and that model output never bypasses the gates.
A live-model case runs only when the configured planner model answers (EVE_PLANNER_MODEL, default qwen3:8b)."""
import asyncio
import json
import time
from pathlib import Path

import pytest

from conftest import count, envelope, free_port, mind_env, run, until
from eve_mind.config import ROOT
from eve_mind.planner import EFFECTFUL, DeepThinker, Planner, validate_plan
from eve_mind.registry import Registry
from fake_http import FakeHTTP

REG = Registry(ROOT / "contracts" / "skills" / "registry.json")
CAPS = [{"skill": n, "version": s["version"]} for n, s in REG.skills.items()]
ENV = envelope()
ENV["containers"] = {"default_tag": "private", "tags": {"minecraft:overworld:1,64,2": "shared"}}   # one shared chest
# example envelope: home zone x/z -32..32 is task_only; the mine zone x 64..192, y -64..60, z -64..64 is free
STATE = {"pos": {"x": 0.5, "y": 64, "z": 0.5}, "dim": "minecraft:overworld", "health": 20,
         "inventory": [{"slot": 0, "item": "minecraft:cobblestone", "count": 2}],
         "nearby_entities": [{"id": 7, "type": "minecraft:zombie", "hostile": True, "protected": False},
                             {"id": 8, "type": "minecraft:villager", "hostile": False, "protected": True}]}


def plan(*steps, question=None):
    return {"steps": [{"skill": s, "params": p, "depends_on": d} for s, p, d in steps], "question": question}


P = lambda x, y, z: {"x": x, "y": y, "z": z}  # noqa: E731
VALID = {
    "walk": plan(("walk_to", {"pos": P(10, 64, -5)}, [])),
    "walk_then_look": plan(("walk_to", {"pos": P(3, 64, 3)}, []), ("look_at", {"pos": P(5, 65, 5)}, [0])),
    "hotbar": plan(("select_hotbar", {"slot": 0}, [])),
    "sneak_jump": plan(("sneak", {"on": True}, []), ("jump", {}, [])),
    "say": plan(("chat_say", {"text": "hello"}, [])),
    "honest_refusal": plan(question="I have no skill for flying."),
    "place_owned": plan(("place_block", {"pos": P(70, 50, 1), "item": "minecraft:cobblestone"}, [])),
    "place_two_owned": plan(("place_block", {"pos": P(70, 50, 1), "item": "minecraft:cobblestone"}, []),
                            ("place_block", {"pos": P(71, 50, 1), "item": "minecraft:cobblestone"}, [0])),
    "mine": plan(("mine_block", {"pos": P(70, 49, 3), "collect": False}, [])),
    "home": plan(("go_home", {}, [])),
    "open": plan(("open_container", {"pos": P(1, 64, 2)}, [])),
    "walk_tolerance": plan(("walk_to", {"pos": P(20, 70, 20), "tolerance": 2, "allow_break": False}, [])),
    "attack_observed_hostile": plan(("look_at", {"entity_id": 7}, []), ("attack_entity", {"entity_id": 7}, [0])),
}
SAID = {   # what the owner said for each valid plan: coordinates in a plan must come from here or observation
    "walk": "walk to 10 64 -5", "walk_then_look": "go to 3 64 3 and look at 5 65 5",
    "place_owned": "place cobblestone at 70 50 1", "place_two_owned": "cobblestone at 70 50 1 and 71 50 1",
    "mine": "mine 70 49 3 but leave the drop", "open": "open the chest at 1 64 2",
    "attack_observed_hostile": "look at the zombie, entity 7, and hit it",
    "walk_tolerance": "walk to 20 70 20 roughly",
}
INVALID = {
    "invented_coordinates": (plan(("mine_block", {"pos": P(5, 60, 5)}, [])),
                             "is not from the request or observed state"),
    "invented_skill": (plan(("teleport", {"pos": P(0, 64, 0)}, [])), "plan:"),
    "extra_param": (plan(("walk_to", {"pos": P(1, 64, 1), "speed": 3}, [])), "Additional properties"),
    "missing_param": (plan(("place_block", {"pos": P(1, 64, 1)}, [])), "'item' is a required property"),
    "y_out_of_range": (plan(("walk_to", {"pos": P(1, 400, 1)}, [])), "outside -64..320"),
    "beyond_explore_radius": (plan(("walk_to", {"pos": P(999999, 64, 0)}, [])), "explore_radius"),
    "forward_dependency": (plan(("jump", {}, [1]), ("jump", {}, [])), "depends_on must name earlier steps"),
    "self_dependency": (plan(("jump", {}, [0])), "depends_on must name earlier steps"),
    "missing_prerequisite": (plan(("place_block", {"pos": P(1, 64, 1), "item": "minecraft:stone"}, [])),
                             "prerequisite: 1 x minecraft:stone needed, 0 in inventory"),
    "too_many_steps": (plan(*[("jump", {}, [])] * 9), "plan:"),
    "missing_question_key": ({"steps": []}, "plan:"),
    "extra_top_level_key": ({"steps": [], "question": None, "run": "rm -rf"}, "plan:"),
    "params_not_object": (plan(("jump", "now", [])), "plan:"),
    "float_block_pos": (plan(("mine_block", {"pos": P(3.5, 63, 3)}, [])), "is not of type 'integer'"),
    "bad_item_id": (plan(("place_block", {"pos": P(1, 64, 1), "item": "Cobblestone!"}, [])), "does not match"),
    "not_in_caps": (plan(("mine_block", {"pos": P(3, 63, 3)}, [])), "not implemented by the body"),
    # 45L CP-41 live attempt 1: effectful steps the owner never asked for, and effects the envelope forbids
    "unasked_attack": (plan(("attack_entity", {"entity_id": 7}, [])), "which the request does not name"),
    "unasked_mine_elsewhere": (plan(("mine_block", {"pos": P(70, 49, 0)}, [])), "was not given in the request"),
    "unasked_use": (plan(("use_item", {"hold_ms": 500}, [])), "asks for no use/eat"),
    "private_container": (plan(("open_container", {"pos": P(1, 64, 1)}, [])), "envelope denies container"),
    "plan_breaks_in_task_only_zone": (plan(("mine_block", {"pos": P(1, 64, 1)}, [])), "home:break=task_only"),
    "place_outside_every_zone": (plan(("place_block", {"pos": P(40, 64, 40), "item": "minecraft:cobblestone"}, [])),
                                 "no_zone:place=none"),
    "invented_entity": (plan(("attack_entity", {"entity_id": 4242}, [])), "not in the observed nearby_entities"),
    "protected_entity": (plan(("attack_entity", {"entity_id": 8}, [])), "is protected"),
}


@pytest.mark.parametrize("name", list(VALID))
def test_valid_plan(name):
    assert validate_plan(VALID[name], REG, CAPS, "manual", ENV, STATE, request=SAID.get(name, "")) == []


@pytest.mark.parametrize("name", list(INVALID))
def test_invalid_plan(name):
    p, why = INVALID[name]
    caps = [c for c in CAPS if c["skill"] != "mine_block"] if name == "not_in_caps" else CAPS
    errs = validate_plan(p, REG, caps, "manual", ENV, STATE, request="craft a pickaxe, maybe at 1 64 1")
    assert errs and any(why in e for e in errs), errs


def test_manifest_coverage():
    assert len(VALID) >= 10 and len(INVALID) >= 10


# ---------------------------------------------------------------- adapter against a fake Ollama
def with_fake(fn, **cfg):
    def go(tmp_path):
        async def inner():
            fake = FakeHTTP()
            try:
                async with mind_env(tmp_path, **cfg) as e:
                    e.mind.planner = Planner(url=fake.url, model="qwen3:8b", timeout_s=cfg.get("planner_timeout_s", 2))
                    await fn(e, fake)
            finally:
                fake.close()
        run(inner())
    return go


def test_schema_enum_is_registry_and_caps(tmp_path):
    async def fn(e, fake):
        fake.ollama(VALID["walk"])
        r = await e.mind.plan_and_run("walk to 10 64 -5", "cli", step_wait=3)
        assert r["ok"] and r["results"][0]["status"] == "ok" and e.body.pos == P(10, 64, -5)
        body = fake.requests[-1]["body"]
        enum = [a["properties"]["skill"]["const"] for a in body["format"]["properties"]["steps"]["items"]["anyOf"]]
        from eve_mind.planner import RUNNER_ONLY, catalogue          # O94e: the pruned list, never a runner-only skill
        listed, _ = catalogue(REG, e.body.caps, e.mind.fresh_state(), "walk to 10 64 -5")
        assert enum == sorted(listed) and set(listed) <= set(REG.dispatchable(e.body.caps)) - set(RUNNER_ONLY)
        assert body["think"] is False
        assert "$ref" not in json.dumps(body["format"])
        assert body["options"]["temperature"] == 0 and body["messages"][1]["content"] == "walk to 10 64 -5"
        assert count(e.db, "SELECT count(*) FROM decisions WHERE kind='plan' AND validated=1") == 1
    with_fake(fn)(tmp_path)


def test_caps_shrink_the_enum(tmp_path):
    async def fn(e, fake):
        e.hub.links["body"].hello["caps"] = [c for c in e.body.caps if c["skill"] != "mine_block"]
        fake.ollama(VALID["home"])
        await e.mind.plan_and_run("go home", "cli", step_wait=1)
        items = fake.requests[-1]["body"]["format"]["properties"]["steps"]["items"]["anyOf"]
        assert "mine_block" not in [a["properties"]["skill"]["const"] for a in items]
    with_fake(fn)(tmp_path)


def test_invented_skill_from_model_is_never_dispatched(tmp_path):
    async def fn(e, fake):
        fake.ollama(plan(("fly_to", {"pos": P(0, 200, 0)}, [])))
        r = await e.mind.plan_and_run("fly up", "cli")
        assert r["error"] == "invalid_plan" and sum(e.body.requests.values()) == 0
        assert count(e.db, "SELECT count(*) FROM decisions WHERE kind='plan' AND validated=0") == 1
    with_fake(fn)(tmp_path)


def test_malformed_model_output(tmp_path):
    async def fn(e, fake):
        fake.ollama("Sure! Here is your plan: walk north")
        r = await e.mind.plan_and_run("walk north", "cli")
        assert r["error"] == "malformed_output" and sum(e.body.requests.values()) == 0
    with_fake(fn)(tmp_path)


def test_incomplete_reply_is_a_failure(tmp_path):
    async def fn(e, fake):
        fake.reply = {"message": {"content": json.dumps(VALID["walk"])}, "done": False}
        r = await e.mind.plan_and_run("walk to 10 64 -5", "cli")
        assert r["error"] == "planner_incomplete" and sum(e.body.requests.values()) == 0
    with_fake(fn)(tmp_path)


def test_stalled_planner_is_bounded(tmp_path):
    async def fn(e, fake):
        fake.ollama(VALID["walk"])
        fake.delay_s = 3.0
        t0 = time.monotonic()
        r = await e.mind.plan_and_run("walk to 10 64 -5", "cli")
        assert r["error"] == "planner_timeout" and time.monotonic() - t0 < 1.5
        assert sum(e.body.requests.values()) == 0
    with_fake(fn, planner_timeout_s=0.5)(tmp_path)


def test_planner_absent(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.mind.planner = Planner(url=f"http://127.0.0.1:{free_port()}", model="qwen3:8b", timeout_s=8)
            assert (await e.mind.plan_and_run("walk somewhere", "cli"))["error"] == "planner_unavailable"
    run(go())


def test_plan_arriving_after_stop_cannot_run(tmp_path):
    async def fn(e, fake):
        fake.ollama(VALID["walk"])
        fake.delay_s = 0.6
        task = asyncio.create_task(e.mind.plan_and_run("walk to 10 64 -5", "cli"))
        await asyncio.sleep(0.2)
        await e.mind.stop("cli", "changed my mind")
        await e.mind.resume("cli")                       # even after a resume, the old plan is dead
        r = await task
        assert r["error"] == "stale_plan" and sum(e.body.requests.values()) == 0   # Stage B: dropped before do()
    with_fake(fn)(tmp_path)


def test_trusted_chat_goes_rules_then_planner(tmp_path):
    async def fn(e, fake):
        fake.ollama(plan(question="I can't craft yet."))
        await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": "make me a castle"})
        await e.body.wait_for(lambda m: m["type"] == "chat.send" and "craft" in m["payload"]["text"])
        await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": "walk to 2 64 2"})
        await until(lambda: e.body.pos == P(2, 64, 2), timeout=3)
        assert len(fake.requests) == 1                   # the walk was handled by rules, not the model
    with_fake(fn)(tmp_path)


# ---------------------------------------------------------------- deep thinker
M = {"flash": "deepseek/flash-id-from-cp06", "pro": "deepseek/pro-id-from-cp06"}


def test_deepseek_off_until_switched_on(tmp_path):
    """S15: a key in the environment is not enough; EVE_DEEPSEEK_ENABLED=1 comes only after CP-06 passes."""
    async def go():
        async with mind_env(tmp_path) as e:
            prices = {M["flash"]: [1.0, 2.0]}
            for d in (DeepThinker(e.db, e.cfg.envelope, key="", models=M, prices=prices, enabled=True),
                      DeepThinker(e.db, e.cfg.envelope, key="k-not-real", models=M, prices=prices, enabled=False),
                      DeepThinker(e.db, e.cfg.envelope, key="k-not-real", models={}, prices=prices, enabled=True),
                      DeepThinker(e.db, e.cfg.envelope, key="k-not-real", models=M, prices={}, enabled=True),
                      DeepThinker(e.db, {"budgets": {}}, key="k-not-real", models=M, prices=prices, enabled=True)):
                assert not d.enabled
                assert (await d.ask("design", [{"role": "user", "content": "x"}]))["error"] == "deep_thinker_disabled"
            d = DeepThinker(e.db, e.cfg.envelope, key="k-not-real", models=M, prices=prices, enabled=True)
            assert (await d.ask("design", [{"role": "user", "content": "x"}], role="pro"))["error"] == "no_price"
            assert (await d.ask("design", [{"role": "user", "content": "x"}], role="huge"))["error"] == "no_model"
            assert count(e.db, "SELECT count(*) FROM spend") == 0
    run(go())


def test_openrouter_request_cost_and_cap(tmp_path):
    async def go():
        fake = FakeHTTP()
        fake.reply = {"choices": [{"message": {"content": "ok"}}],
                      "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.0042,
                                "prompt_tokens_details": {"cached_tokens": 200}}}
        try:
            async with mind_env(tmp_path) as e:
                d = DeepThinker(e.db, {"budgets": {"deepseek_monthly_usd": 20}}, key="test-key-not-real",
                                base_url=fake.url, models=M, prices={M["flash"]: [1.0, 2.0]}, enabled=True)
                r = await d.ask("review", [{"role": "user", "content": "hello"}], max_tokens=100)
                assert r["ok"] and r["usd"] == 0.0042 and r["cost_source"] == "openrouter" and r["model"] == M["flash"]
                req = fake.requests[0]
                assert req["path"] == "/chat/completions" and req["auth"] == "Bearer test-key-not-real"
                assert req["body"]["model"] == M["flash"] and req["body"]["usage"] == {"include": True}
                row = e.db.execute("SELECT provider, model, tokens_in, tokens_out, usd, cached FROM spend").fetchone()
                assert tuple(row) == ("openrouter", M["flash"], 1000, 500, 0.0042, 200)
                fake.reply = {"choices": [{"message": {"content": "ok"}}],
                              "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
                r = await d.ask("review", [{"role": "user", "content": "x"}], max_tokens=10)
                assert r["cost_source"] == "prices"
                e.db.execute("INSERT INTO spend (ts, provider, usd) VALUES (?, 'openrouter', 19.9999)",
                             (int(time.time() * 1000),))
                r = await d.ask("review", [{"role": "user", "content": "x"}], max_tokens=1000)
                assert r["error"] == "budget_exhausted" and len(fake.requests) == 2   # never sent
        finally:
            fake.close()
    run(go())


def test_deepseek_concurrent_calls_cannot_overshoot_the_cap(tmp_path):
    """Each call alone fits the remaining budget, two together would not: the lock makes the second see the first."""
    async def go():
        fake = FakeHTTP()
        fake.delay_s = 0.3
        fake.reply = {"choices": [{"message": {"content": "ok"}}],
                      "usage": {"prompt_tokens": 1000, "completion_tokens": 1000}}
        try:
            async with mind_env(tmp_path) as e:
                e.db.execute("INSERT INTO spend (ts, provider, usd) VALUES (?, 'openrouter', 19.994)",  # 0.006 left
                             (int(time.time() * 1000),))
                d = DeepThinker(e.db, {"budgets": {"deepseek_monthly_usd": 20}}, key="k-not-real", base_url=fake.url,
                                models=M, prices={M["flash"]: [2.0, 3.0]}, enabled=True)
                msgs = [{"role": "user", "content": "hi"}]
                r1, r2 = await asyncio.gather(d.ask("a", msgs, max_tokens=1000), d.ask("b", msgs, max_tokens=1000))
                assert sorted([r1.get("error"), r2.get("error")], key=str) == [None, "budget_exhausted"]
                assert len(fake.requests) == 1 and d.month_spend() <= 20
        finally:
            fake.close()
    run(go())


# ---------------------------------------------------------------- live model (skips without one)
REQUESTS = json.loads((Path(__file__).parent / "fixtures" / "cp41_requests.json").read_text("utf-8"))
LIVE = Planner(timeout_s=120)


@pytest.mark.ollama
@pytest.mark.skipif(not LIVE.available(), reason=f"{LIVE.model} not available at {LIVE.url}")
@pytest.mark.parametrize("group", ["feasible", "impossible", "misleading"])
def test_live_planner(group, tmp_path):
    rows = []

    async def go():
        for req in REQUESTS[group]:
            r = await LIVE.plan(req["text"], REG, CAPS, STATE, ENV)
            errs = (validate_plan(r["plan"], REG, CAPS, "manual", ENV, STATE, request=req["text"]) if r["ok"]
                    else [r["error"]])
            skills = [s["skill"] for s in r["plan"]["steps"]] if r["ok"] and not errs else []
            rows.append({"text": req["text"], "valid": not errs, "skills": skills, "errors": errs[:2],
                         "latency_ms": r.get("latency_ms"), "raw": r.get("raw", "")[:300]})
    run(go())
    out = Path(__file__).parent.parent / "var" / f"cp41-live-{LIVE.model.replace(':', '_')}-{group}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), "utf-8")
    for row, req in zip(rows, REQUESTS[group]):
        if group == "feasible":
            assert row["valid"] and row["skills"] == req["skills"], row
        else:   # oracle rev 2 (as tools/live_cp41.py): a validated plan may move, but changes the world only if allowed
            effectful = {s for s in row["skills"] if EFFECTFUL & set(REG.skills[s]["effects"])}
            assert effectful <= set(req.get("allowed", [])), row


def test_planner_refuses_a_prompt_ollama_truncated(monkeypatch):
    """Registry 0.15.0 pushed the catalogue past qwen3:8b's 4096-token context: Ollama silently cut the front of the
    prompt and the plan was not the planner's ("go back home" -> walk_to). planner-v3 sends one-line summaries, and a
    reply whose prompt_eval_count shows a cut prompt is refused, never acted on."""
    from eve_mind import planner as P
    reply = {"message": {"content": json.dumps({"steps": [{"skill": "walk_to", "params": {"pos": {"x": 0, "y": 64,
                                                                                              "z": 0}},
                                                       "depends_on": []}], "question": None})}, "done": True}
    seen = {}

    def post(url, body, timeout):
        seen["system"] = body["messages"][0]["content"]
        return {**reply, "prompt_eval_count": len(seen["system"]) // 8}          # ~8 chars/token: cut
    monkeypatch.setattr(P, "_post", post)
    r = run(P.Planner().plan("go back home", REG, CAPS, STATE, ENV))
    assert r["ok"] is False and r["error"] == "planner_truncated", r
    monkeypatch.setattr(P, "_post", lambda url, body, timeout: {**reply, "prompt_eval_count": len(
        body["messages"][0]["content"]) // 4})                                      # ~4 chars/token: whole
    assert run(P.Planner().plan("go back home", REG, CAPS, STATE, ENV))["ok"] is True
    assert all(len(line) < 1200 for line in seen["system"].splitlines())           # no paragraph summaries
    assert P.brief("One. Two.") == "One." and len(P.brief("x " * 200)) <= P.SUMMARY_MAX


def test_o86g_the_planner_catalogue_for_the_current_registry_stays_under_7000_tokens():
    """O86g: registry growth must fail here, in CI, not in game. Every registry skill (the worst case) in the planner-v3
    prompt, estimated at 3.6 chars/token (qwen3:8b read this prompt at 3.69 chars/token on 2026-10-21: 12352 chars ->
    3347 tokens, registry 0.15.0), stays under 7000 tokens of the 8192 every planner call asks for (O94e-shark; the
    prompt_eval_count truncation check stays the hard guard, also after a fallback to 4096)."""
    from eve_mind import planner as P
    lines = [f"- {n}: {P.brief(s['summary'])} params={json.dumps(s['params'], separators=(',', ':'))}"
             for n, s in sorted(REG.skills.items())]
    system = P.SYSTEM.format(defs=json.dumps(REG.raw["$defs"], separators=(",", ":")), skills="\n".join(lines),
                             state=P.state_summary(STATE, ENV))
    est = len(system) / 3.6
    assert est < 7000, f"planner prompt ~{est:.0f} tokens for registry {REG.version} ({len(REG.skills)} skills): trim " \
                       f"summaries/params or raise the context on every call (foundation's VRAM call)"


def test_o94e_every_planner_call_asks_for_8192_and_a_refusing_server_falls_back_to_4096(monkeypatch):
    """O94e-shark: num_ctx 8192 on every call (config planner_num_ctx); a server that refuses it (an HTTP error) gets
    one retry at 4096, sticky for the next calls, and the truncation guard still judges the reply."""
    import urllib.error

    from eve_mind import planner as P
    seen, refuse = [], [True]

    def post(url, body, timeout):
        seen.append(body["options"]["num_ctx"])
        if refuse[0] and body["options"]["num_ctx"] > 4096:
            raise urllib.error.HTTPError(url, 500, "model requires more memory", {}, None)
        return {"message": {"content": json.dumps({"steps": [], "question": "q"})}, "done": True,
                "prompt_eval_count": 3000}
    monkeypatch.setattr(P, "_post", post)
    pl = P.Planner(url="http://x", model="qwen3:8b", timeout_s=5)
    r = run(pl.plan("go home", REG, CAPS, STATE, ENV))
    assert seen == [8192, 4096] and r["num_ctx"] == 4096 and pl.num_ctx == 4096, (seen, r)
    seen.clear()
    run(pl.plan("go home", REG, CAPS, STATE, ENV))
    assert seen == [4096], seen                                                   # sticky
    seen.clear()
    refuse[0] = False
    ok = P.Planner(url="http://x", model="qwen3:8b", timeout_s=5)
    run(ok.plan("go home", REG, CAPS, STATE, ENV))
    assert seen == [8192], seen


def newest_registry():
    """The newest registry in contracts/skills (a draft included): CI fails when the registry outgrows the bar."""
    drafts = sorted((ROOT / "contracts" / "skills" / "drafts").glob("registry-*.json"),
                    key=lambda p: tuple(int(x) for x in p.stem.split("-")[1].split(".")))
    return Registry(drafts[-1]) if drafts else REG


WORST = {**STATE, "nearby_entities": [{"id": 1, "type": "minecraft:cow"}, {"id": 2, "type": "minecraft:sheep"},
                                      {"id": 3, "type": "minecraft:zombie", "hostile": True}]}


def test_o94e_worst_realistic_state_prompt_stays_under_3500():
    """O94e (c9acf833): with every state gate open (farm animals and a hostile near), the pruned planner-v3 prompt for
    the newest registry stays under 3500 estimated tokens (0.16.0 draft: ~3365); 7000 stays the context bar (O86g)."""
    from eve_mind import planner as P
    reg = newest_registry()
    caps = [{"skill": n, "version": s["version"]} for n, s in reg.skills.items()]
    skills, away = P.catalogue(reg, caps, WORST, "go home")
    lines = [f"- {n}: {P.brief(s['summary'])} params={json.dumps(s['params'], separators=(',', ':'))}"
             for n, s in sorted(skills.items())] + ([f"Not available now: {', '.join(away)}"] if away else [])
    system = P.SYSTEM.format(defs=json.dumps(reg.raw["$defs"], separators=(",", ":")), skills="\n".join(lines),
                             state=P.state_summary(WORST, ENV))
    est = len(system) / 3.6
    assert est < 3500, f"pruned planner prompt ~{est:.0f} tokens for registry {reg.version}: compact params next (O94e)"


def test_o94e_gated_skills_are_left_out_named_with_why_and_listed_when_asked():
    """O94e: runner-only skills never reach the planner's list; a skill do() would refuse on this state (no sheep near)
    is left out of the list and the format enum, named in the appendix with why, and listed when the request names it."""
    from eve_mind import planner as P
    reg = newest_registry()
    if "shear_sheep" not in reg.skills:
        pytest.skip("registry without shear_sheep")
    caps = [{"skill": n, "version": s["version"]} for n, s in reg.skills.items()]
    quiet = {**STATE, "nearby_entities": []}
    skills, away = P.catalogue(reg, caps, quiet, "go home")
    assert "shear_sheep" not in skills and "shear_sheep (no sheep near)" in away, away
    assert not set(P.RUNNER_ONLY) & set(skills)
    enum = {s["properties"]["skill"]["const"] for s in P.format_schema(skills, reg.raw["$defs"])["properties"]["steps"]
            ["items"]["anyOf"]}
    assert enum == set(skills)
    asked, _ = P.catalogue(reg, caps, quiet, "please shear the sheep")
    assert "shear_sheep" in asked and not set(P.RUNNER_ONLY) & set(asked)
    farm, away = P.catalogue(reg, caps, WORST, "go home")
    assert {"shear_sheep", "milk_cow", "fight_melee"} <= set(farm) and not away
