"""O146 (Hunter live: "kill the pig nearby" -> planner refusal; "kill 3 sheep" -> "'3' is protected"): killing
livestock is normal survival, by rule, never the planner - the nearest adult, unnamed, untamed animals within 24;
fight_melee on each on 0.15.1. A named animal is never picked; "shear the sheep" stays the wool path."""
import json

from conftest import mind_env, run, until

PIG = {"id": 5, "uuid": "u5", "type": "minecraft:pig", "pos": {"x": 3.5, "y": 64.0, "z": 0.5}, "distance": 3.0,
       "hostile": False, "protected": False, "line_of_sight": True}
SHEEP = [{"id": 10 + i, "uuid": f"s{i}", "type": "minecraft:sheep", "pos": {"x": 4.5 + i, "y": 64.0, "z": 0.5},
          "distance": 4.0 + i, "hostile": False, "protected": False, "line_of_sight": True} for i in range(4)]
SHEEP[0]["name"] = "Dolly"                                       # his named sheep: never a target


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def fights(e):
    return [json.loads(p)["entity_id"] for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")
            if s == "fight_melee"]


def test_o146_livestock_kills_are_rules_never_the_planner(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.05

            async def no_planner(*a, **k):
                raise AssertionError("a kill order never reaches the planner")
            e.mind.planner.plan = no_planner
            e.body.nearby = [PIG] + SHEEP
            await until(lambda: len((e.mind.fresh_state() or {}).get("nearby_entities", [])) == 5)
            await e.mind.owner_text("kill the pig nearby", "Hunter")
            assert fights(e) == [5] and said[-1] == "Going for the pig.", (fights(e), said)
            await until(lambda: e.mind.open_action() is None)
            await e.mind.owner_text("kill 3 sheep", "Hunter")
            await until(lambda: len(fights(e)) == 4 and e.mind.open_action() is None, timeout=5, what="3 sheep in turn")
            assert fights(e)[1:] == [11, 12, 13] and "Going for 3 sheep." in said, (fights(e), said)
            assert not any("protected" in t for t in said), said
            n = len(fights(e))
            await e.mind.owner_text("shear the sheep", "Hunter")                  # the wool path, no kill
            assert len(fights(e)) == n
    run(go())
