"""S87 (session 4): O159 "chop 8 logs" refused break zone:home:break=task_only - an owner's area order now carries its
task grant (grant_region over the task_only part of her work box); O161 "craft the 8 logs into planks" -> "step 0:
craft target 7,69,-14 was not given in the request" - 2x2 crafts carry no target, a known station's target passes
the gate, and the line itself is a rule order. Fake sense / body, no model."""
import asyncio
import json

import pytest

from conftest import ROOT, envelope, mind_env, run, until

from eve_mind import workstations
from eve_mind.mind import area_grants
from eve_mind.planner import validate_plan

pytestmark = pytest.mark.skipif(not (ROOT / "data" / "game" / "26.3" / "recipe").exists(),
                                reason="BLOCKED: data/game/26.3 not extracted")
GROUND = {(x, y, z): ("minecraft:stone" if y < 64 else "minecraft:air")
          for x in range(-6, 7) for y in range(60, 68) for z in range(-6, 7)}


def actions(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


def events(e, kind):
    return [json.loads(p) for (p,) in e.mind.db.execute("SELECT payload FROM events WHERE kind=? ORDER BY rowid", (kind,))]


async def setup(e, inv, blocks=None):
    e.body.walk_s = 0.02
    e.body.inventory = [{"slot": i, "item": k, "count": n} for i, (k, n) in enumerate(inv.items())]
    await until(lambda: len((e.mind.fresh_state() or {}).get("inventory", [])) == len(inv), what="inventory")
    b = dict(GROUND if blocks is None else blocks)

    async def near(st, r, dy):
        return b
    e.mind.blocks_near = near
    return b


def test_o159_chop_8_logs_carries_its_task_grant(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:                  # envelope: home -32..32 is break/place task_only
            await setup(e, {})
            await e.mind.owner_text("chop 8 logs", "Silvershark3600")
            await until(lambda: actions(e) and events(e, "grant_region"), what="the grant")
            (skill, params), = actions(e)
            aid = e.mind.db.execute("SELECT action_id FROM actions").fetchone()[0]
            gs = events(e, "grant_region")
            assert skill in ("gather", "mine_resource") and all(g["action_id"] == aid for g in gs), (skill, gs)
            assert {g["effect"] for g in gs} == {"break"}, gs                # her skill breaks; it places nothing
            r = params.get("radius", 16)
            lo = {a: min(g["min"][a] for g in gs) for a in "xyz"}
            hi = {a: max(g["max"][a] for g in gs) for a in "xyz"}
            assert lo == {"x": -r, "y": 64 - r, "z": -r} and hi == {"x": r, "y": 64 + r, "z": r}, (lo, hi)
            assert not events(e, "grant_region.refused")
    run(go())


def test_o159_area_grants_only_task_only_cells():
    sk = {"name": "mine_resource", "effects": ["move", "break"],
          "params": {"properties": {"radius": {"default": 16}}}}
    st = {"pos": {"x": 30.5, "y": 64, "z": 0.5}, "dim": "minecraft:overworld"}
    env = {"zones": [{"id": "home", "dim": "minecraft:overworld", "min": {"x": -32, "y": -64, "z": -32},
                      "max": {"x": 32, "y": 319, "z": 32}, "break": "task_only", "place": "task_only"},
                     {"id": "wild", "dim": "minecraft:overworld", "min": {"x": 33, "y": -64, "z": -32},
                      "max": {"x": 99, "y": 319, "z": 32}, "break": "free", "place": "none"}]}
    g = area_grants(sk, {"count": 8}, st, env)
    assert g and all(e == "break" and m["x"] >= 14 and x["x"] <= 32 for e, m, x in g), g   # cut at home's edge
    assert area_grants({**sk, "name": "walk_to"}, {}, st, env) == []
    assert area_grants(sk, {"count": 8}, {**st, "dim": "minecraft:the_nether"}, env) == []


@pytest.mark.parametrize("line,inv,want", [
    ("craft the 8 logs into planks", {"minecraft:oak_log": 8}, [("craft", {"item": "minecraft:oak_planks", "count": 32})]),
    ("craft planks", {"minecraft:oak_log": 2}, [("craft", {"item": "minecraft:oak_planks", "count": 4})]),
    ("craft 4 sticks", {"minecraft:oak_planks": 2}, [("craft", {"item": "minecraft:stick", "count": 4})]),
])
def test_o161_2x2_crafts_carry_no_target(tmp_path, line, inv, want):
    async def go():
        async with mind_env(tmp_path) as e:
            said = []
            orig = e.mind.say

            async def say(t, *a, **k):
                said.append(t)
                return await orig(t, *a, **k)
            e.mind.say = say
            await setup(e, inv)
            await e.mind.owner_text(line, "Silvershark3600")
            await until(lambda: len(actions(e)) == len(want) and e.mind.open_action() is None, what="the craft")
            assert actions(e) == want, (actions(e), said)
            assert not any("safe plan" in t or "not given" in t for t in said), said
    run(go())


def test_o161_stone_pickaxe_at_the_table_in_reach(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            b = await setup(e, {"minecraft:cobblestone": 3, "minecraft:stick": 2})
            b[(2, 64, 0)] = "minecraft:crafting_table"
            await e.mind.owner_text("craft a stone pickaxe", "Silvershark3600")
            await until(lambda: len(actions(e)) == 1 and e.mind.open_action() is None, what="the craft")
            assert actions(e) == [("craft", {"item": "minecraft:stone_pickaxe", "count": 1,
                                             "pos": {"x": 2, "y": 64, "z": 0}})], actions(e)
    run(go())


def test_o161_planner_gate_blocks_guesses_not_knowledge(tmp_path):
    """The planner's plan for Hunter's line (a table position on the planks step) and a 3x3 craft: the 2x2 target is
    dropped, a target on a known table passes, an invented one is still refused."""
    async def go():
        async with mind_env(tmp_path) as e:
            b = await setup(e, {"minecraft:oak_log": 8, "minecraft:cobblestone": 3, "minecraft:stick": 2})
            b[(2, 64, 0)] = "minecraft:crafting_table"
            st, caps = e.mind.fresh_state(), e.mind.hub.links["body"].hello["caps"]

            def step(item, pos):
                return {"skill": "craft", "params": {"item": item, "count": 1, **({"pos": pos} if pos else {})},
                        "depends_on": []}

            async def judge(plan, line):
                stations = await workstations.plan_stations(e.mind, plan)
                errs = validate_plan(plan, e.mind.registry, caps, "autonomous", e.mind.env, st, request=line,
                                     known=stations, stations=stations)
                return [x for x in errs if "not given" in x or "not from the request" in x]
            line = "craft the 8 logs into planks"
            plan = {"steps": [step("minecraft:oak_planks", {"x": 7, "y": 69, "z": -14})], "question": None}
            assert await judge(plan, line) == [] and "pos" not in plan["steps"][0]["params"], plan
            plan = {"steps": [step("minecraft:stone_pickaxe", {"x": 2, "y": 64, "z": 0})], "question": None}
            assert await judge(plan, "craft a stone pickaxe") == [], plan
            b.pop((2, 64, 0))                                   # no table anywhere: the guess stays a guess
            plan = {"steps": [step("minecraft:stone_pickaxe", {"x": 7, "y": 69, "z": -14})], "question": None}
            assert await judge(plan, "craft a stone pickaxe"), plan
    run(go())


# --- O163 step 1 (ledger 4b922176: "mine 3 stone" -> not_found within 16) -----------------------------------------
def test_o163_mine_3_stone_radius_32_then_prospect(tmp_path):
    """not_found on stone -> skills' prospect (staircase, hazard stops; never straight down), its outcome said."""
    async def go():
        env = envelope()
        env["zones"] = [{**z, "place": "free"} for z in env["zones"]]   # O188: break stays task_only (O159), place free
        async with mind_env(tmp_path, env=env) as e:
            said = []
            orig = e.mind.say

            async def say(t, *a, **k):
                said.append(t)
                return await orig(t, *a, **k)
            e.mind.say = say
            await setup(e, {})
            e.body.result_override = {"mine_resource": ("failed", "not_found"), "prospect": ("failed", "hazard")}
            await e.mind.owner_text("mine 3 stone", "Silvershark3600")
            await until(lambda: len(actions(e)) == 2 and e.mind.open_action() is None, timeout=6, what="prospect")
            (m1, p1), (pr, pp) = actions(e)
            assert m1 == "mine_resource" and p1["radius"] == 32 and p1["block"] == "minecraft:stone", p1
            assert pr == "prospect" and pp == {"block": "minecraft:stone", "count": 3, "descend_to": 58, "max_blocks": 64,
                                               "minutes": 5, "torch_every": 0}, pp
            assert "No stone I can reach here - digging a staircase down for it." in said, said
            await until(lambda: "hazard" in said[-1] or "prospect" in said[-1], what="the prospect outcome said")
            await asyncio.sleep(0.3)
            assert len(actions(e)) == 2 and not any(s == "walk_to" for s, _ in actions(e)), actions(e)
            aid = e.mind.db.execute("SELECT action_id FROM actions WHERE skill='prospect'").fetchone()[0]
            assert any(g["action_id"] == aid and g["effect"] == "break" for g in events(e, "grant_region")), "O159 grant"
    run(go())


def test_o188_no_prospect_from_inside_a_zone_with_place_rules_or_beside_a_placed_block(tmp_path):
    """O188 (live: the stone fallback's prospect dug out through the shelter's west wall): standing in a zone whose
    place rule is not free (home), or beside a placed block, she says she needs open ground - no prospect is sent."""
    async def go(env, placed):
        async with mind_env(tmp_path / str(placed), env=env) as e:
            said = []
            orig = e.mind.say

            async def say(t, *a, **k):
                said.append(t)
                return await orig(t, *a, **k)
            e.mind.say = say
            await setup(e, {})
            if placed:
                e.mind.db.execute("INSERT INTO block_events (world_id, ts, dim, x, y, z, kind, after, actor) VALUES "
                                  "(?, 1, 'minecraft:overworld', 1, 64, 0, 'place', 'minecraft:oak_planks', 'Hunter')",
                                  (e.mind.world_id,))
            e.body.result_override = {"mine_resource": ("failed", "not_found")}
            await e.mind.owner_text("mine 3 stone", "Silvershark3600")
            if not placed:                                  # all of home is within 16: nowhere open - she asks
                await until(lambda: "I need open ground to dig - lead me out." in said, timeout=5, what="the refusal")
                await asyncio.sleep(0.3)
                assert [s for s, _ in actions(e)] == ["mine_resource"], actions(e)      # no prospect sent
                return
            await until(lambda: len(actions(e)) == 3, timeout=6, what="walk out, then prospect")   # O188 walk-out
            (m1, _), (w, wp), (pr, _) = actions(e)
            assert (m1, w, pr) == ("mine_resource", "walk_to", "prospect"), actions(e)
            assert max(abs(wp["pos"]["x"] - 1.5), abs(wp["pos"]["z"] - 0.5)) > 3, wp     # clear of the placed block
            assert "Stepping out onto open ground to dig." in said, said
    for env, placed in ((envelope(), False), (envelope(zones=[]), True)):              # home zone / a placed block
        (tmp_path / str(placed)).mkdir()
        run(go(env, placed))



# --- CP-86 run 2 (ledger ad0bae41): "mine 12 stone" from the shelter -> 8 stone exposed below a drop, unreachable
#     after 17 attempts, 0 mined; the fallback keyed only on not_found and never fired -----------------------------
@pytest.mark.parametrize("code,mined,want", [
    ("unreachable", 0, 12),      # the exact run-2 result
    ("partial", 0, 12),
    ("unreachable", 5, 7),       # digs for what is still missing
    ("partial", 5, None),        # some came: no dig, the usual partial line
])
def test_cp86_stone_fallback_on_unreachable_and_empty_partial(tmp_path, code, mined, want):
    async def go():
        env = envelope()
        env["zones"] = [{**z, "place": "free"} for z in env["zones"]]   # natural ground (O188 walk-out not needed)
        async with mind_env(tmp_path, env=env) as e:
            said = []
            orig = e.mind.say

            async def say(t, *a, **k):
                said.append(t)
                return await orig(t, *a, **k)
            e.mind.say = say
            await setup(e, {})
            e.body.evidence_extra = {"mine_resource": [{"mined": mined, "attempts": 17, "blocks": [], "drops": {}}]}
            e.body.result_override = {"mine_resource": ("failed", code), "prospect": ("ok", None)}
            await e.mind.owner_text("mine 12 stone", "Silvershark3600")
            n = 1 if want is None else 2
            await until(lambda: len(actions(e)) == n and e.mind.open_action() is None, timeout=6, what="the fallback")
            await asyncio.sleep(0.3)
            assert len(actions(e)) == n, actions(e)
            if want is None:
                assert not any("staircase" in t for t in said), said
                return
            (_m, _p), (pr, pp) = actions(e)
            assert pr == "prospect" and pp["block"] == "minecraft:stone" and pp["count"] == want and \
                pp["descend_to"] == 58 and pp["max_blocks"] == 64 and pp["minutes"] == 5, pp
            assert "No stone I can reach here - digging a staircase down for it." in said, said
            assert not any(t.startswith("I couldn't finish mine resource") for t in said), said   # one line, not two
    run(go())
