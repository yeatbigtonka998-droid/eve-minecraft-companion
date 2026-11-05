"""O126 (45L night, session 3: "go and fight it" -> "an attack needs a named hostile mob; I won't guess who 'it' is"): a
pronoun in a fight order is the hostile last named in chat (his words or hers, 60 s), else the nearest hostile she
sees, else she asks. Dusk follow keeps him within 32 blocks. O128 ("mine grass block" -> "which block do you mean"):
his words as a 26.3 block id, block tags for planks/leaves/wool."""
import json

import pytest

from conftest import ROOT, mind_env, run, until

from eve_mind import follow
from eve_mind.planner import gather_order

SKEL = {"id": 77, "type": "minecraft:skeleton", "pos": {"x": 4.5, "y": 64.0, "z": 0.5}, "distance": 4.0,
        "hostile": True, "protected": False, "line_of_sight": True}
ZOMB = {"id": 78, "type": "minecraft:zombie", "pos": {"x": 3.5, "y": 64.0, "z": 0.5}, "distance": 3.0,
        "hostile": True, "protected": False, "line_of_sight": True}


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def fights(e):
    return [json.loads(p) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")
            if s.startswith("fight")]


def test_o126_fight_it_is_the_mob_last_named_else_the_nearest_hostile_else_a_question(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.05
            await until(lambda: e.mind.fresh_state() is not None)
            r = await e.mind.owner_text("go and fight it", "Hunter")              # nothing named, none in sight
            assert r.get("question") and not fights(e), (r, said)
            e.body.nearby = [ZOMB, SKEL]
            await until(lambda: len((e.mind.fresh_state() or {}).get("nearby_entities", [])) == 2)
            await e.mind.owner_text("there's a skeleton outside", "Hunter")       # he names it ...
            r = await e.mind.owner_text("go and fight it", "Hunter")              # ... so "it" is the skeleton
            assert fights(e) and fights(e)[-1] == {"entity_id": 77}, (r, said, fights(e))
            await until(lambda: e.mind.open_action() is None)
            e.mind.last_hostile = None                                            # nothing named: the nearest
            await e.mind.owner_text("attack them", "Hunter")
            assert fights(e)[-1] == {"entity_id": 78}, fights(e)
    run(go())


def test_o126_dusk_follow_keeps_him_within_32_blocks():
    assert follow.NIGHT_NEAR == 32.0
    assert follow.Follow.near({"x": 0, "y": 0, "z": 0, "distance": 30.0}, {})
    assert not follow.Follow.near({"x": 0, "y": 0, "z": 0, "distance": 33.0}, {})


@pytest.mark.skipif(not (ROOT / "data" / "game" / "26.3" / "blockstates").exists(),
                    reason="BLOCKED: data/game/26.3 not extracted")
def test_o128_block_names_are_tried_as_block_ids():
    assert gather_order("mine grass block")["params"] == {"block": "minecraft:grass_block", "count": 8}
    assert gather_order("mine 5 grass blocks")["params"] == {"block": "minecraft:grass_block", "count": 5}
    assert gather_order("mine 4 andesite")["params"] == {"block": "minecraft:andesite", "count": 4}
    assert gather_order("mine some planks")["params"] == {"tag": "minecraft:planks", "count": 8}
    assert gather_order("gather 6 leaves")["params"] == {"tag": "minecraft:leaves", "count": 6}
    assert gather_order("mine 7 logs")["params"] == {"tag": "minecraft:logs", "count": 7}
    assert gather_order("mine 3 glorp").get("question")                        # nothing matches: she asks
    # addendum (45L: "mine 6 Rose Bush" / "rose bushes"): the game's English block names, plural or not, plants too
    assert gather_order("mine 6 Rose Bush")["params"] == {"block": "minecraft:rose_bush", "count": 6}
    assert gather_order("mine 6 rose bushes")["params"] == {"block": "minecraft:rose_bush", "count": 6}
    assert gather_order("gather 4 red mushrooms")["params"] == {"block": "minecraft:red_mushroom", "count": 4}
    assert gather_order("mine 3 short grass")["params"] == {"block": "minecraft:short_grass", "count": 3}
    assert gather_order("mine 4 netherite").get("question")                   # no block of that name


def test_o126b_sealed_at_night_his_fight_order_opens_fights_returns_and_reseals(tmp_path):
    """O126 (b): sealed in at night, "attack the skeleton" within 24 blocks of the door: open, weapon, fight, back in,
    reseal - each step said, the night loop held off; farther than 24 she says so and stays in."""
    import asyncio

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.05
            ns, calls = e.mind.night, []
            door = {"x": 3, "y": 64, "z": 0}
            ns.current = {"status": "running", "doorway": [door], "sealed": [{"pos": door, "item": "minecraft:dirt"}]}
            ns.task = asyncio.get_running_loop().create_future()          # a night "running"

            async def unseal(r, source, why):
                calls.append(("unseal", r.get("owner_fight")))
                r["sealed"] = []

            async def shelter(r, source):
                calls.append(("shelter", r.get("owner_fight")))
                return True

            async def seal(r, source, why="", only_recheck=False):
                calls.append(("seal", r.get("owner_fight")))
                r["sealed"] = [{"pos": door, "item": "minecraft:dirt"}]
            ns.unseal, ns.shelter, ns.seal = unseal, shelter, seal
            e.body.nearby = [SKEL]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"))
            r = await e.mind.owner_text("attack the skeleton", "Hunter")
            assert r.get("fight") == "ok", (r, said)
            assert calls == [("unseal", True), ("shelter", True), ("seal", True)], calls
            skills = [s for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            assert skills[-2:] == ["equip_best", "fight_melee"], skills
            assert said[:2] == ["Opening the door.", "It's an archer; I'll rush it."] and said[-1] == "Back inside and sealed.", said   # no bow (O139)
            assert not ns.current.get("owner_fight")
            far = {**SKEL, "id": 90, "pos": {"x": 40.5, "y": 64.0, "z": 0.5}, "distance": 40.0}
            e.body.nearby = [far]
            await until(lambda: (e.mind.fresh_state() or {})["nearby_entities"][0]["id"] == 90)
            n = len(calls)
            await e.mind.owner_text("attack the skeleton", "Hunter")
            assert said[-1].startswith("That's too far from the shelter at night") and len(calls) == n, said
            ns.task.cancel()
    run(go())
