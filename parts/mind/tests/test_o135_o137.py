"""O137: deposit/withdraw split the item from the container on " into | in | to | from | out of " first; item words
resolve through the game's English names, tool synonyms and the registry; unknown -> one question; a kit item he names
may go (allow_essentials for that item only). O135: "the chest / that chest / the chest near you / the one I just
placed / my chest" with no known shared chest -> the nearest (or newest placed) within 16 blocks, one yes / no, then
remembered as "the chest" and tagged shared with sense (container.tag)."""
import asyncio
import json

import pytest

from conftest import mind_env, run, until
from eve_mind.memory import item_names
from eve_mind.owner_phrases import match, resolve_item
from eve_mind.planner import Planner

needs_data = pytest.mark.skipif(not item_names(), reason="no 26.3 game data (en_us.json) on this machine")


def view(containers=(), named=None, owner=(10, 64, 0)):
    inv = {"minecraft:cobblestone": 20, "minecraft:stone_pickaxe": 1, "minecraft:iron_sword": 1,
           "minecraft:torch": 12, "minecraft:raw_iron": 4}
    return {"st": {"pos": {"x": 0.5, "y": 64.0, "z": 0.5}, "dim": "minecraft:overworld",
                   "inventory": [{"slot": i, "item": k, "count": n} for i, (k, n) in enumerate(inv.items())]},
            "owner_pos": owner and {"dim": "minecraft:overworld", "x": owner[0], "y": owner[1], "z": owner[2]},
            "zones": [], "designs": [], "snapshots": [], "containers": list(containers), "named_chest": named}


C = lambda x, z, shared=False, **k: {"pos": {"x": x, "y": 64, "z": z}, "kind": "minecraft:chest",  # noqa: E731
                                     "shared": shared, "items": {}, **k}


# ---- O137

@needs_data
def test_item_words_resolve_by_game_names_tools_and_registry():
    for words, iid in (("cobblestone pickaxe", "stone_pickaxe"), ("wood sword", "wooden_sword"),
                       ("iron pick", "iron_pickaxe"), ("Stone Pickaxes", "stone_pickaxe"), ("the torches", "torch"),
                       ("cobble", "cobblestone"), ("10 iron", "iron_ingot"), ("glass panes", "glass_pane"),
                       ("rose bushes", "rose_bush"), ("oak planks", "oak_planks"), ("diamond boots", "diamond_boots"),
                       ("all your cobblestone", "cobblestone"), ("minecraft:bread", "bread")):
        assert resolve_item(words)[0] == f"minecraft:{iid}", words
    assert resolve_item("zzz thing") == (None, "zzz thing")


def test_item_split_from_container_and_kit_item_named():
    v = view([C(2, 2, shared=True)])
    at = {"x": 2, "y": 64, "z": 2}
    assert match("put the cobble in the chest", v)["do"] == [("deposit", {"pos": at, "item": "minecraft:cobblestone",
                                                                          "count": 20})]
    assert match("put 5 torches into the chest", v)["do"] == [("deposit", {"pos": at, "item": "minecraft:torch",
                                                                           "count": 5, "allow_essentials": True})]
    assert match("store your cobblestone pickaxe in the chest", v)["do"] == [
        ("deposit", {"pos": at, "item": "minecraft:stone_pickaxe", "count": 1, "allow_essentials": True})]
    bag = match("empty your bag into the chest", v)["do"]
    assert {s[1]["item"] for s in bag} == {"minecraft:cobblestone", "minecraft:raw_iron"}      # kit stays
    assert all("allow_essentials" not in s[1] for s in bag)
    assert match("put the zzz thing in the chest", v) == {"ask": "Which item do you mean by 'zzz thing'?"}
    assert match("Put torches every 8 blocks in tunnels.".lower().rstrip("."), v) is None    # a standing rule, as before


def test_withdraw_split_and_holder():
    v = view([C(2, 2, shared=True, items={"minecraft:iron_ingot": 9}), C(4, 0, shared=True, items={"minecraft:bread": 3})])
    assert match("take 5 iron out of the chest", v)["do"] == [("withdraw", {"pos": {"x": 2, "y": 64, "z": 2},
                                                                           "item": "minecraft:iron_ingot", "count": 5})]
    assert match("get the bread from the chest", v)["do"][0][1] == {"pos": {"x": 4, "y": 64, "z": 0},
                                                                    "item": "minecraft:bread", "count": 3}


# ---- O135

def test_no_shared_chest_asks_about_the_nearest_or_newest_once():
    v = view([C(9, 1), C(30, 30), C(1, 1, placed_ms=5), C(3, 1, placed_ms=9)])
    r = match("put the cobble in the chest", view([C(9, 1), C(30, 30)]))
    assert r["ask"] == "The chest at 9 64 1?" and r["then"][0][1]["pos"] == {"x": 9, "y": 64, "z": 1}
    assert match("put the cobble in the one I just placed", v)["ask"] == "The chest at 3 64 1?"   # newest placed
    near = view([C(9, 0), C(1, 0)])
    assert match("put the cobble in the chest near you", near)["ask"] == "The chest at 1 64 0?"   # near HER
    assert match("put the cobble in that chest", near)["ask"] == "The chest at 9 64 0?"           # near HIM
    assert match("take 2 bread from my chest", near)["ask"] == "The chest at 9 64 0?"
    assert match("put the cobble in the chest", view())["scan"] == {"center": {"x": 10, "y": 64, "z": 0}, "radius": 16}
    gone = view()
    gone["scanned"] = True
    assert match("put the cobble in the chest", gone) == {"say": "I don't see a chest within 16 blocks."}
    assert match("put the cobble in the chest", view([C(9, 0)], named={"x": 7, "y": 64, "z": 7}))["do"][0][1]["pos"] == \
        {"x": 7, "y": 64, "z": 7}                                                   # the one he confirmed before


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


def chest_row(db, x, z, cid="c1"):
    db.execute("INSERT INTO containers (container_id, world_id, dim, x, y, z, kind, tag, loaded, gone) "
               "VALUES (?,?,?,?,?,?,?,?,1,0)", (cid, "eve-dev:000000000000", "minecraft:overworld", x, 64, z,
                                                "minecraft:chest", "private"))


def test_yes_remembers_tags_and_deposits(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.inventory = [{"slot": 0, "item": "minecraft:cobblestone", "count": 20}]
            chest_row(e.db, 40, 1)                         # private by the envelope's default; 40 blocks from home
            e.mind.world_id = e.mind.world_id or "eve-dev:000000000000"
            await asyncio.sleep(0.3)
            q = await hear(e, "put the cobble in the chest near you")
            assert q.startswith("I don't see a chest") or q.startswith("The chest at"), q    # 40 blocks: not near
            e.db.execute("UPDATE containers SET x=3, z=2 WHERE container_id='c1'")
            assert await hear(e, "put the cobble in the chest near you") == "The chest at 3 64 2?"
            n = len(e.sense.received) if hasattr(e.sense, "received") else 0
            await hear(e, "yes")
            await asyncio.sleep(0.3)
            row = e.db.execute("SELECT params FROM actions WHERE skill='deposit'").fetchone()
            assert row and json.loads(row[0]) == {"pos": {"x": 3, "y": 64, "z": 2}, "item": "minecraft:cobblestone",
                                                  "count": 20}
            assert e.mind.memory.current(e.mind.world_id, "the chest", "location")["object"] == "3 64 2"
            ev = e.db.execute("SELECT payload FROM events WHERE kind IN ('container.tag', 'container.tag.unsent')"
                              ).fetchone()
            tag = json.loads(ev[0])
            assert tag["pos"] == {"x": 3, "y": 64, "z": 2} and tag["tag"] == "shared" and tag["by"] == "Hunter" and \
                tag["line"] == "put the cobble in the chest near you"
    run(go())


def test_no_chest_in_the_index_scans_once(tmp_path, monkeypatch):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.inventory = [{"slot": 0, "item": "minecraft:cobblestone", "count": 20}]
            await asyncio.sleep(0.3)
            asked = []

            async def request(peer, typ, payload, timeout=1.0):
                asked.append((typ, payload))
                lo, hi = payload["min"], payload["max"]
                n = (hi["x"] - lo["x"] + 1) * (hi["y"] - lo["y"] + 1) * (hi["z"] - lo["z"] + 1)
                data = [0] * n
                i = ((64 - lo["y"]) * (hi["z"] - lo["z"] + 1) + (5 - lo["z"])) * (hi["x"] - lo["x"] + 1) + (4 - lo["x"])
                data[i] = 1
                return {"palette": ["minecraft:air", "minecraft:chest[facing=north]"], "data": data, "unloaded": []}
            monkeypatch.setattr(e.mind.hub, "request", request)
            assert await hear(e, "put the cobble in the chest near you") == "The chest at 4 64 5?"
            assert [a[0] for a in asked] == ["query.blocks"]
    run(go())


# ---- O138 (45L: "plan: mine 10 logs then craft a crafting table and place it" -> "'it' - which one?", then "near
# you" -> small talk)

def test_it_after_craft_is_the_thing_crafted():
    from eve_mind.owner_phrases import split_it
    assert split_it("craft a crafting table and place it") == ["craft a crafting table", "place the crafting table"]
    assert split_it("make 4 torches and give them to me") == ["make 4 torches", "give the torches to me"]
    assert split_it("craft a crafting table and place it near you") == ["craft a crafting table",
                                                                       "place the crafting table near you"]
    assert split_it("mine 10 logs") == ["mine 10 logs"] and split_it("put it there") == ["put it there"]


def air(e):
    real = e.mind.hub.request

    async def request(peer, typ, payload, timeout=1.0):
        if typ != "query.blocks":
            return await real(peer, typ, payload, timeout=timeout)
        lo, hi = payload["min"], payload["max"]
        layer = (hi["x"] - lo["x"] + 1) * (hi["z"] - lo["z"] + 1)          # x fastest, then z, then y
        data = [1 if y <= 63 else 0 for y in range(lo["y"], hi["y"] + 1) for _ in range(layer)]   # stone floor
        return {"palette": ["minecraft:air", "minecraft:stone"], "data": data, "unloaded": []}
    e.mind.hub.request = request


def test_plan_runs_each_item_when_it_starts_and_places_what_it_crafted(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said, orig = [], e.mind.say

            async def say(t):
                said.append(t)
                return await orig(t)
            e.mind.say = say
            air(e)
            # the fake body does not add crafted items: the table is already in her bag so the place step can run
            e.body.inventory = [{"slot": 0, "item": "minecraft:oak_log", "count": 2},
                                {"slot": 1, "item": "minecraft:crafting_table", "count": 1}]
            await asyncio.sleep(0.3)
            await e.mind.owner_text("plan: mine 10 logs then craft a crafting table and place it", "Hunter")
            await until(lambda: e.db.execute("SELECT count(*) FROM actions WHERE skill='place_block'").fetchone()[0],
                        timeout=8, what="the place step")
            skills = [r[0] for r in e.db.execute("SELECT skill FROM actions ORDER BY rowid")]
            assert skills[0] == "mine_resource" and skills[-1] == "place_block", skills   # in order, each when it starts
            item = json.loads(e.db.execute("SELECT params FROM actions WHERE skill='place_block'").fetchone()[0])["item"]
            assert item == "minecraft:crafting_table"
            assert not any("which one" in s for s in said), said
    run(go())


def test_a_place_answer_to_her_question_places_never_talks(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            air(e)
            e.body.inventory = [{"slot": 0, "item": "minecraft:crafting_table", "count": 1}]
            await asyncio.sleep(0.3)
            e.mind.last_item = "minecraft:crafting_table"
            assert await hear(e, "put it there") == "'it' - which one, or where exactly?"   # agenda's gate
            await hear(e, "near you")
            await until(lambda: e.db.execute("SELECT count(*) FROM actions WHERE skill='place_block'").fetchone()[0],
                        timeout=3, what="place_block after 'near you'")
            e.mind.asked = {"kind": "rerun", "text": "place the crafting table", "sender": "Hunter", "at": 1e12}
            r = await e.mind.consume_ask("at 3 64 4", "Hunter", "owner")
            assert r is not None and "talk" not in str(r.get("source", ""))
    run(go())


@pytest.mark.parametrize("line,item", [("plan: mine 6 logs then make a chest and place it", "minecraft:chest"),
                                       ("plan: mine 8 cobblestone, make a furnace and then place it", "minecraft:furnace")])
def test_hunters_plans_place_what_they_made_without_asking(tmp_path, line, item):
    async def go():
        async with mind_env(tmp_path) as e:
            said, orig = [], e.mind.say

            async def say(t):
                said.append(t)
                return await orig(t)
            e.mind.say = say
            air(e)
            # the fake body adds nothing it mines or crafts: what each step needs is already in her bag
            e.body.inventory = [{"slot": 0, "item": "minecraft:oak_planks", "count": 8},
                                {"slot": 1, "item": "minecraft:cobblestone", "count": 8},
                                {"slot": 2, "item": "minecraft:crafting_table", "count": 1},
                                {"slot": 3, "item": item, "count": 1}]
            await asyncio.sleep(0.3)
            await e.mind.owner_text(line, "Hunter")
            await until(lambda: e.db.execute("SELECT count(*) FROM actions WHERE skill='place_block'").fetchone()[0],
                        timeout=8, what=f"place_block ({said})")
            placed = json.loads(e.db.execute("SELECT params FROM actions WHERE skill='place_block' ORDER BY rowid DESC"
                                             ).fetchone()[0])["item"]
            assert placed == item and not any("which one" in s for s in said), said
    run(go())


def test_an_explanation_naming_the_item_answers_her_question(tmp_path):
    from eve_mind.owner_phrases import item_in
    assert item_in("it being the chest you have to craft") == "minecraft:chest" and item_in("near you") is None

    async def go():
        async with mind_env(tmp_path) as e:
            air(e)
            e.body.inventory = [{"slot": 0, "item": "minecraft:chest", "count": 1}]
            await asyncio.sleep(0.3)
            assert await hear(e, "put it there") == "'it' - which one, or where exactly?"
            await hear(e, "it being the chest you have to craft")
            await until(lambda: e.db.execute("SELECT count(*) FROM actions WHERE skill='place_block'").fetchone()[0],
                        timeout=3, what="place_block after the explanation")
            assert json.loads(e.db.execute("SELECT params FROM actions WHERE skill='place_block'").fetchone()[0])[
                "item"] == "minecraft:chest"
    run(go())
