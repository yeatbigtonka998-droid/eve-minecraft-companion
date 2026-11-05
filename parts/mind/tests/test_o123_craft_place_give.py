"""O123/O124 (45L session 3): "craft a crafting table" was planned with an invented table position and refused; "can you
make a stone pickaxe?" said yes with no cobblestone. Craft, place-beside-her, give and can-make are rules over her
inventory and the recipes - no planner, no model."""
import json

import pytest

from conftest import ROOT, mind_env, run, until

from eve_mind import craftkit

pytestmark = pytest.mark.skipif(not (ROOT / "data" / "game" / "26.3" / "recipe").exists(),
                                reason="BLOCKED: data/game/26.3 not extracted")
GROUND = {(x, y, z): ("minecraft:stone" if y < 64 else "minecraft:air")
          for x in range(-6, 7) for y in range(60, 68) for z in range(-6, 7)}


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def actions(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


def test_o123_craft_chain_table_kit_place_and_can_make(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.02
            e.body.inventory = [{"slot": 0, "item": "minecraft:oak_log", "count": 2}]
            await until(lambda: (e.mind.fresh_state() or {}).get("inventory"))
            blocks = dict(GROUND)

            async def near(st, r, dy):
                return blocks
            e.mind.blocks_near = near
            r = await e.mind.owner_text("can you make a stone pickaxe?", "Hunter")
            assert r["reply"].endswith("I'm missing 3 cobblestone.") and not actions(e), (r, said)
            assert "crafting table" in (await e.mind.owner_text("what can you craft?", "Hunter"))["reply"]
            await e.mind.owner_text("craft a crafting table", "Hunter")          # 2x2: no position, no planner
            await until(lambda: len(actions(e)) == 2 and e.mind.open_action() is None, timeout=5, what="planks + table")
            assert actions(e) == [("craft", {"item": "minecraft:oak_planks", "count": 4}),
                                  ("craft", {"item": "minecraft:crafting_table", "count": 1})], actions(e)
            assert any(t.startswith("Making crafting table:") for t in said), said
            # a 3x3 recipe, no table in reach, one in her kit: put it down beside her, craft, pick it back up
            e.body.inventory = [{"slot": 0, "item": "minecraft:oak_planks", "count": 2},
                                {"slot": 1, "item": "minecraft:cobblestone", "count": 3},
                                {"slot": 2, "item": "minecraft:crafting_table", "count": 1}]
            await until(lambda: len((e.mind.fresh_state() or {}).get("inventory", [])) == 3)
            n = len(actions(e))
            await e.mind.owner_text("craft a stone pickaxe", "Hunter")
            await until(lambda: len(actions(e)) == n + 4 and e.mind.open_action() is None, timeout=5, what="the chain")
            cell = {"x": 0, "y": 64, "z": 1}                                       # yaw 0: she faces south (+z)
            assert actions(e)[n:] == [("craft", {"item": "minecraft:stick", "count": 4}),
                                      ("place_block", {"pos": cell, "item": "minecraft:crafting_table"}),
                                      ("craft", {"item": "minecraft:stone_pickaxe", "count": 1, "pos": cell}),
                                      ("mine_block", {"pos": cell})], actions(e)[n:]
            # a table in reach is used where it stands
            blocks[(2, 64, 0)] = "minecraft:crafting_table"
            n = len(actions(e))
            await e.mind.owner_text("craft a stone pickaxe", "Hunter")
            await until(lambda: len(actions(e)) == n + 2 and e.mind.open_action() is None, timeout=5, what="at the table")
            assert actions(e)[-1] == ("craft", {"item": "minecraft:stone_pickaxe", "count": 1,
                                                "pos": {"x": 2, "y": 64, "z": 0}}), actions(e)[n:]
            # "place it near you": the first free cell she faces, with a floor
            n = len(actions(e))
            await e.mind.owner_text("place the crafting table near you", "Hunter")
            await until(lambda: len(actions(e)) == n + 1, what="the place")
            assert actions(e)[-1] == ("place_block", {"pos": cell, "item": "minecraft:crafting_table"}), actions(e)[-1]
            # O124: drop_item is a registry 0.16.0 skill - on 0.15.0 she says so, nothing is sent
            await until(lambda: e.mind.open_action() is None)
            n = len(actions(e))
            r = await e.mind.owner_text("give me the crafting table", "Hunter")
            assert said[-1] == "I can't hand things over yet." and len(actions(e)) == n, (r, said)
            r = await e.mind.owner_text("give me a moment", "Hunter")             # not an item: not a give order
            assert said[-1] != "I can't hand things over yet.", said
    run(go())


def test_o123_free_cell_and_block_decode():
    lo, hi = {"x": 0, "y": 63, "z": 0}, {"x": 1, "y": 64, "z": 1}
    ans = {"palette": ["minecraft:stone", "minecraft:air", "minecraft:oak_stairs"], "data": [0, 0, 0, 0, 1, 2, 1, 1]}
    b = craftkit.decode(ans, lo, hi)                                        # x fastest, then z, then y
    assert b[(0, 63, 0)] == "minecraft:stone" and b[(1, 64, 0)] == "minecraft:oak_stairs" and b[(0, 64, 1)] == "minecraft:air"
    st = {"pos": {"x": 0.5, "y": 64, "z": 0.5}, "yaw": 90}                  # yaw 90: she faces west (-x)
    assert craftkit.free_cell(dict(GROUND), st) == {"x": -1, "y": 64, "z": 0}
    assert craftkit.free_cell({}, st) is None
