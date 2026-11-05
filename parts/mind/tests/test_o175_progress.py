"""O175 PROGRESSION MODE: "let's progress" says where they are and runs the next milestone's commands through the
owner path, milestone after milestone; "what do we need for X" only answers; a failure stops the run with a line; his
next order ends it. Plus the table's command forms: prospect / shear / smelt into charcoal."""
import asyncio
import json

import pytest

from conftest import envelope, mind_env, run, until

GROUND = {(x, y, z): ("minecraft:stone" if y < 64 else "minecraft:air")
          for x in range(-8, 9) for y in range(60, 68) for z in range(-8, 9)}


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def actions(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


def test_o175_lets_progress_runs_wood_then_planks_and_stops_on_a_failure(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.05

            async def near(st, r, dy):
                return GROUND
            e.mind.blocks_near = near
            await until(lambda: e.mind.fresh_state() is not None)
            r = await e.mind.owner_text("let's progress", "Hunter")
            assert r.get("progressing") == "wood", (r, said)
            await until(lambda: any(s == "gather" for s, _ in actions(e)), timeout=5, what="the wood command")
            e.body.inventory = [{"slot": 0, "item": "minecraft:oak_log", "count": 8}]     # the chop's logs
            await until(lambda: any(s == "craft" for s, _ in actions(e)), timeout=8, what="the planks command")
            assert any(t.startswith("Wood done.") for t in said), said
            e.body.result_override = {"craft": ("failed", "missing_ingredients")}
            await until(lambda: any(t.startswith("I stopped at") for t in said), timeout=8, what="the stop line")
    run(go())


def test_o175_what_do_we_need_answers_and_his_order_ends_a_run(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 2.0
            await until(lambda: e.mind.fresh_state() is not None)
            r = await e.mind.owner_text("what do we need for a diamond?", "Hunter")
            assert r.get("reply") and not actions(e), (r, actions(e))
            await e.mind.owner_text("let's progress", "Hunter")
            await until(lambda: e.mind.open_action() is not None, timeout=5)
            gen = e.mind.progress_gen
            await e.mind.owner_text("go to 3 64 3", "Hunter")                      # his order ends the run
            assert e.mind.progress_gen > gen
    run(go())


def test_o175_table_command_forms(tmp_path):
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:              # open ground: no walk-out
            e.body.walk_s = 0.05
            await until(lambda: e.mind.fresh_state() is not None)

            async def near(st, r, dy):
                return GROUND
            e.mind.blocks_near = near
            said = said_by(e)
            r = await e.mind.owner_text("prospect iron ore down to y 16", "Hunter")
            await asyncio.sleep(1.0)
            assert any(s == "prospect" for s, _ in actions(e)), (r, said, actions(e))
            p = [p for s, p in actions(e) if s == "prospect"][-1]
            assert p["tag"] == "minecraft:iron_ores" and p["descend_to"] == 16 and p["count"] == 11, p   # agenda f7288182: 11 iron
            await until(lambda: e.mind.open_action() is None)
            await e.mind.owner_text("shear 3 sheep", "Hunter")
            await until(lambda: any(s == "gather" and p.get("product") == "wool" for s, p in actions(e)), timeout=5)
    run(go())


def test_o214_a_reflex_interrupted_step_resumes_with_what_is_left(tmp_path):
    """CP-86 17:24:53: creeper_flee pre-empted "mine 12 stone" after 0 and the run stopped. Now: mine 12 -> interrupted
    after 3 -> held while the reflex runs -> mine 9 once it ends -> done, with no failure line."""
    from eve_mind import progress

    def mines(e):
        return [(a, json.loads(p)) for a, s, p in e.mind.db.execute(
            "SELECT action_id, skill, params FROM actions ORDER BY rowid") if s == "mine_resource"]

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.3
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.result_override = {"mine_resource": [("failed", "interrupted")]}
            e.body.evidence_extra = {"mine_resource": [{"mined": 3}]}
            e.body.reflex_active = "creeper_flee"
            t = asyncio.ensure_future(progress.run_command(e.mind, "mine 12 stone", "Hunter", "owner"))
            await until(lambda: bool(mines(e)) and e.mind.status_of(mines(e)[0][0]) == "failed", timeout=5)
            await asyncio.sleep(1.0)
            assert len(mines(e)) == 1, mines(e)                                    # held while the reflex runs
            e.body.reflex_active = None
            assert await asyncio.wait_for(t, 10)
            assert [p["count"] for _, p in mines(e)] == [12, 9], mines(e)
            assert not any("couldn't finish" in s for s in said), said
    run(go())


@pytest.mark.parametrize("sender,source", [("Hunter", "owner"), ("cli", "cli")])   # CP-86 run 3: eve ask = cli
def test_cp86_a_run_step_follows_the_dig_fallback(tmp_path, sender, source):
    """CP-86 run 2: "mine 12 stone" ended unreachable with 0 mined; dig_for's prospect carries the step, so the run waits
    for the prospect and counts the step done when it is, instead of "I stopped at cobblestone" while she digs."""
    from eve_mind import progress

    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:          # open ground: no walk-out
            said = said_by(e)
            e.body.walk_s = 0.3

            async def near(st, r, dy):
                return GROUND
            e.mind.blocks_near = near
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.result_override = {"mine_resource": [("failed", "unreachable")]}
            e.body.evidence_extra = {"mine_resource": [{"mined": 0}]}
            assert await asyncio.wait_for(progress.run_command(e.mind, "mine 12 stone", sender, source), 15)
            p = [p for s, p in actions(e) if s == "prospect"]
            assert p and p[-1]["count"] == 12 and p[-1]["block"] == "minecraft:stone", actions(e)
            assert not any(t.startswith("I couldn't finish") for t in said), said
    run(go())


def test_cp86_run4_a_failed_prospect_climbs_out_and_a_blocked_go_home_digs_its_way(tmp_path):
    """CP-86 run 4 (20:01:48-20:02:47): mine 12 stone (eve ask) unreachable, 0 mined -> the fallback prospect failed
    "pushed off the tunnel" 4 below its start -> go_home from the pit ended home_blocked. Now: dig_up back to the start
    cell first, and the blocked go_home becomes one walk to the shelter that may break and place."""
    from eve_mind import progress

    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            said = said_by(e)
            e.body.walk_s = 0.3

            async def near(st, r, dy):
                return GROUND
            e.mind.blocks_near = near
            await until(lambda: e.mind.fresh_state() is not None)
            start = dict(e.body.pos)
            e.body.result_override = {"mine_resource": [("failed", "unreachable")],
                                      "prospect": [("failed", "unreachable")], "go_home": [("failed", "home_blocked")]}
            e.body.evidence_extra = {"mine_resource": [{"mined": 0}]}
            t = asyncio.ensure_future(progress.run_command(e.mind, "mine 12 stone", "cli", "cli"))
            await until(lambda: any(s == "prospect" for s, _ in actions(e)), timeout=8, what="the fallback prospect")
            e.body.pos = {**start, "y": start["y"] - 4}                      # 4 down the staircase when it fails
            assert not await asyncio.wait_for(t, 10)                         # the step still ends unfinished
            await until(lambda: any(s == "dig_up" for s, _ in actions(e)), timeout=5, what="the climb out")
            up = [p for s, p in actions(e) if s == "dig_up"][-1]
            assert up["toward"] == {a: int(start[a] // 1) for a in "xyz"}, (up, start)
            assert "Climbing back out first." in said, said
            busy = await e.mind.do("go_home", {}, source="cli")               # mid-climb: the climb owns her
            assert busy.get("code") == "busy", busy
            await until(lambda: e.mind.climbing.done(), timeout=5, what="the climb's end")
            e.body.pos = {**start, "y": start["y"] - 4}                      # the fake did not move her up
            r = await e.mind.do("go_home", {}, source="cli", wait=5)          # the park's go_home from the pit
            assert r.get("code") == "home_blocked", r
            await until(lambda: any(s == "walk_to" and p.get("allow_break") for s, p in actions(e)), timeout=8,
                        what="the digging walk home")
            w = [p for s, p in actions(e) if s == "walk_to" and p.get("allow_break")][-1]
            home = e.mind.env["home"]["center"]
            assert w["pos"]["x"] == home["x"] + 0.5 and w["pos"]["z"] == home["z"] + 0.5, (w, home)
            assert "The way home is blocked - I'll dig my way there." in said, said
    run(go())


def test_cp86_the_fallback_digs_near_where_the_order_began(tmp_path):
    """CP-86 run 4: the mine wandered and left her 33 m west; the prospect dug there. Now the fallback walks back to
    the nearest natural cell within 16 of where the order began, then digs."""
    import math

    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            said = said_by(e)
            e.body.walk_s = 0.3

            async def near(st, r, dy):
                return GROUND
            e.mind.blocks_near = near
            await until(lambda: e.mind.fresh_state() is not None)
            began = dict(e.body.pos)
            e.body.result_override = {"mine_resource": [("failed", "unreachable")]}
            e.body.evidence_extra = {"mine_resource": [{"mined": 0}]}
            await e.mind.owner_text("mine 12 stone", "cli", source="cli")
            await until(lambda: any(s == "mine_resource" for s, _ in actions(e)), timeout=5)
            e.body.pos = {**began, "x": began["x"] - 30}                     # where the failed walks left her
            await until(lambda: any(s == "prospect" for s, _ in actions(e)), timeout=10, what="the prospect")
            assert "Going back near where you asked, to dig there." in said, said
            back = [p["pos"] for s, p in actions(e) if s == "walk_to"][-1]
            assert math.dist([back[a] for a in "xz"], [began[a] for a in "xz"]) <= 16, (back, began)
    run(go())
