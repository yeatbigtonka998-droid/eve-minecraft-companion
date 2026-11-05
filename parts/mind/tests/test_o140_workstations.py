"""O140 (45L 2026-10-22, mind 69216315): "make a chest" crafted a second table and stopped on "place block: occupied";
"eve go to the crafting table and make a chest" / "go into the crafting table and craft a chest" asked "Where exactly?"
with a table 2 blocks from her at 7 71 21. Fake sense (blocks_near) and fake body; no model."""
import json

import pytest

from conftest import ROOT, mind_env, run, until

from eve_mind import workstations

pytestmark = pytest.mark.skipif(not (ROOT / "data" / "game" / "26.3" / "recipe").exists(),
                                reason="BLOCKED: data/game/26.3 not extracted")
ME = {"x": 5.5, "y": 71.0, "z": 21.5}                       # Hunter's screenshot: the table 2 blocks away at 7 71 21
TABLE = (7, 71, 21)


def ground():
    return {(x, y, z): ("minecraft:grass_block" if y < 71 else "minecraft:air")
            for x in range(-14, 26) for y in range(67, 76) for z in range(3, 40)}


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def actions(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


async def world(e, blocks, inv, pos=ME):
    e.body.walk_s = 0.02
    e.body.pos = dict(pos)
    e.body.inventory = [{"slot": i, "item": k, "count": n} for i, (k, n) in enumerate(inv.items())]
    await until(lambda: (e.mind.fresh_state() or {}).get("pos") == pos and
                len((e.mind.fresh_state() or {}).get("inventory", [])) == len(inv), what="state")

    async def near(st, r, dy):
        return blocks
    e.mind.blocks_near = near


async def settled(e, n, what):
    await until(lambda: len(actions(e)) == n and e.mind.open_action() is None and
                (e.mind.steps_task is None or e.mind.steps_task.done()), timeout=6, what=what)


WALK = ("walk_to", {"pos": {"x": 7.5, "y": 71.0, "z": 21.5}, "tolerance": 2.0})
CHEST_AT_TABLE = ("craft", {"item": "minecraft:chest", "count": 1, "pos": dict(zip("xyz", TABLE))})


@pytest.mark.parametrize("line", ["eve go to the crafting table and make a chest",
                                  "go into the crafting table and craft a chest"])
def test_o140_hunters_go_to_table_lines(tmp_path, line):
    """(1)+(2)+(5): no "Where exactly?" - walk to the nearest table, craft the chest there, said in one line."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            blocks = ground()
            blocks[TABLE] = "minecraft:crafting_table"
            await world(e, blocks, {"minecraft:oak_planks": 8})
            r = await e.mind.owner_text(line, "Silvershark3600")
            assert r.get("steps") == 2, (r, said)
            await settled(e, 2, "walk + craft")
            assert said == ["Walking to the crafting table at 7 71 21, then making a chest."], said
            assert actions(e) == [WALK, CHEST_AT_TABLE], actions(e)
    run(go())


def test_o140_make_a_chest_uses_carried_table_and_retries_occupied(tmp_path):
    """(3)+(4): Hunter's "make a chest" - she carries a table: never crafts one; the first cell is occupied, the next
    free cell is used and the craft and the pick-up follow it."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await world(e, ground(), {"minecraft:oak_planks": 8, "minecraft:crafting_table": 1})
            e.body.result_override = {"place_block": [("failed", "occupied")]}
            await e.mind.owner_text("make a chest", "Silvershark3600")
            await settled(e, 4, "the chain")
            a = actions(e)
            assert not any(s == "craft" and p["item"] == "minecraft:crafting_table" for s, p in a), a
            first, second = a[0][1]["pos"], a[1][1]["pos"]
            assert [s for s, _ in a] == ["place_block", "place_block", "craft", "mine_block"] or \
                [s for s, _ in a] == ["place_block", "place_block", "craft", "mine_block"], a
            assert first == {"x": 5, "y": 71, "z": 22} and second != first, a     # yaw 0: south first
            assert a[2] == ("craft", {"item": "minecraft:chest", "count": 1, "pos": second}), a
            assert a[3] == ("mine_block", {"pos": second}), a
            assert said[0].startswith("Making chest:") and "1 crafting table" not in said[0], said
            assert not any("stopped" in t for t in said), said
    run(go())


def test_o140_cell_checked_before_dispatch_and_eight_tries(tmp_path):
    """(4): Hunter standing in the cell she faces -> that cell is never sent; 8 occupied answers -> she stops, said."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await world(e, ground(), {"minecraft:crafting_table": 1})
            e.body.nearby = [{"type": "minecraft:player", "name": "Silvershark3600", "uuid": "u1", "distance": 1,
                              "pos": {"x": 5.4, "y": 71.0, "z": 22.6}}]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"), what="Hunter seen")
            await e.mind.owner_text("place the crafting table near you", "Silvershark3600")
            await settled(e, 1, "one place")
            assert actions(e)[0][1]["pos"] != {"x": 5, "y": 71, "z": 22}, actions(e)
            e.body.result_override = {"place_block": [("failed", "occupied")] * 30}
            await e.mind.owner_text("place the crafting table near you", "Silvershark3600")
            await settled(e, 1 + workstations.TRIES, "eight tries")
            cells = [json.dumps(p["pos"]) for _, p in actions(e)[1:]]
            assert len(set(cells)) == workstations.TRIES, cells
            assert said[-1].startswith("I stopped after place block") or "occupied" in said[-1] or \
                "spot" in said[-1].lower(), said
    run(go())


def test_o140_chain_walks_to_far_table_instead_of_crafting_one(tmp_path):
    """(3): no table in reach, none carried, one 9 blocks away -> walk there and craft; no new table."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            blocks = ground()
            blocks[(14, 71, 21)] = "minecraft:crafting_table"
            await world(e, blocks, {"minecraft:oak_planks": 8})
            await e.mind.owner_text("make a chest", "Silvershark3600")
            await settled(e, 2, "walk + craft")
            assert said == ["Walking to the crafting table at 14 71 21, then making a chest."], said
            assert actions(e)[1] == ("craft", {"item": "minecraft:chest", "count": 1,
                                               "pos": {"x": 14, "y": 71, "z": 21}}), actions(e)
    run(go())


def test_o140_ask_only_when_none_then_his_xyz(tmp_path, caplog):
    caplog.set_level("INFO", logger="eve.mind")

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            blocks = ground()
            await world(e, blocks, {"minecraft:oak_planks": 8})
            r = await e.mind.owner_text("eve go to the crafting table and make a chest", "Silvershark3600")
            assert r.get("question") and not actions(e), r
            assert said == ["I don't see a crafting table within 16 blocks of us. Where is it? Give me the x y z."], said
            assert "O140 crafting table search: query.blocks radius 16" in caplog.text and "0 matches" in caplog.text
            blocks[TABLE] = "minecraft:crafting_table"
            await e.mind.owner_text("7 71 21", "Silvershark3600")
            await settled(e, 2, "walk + craft")
            assert actions(e) == [WALK, CHEST_AT_TABLE], actions(e)
            assert said[-1] == "Walking to the crafting table at 7 71 21, then making a chest.", said
    run(go())


def test_o140_open_table_and_furnace_smelt(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            blocks = ground()
            blocks[TABLE] = "minecraft:crafting_table"
            blocks[(3, 71, 25)] = "minecraft:furnace"
            await world(e, blocks, {"minecraft:raw_iron": 3, "minecraft:coal": 2})
            await e.mind.owner_text("open the crafting table", "Silvershark3600")
            await settled(e, 2, "walk + use")
            assert actions(e)[1] == ("use_item", {"target_pos": {"x": 7, "y": 71, "z": 21}}), actions(e)
            assert said[-1] == "Walking to the crafting table at 7 71 21, then opening it.", said
            await e.mind.owner_text("go to the furnace near you and smelt the iron", "Silvershark3600")
            await settled(e, 4, "walk + smelt")
            assert actions(e)[3] == ("smelt", {"pos": {"x": 3, "y": 71, "z": 25}, "item": "minecraft:iron_ingot",
                                               "count": 3}), actions(e)
            assert said[-1].startswith("Walking to the furnace at 3 71 25, then smelting 3 raw iron"), said
    run(go())


def test_o140_parse():
    p = workstations.parse
    assert p("eve go to the crafting table and make a chest")[:3] == ("crafting table", "make a chest", False)
    assert p("go into the crafting table and craft a chest")[:3] == ("crafting table", "craft a chest", False)
    assert p("open that furnace")[:3] == ("furnace", None, True)
    assert p("make a chest at the crafting table near me")[:2] == ("crafting table", "make a chest")
    assert p("open the chest") is None                      # owner_phrases' look-inside stays
    assert p("go to 7 71 21") is None and p("make a chest") is None


# --- O140-b (45L: "go to the water nearby and place the boat" / "place boat in the water" / "55 62 -19 place boat"
#     -> "Which block, and where? For example: place cobblestone at x y z.") ------------------------------------------
SHORE = {"x": 40.5, "y": 63.0, "z": -19.5}


def lakeside(water=True):
    b = {(x, y, z): ("minecraft:grass_block" if y < 63 else "minecraft:air")
         for x in range(30, 62) for y in range(58, 66) for z in range(-26, -12)}
    if water:
        for x in range(53, 59):
            for z in range(-21, -16):
                b[(x, 62, z)] = "minecraft:water"
    return b


async def shore_world(e, blocks, inv):
    e.body.walk_s = 0.02
    e.body.pos = dict(SHORE)
    e.body.inventory = [{"slot": s, "item": k, "count": 1} for k, s in inv.items()]
    await until(lambda: (e.mind.fresh_state() or {}).get("pos") == SHORE and
                len((e.mind.fresh_state() or {}).get("inventory", [])) == len(inv), what="state")

    async def near(st, r, dy):
        return blocks
    e.mind.blocks_near = near


BOAT_AT = [("select_hotbar", {"slot": 2}), ("look_at", {"pos": {"x": 53.5, "y": 62.9, "z": -19.5}}), ("use_item", {})]   # body: aim, then use with no target


@pytest.mark.parametrize("line", ["go to the water nearby and place the boat", "place boat in the water"])
def test_o140b_hunters_boat_lines(tmp_path, line):
    """Nearest water, walk to the shore cell beside it, select the boat's hotbar slot, use it on the water. One line."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await shore_world(e, lakeside(), {"minecraft:oak_boat": 2})
            r = await e.mind.owner_text(line, "Silvershark3600")
            assert r.get("steps") == 4, (r, said)
            await settled(e, 4, "walk + select + aim + use")
            assert actions(e) == [("walk_to", {"pos": {"x": 52.5, "y": 63.0, "z": -19.5}, "tolerance": 1.0})] + BOAT_AT, \
                actions(e)
            assert said == ["Walking to the water at 53 62 -20, then putting the boat on the water."], said
            assert not any(s == "place_block" for s, _ in actions(e))
    run(go())


def test_o140b_coordinates_first_and_as_answer(tmp_path):
    """"55 62 -19 place boat" is an order with its target; with no water in sight she asks (never "place cobblestone")
    and his bare "55 62 -19" is consumed as the target."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            blocks = lakeside(water=False)
            await shore_world(e, blocks, {"minecraft:oak_boat": 2})
            r = await e.mind.owner_text("place boat in the water", "Silvershark3600")
            assert r.get("question") and not actions(e), r
            assert said == ["I don't see any water within 32 blocks. Where should I put the boat? Give me the x y z."], said
            assert not any("cobblestone" in t for t in said)
            blocks.update(lakeside())
            await e.mind.owner_text("55 62 -19", "Silvershark3600")
            await settled(e, 4, "the answer used")
            assert actions(e)[-2:] == [("look_at", {"pos": {"x": 55.5, "y": 62.9, "z": -18.5}}), ("use_item", {})], actions(e)
            assert said[-1] == "Walking to the water at 55 62 -19, then putting the boat on the water.", said
            await e.mind.owner_text("55 62 -19 place boat", "Silvershark3600")
            await settled(e, 7, "coordinates first")         # she stands there already: select + aim + use
            assert actions(e)[-2:] == [("look_at", {"pos": {"x": 55.5, "y": 62.9, "z": -18.5}}), ("use_item", {})], actions(e)
            assert not any("Which block" in t for t in said), said
    run(go())


def test_o140b_boat_not_in_hotbar_and_natural_places(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            blocks = lakeside()
            blocks[(44, 63, -15)] = blocks[(44, 64, -15)] = "minecraft:oak_log"
            await shore_world(e, blocks, {"minecraft:oak_boat": 20})
            await e.mind.owner_text("place the boat in the water", "Silvershark3600")
            assert said[-1] == "My oak boat is in my pack, not my hotbar - put it in my hotbar and tell me again.", said
            assert not actions(e)
            await e.mind.owner_text("go to the nearest tree", "Silvershark3600")
            await settled(e, 1, "walk to the tree")
            assert said[-1] == "Walking to the tree at 44 63 -15.", said
            assert actions(e)[0][0] == "walk_to" and actions(e)[0][1]["tolerance"] == 1.0, actions(e)
            blocks[(43, 62, -17)] = "minecraft:water"                          # a puddle, nearer than the pond
            await until(lambda: (e.mind.fresh_state() or {})["pos"]["x"] < 46, what="by the tree")
            await e.mind.owner_text("go to the water", "Silvershark3600")
            await settled(e, 2, "walk to the water")
            assert said[-1] == "Walking to the water at 43 62 -17.", said
            await e.mind.owner_text("go to the lake", "Silvershark3600")
            await settled(e, 3, "walk to the lake")
            assert said[-1].startswith("Walking to the water at 53 62 -"), said      # 3+ water neighbours: not the puddle
            await e.mind.owner_text("go to the hill", "Silvershark3600")
            assert said[-1] == "Which hill? Give me its x y z.", said
    run(go())


def test_o140b_parse():
    assert workstations.pull_xyz("55 62 -19 place boat") == ("place boat", {"x": 55, "y": 62, "z": -19})
    assert workstations.USE.match("place boat in the water")[1] == "boat"
    assert workstations.USE.match("put the oak boat in the lake")[1] == "oak boat"
    assert workstations.USE.match("place cobblestone at 1 2 3") is None          # a block: place_block's path
    assert workstations.GO_OUT.match("go to the water nearby and place the boat")[2] == "place the boat"


# --- O147 (45L, 0.15.1: "place it near you" x3 -> twice "occupied (Eve herself stands in the target space)") ---------
@pytest.mark.parametrize("pos,banned", [
    ({"x": 5.9, "y": 71.0, "z": 21.1}, {(5, 20), (6, 20), (6, 21)}),           # x.9 / z.1 edge
    ({"x": 5.1, "y": 71.0, "z": 21.5}, {(4, 21)}),                             # x.1 edge
    ({"x": 5.5, "y": 71.0, "z": 21.9}, {(5, 22)}),                             # z.9: the cell she faces is in her box
])
def test_o147_never_a_cell_her_box_touches(tmp_path, pos, banned):
    async def go():
        async with mind_env(tmp_path) as e:
            await world(e, ground(), {"minecraft:crafting_table": 1}, pos)
            for c in banned:
                assert workstations.in_her_box((c[0], 71, c[1]), {"pos": pos}), c
            await e.mind.owner_text("place the crafting table near you", "Silvershark3600")
            await settled(e, 1, "one place, first try")
            (skill, p), = actions(e)
            assert skill == "place_block" and (p["pos"]["x"], p["pos"]["z"]) not in banned, p
            x, z = p["pos"]["x"] + .5 - pos["x"], p["pos"]["z"] + .5 - pos["z"]
            assert (x * x + z * z) ** .5 >= 1.0, p
            if pos["z"] < 21.8:
                assert p["pos"] == {"x": 5, "y": 71, "z": 22}, p                # the cell ahead, >= 1.0 from her
    run(go())


def test_o147_all_eight_blocked_steps_back_first(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            blocks = ground()
            for x, z in ((4, 22), (6, 22), (4, 21), (6, 21), (4, 20), (5, 20), (6, 20)):
                blocks[(x, 71, z)] = "minecraft:oak_sapling"                   # not free; (5, 22) is in her box
            pos = {"x": 5.5, "y": 71.0, "z": 21.9}
            await world(e, blocks, {"minecraft:crafting_table": 1}, pos)
            await e.mind.owner_text("place the crafting table near you", "Silvershark3600")
            await settled(e, 2, "walk back + place")
            (w, wp), (pl, pp) = actions(e)
            assert w == "walk_to" and wp["tolerance"] == 0.5 and abs(wp["pos"]["z"] - 20.9) < 1e-6 and                 wp["pos"]["x"] == 5.5, wp                                      # one block back along her facing (+z)
            assert pl == "place_block" and pp["pos"] == {"x": 5, "y": 71, "z": 19}, pp
            assert not any("stopped" in t for t in said), said
    run(go())


# --- O148 (45L: "use the wooden shovel to mine 10 grass blocks" -> "Which block? Give me its x y z.") -------------
GRASS10 = ("mine_resource", {"block": "minecraft:grass_block", "count": 10, "max_blocks": 64, "radius": 32})   # + O143 budget, O163 radius


@pytest.mark.parametrize("line,inv,select,said_line", [
    ("use the wooden shovel to mine 10 grass blocks", {"minecraft:wooden_shovel": 1}, 1, None),
    ("use the wooden pickaxe to mine 10 grass blocks", {"minecraft:wooden_pickaxe": 0, "minecraft:stone_shovel": 3}, 3,
     "A wooden pickaxe won't help with grass block - using my stone shovel."),
    ("use the diamond shovel to mine 10 grass blocks", {"minecraft:wooden_shovel": 2}, 2,
     "I don't have a diamond shovel - using my wooden shovel."),
    ("mine 10 grass blocks with my hands", {"minecraft:wooden_shovel": 0}, 1, None),
])
def test_o148_tool_hint(tmp_path, line, inv, select, said_line):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await shore_world(e, lakeside(), inv)
            await e.mind.owner_text(line, "Silvershark3600")
            await settled(e, 2, "select + mine")
            assert actions(e) == [("select_hotbar", {"slot": select}), GRASS10], (actions(e), said)
            assert not any("Which block" in t for t in said), said
            if said_line:
                assert said_line in said, said
    run(go())


def test_o148_parse():
    th = workstations.tool_hint
    assert th("use the wooden shovel to mine 10 grass blocks") == ("mine 10 grass blocks", "wooden shovel")
    assert th("with my pickaxe, mine 5 stone") == ("mine 5 stone", "pickaxe")
    assert th("mine 10 dirt by hand") == ("mine 10 dirt", "hands")
    assert th("fill the bucket with water") == ("fill the bucket with water", None)
    assert th("use the flint and steel on the portal")[1] is None


# --- O151 (obsidian + a nether portal) ---------------------------------------------------------------------------
LAVA = (40, 62, -24)


def lava_world():
    b = lakeside()
    for x in (39, 40, 41):
        for z in (-25, -24):
            b[(x, 62, z)] = "minecraft:lava"
    return b


def skills(e):
    return [s for s, _ in actions(e)]


def test_o151_fill_and_pour(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await shore_world(e, lava_world(), {"minecraft:bucket": 0})
            await e.mind.owner_text("fill the bucket with water", "Silvershark3600")
            await settled(e, 4, "fill")
            assert skills(e) == ["select_hotbar", "walk_to", "look_at", "use_item"], actions(e)
            assert actions(e)[2][1] == {"pos": {"x": 53.5, "y": 62.5, "z": -19.5}} and actions(e)[3][1] == {}, actions(e)
            assert said[-1] == "Filling my bucket with water at 53 62 -20.", said
            e.body.pos = dict(SHORE)
            e.body.inventory = [{"slot": 0, "item": "minecraft:water_bucket", "count": 1}]
            await until(lambda: (e.mind.fresh_state() or {}).get("pos") == SHORE and any(
                s["item"] == "minecraft:water_bucket" for s in (e.mind.fresh_state() or {}).get("inventory", [])))
            await e.mind.owner_text("pour the water on the lava", "Silvershark3600")
            await settled(e, 8, "pour")
            assert skills(e)[4:] == ["select_hotbar", "walk_to", "look_at", "use_item"], actions(e)
            assert actions(e)[6][1] == {"pos": {"x": 40.5, "y": 62.5, "z": -23.5}}, actions(e)
            assert said[-1] == "Pouring the water on the lava (40 62 -24).", said
    run(go())


def test_o151_make_obsidian(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await shore_world(e, lava_world(), {"minecraft:bucket": 0})
            await e.mind.owner_text("make obsidian", "Silvershark3600")
            assert said[-1] == "I need a diamond pickaxe to mine obsidian." and not actions(e), said
            e.body.inventory = [{"slot": 0, "item": "minecraft:bucket", "count": 1},
                                {"slot": 1, "item": "minecraft:diamond_pickaxe", "count": 1}]
            await until(lambda: len((e.mind.fresh_state() or {}).get("inventory", [])) == 2)
            await e.mind.owner_text("make obsidian", "Silvershark3600")
            await settled(e, 10, "the obsidian chain")
            assert skills(e) == ["select_hotbar", "walk_to", "look_at", "use_item",           # fill at the pond
                                 "select_hotbar", "walk_to", "look_at", "use_item",           # pour on the lava
                                 "select_hotbar", "mine_block"], actions(e)
            assert actions(e)[4][1] == {"slot": 0} and actions(e)[8][1] == {"slot": 1}, actions(e)
            assert actions(e)[9][1] == {"pos": dict(zip("xyz", LAVA)), "expect_block": "minecraft:obsidian"}, actions(e)
            assert said[-1] == ("Making obsidian: fill my bucket at 53 62 -20, then pour it on the lava at 40 62 -24, "
                                "then mine the obsidian with my diamond pickaxe."), said
    run(go())


@pytest.mark.parametrize("line", ["light the portal", "use the flint and steel on the portal"])
def test_o151_light_the_portal(tmp_path, line):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            b = lakeside()
            b[(36, 63, -19)] = b[(36, 67, -19)] = b[(35, 63, -19)] = "minecraft:obsidian"
            b[(36, 66, -19)] = "minecraft:air"                                 # the world above y 65
            await shore_world(e, b, {"minecraft:flint_and_steel": 4})
            await e.mind.owner_text(line, "Silvershark3600")
            await settled(e, 4, "light")
            assert skills(e) == ["select_hotbar", "walk_to", "look_at", "use_item"], actions(e)
            assert actions(e)[0][1] == {"slot": 4} and actions(e)[2][1] == {"pos": {"x": 36.5, "y": 63.5, "z": -18.5}}
            assert said[-1] == "Lighting the portal at 36 63 -19.", said
    run(go())
