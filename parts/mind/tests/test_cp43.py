"""CP-43 (mind side) — the objective runner on fakes: a scripted goal (what the local model returns), a fake world that
models supplies, inventory and occupancy (tools/rehearse_cp43.model_world), and the five fixture variants' logic:
baseline, supply removed, blocked approach, urgent need, owner stop + continue. Plus the goal's grounding rules."""
import asyncio
import json

from conftest import envelope, mind_env, run, until
from eve_mind.planner import validate_plan
from rehearse_cp43 import COB, SPEC, model_world

M1, M2 = (dict(zip("xyz", SPEC["fixture"]["supplies"][k])) for k in ("M1", "M2"))
COL = [dict(zip("xyz", c)) for c in SPEC["fixture"]["target_column"]]
TEXT = SPEC["objective_text"]


def step(skill, **params):
    return {"skill": skill, "params": params, "depends_on": []}


def plan(*steps, question=None):
    return {"steps": list(steps), "question": question}


GOAL = plan(step("mine_block", pos=M1), step("mine_block", pos=M2),
            *[step("place_block", pos=c, item=COB) for c in COL])


class Script:
    """Stands in for the Ollama planner: returns the next scripted plan and keeps every request it saw."""
    def __init__(self, *plans):
        self.plans, self.requests = list(plans), []

    async def plan(self, text, registry, caps, state, env):
        self.requests.append(text)
        p = self.plans.pop(0) if self.plans else plan()
        return {"ok": True, "plan": p, "model": "script", "prompt_version": "test", "raw": "", "latency_ms": 1}


async def start(e, *plans, budget=None):
    e.mind.planner = Script(*plans)
    e.body.pos = dict(zip("xyz", SPEC["fixture"]["eve"]["pos"]))
    e.body.inventory = [{"slot": 0, "item": COB, "count": 1}, {"slot": 3, "item": "minecraft:golden_apple", "count": 2}]
    e.body.walk_s = 0.05
    world = model_world(e.body)
    await until(lambda: (e.mind.fresh_state() or {}).get("inventory"), timeout=3)
    r = await e.mind.cli("objective", {"text": TEXT, "budget": budget})
    assert r["ok"], r
    return r["objective_id"], world


async def finished(e, oid, timeout=15):
    await until(lambda: e.mind.objectives.get(oid).status != "running", timeout=timeout)
    return await e.mind.cli("objective", {"status": oid})


def column(world):
    return [world.get(tuple(c.values())) == COB for c in COL]


def test_goal_grounding_chained_only_from_stated_blocks():
    from eve_mind.config import ROOT
    from eve_mind.registry import Registry
    reg = Registry(ROOT / "contracts" / "skills" / "registry.json")
    caps = [{"skill": n, "version": s["version"]} for n, s in reg.skills.items()]
    st = {"pos": dict(zip("xyz", SPEC["fixture"]["eve"]["pos"])), "dim": "minecraft:overworld",
          "inventory": [{"slot": 0, "item": COB, "count": 3}], "nearby_entities": []}
    env = envelope()
    assert validate_plan(GOAL, reg, caps, "manual", env, st, request=TEXT, chain_place=True) == []
    assert any("not given in the request" in x for x in validate_plan(GOAL, reg, caps, "manual", env, st, request=TEXT))
    gap = plan(step("place_block", pos=COL[0], item=COB), step("place_block", pos=COL[2], item=COB))
    assert validate_plan(gap, reg, caps, "manual", env, st, request=TEXT, chain_place=True)    # gaps are not chained


def test_v1_baseline_gathers_then_builds_bottom_up(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            oid, world = await start(e, GOAL)
            s = await finished(e, oid)
            assert s["status"] == "done" and column(world) == [True] * 3, s["report"]
            assert s["report"].startswith("done: 3/3 placed (96 49 -43, 96 50 -43, 96 51 -43); mined 2")
            kinds = [x["skill"] for x in s["ledger"] if x["skill"] != "walk_to"]
            assert kinds == ["mine_block", "mine_block", "place_block", "place_block", "place_block"]
            assert len(e.mind.planner.requests) == 1                    # understood once, then deterministic
    run(go())


def test_v2_supply_removed_partial_without_inventing_a_source(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            oid, world = await start(e, GOAL)
            await until(lambda: any(x["skill"] == "mine_block" for x in e.mind.objectives.get(oid).ledger))
            world.pop(tuple(M2.values()), None)                         # the fault: M2 disappears
            s = await finished(e, oid)
            assert s["status"] == "partial" and "short 1" in s["reason"] and "not invent" in s["reason"]
            assert column(world) == [True, True, False], s["report"]
    run(go())


def test_v3_blocked_approach_tries_another_spot_never_the_failed_walk(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.user_override = {"walk_to": [("failed", "unreachable")]}     # the first approach is walled off
            oid, world = await start(e, GOAL)
            s = await finished(e, oid)
            o = e.mind.objectives.get(oid)
            assert s["status"] == "done" and column(world) == [True] * 3
            assert len(o.failed) == 1 and o.failed[0]["code"] == "unreachable" and s["replans"] == 1
            # a retry of the same walk is allowed only from another situation (she was moved), never from the same one
            f = o.failed[0]
            assert not o.failed_before("walk_to", json.loads(f["key"][1]), {"pos": dict(zip("xyz", f["at"][:3])),
                                                                             "inventory_hash": "other"})
    run(go())


def test_v4_urgent_need_pauses_then_resumes_without_redoing(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 0.3
            oid, world = await start(e, GOAL)
            e.body.walk_s = 0.3
            await until(lambda: any(x["skill"] == "place_block" for x in e.mind.objectives.get(oid).ledger), timeout=10)
            e.body.food = 2
            await until(lambda: "paused for food" in e.mind.objectives.get(oid).notes, timeout=5)
            e.body.food = 20                                            # the body's reflex ate
            s = await finished(e, oid)
            assert s["status"] == "done" and column(world) == [True] * 3 and "resumed from the completed work" in s["report"]
            done = [x["done"] for x in e.mind.objectives.get(oid).ledger if x["skill"] != "walk_to"]
            assert len(done) == len(set(done)) == 5                     # nothing redone
    run(go())


def test_v5_owner_stop_then_continue_redoes_nothing(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            oid, world = await start(e, GOAL)
            e.body.walk_s = 0.3
            await until(lambda: sum(x["skill"] == "place_block" for x in e.mind.objectives.get(oid).ledger) >= 2,
                        timeout=10)
            await e.mind.stop("cli", "owner stop")
            s = await finished(e, oid)
            assert s["status"] == "stopped" and s["report"].startswith("stopped: 2/3 placed"), s["report"]
            await e.mind.resume("cli")
            await asyncio.sleep(0.5)
            assert e.mind.objectives.get(oid).status == "stopped"          # a resume never continues by itself
            assert (await e.mind.cli("objective", {"continue": oid}))["ok"]
            s = await finished(e, oid)
            assert s["status"] == "done" and column(world) == [True] * 3
            assert sum(x["skill"] == "place_block" for x in s["ledger"]) == 3   # the first two not re-placed
    run(go())


def test_ungrounded_goal_is_never_executed(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            bad = plan(step("place_block", pos={"x": 120, "y": 50, "z": 0}, item=COB))    # not in the objective
            oid, world = await start(e, bad, bad, bad)
            s = await finished(e, oid)
            assert s["status"] == "partial" and "could not understand" in s["reason"] and s["actions"] == 0
    run(go())


def test_budget_ends_partial(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            oid, world = await start(e, GOAL, budget={"max_actions": 3})
            s = await finished(e, oid)
            assert s["status"] == "partial" and s["reason"] == "budget exhausted" and s["actions"] <= 3
    run(go())


def test_goal_shifted_by_one_block_is_rejected(tmp_path):
    """lane-B dev smoke: the model's goal started one block above the stated base (96 50 -43) and passed the plan
    validator's one-block grounding; goal blocks must be exactly stated or touch an earlier goal block."""
    async def go():
        async with mind_env(tmp_path) as e:
            up = [dict(c, y=c["y"] + 1) for c in COL]
            shifted = plan(step("mine_block", pos=M1), *[step("place_block", pos=c, item=COB) for c in up])
            oid, world = await start(e, shifted, shifted, shifted)
            s = await finished(e, oid)
            assert s["status"] == "partial" and "could not understand" in s["reason"] and s["actions"] == 0
            assert "neither a position the objective states" in s["reason"]
    run(go())
