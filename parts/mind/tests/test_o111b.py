"""O111-b (45L live owner session 2026-10-22): her question takes his next line as the answer; bare numbers are never
facts; questions are never orders or capability "yes"; state questions come from the state frame; spoken resource
names become real block ids / tags (unknown -> one question); no reply waits on the fact extractor."""
import asyncio
import inspect
import time

import pytest

from conftest import count, mind_env, run
from eve_mind import dialogue, memory, mind as mind_mod
from eve_mind.planner import Planner, gather_order, normalize_plan, resource_params


@pytest.fixture(autouse=True)
def no_planner(monkeypatch):
    """These lines must never reach the planner model; a call fails the line visibly instead of using Ollama."""
    async def refused(self, *a, **k):
        return {"ok": False, "error": "planner_unavailable", "detail": "test stub", "model": "stub",
                "prompt_version": "stub"}
    monkeypatch.setattr(Planner, "plan", refused)


async def hear(e, text, sender="Hunter"):
    q = len(e.body.received)
    await e.sense.send("owner.chat", {"sender": sender, "trusted": True, "text": text})
    m = await e.body.wait_for(lambda m: m["type"] == "chat.send", start=q)
    return m["payload"]["text"]


def acts(e, skill):
    return [dict(r) for r in e.db.execute("SELECT * FROM actions WHERE skill=?", (skill,))]


def facts(e):
    return count(e.db, "SELECT count(*) FROM facts WHERE source IN ('owner_chat', 'correction')")


def test_ask_consumes_coordinates_number_name_and_yes(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            assert "x y z" in await hear(e, "look at that")                 # her question...
            await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": "1 65 1"})
            await asyncio.sleep(0.5)                                         # ...his bare answer runs the order
            a = acts(e, "look_at")
            assert len(a) == 1 and '"x": 1' in a[0]["params"] and '"z": 1' in a[0]["params"]
            assert "1 to 9" in await hear(e, "select a hotbar slot")
            await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": "3"})
            await asyncio.sleep(0.5)
            a = acts(e, "select_hotbar")
            assert len(a) == 1 and '"slot": 2' in a[0]["params"]
            assert "netherite" in await hear(e, "mine 4 netherite")          # unknown resource -> one question
            assert (await hear(e, "oak logs")).startswith("On it: 4 oak log")   # a name answers it
            await asyncio.sleep(0.3)
            a = acts(e, "mine_resource")
            assert len(a) == 1 and "minecraft:oak_log" in a[0]["params"] and '"count": 4' in a[0]["params"]
            assert "x y z" in await hear(e, "look at that")
            assert await hear(e, "yes") == "OK."                            # yes/no consumed, not filed
            assert facts(e) == 0
    run(go())


def test_choice_answer_stores_the_named_one(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            async def two(text, known):
                return {"ok": True, "facts": [
                    {"world_specific": True, "subject": "shed", "predicate": "location", "object": o,
                     "certain": True, "corrects": False} for o in ("10 64 20", "12 64 20")]}
            e.mind.extractor = two
            assert (await hear(e, "the shed is at 10 64 20, no wait, 12 64 20")).startswith("Which is right for shed")
            e.mind.extractor = None
            assert "12 64 20" in await hear(e, "12 64 20")
            assert [r[0] for r in e.db.execute("SELECT object FROM facts WHERE superseded_by IS NULL")] == ["12 64 20"]
    run(go())


def test_bare_numbers_are_never_facts_or_orders(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            for line in ("5 100 -40", "-19 112 -74", "42"):
                assert (await hear(e, line)).startswith("What should I do")
            assert facts(e) == 0 and sum(e.body.requests.values()) == 0
            assert await e.mind.facts_from_words("-19 112 -73") is None
    run(go())


def test_questions_are_never_orders_or_capability_yes(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            for line in ("you don't have bread on you?", "do you have bread", "can you walk to 1 64 1",
                         "could you mine 8 logs"):
                assert "Yes, I can do that" not in await hear(e, line)
            assert sum(e.body.requests.values()) == 0
            ctx = dialogue.context(e.mind)
            it = {"intent": "capability_question", "skill": "use_item", "topic": "other", "subject": "bread",
                  "item": "bread", "who": ""}
            assert dialogue.resolve(it, "you don't have bread on you?", ctx) == (None, True)
            assert dialogue.resolve(it, "can you eat bread?", ctx)[0] == ["Yes, I can do that (use_item)."]
            assert dialogue.resolve({**it, "intent": "command"}, "do you eat bread", ctx) == (None, True)
    run(go())


def test_resource_names_normalised_before_the_validator():
    assert resource_params("logs") == resource_params("minecraft:log") == {"tag": "minecraft:logs"}
    assert resource_params("wood") == {"tag": "minecraft:logs"}
    assert resource_params("oak logs") == {"block": "minecraft:oak_log"}
    assert resource_params("stone") == resource_params("cobblestone") == {"block": "minecraft:stone"}
    assert resource_params("dirt") == {"block": "minecraft:dirt"} and resource_params("sand") == {"block": "minecraft:sand"}
    assert resource_params("iron ore") == resource_params("minecraft:iron_ore") == {"tag": "minecraft:iron_ores"}
    assert resource_params("diamonds") == {"tag": "minecraft:diamond_ores"}
    assert resource_params("ore") is None and resource_params("netherite") is None
    step = lambda b: {"steps": [{"skill": "mine_resource", "params": {"block": b, "count": 8, "radius": 16},  # noqa
                                 "depends_on": []}]}
    assert normalize_plan(step("minecraft:log"))["steps"][0]["params"] == {"tag": "minecraft:logs", "count": 8,
                                                                           "radius": 16}
    asked = normalize_plan(step("minecraft:ore"))
    assert asked["steps"] == [] and asked["question"].startswith("Which block do you mean by 'ore'")
    assert normalize_plan(step("minecraft:deepslate"))["steps"][0]["params"]["block"] == "minecraft:deepslate"
    assert gather_order("chop 8 logs") == {"skill": "mine_resource", "params": {"tag": "minecraft:logs", "count": 8}}
    assert gather_order("collect 20 cobblestone please")["params"] == {"block": "minecraft:stone", "count": 20}
    assert gather_order("mine 4 netherite")["ask"] == {"kind": "resource", "count": 4}
    for not_gather in ("get me a pickaxe", "mine that block", "dig a hole", "mine 2 3 4", "walk to 1 2 3"):
        assert gather_order(not_gather) is None, not_gather


def test_gather_orders_dispatch_by_rule_with_a_reply(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            assert await hear(e, "mine 5 coal") == "On it: 5 coal ores."
            await asyncio.sleep(0.3)
            a = acts(e, "mine_resource")
            assert len(a) == 1 and '"tag": "minecraft:coal_ores"' in a[0]["params"] and '"count": 5' in a[0]["params"]
            await hear(e, "cancel")
            await asyncio.sleep(0.3)
            await hear(e, "chop 8 logs")                 # wood stays eve-mind's rule: gather {product: wood}
            await asyncio.sleep(0.3)
            assert len(acts(e, "gather")) == 1
    run(go())


def test_no_reply_waits_on_the_extractor(tmp_path, monkeypatch):
    assert inspect.signature(memory.extract_facts).parameters["timeout"].default <= 10
    assert mind_mod.EXTRACT_TIMEOUT_S <= 10

    async def go():
        async with mind_env(tmp_path) as e:
            async def stall(text, known):
                await asyncio.sleep(30)
            e.mind.extractor = stall
            t0 = time.monotonic()
            assert await hear(e, "look at 1 65 1")                          # a rules-mapped order: at once
            assert (await hear(e, "mine 5 coal")).startswith("On it")        # a rules-mapped order: at once
            assert time.monotonic() - t0 < 2
            monkeypatch.setattr(mind_mod, "EXTRACT_TIMEOUT_S", 0.2)
            t0 = time.monotonic()
            await e.mind.facts_from_words("the shed is by the lake")        # a stalled extractor is cut off
            assert time.monotonic() - t0 < 1
    run(go())


def test_forget_retires_one_false_fact(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            subj = "container minecraft:overworld:-33,-22,-102"
            r = e.mind.memory.remember(e.mind.world_id, subj, "location", "-19 112 -73", "owner_chat")
            fid = r["fact_id"]
            assert not (await e.mind.cli("forget", {"fact_id": fid[:8], "subject": subj, "predicate": "status"}))["ok"]
            r = await e.mind.cli("forget", {"fact_id": fid[:8], "subject": subj, "predicate": "location"})
            assert r["ok"] and e.mind.memory.current(e.mind.world_id, subj, "location") is None
            assert count(e.db, "SELECT count(*) FROM facts WHERE fact_id=? AND superseded_by='retracted'", fid) == 1
    run(go())
