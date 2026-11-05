"""O169 (45L S28-DEATH-2 timeline: "hurt, backing off" walked toward Hunter on a pillar, failed unreachable 3x, and
cancelled his "go home"): the back-off never heads for him unless he is at walkable height, falls through each
destination once, takes his "go home" as its destination, and eats only with no hostile within 3. S28-DEATH-2: at 6 HP
or less with a hostile within 8 while body's reflex holds her, she says "I'm hurt, holding." and his non-defensive
orders wait until the reflex ends."""
import json

import pytest

from conftest import mind_env, run, until

ZOMB = {"id": 61, "type": "minecraft:zombie", "pos": {"x": 2.5, "y": 64.0, "z": 0.5}, "distance": 2.0,
        "hostile": True, "protected": False, "line_of_sight": True}
PILLAR = {"id": 9, "uuid": "u-hunter", "type": "minecraft:player", "name": "Hunter",
          "pos": {"x": 6.5, "y": 80.0, "z": 0.5}, "distance": 17.0, "hostile": False, "protected": True}


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def skills(e):
    return [s for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


def test_o169_back_off_skips_a_pillar_owner_takes_go_home_and_never_eats_beside_a_zombie(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s, e.body.food = 1.0, 15
            e.body.inventory = [{"slot": 1, "item": "minecraft:bread", "count": 4}]
            e.body.nearby = [ZOMB, PILLAR]
            await until(lambda: len((e.mind.fresh_state() or {}).get("nearby_entities", [])) == 2)
            e.body.health = 14
            await until(lambda: e.mind._hp == 14)
            e.body.health = 6
            await until(lambda: "I'm hurt, backing off." in said, timeout=5)
            await until(lambda: e.mind.open_action() is not None, what="the first retreat")
            first = json.loads(e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid DESC").fetchone()[1])
            assert e.mind.open_action()["skill"] == "go_home", (skills(e), first)   # not toward him on the pillar
            r = await e.mind.owner_text("go home", "Hunter")                          # his order during the back-off
            assert r == {"ok": True, "redirected": True} and said[-1] == "Heading there.", (r, said)
            await until(lambda: said[-1] == "Ready.", timeout=10, what="ready")
            assert "use_item" not in skills(e), skills(e)                              # the zombie is at 2 m: no eating
            assert skills(e).count("go_home") == 2, skills(e)                          # his redirect ran
    run(go())


@pytest.mark.parametrize("sender,source", [("Hunter", "owner"), ("cli", "cli")])   # CP-86 (c): eve ask defers too
def test_s28_hurt_and_held_by_the_reflex_she_holds_and_defers_his_order(tmp_path, sender, source):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.05
            e.body.nearby = [{**ZOMB, "distance": 5.0}]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"))
            e.body.reflex_active = "shield"
            e.body.health = 6
            await until(lambda: "I'm hurt, holding." in said, timeout=5)
            r = await e.mind.owner_text("go to 5 64 5", sender, source=source)
            assert r == {"ok": True, "deferred": True} and "walk_to" not in skills(e), (r, skills(e))
            e.body.reflex_active = None
            e.body.nearby = []
            await until(lambda: "walk_to" in skills(e), timeout=5, what="the deferred order after the reflex")
    run(go())
