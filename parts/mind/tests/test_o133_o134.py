"""O133 (Hunter live: "mine nearest trees" -> "which block do you mean"): clearing the trees is one gather {wood, 256}
call per round, resumed after each inventory_full once her logs are in a chest by where the job started (crafted
and put down the first time); it ends when no tree is left. O134: the mind can serve registry 0.15.1 (EVE_REGISTRY)
and "give me X" then walks to him and dispatches drop_item for real."""
import json

import pytest

import fake_body
from conftest import ROOT, mind_env, run, until

from eve_mind.config import Config

GROUND = {(x, y, z): ("minecraft:stone" if y < 64 else "minecraft:air")
          for x in range(-6, 7) for y in range(60, 68) for z in range(-6, 7)}
HUNTER = {"id": 9, "uuid": "u-hunter", "type": "minecraft:player", "name": "Hunter", "pos": {"x": 6.5, "y": 64.0, "z": 0.5},
          "distance": 6.0, "hostile": False, "protected": True, "line_of_sight": True}


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def actions(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


@pytest.mark.skipif(not (ROOT / "data" / "game" / "26.3" / "recipe").exists(), reason="BLOCKED: data/game/26.3 not extracted")
def test_o133_clear_the_trees_loops_gather_and_stores_logs_in_a_chest_when_full(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.02
            e.body.inventory = [{"slot": 0, "item": "minecraft:oak_log", "count": 20}]
            await until(lambda: (e.mind.fresh_state() or {}).get("inventory"))

            async def near(st, r, dy):
                return GROUND
            e.mind.blocks_near = near
            e.body.result_override = {"gather": [("failed", "inventory_full"), ("failed", "inventory_full"),
                                                 ("failed", "not_found")]}
            r = await e.mind.owner_text("mine nearest trees", "Hunter")
            assert r == {"ok": True, "clearing": 24} and said[0] == "Clearing the nearest trees; say stop when it's enough."
            await until(lambda: any(t.startswith("No more trees") for t in said), timeout=10, what="the end line")
            a = actions(e)
            gathers = [p for s, p in a if s == "gather"]
            assert gathers == [{"product": "wood", "count": 256, "radius": 24}] * 3, gathers
            assert [s for s, p in a].count("place_block") == 2           # her table (then picked back up), the chest
            chest = [p["pos"] for s, p in a if s == "place_block" and p["item"] == "minecraft:chest"]
            assert len(chest) == 1 and [p for s, p in a if s == "deposit"] == [
                {"pos": chest[0], "item": "minecraft:oak_log", "count": 20}] * 2, a     # the 2nd full reuses the chest
            assert sum(t.startswith("Put ") for t in said) == 2, said
    run(go())


def test_o133_a_new_order_ends_the_clearing(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 2.0
            await until(lambda: e.mind.fresh_state() is not None)
            await e.mind.owner_text("clear the forest", "Hunter")
            await until(lambda: e.mind.open_action() is not None and e.mind.open_action()["skill"] == "gather")
            await e.mind.owner_text("go to 3 64 3", "Hunter")
            assert any(t.startswith("Stopping gather") or t.startswith("Stopping clearing") for t in said), said
            await until(lambda: e.mind.open_action() is None, timeout=8)
            assert [s for s, p in actions(e)].count("gather") == 1, actions(e)
    run(go())


def test_o134_registry_override_and_give_dispatches_drop_item(tmp_path, monkeypatch):
    reg = json.loads((ROOT / "contracts" / "skills" / "registry.json").read_text("utf-8"))
    reg["registry_version"] = "0.15.1"
    reg["skills"].append(json.loads((ROOT / "parts" / "mind" / "tests" / "fixtures" / "drop_item_skill_0151.json")
                                    .read_text("utf-8")))
    path = tmp_path / "registry-0.15.1.json"
    path.write_text(json.dumps(reg), "utf-8")
    monkeypatch.setenv("EVE_REGISTRY", str(path))
    assert Config(token="t" * 16, root=ROOT).registry_path == path
    monkeypatch.setattr(fake_body, "REGISTRY", path)                 # body 0.15.1 offers drop_item in its caps

    async def go():
        async with mind_env(tmp_path / "m") as e:
            said = said_by(e)
            e.body.walk_s = 0.02
            e.body.nearby = [HUNTER]
            e.body.inventory = [{"slot": 0, "item": "minecraft:bread", "count": 5},
                                {"slot": 1, "item": "minecraft:oak_log", "count": 3}]
            await until(lambda: (e.mind.fresh_state() or {}).get("inventory") and
                        e.mind.fresh_state()["nearby_entities"])
            assert e.mind.registry.version == "0.15.1"
            await e.mind.owner_text("give me 2 oak logs", "Hunter")
            await until(lambda: [s for s, p in actions(e)][-1:] == ["drop_item"], timeout=5, what="drop_item")
            a = actions(e)
            assert a[-2][0] == "walk_to" and a[-1][1] == {"item": "minecraft:oak_log", "count": 2,
                                                          "toward": {"x": 6, "y": 64, "z": 0}, "kit_ok": True}, a   # named exactly
            await until(lambda: e.mind.open_action() is None)
            await e.mind.owner_text("hand me the bread", "Hunter")          # he named it: kit_ok for that item
            await until(lambda: actions(e)[-1][0] == "drop_item" and actions(e)[-1][1]["item"] == "minecraft:bread",
                        timeout=5, what="bread")
            assert actions(e)[-1][1]["kit_ok"] is True and actions(e)[-1][1]["count"] == 1, actions(e)[-1]
            assert not any("can't hand things over" in t for t in said), said
    (tmp_path / "m").mkdir()
    run(go())
