"""S93 (no hardware): the progression past the furnace, one milestone at a time. Each case is a realistic inventory frame
for which agenda's table names that milestone; "let's progress to <it>" runs its commands through the owner path, and
the dispatched skills are checked. Charcoal, iron, smelting and food use her own carried furnace when none is placed."""
import asyncio
import json

import pytest

from conftest import envelope, mind_env, run, until

GROUND = {(x, y, z): ("minecraft:stone" if y < 64 else "minecraft:air")
          for x in range(-8, 9) for y in range(60, 68) for z in range(-8, 9)}
KIT = {"stone_pickaxe": 1, "crafting_table": 1, "furnace": 1}       # what she carries once the furnace milestone is done
LATE = {**KIT, "iron_pickaxe": 1, "shield": 2, "cooked_beef": 8, "white_bed": 1, "torch": 16}
COWS = [{"id": 50 + i, "type": "minecraft:cow", "hostile": False, "baby": False,
         "pos": {"x": 3.0 + i, "y": 64.0, "z": 2.0}, "distance": 3.5 + i} for i in range(3)]


def inv(items):
    return [{"slot": i, "item": "minecraft:" + k, "count": n} for i, (k, n) in enumerate(items.items())]


CASES = [   # milestone, held items, nearby, the skills it dispatches in order (with a key param check)
    ("fuel", dict(KIT), [], [("mine_resource", {"tag": "minecraft:coal_ores", "count": 8}),
                             ("prospect", {"tag": "minecraft:coal_ores", "descend_to": 48})]),   # agenda f7288182
    ("raw_iron", {**KIT, "charcoal": 4}, [], [("mine_resource", {"tag": "minecraft:iron_ores", "count": 11}),
                                              ("prospect", {"tag": "minecraft:iron_ores", "descend_to": 16})]),
    ("iron_ingots", {**KIT, "charcoal": 4, "raw_iron": 11}, [], [("place_block", {"item": "minecraft:furnace"}),
                                                                ("smelt", {"item": "minecraft:iron_ingot", "count": 11})]),
    ("iron_pickaxe", {**KIT, "charcoal": 4, "iron_ingot": 8, "stick": 4}, [],
     [("craft", {"item": "minecraft:iron_pickaxe"})]),
    ("shield", {**KIT, "iron_pickaxe": 1, "iron_ingot": 5, "oak_planks": 12}, [], [("craft", {"item": "minecraft:shield"})]),
    ("food", {**KIT, "iron_pickaxe": 1, "shield": 2, "iron_ingot": 3, "beef": 3, "coal": 8}, COWS,
     [("place_block", {"item": "minecraft:furnace"}), ("smelt", {"item": "minecraft:cooked_beef", "count": 3})]),
    ("wool", {**KIT, "iron_pickaxe": 1, "shield": 2, "iron_ingot": 3, "cooked_beef": 8}, [],
     [("craft", {"item": "minecraft:shears"}), ("gather", {"product": "wool", "count": 3})]),
    ("bed", {**KIT, "iron_pickaxe": 1, "shield": 2, "iron_ingot": 3, "cooked_beef": 8, "white_wool": 3, "oak_planks": 16},
     [], [("craft", {"item": "minecraft:white_bed"})]),
    ("torches", {**KIT, "iron_pickaxe": 1, "shield": 2, "iron_ingot": 3, "cooked_beef": 8, "white_bed": 1, "coal": 4,
                 "stick": 4}, [], [("craft", {"item": "minecraft:torch"})]),
    ("diamond", {**LATE, "iron_ingot": 3}, [], [("mine_resource", {"tag": "minecraft:diamond_ores", "count": 3}),
                                                ("prospect", {"tag": "minecraft:diamond_ores", "descend_to": -58,
                                                              "minutes": 20, "min_health": 10, "count": 3})]),
    ("diamond_pickaxe", {**LATE, "iron_ingot": 3, "diamond": 3, "stick": 4}, [],
     [("craft", {"item": "minecraft:diamond_pickaxe"})]),
    ("bucket", {**LATE, "diamond_pickaxe": 1, "iron_ingot": 3}, [], [("craft", {"item": "minecraft:bucket"})]),
    ("flint_and_steel", {**LATE, "diamond_pickaxe": 1, "bucket": 1, "iron_ingot": 1, "flint": 1}, [],
     [("mine_resource", {"block": "minecraft:gravel", "count": 30}), ("craft", {"item": "minecraft:flint_and_steel"})]),
    ("obsidian", {**LATE, "diamond_pickaxe": 1, "bucket": 1, "flint_and_steel": 1}, [],
     [("prospect", {"block": "minecraft:obsidian", "descend_to": -54, "minutes": 20})]),
    ("scaffold_dirt", {**LATE, "diamond_pickaxe": 1, "bucket": 1, "flint_and_steel": 1, "obsidian": 10}, [],
     [("mine_resource", {"block": "minecraft:dirt", "count": 23})]),
]


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def actions(e, n0=0):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")][n0:]


def subsequence(want, got):
    it = iter(got)
    return all(any(s == ws and all(p.get(k) == v for k, v in wp.items()) for s, p in it) for ws, wp in want)


async def progress_to(e, milestone, held, nearby=()):
    said = said_by(e)
    e.body.walk_s = 0.05

    async def near(st, r, dy):
        return GROUND
    e.mind.blocks_near = near
    e.body.inventory, e.body.nearby = inv(held), list(nearby)
    await until(lambda: len((e.mind.fresh_state() or {}).get("inventory", [])) == len(held), what="the frame")
    r = await e.mind.owner_text(f"let's progress to {milestone.replace('_', ' ')}", "Hunter")
    assert r.get("progressing") == milestone, (r, said)
    end = f"{milestone.replace('_', ' ').capitalize()} done"
    is_done = f"{milestone.replace('_', ' ').capitalize()} is done"      # agenda f7288182: the target's own line
    await until(lambda: any(t.startswith((end, is_done, "I stopped at")) or "isn't done yet" in t for t in said),
                timeout=30,
                what=f"the end of {milestone}")
    return said


@pytest.mark.parametrize("milestone,held,nearby,want", CASES, ids=[c[0] for c in CASES])
def test_s93_milestone(tmp_path, milestone, held, nearby, want):
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            said = await progress_to(e, milestone, held, nearby)
            got = actions(e)
            assert subsequence(want, got), (milestone, [(s, p) for s, p in got], said)
            assert not any("plan for" in t or "planner" in t for t in said), said
    run(go())


def test_s93_a_fallback_command_is_skipped_once_the_milestone_is_done(tmp_path):
    """raw_iron: "mine 8 iron ore" got them -> "prospect iron ore down to y 16" (the fallback) never runs."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            held = {**KIT, "charcoal": 4}
            task = asyncio.ensure_future(progress_to(e, "raw_iron", held))
            await until(lambda: any(s == "mine_resource" for s, _ in actions(e)), timeout=5)
            e.body.inventory = inv({**held, "raw_iron": 11})                # the mine's haul
            said = await task
            assert not any(s == "prospect" for s, _ in actions(e)), actions(e)
            assert any(t.startswith(("Raw iron done", "Raw iron is done")) for t in said), said
    run(go())


def test_s93_spare_shield(tmp_path):                 # agenda f7288182: "craft 2 shields" (the total)
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            await progress_to(e, "spare_shield", {**KIT, "iron_pickaxe": 1, "shield": 1, "iron_ingot": 4,
                                                   "oak_planks": 12})
            assert any(s == "craft" and p.get("item") == "minecraft:shield" for s, p in actions(e)), actions(e)
    run(go())


PORTAL_KIT = {**LATE, "diamond_pickaxe": 1, "bucket": 1, "flint_and_steel": 1}


def test_s93_portal_frame(tmp_path):
    from test_o132_build_flow import portal_catalogue

    async def go():
        portal_catalogue(tmp_path)
        async with mind_env(tmp_path, env=envelope(zones=[]), designs_dir=tmp_path / "designs") as e:
            await until(lambda: e.mind.fresh_state() is not None)
            said = said_by(e)
            e.body.walk_s = 0.05

            async def near(st, r, dy):
                return GROUND
            e.mind.blocks_near = near
            e.body.inventory = inv({**PORTAL_KIT, "obsidian": 10, "dirt": 23})
            await until(lambda: len((e.mind.fresh_state() or {}).get("inventory", [])) == len(PORTAL_KIT) + 2)
            r = await e.mind.owner_text("let's progress to portal frame", "Hunter")
            assert r.get("progressing") == "portal_frame", (r, said)
            await until(lambda: any("design" in t.lower() or "build" in t.lower() or "portal" in t.lower()
                                    for t in said[1:]), timeout=10, what="the build's first line")
            assert not any("don't have a design" in t for t in said), said
    run(go())


def test_s93_nether_portal_is_lit(tmp_path, monkeypatch):
    from eve_mind import progress
    from test_o140_workstations import lakeside, shore_world
    monkeypatch.setattr(progress, "portal_light", lambda mind, a: True)    # sense hello >= 0.1.27 (live since 10-22)

    async def go():
        async with mind_env(tmp_path) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            said = said_by(e)
            b = lakeside()
            b[(36, 63, -19)] = b[(36, 67, -19)] = b[(35, 63, -19)] = "minecraft:obsidian"
            b[(36, 66, -19)] = "minecraft:air"
            await shore_world(e, b, {"minecraft:flint_and_steel": 4})
            kit = {"flint_and_steel": 1, **{k: v for k, v in PORTAL_KIT.items() if k != "flint_and_steel"}}
            e.body.inventory = inv(kit)                                     # flint and steel in hotbar slot 0
            await until(lambda: len((e.mind.fresh_state() or {}).get("inventory", [])) == len(kit))
            e.mind.progress_marks.add("eve:portal_frame")                  # the frame milestone's own mark
            r = await e.mind.owner_text("let's progress to nether portal", "Hunter")
            assert r.get("progressing") == "nether_portal", (r, said)
            await until(lambda: any(s == "use_item" for s, _ in actions(e)), timeout=10, what="the light")
            assert any(t.startswith("Lighting the portal at") for t in said), said
    run(go())


def test_o251_a_short_milestone_is_run_again_twice_then_says_what_fell_short(tmp_path):
    """O251: fuel's steps end ok but only 4 coal came (the fake inventory) -> fuel twice more, then the stop line with
    agenda's own want and the one question."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            held = dict(KIT)
            task = asyncio.ensure_future(progress_to(e, "fuel", held))
            await until(lambda: any(s == "mine_resource" for s, _ in actions(e)), timeout=5)
            e.body.inventory = inv({**held, "coal": 4})                     # short of agenda's 8
            said = await task
            mines = [p for s, p in actions(e) if s == "mine_resource"]
            assert len(mines) == 3, mines                                   # the first round + 2 re-runs
            assert said.count("Not enough yet - trying fuel again.") == 2, said
            stop = [t for t in said if t.startswith("I stopped at fuel - ")]
            assert stop and stop[-1].endswith("Should I try again?") and "I have 4 of the 8" in stop[-1], said
            assert e.mind.asked and e.mind.asked["kind"] == "progress"
    run(go())


def test_o251_a_rerun_that_gets_enough_finishes_the_milestone(tmp_path):
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            held = dict(KIT)
            task = asyncio.ensure_future(progress_to(e, "fuel", held))
            await until(lambda: [s for s, _ in actions(e)].count("mine_resource") == 2, timeout=10, what="the re-run")
            e.body.inventory = inv({**held, "coal": 8})
            said = await task
            assert any(t.startswith("Fuel") and "done" in t for t in said), said
            assert not any(t.startswith("I stopped at") for t in said), said
    run(go())
