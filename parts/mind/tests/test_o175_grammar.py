"""O175 part 4 / O177: one owner command grammar - table of phrasings -> {verb, object, qty, place}. Hunter: "we're
not dumb, this isn't a dumb system." Columns: line, verb, object id (or kind for no id), qty kind, n, place kind,
anchor."""
import pytest

from eve_mind.grammar import OWNER_RADIUS, numbers, parse, parse_all

L, LOGS = "minecraft:logs", "minecraft:"
T = [
    # the design note's 20
    ("mine the 17 nearest trees", "mine", L, "nearest", 17, "none", None),
    ("mine nearest trees up to 18", "mine", L, "up_to", 18, "none", None),
    ("mine near trees", "mine", L, "open", 8, "anchor", "trees"),
    ("get me some wood", "collect", L, "some", 8, "none", None),
    ("kill 3 sheep near the river", "kill", "minecraft:sheep", "exact", 3, "anchor", "river"),
    ("collect 8 wood logs from nearby trees", "collect", L, "exact", 8, "near_her", "trees"),
    ("chop seventeen oak logs", "chop", "minecraft:oak_log", "exact", 17, "none", None),
    ("mine at least 20 cobblestone", "mine", "minecraft:stone", "at_least", 20, "none", None),
    ("get all the iron around here", "collect", "minecraft:iron_ores", "all", None, "here", None),
    ("keep mining coal until I say stop", "mine", "minecraft:coal_ores", "open", 8, "none", None),
    ("craft 4 torches", "craft", "minecraft:torch", "exact", 4, "none", None),
    ("smelt all the iron", "smelt", "minecraft:iron_ingot", "all", None, "none", None),
    ("find the nearest water", "find", "minecraft:water", "nearest", 1, "none", None),
    ("kill every zombie within 20 blocks", "kill", "minecraft:zombie", "all", None, "radius", None),
    ("harvest the wheat by the barn", "harvest", "minecraft:wheat", "none", None, "anchor", "barn"),
    ("gather a dozen sand over there", "collect", "minecraft:sand", "exact", 12, "there", None),
    ("give me 10 bread", "give", "minecraft:bread", "exact", 10, "none", None),
    ("follow me", "follow", "owner", "none", None, "none", None),
    ("mine 10-20 stone near the base", "mine", "minecraft:stone", "up_to", 20, "anchor", "base"),
    ("chop 8 logs", "chop", L, "exact", 8, "none", None),
    # session 1-4 lines (O111, O157, O158, O164)
    ("Collect 8 logs from nearby trees", "collect", L, "exact", 8, "near_her", "trees"),
    ("gather 10 logs around here", "collect", L, "exact", 10, "here", None),
    ("chop 8 logs please", "chop", L, "exact", 8, "none", None),
    ("mine 7 logs", "mine", L, "exact", 7, "none", None),
    ("mine up some logs", "mine", L, "some", 8, "none", None),
    ("find exposed stone", "find", "minecraft:stone", "none", None, "none", None),
    # number words, ranges, plurals
    ("chop twenty one birch logs", "chop", "minecraft:birch_log", "exact", 21, "none", None),
    ("mine a hundred cobblestone", "mine", "minecraft:stone", "exact", 100, "none", None),
    ("get a couple of oak logs", "collect", "minecraft:oak_log", "exact", 2, "none", None),
    ("chop down three spruce trees", "chop", "minecraft:spruce_log", "exact", 3, "none", None),
    ("mine 5 to 10 iron", "mine", "minecraft:iron_ores", "up_to", 10, "none", None),
    ("mine up to 32 coal", "mine", "minecraft:coal_ores", "up_to", 32, "none", None),
    ("mine no more than 6 diamonds", "mine", "minecraft:diamond_ores", "up_to", 6, "none", None),
    ("collect at least 4 sand", "collect", "minecraft:sand", "at_least", 4, "none", None),
    ("get a few dirt", "collect", "minecraft:dirt", "some", 8, "none", None),
    ("grab some gravel", "collect", "minecraft:gravel", "some", 8, "none", None),
    ("chop a tree", "chop", L, "exact", 1, "none", None),
    ("chop the nearest tree", "chop", L, "nearest", 1, "none", None),
    ("chop trees", "chop", L, "none", None, "none", None),
    ("chop", "chop", L, "none", None, "none", None),
    ("cut down some dark oak", "chop", "minecraft:dark_oak_log", "some", 8, "none", None),
    # open-ended
    ("keep chopping trees", "chop", L, "open", 8, "none", None),
    ("mine stone until I tell you to stop", "mine", "minecraft:stone", "open", 8, "none", None),
    ("collect wood nonstop", "collect", L, "open", 8, "none", None),
    ("keep gathering sand around here", "collect", "minecraft:sand", "open", 8, "here", None),
    # places
    ("mine coal near me", "mine", "minecraft:coal_ores", "none", None, "near_owner", None),
    ("chop 5 logs near you", "chop", L, "exact", 5, "near_her", None),
    ("mine 3 iron you can see", "mine", "minecraft:iron_ores", "exact", 3, "near_her", None),
    ("get 12 sand by the water", "collect", "minecraft:sand", "exact", 12, "anchor", "water"),
    ("chop 6 logs within 50 blocks", "chop", L, "exact", 6, "radius", None),
    ("collect 4 clay from the river", "collect", "minecraft:clay", "exact", 4, "anchor", "river"),
    ("mine 9 stone over here", "mine", "minecraft:stone", "exact", 9, "here", None),
    # fights
    ("kill the zombie", "kill", "minecraft:zombie", "none", None, "none", None),
    ("attack 2 skeletons", "kill", "minecraft:skeleton", "exact", 2, "none", None),
    ("kill all the mobs around here", "kill", "mob", "all", None, "here", None),
    ("kill all the hostiles near me", "kill", "hostile", "all", None, "near_owner", None),
    ("fight that creeper", "kill", "minecraft:creeper", "none", None, "none", None),
    ("shoot the spider", "shoot", "minecraft:spider", "none", None, "none", None),
    ("hunt 3 cows", "kill", "minecraft:cow", "exact", 3, "none", None),
    # crafts, smelts, gives, finds
    ("make a crafting table", "craft", "minecraft:crafting_table", "exact", 1, "none", None),
    ("craft me an iron pickaxe", "craft", "minecraft:iron_pickaxe", "exact", 1, "none", None),
    ("make 16 sticks", "craft", "minecraft:stick", "exact", 16, "none", None),
    ("craft a cobblestone pickaxe", "craft", "minecraft:stone_pickaxe", "exact", 1, "none", None),
    ("cook the beef", "smelt", "minecraft:beef", "none", None, "none", None),
    ("give me 5 torches", "give", "minecraft:torch", "exact", 5, "none", None),
    ("look for iron", "find", "minecraft:iron_ores", "none", None, "none", None),
    ("locate the closest coal", "find", "minecraft:coal_ores", "nearest", 1, "none", None),
    ("Eve, please chop 8 logs for me", "chop", L, "exact", 8, "none", None),
    ("hey eve could you mine 4 iron near the base", "mine", "minecraft:iron_ores", "exact", 4, "anchor", "base"),
]


def _id(r):
    o = r["object"]
    return o["id"] or (o["words"] if o["kind"] == "entity" else o["kind"])


@pytest.mark.parametrize("line,verb,obj,qk,n,pk,anchor", T, ids=[t[0] for t in T])
def test_grammar_table(line, verb, obj, qk, n, pk, anchor):
    r = parse(line)
    assert r is not None, line
    got = (r["verb"], _id(r), r["qty"]["kind"], r["qty"]["n"], r["place"]["kind"], r["place"]["anchor"])
    assert got == (verb, obj, qk, n, pk, anchor), got


def test_table_is_big_enough():
    assert len(T) >= 60


def test_numbers_and_caps():
    assert numbers("seventeen trees, twenty one logs, a dozen eggs, a couple of rocks, a hundred stone") == \
        "17 trees, 21 logs, 12 eggs, 2 rocks, 100 stone"
    assert parse("chop 6 logs within 50 blocks")["place"]["radius"] == 50
    assert parse("chop 6 logs within 150 blocks")["place"]["radius"] == OWNER_RADIUS == 100   # one cap constant (O238)


def test_pronouns_and_several_orders():
    ctx = {"last_item": "minecraft:furnace", "last_hostile": "minecraft:zombie"}
    assert parse("place it", ctx)["object"] == {"kind": "pronoun", "id": "minecraft:furnace", "words": "it"}
    assert parse("kill it", ctx)["object"]["id"] == "minecraft:zombie"
    two = parse_all("make a furnace and place it near me")
    assert [(i["verb"], i["object"]["id"]) for i in two] == [("craft", "minecraft:furnace"), ("place", "minecraft:furnace")]
    assert two[1]["place"]["kind"] == "near_owner"


@pytest.mark.parametrize("line", ["stop", "what are you doing?", "where are you", "thanks eve", "the chest is at 1 2 3",
                                  "I like oak doors", "how many logs do you have?", "good night"])
def test_not_orders(line):
    assert parse(line) is None


# ---- through the owner path (fake body): the grammar's orders reach the skills with their limits and places

import asyncio  # noqa: E402
import json  # noqa: E402

from conftest import mind_env, run, until  # noqa: E402
from eve_mind.planner import Planner  # noqa: E402


@pytest.fixture
def no_planner(monkeypatch):
    async def refused(self, *a, **k):
        return {"ok": False, "error": "planner_unavailable", "detail": "test stub", "model": "stub",
                "prompt_version": "stub"}
    monkeypatch.setattr(Planner, "plan", refused)


async def hear(e, text):
    q = len(e.body.received)
    await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": text})
    return (await e.body.wait_for(lambda m: m["type"] == "chat.send", start=q, timeout=6))["payload"]["text"]


def acts(e):
    return [(r[0], json.loads(r[1])) for r in e.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


@pytest.mark.parametrize("line,reply,skill,params", [
    ("mine the 17 nearest trees", "On it: the 17 nearest logs.", "mine_resource",
     {"tag": "minecraft:logs", "count": 17, "radius": 32}),
    ("chop seventeen oak logs", "On it: 17 oak logs.", "mine_resource",
     {"block": "minecraft:oak_log", "count": 17, "radius": 32}),
    ("mine up to 32 coal", "On it: up to 32 coal ores.", "mine_resource",
     {"tag": "minecraft:coal_ores", "count": 32, "radius": 32}),
    ("collect 8 wood logs from nearby trees", "On it: 8 logs within 16 blocks.", "gather",
     {"product": "wood", "count": 8, "radius": 16}),
    ("kill all the mobs around here", "On it - every mob within 16 blocks, nearest first.", "fight_melee",
     {"radius": 16, "max_targets": 16}),
])
def test_grammar_orders_reach_the_skill(tmp_path, no_planner, line, reply, skill, params):
    async def go():
        async with mind_env(tmp_path) as e:
            assert await hear(e, line) == reply
            await until(lambda: any(s == skill for s, _ in acts(e)), what=skill)
            assert (skill, params) in acts(e), acts(e)
    run(go())


def test_open_ended_runs_in_chunks_until_he_says_stop(tmp_path, no_planner):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 0.2
            assert (await hear(e, "mine near trees")).startswith("On it - I'll keep at the logs in batches of 8")
            await until(lambda: len([s for s, _ in acts(e) if s == "mine_resource"]) >= 2, timeout=5, what="2 chunks")
            await hear(e, "stop")
            await asyncio.sleep(0.6)
            n = len([s for s, _ in acts(e) if s == "mine_resource"])
            await asyncio.sleep(0.6)
            assert len([s for s, _ in acts(e) if s == "mine_resource"]) == n         # nothing after the stop
    run(go())


def test_near_the_base_walks_home_first(tmp_path, no_planner):
    async def go():
        async with mind_env(tmp_path) as e:
            assert await hear(e, "mine 10-20 stone near the base") == "On it: up to 20 stone within 16 blocks."
            await until(lambda: any(s == "mine_resource" for s, _ in acts(e)), what="the mine after the walk")
            assert [s for s, _ in acts(e)][-2:] == ["walk_to", "mine_resource"]
    run(go())
