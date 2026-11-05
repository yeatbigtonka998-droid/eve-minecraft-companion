"""S93 conversation layer, owner path: she narrates each milestone's start and end and why a fallback runs; a stopped
run says why (the failing step's own line) and asks one question - yes / "try again" runs it again, no leaves it;
"why did you stop?" is answered from that stop (else the last failure line)."""
import asyncio
import json

import pytest

from conftest import envelope, mind_env, run, until

GROUND = {(x, y, z): ("minecraft:stone" if y < 64 else "minecraft:air")
          for x in range(-8, 9) for y in range(60, 68) for z in range(-8, 9)}
KIT = {"stone_pickaxe": 1, "crafting_table": 1, "furnace": 1}


def inv(items):
    return [{"slot": i, "item": "minecraft:" + k, "count": n} for i, (k, n) in enumerate(items.items())]


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def skills(e):
    return [s for (s,) in e.mind.db.execute("SELECT skill FROM actions ORDER BY rowid")]


async def world(e, held):
    e.body.walk_s = 0.05

    async def near(st, r, dy):
        return GROUND
    e.mind.blocks_near = near
    e.body.inventory = inv(held)
    await until(lambda: len((e.mind.fresh_state() or {}).get("inventory", [])) == len(held), what="the frame")


async def stopped_iron_pickaxe(e):
    """iron_pickaxe's craft fails -> the stop line with its why and one question."""
    said = said_by(e)
    await world(e, {**KIT, "charcoal": 4, "iron_ingot": 8, "stick": 4})
    e.body.result_override = {"craft": [("failed", "missing_ingredients")]}
    await e.mind.owner_text("let's progress to iron pickaxe", "Hunter")
    await until(lambda: any(t.startswith("I stopped at iron pickaxe - ") for t in said), timeout=10, what="the stop")
    return said


@pytest.mark.parametrize("answer", ["yes", "try again"])
def test_talk_a_stop_says_why_asks_once_and_yes_runs_it_again(tmp_path, answer):
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            said = await stopped_iron_pickaxe(e)
            stop = next(t for t in said if t.startswith("I stopped at iron pickaxe - "))
            assert stop == ("I stopped at iron pickaxe - I don't have what I need to make that. "
                            "Should I try again?"), stop                    # the craft's own line is the why
            assert e.mind.asked and e.mind.asked["kind"] == "progress", e.mind.asked
            n = skills(e).count("craft")
            await e.mind.owner_text(answer, "Hunter")
            await until(lambda: skills(e).count("craft") > n, timeout=8, what="the run again")
    run(go())


def test_talk_no_leaves_it(tmp_path):
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            said = await stopped_iron_pickaxe(e)
            n = skills(e).count("craft")
            await e.mind.owner_text("no", "Hunter")
            await until(lambda: "OK, I'll wait for you." in said, timeout=3)
            await asyncio.sleep(1.0)
            assert skills(e).count("craft") == n
    run(go())


def test_talk_why_did_you_stop_is_answered_from_the_stop(tmp_path):
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            said = said_by(e)
            await e.mind.owner_text("why did you stop?", "Hunter")
            assert said[-1] == "I haven't stopped on anything.", said
            said = await stopped_iron_pickaxe(e)
            r = await e.mind.owner_text("why did you stop?", "Hunter")
            assert r.get("reply", "").startswith("I was on iron pickaxe ('craft an iron pickaxe'): "), (r, said)
            assert r["reply"].endswith("I don't have what I need to make that."), r
    run(go())


def test_talk_narrates_start_end_and_the_fallback(tmp_path):
    """raw_iron: the mine ends ok but not enough came -> "Not enough yet - next I'll prospect ...", then the end."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            said = said_by(e)
            await world(e, {**KIT, "charcoal": 4})
            await e.mind.owner_text("let's progress to raw iron", "Hunter")
            await until(lambda: any(t.startswith(("Raw iron done", "Raw iron isn't done", "I stopped at")) for t in said),
                        timeout=15, what="the end")
            assert said[0].startswith("Next: iron"), said
            assert "Not enough yet - next I'll prospect iron ore down to y 16." in said, said
    run(go())
