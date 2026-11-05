"""O111-b: every phrase in parts/builder|skills|combat/OWNER-PHRASES.md (23db191e, 86b928a0, 272569b5..085f4011) maps
by rule to its dispatch, one question back, or an owner-worded refusal."""
import asyncio
import json
import time

import pytest

from conftest import mind_env, run
from eve_mind.commands import map_command
from eve_mind.owner_phrases import code_line, match
from eve_mind.planner import Planner, gather_order

E = lambda i, t, d, **k: {"id": i, "type": f"minecraft:{t}", "distance": d, "hostile": True, "protected": False,  # noqa
                          "pos": {"x": d, "y": 64, "z": 0}, **k}
NOW = int(time.time() * 1000)


def view(bow=True, owner=(10, 64, -3)):
    inv = {"minecraft:raw_iron": 5, "minecraft:oak_log": 3, "minecraft:cobblestone": 20, "minecraft:beef": 2,
           "minecraft:diamond": 1, "minecraft:sand": 10, "minecraft:iron_sword": 1, "minecraft:bread": 4}
    if bow:
        inv |= {"minecraft:bow": 1, "minecraft:arrow": 32}
    return {
        "st": {"pos": {"x": 0.5, "y": 64.0, "z": 0.5}, "dim": "minecraft:overworld",
               "inventory": [{"slot": i, "item": k, "count": n} for i, (k, n) in enumerate(inv.items())],
               "nearby_entities": [E(21, "zombie", 5), E(22, "spider", 8), E(23, "skeleton", 9), E(24, "creeper", 10),
                                   E(25, "villager", 6, hostile=False, protected=True, name="Bob"),
                                   E(26, "player", 7, hostile=False, protected=True, name="Alex")]},
        "owner_pos": owner and {"dim": "minecraft:overworld", "x": owner[0], "y": owner[1], "z": owner[2]},
        "home": {"dim": "minecraft:overworld", "center": {"x": 0, "y": 64, "z": 0}},
        "zones": [{"id": "home", "min": {"x": -32, "y": -64, "z": -32}, "max": {"x": 32, "y": 319, "z": 32}},
                  {"id": "farm", "min": {"x": 0, "y": 63, "z": 0}, "max": {"x": 10, "y": 64, "z": 10}}],
        "designs": [{"project_id": "cabin", "version": 1, "design_hash": "h-c1", "category": "eve"},
                    {"project_id": "cabin", "version": 2, "design_hash": "h-c2", "category": "eve"},
                    {"project_id": "tower", "version": 1, "design_hash": "h-t1", "category": "eve"},
                    {"project_id": "hut", "version": 1, "design_hash": "h-h1", "category": "eve"},
                    {"project_id": "gift", "version": 1, "design_hash": "h-g1", "category": "owner_supplied"}],
        "snapshots": [{"snapshot_id": "s-old", "zone_id": "home", "created_ts": NOW - 86400 * 1000},
                      {"snapshot_id": "s-new", "zone_id": "home", "created_ts": NOW}],
        "containers": [{"pos": {"x": 2, "y": 64, "z": 2}, "kind": "minecraft:chest", "shared": True,
                        "items": {"minecraft:bread": 5, "minecraft:iron_ingot": 12}},
                       {"pos": {"x": 5, "y": 64, "z": 5}, "kind": "minecraft:chest", "shared": False,
                        "items": {"minecraft:emerald": 3}},
                       {"pos": {"x": 3, "y": 64, "z": 3}, "kind": "minecraft:barrel", "shared": True, "items": {}},
                       {"pos": {"x": 1, "y": 64, "z": 0}, "kind": "minecraft:furnace", "shared": True, "items": {}}]}


def do(text, v=None):
    r = match(text, v or view())
    assert r and "do" in r, (text, r)
    return r["do"][0] if len(r["do"]) == 1 else r["do"]


def asks(text, v=None):
    r = match(text, v or view())
    assert r and "ask" in r, (text, r)
    return r["ask"]


def says(text, v=None):
    r = match(text, v or view())
    assert r and set(r) == {"say"}, (text, r)
    return r["say"]


def started(text, v=None):
    r = match(text, v or view())
    assert r and "start" in r, (text, r)
    return r["start"], r["args"]


# ---- skills sheet

def test_mine_block():
    assert asks("break that block") == asks("dig this out") == "Which block? Give me its x y z."
    assert do("remove the block at 1 64 2") == ("mine_block", {"pos": {"x": 1, "y": 64, "z": 2}})


def test_mine_resource_by_rule():
    assert gather_order("get 10 stone")["params"] == {"block": "minecraft:stone", "count": 10}
    assert gather_order("mine some coal")["params"] == {"tag": "minecraft:coal_ores", "count": 8}
    assert map_command("chop 8 logs")["skill"] == "gather"                      # eve-mind's wood rule
    assert gather_order("fetch iron you can see")["params"] == {"tag": "minecraft:iron_ores", "count": 8}


def test_prospect():
    assert do("dig for iron") == ("prospect", {"tag": "minecraft:iron_ores", "count": 8})
    assert do("go find diamonds underground") == ("prospect", {"tag": "minecraft:diamond_ores", "count": 8})
    assert do("branch-mine for coal") == ("prospect", {"tag": "minecraft:coal_ores", "count": 8})
    assert asks("tunnel east for ore") == "Which ore should I dig for?"
    assert do("dig deep for 3 diamonds")[1]["descend_to"] == 12
    assert do("tunnel north for iron at y 12")[1] == {"tag": "minecraft:iron_ores", "count": 8, "direction": "north",
                                                      "descend_to": 12}


def test_dig_up():
    home = {"toward": {"x": 0, "y": 64, "z": 0}}
    assert do("get out of the hole") == do("dig your way up") == do("come up to the surface") == ("dig_up", home)
    assert do("dig your way up to me") == ("dig_up", {"toward": {"x": 10, "y": 64, "z": -3}})


def test_craft():
    assert asks("make a pickaxe") == "Which pickaxe: wooden, stone, iron or diamond?"
    assert do("make an iron pickaxe") == ("craft", {"item": "minecraft:iron_pickaxe", "count": 1})
    assert do("craft 16 torches") == ("craft", {"item": "minecraft:torch", "count": 16})
    assert do("make a crafting table") == ("craft", {"item": "minecraft:crafting_table", "count": 1})
    assert do("turn the logs into planks") == ("craft", {"item": "minecraft:oak_planks", "count": 12})
    assert match("build a house", view()) is None and match("make a wish", view()) is None   # planner / talk as before


def test_smelt():
    furnace = {"x": 1, "y": 64, "z": 0}
    assert do("smelt the iron") == ("smelt", {"pos": furnace, "item": "minecraft:iron_ingot", "count": 5})
    assert do("cook the beef") == ("smelt", {"pos": furnace, "item": "minecraft:cooked_beef", "count": 2})
    assert do("make 8 glass") == ("smelt", {"pos": furnace, "item": "minecraft:glass", "count": 8})
    assert asks("cook everything in the furnace") == "What should I cook?"
    far = view()
    far["containers"] = [c for c in far["containers"] if "furnace" not in c["kind"]]
    assert says("smelt the iron", far) == "I need a furnace nearby for that."
    assert says("cook the chicken") == "I have no chicken to cook."
    assert do("make charcoal")[1]["item"] == do("burn the logs")[1]["item"] == "minecraft:charcoal"


def test_withdraw():
    chest = {"x": 2, "y": 64, "z": 2}
    assert do("take 10 iron from the chest") == ("withdraw", {"pos": chest, "item": "minecraft:iron_ingot", "count": 10})
    assert do("get the bread out of storage") == ("withdraw", {"pos": chest, "item": "minecraft:bread", "count": 5})
    assert asks("take bread from the chest") == "How many bread?"
    assert asks("take 2 emeralds from my chest").startswith("The chest at")         # O135: confirm, then tag shared
    assert says("take 2 gold from the chest") == "I don't know a chest with gold ingot in it."


def test_deposit():
    chest = {"x": 2, "y": 64, "z": 2}
    v = view()
    v["containers"] = [c for c in v["containers"] if "barrel" not in c["kind"] and "furnace" not in c["kind"]]
    assert do("put the cobble in the chest", v) == ("deposit", {"pos": chest, "item": "minecraft:cobblestone",
                                                                  "count": 20})
    ores = do("store your ores", v)
    assert sorted(s[1]["item"] for s in ores) == ["minecraft:diamond", "minecraft:raw_iron"]
    bag = match("empty your bag into the box", v)["do"]
    items = {s[1]["item"] for s in bag}
    assert "minecraft:cobblestone" in items and not items & {"minecraft:iron_sword", "minecraft:bow", "minecraft:bread"}
    two = view()
    two["containers"].append({"pos": {"x": 4, "y": 64, "z": 0}, "kind": "minecraft:chest", "shared": True, "items": {}})
    assert asks("put the cobble in the chest", two) == "Which chest? Give me its x y z."


def test_open_container():
    assert do("check the barrel") == ("open_container", {"pos": {"x": 3, "y": 64, "z": 3}})
    assert asks("check the chest") == "Which chest? Give me its x y z."
    assert match("what's in that chest?", view()) is None          # a question: the mind's talk path, never executed


def test_harvest_replant():
    box = {"min": {"x": 0, "y": 63, "z": 0}, "max": {"x": 10, "y": 64, "z": 10}}
    assert do("harvest the farm") == do("bring in the wheat") == ("harvest_replant", {**box, "replant": True})
    assert do("pick the carrots and replant") == ("harvest_replant", {**box, "replant": True})
    assert do("harvest the farm and don't replant")[1]["replant"] is False
    v = view()
    v["zones"] = v["zones"][:1]
    assert asks("harvest the farm", v).startswith("Where is the farm?")


def test_gather_animals():
    assert asks("shear the sheep") == "How much wool?"
    assert do("shear the sheep 6") == ("gather", {"product": "wool", "count": 6})
    assert do("milk a cow") == ("gather", {"product": "milk", "count": 1})
    assert do("bring me 6 wool") == ("gather", {"product": "wool", "count": 6})
    assert gather_order("get some wood")["params"] == {"tag": "minecraft:logs", "count": 8}


def test_equip_best():
    assert do("gear up") == do("put your armour on") == ("equip_best", {})
    assert do("use your best sword") == ("equip_best", {"weapon": True})
    assert do("get your pickaxe out") == ("equip_best", {"for_block": "minecraft:stone"})


def test_code_lines():
    assert code_line("not_found", thing="coal", radius=16) == "I couldn't find any coal within 16 blocks."
    assert "{" not in code_line("not_found") and "{" not in code_line("partial") and code_line("private_container").startswith("That's your private chest")
    assert code_line("no_ammo") == "I'm out of arrows." and code_line("made_up") is None


# ---- combat sheet

def test_fight_melee():
    assert do("kill that zombie") == ("fight_melee", {"entity_id": 21})
    assert do("kill the spider") == ("fight_melee", {"entity_id": 22})
    for line in ("fight with me", "help me", "get them", "clear the mobs around here"):
        assert do(line) == ("fight_melee", {}), line
    assert do("clear everything near the farm") == ("fight_melee", {"radius": 32})
    assert do("deal with the skeleton", view(bow=False)) == ("fight_melee", {"entity_id": 23})
    assert do("deal with the skeleton") == ("fight_ranged", {"entity_id": 23})


def test_fight_ranged():
    assert do("shoot the skeleton") == ("fight_ranged", {"entity_id": 23})
    assert do("shoot that creeper") == ("fight_ranged", {"entity_id": 24})
    assert do("take it out from here") == do("use your bow") == ("fight_ranged", {})
    assert do("kill the creeper") == ("fight_ranged", {"entity_id": 24})          # never a sword on a creeper
    assert says("shoot the skeleton", view(bow=False)) == "I have no bow."
    assert says("kill the creeper", view(bow=False)).startswith("I don't sword-fight creepers")


def test_combat_refusals_and_asks():
    assert says("guard the door") == "I can't hold a door yet."
    assert says("kill the villager") == "I won't attack Bob: it's protected."
    assert says("kill Alex") == "I don't fight players."
    assert says("kill the witch") == "I don't see a witch near me."
    assert asks("kill that one") == "Which one? Tell me what kind of mob."
    v = view()
    v["st"]["nearby_entities"].append(E(27, "zombie", 5.3))
    assert asks("kill the zombie", v).startswith("Two zombies are just as close")
    assert match("run", view())["cancel"] is True
    assert map_command("stop fighting") == {"kind": "control", "op": "stop"}       # STOP wins (stronger than cancel)
    assert match("get wood", view()) is None


# ---- builder sheet

def test_build_schematic():
    assert started("build the cabin") == ("build", {"project_id": "cabin", "version": 2, "design_hash": "h-c2"})
    assert started("build the cabin here")[1]["origin"] == {"x": 10, "y": 64, "z": -3}
    assert asks("build the cabin here", view(owner=None)) == "Where are you? Give me x y z."
    assert started("build the cabin at 120 64 -30")[1]["origin"] == {"x": 120, "y": 64, "z": -30}
    assert started("build version 1 of the tower")[1] == {"project_id": "tower", "version": 1, "design_hash": "h-t1"}
    assert says("build version 2 of the tower").startswith("Version 2 isn't approved")
    assert started("finish the hut")[1]["project_id"] == "hut"
    assert started("build the schematic I gave you at 1 2 3")[1] == {"project_id": "gift", "version": 1,
                                                                    "design_hash": "h-g1",
                                                                    "origin": {"x": 1, "y": 2, "z": 3}}
    assert asks("build it at 120 64 -30").startswith("Which design:")


def test_repair_zone():
    for line in ("fix the house", "undo the creeper damage at home", "put the base back the way it was",
                 "repair home from yesterday's snapshot"):
        assert asks(line) == "Which part should I repair? Give me its corners: x y z to x y z.", line
    assert says("repair the farm") == "I have no saved snapshot of farm. Save it first: eve save_zone farm."
    kind, a = started("repair home from 0 64 0 to 5 66 5")
    assert kind == "repair" and a == {"zone_id": "home", "snapshot_id": "s-new",
                                      "box": {"min": {"x": 0, "y": 64, "z": 0}, "max": {"x": 5, "y": 66, "z": 5}}}
    assert started("repair home from yesterday's snapshot 0 64 0 to 5 66 5")[1]["snapshot_id"] == "s-old"
    assert match("fix me a sandwich", view()) is None


def test_bridge():
    assert asks("bridge over to there") == asks("get across to the other island") == \
        "Where to? Give me the x y z of the far side."
    assert says("build a bridge to 140 64 -30") == "That isn't in a straight line from me. Walk me to a start in line " \
                                                   "with it first."
    assert says("build a bridge to 140 64 0") == "That's more than 64 blocks; walk me closer first."
    assert started("build a bridge to 30 63 0") == ("bridge", {"to": {"x": 30, "y": 63, "z": 0}})
    assert started("make a path across the gap to me", view(owner=(20, 64, 0))) == ("bridge", {"to": {"x": 20, "y": 63,
                                                                                                    "z": 0}})


# ---- through the hub (fake body): the hook, a rerun ask, steps in turn

@pytest.fixture(autouse=True)
def no_planner(monkeypatch):
    async def refused(self, *a, **k):
        return {"ok": False, "error": "planner_unavailable", "detail": "test stub", "model": "stub",
                "prompt_version": "stub"}
    monkeypatch.setattr(Planner, "plan", refused)


async def hear(e, text):
    q = len(e.body.received)
    await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": text})
    return (await e.body.wait_for(lambda m: m["type"] == "chat.send", start=q))["payload"]["text"]


def test_phrases_through_the_mind(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.nearby = [E(21, "zombie", 5)]
            await asyncio.sleep(0.3)
            assert await hear(e, "kill that zombie") == "Fighting the zombie."
            await asyncio.sleep(0.3)
            row = e.db.execute("SELECT params FROM actions WHERE skill='fight_melee'").fetchone()
            assert row and json.loads(row[0]) == {"entity_id": 21}
            await hear(e, "cancel")
            await asyncio.sleep(0.3)
            assert await hear(e, "shear the sheep") == "How much wool?"
            assert await hear(e, "6") == "Getting 6 wool."                 # his number completes the order
            await asyncio.sleep(0.3)
            row = e.db.execute("SELECT params FROM actions WHERE skill='gather'").fetchone()
            assert row and json.loads(row[0]) == {"product": "wool", "count": 6, "radius": 32}   # O163
            assert await hear(e, "guard the door") == "I can't hold a door yet."
    run(go())
