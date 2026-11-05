"""O164 (session 4: "Find exposed stone" -> "I couldn't make a safe plan for that: step 0: mine_resource without a
target..."): find / locate / where is (the nearest) X = the nearest such block within 32 of him, a walk to it, where
it is said relative to him; none -> she says so and offers to dig; a refused plan is one plain sentence to him."""
import json

import pytest

from conftest import mind_env, run, until
from eve_mind.owner_phrases import compass, find_target
from eve_mind.planner import Planner

HUNTER = {"id": 9, "uuid": "u-hunter", "type": "minecraft:player", "name": "Hunter", "pos": {"x": 0.5, "y": 64.0, "z": 0.5},
          "distance": 1.0, "hostile": False, "protected": True, "line_of_sight": True}


def world(e, cells):
    """sense's query.blocks over a world of stone below y 64, air above, plus `cells` {(x, y, z): id}."""
    real = e.mind.hub.request

    async def request(peer, typ, payload, timeout=1.0):
        if typ != "query.blocks":
            return await real(peer, typ, payload, timeout=timeout)
        lo, hi = payload["min"], payload["max"]
        pal, data = ["minecraft:air", "minecraft:dirt"], []
        for y in range(lo["y"], hi["y"] + 1):
            for z in range(lo["z"], hi["z"] + 1):
                for x in range(lo["x"], hi["x"] + 1):
                    b = cells.get((x, y, z), "minecraft:dirt" if y < 64 else "minecraft:air")
                    if b not in pal:
                        pal.append(b)
                    data.append(pal.index(b))
        return {"palette": pal, "data": data, "unloaded": []}
    e.mind.hub.request = request


async def hear(e, text):
    q = len(e.body.received)
    await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": text})
    return (await e.body.wait_for(lambda m: m["type"] == "chat.send", start=q, timeout=8))["payload"]["text"]


@pytest.fixture(autouse=True)
def no_planner(monkeypatch):
    async def refused(self, *a, **k):
        return {"ok": False, "error": "planner_unavailable", "detail": "test stub", "model": "stub",
                "prompt_version": "stub"}
    monkeypatch.setattr(Planner, "plan", refused)


def test_find_words():
    assert find_target("Find exposed stone") == {"what": "stone", "ids": ["minecraft:stone"], "suffix": None,
                                                 "exposed": True}
    assert find_target("where is the nearest water")["ids"] == ["minecraft:water"]
    assert find_target("look for some logs")["suffix"] == "_log"
    for not_find in ("where are you", "where is home", "where is the chest", "find me a moment"):
        assert find_target(not_find) is None, not_find
    assert (compass(20, 0), compass(0, -5), compass(-3, 3)) == ("east", "north", "south-west")


def test_find_exposed_stone_walks_there_and_says_where(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.nearby = [HUNTER]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"), what="his entry in a state frame")
            world(e, {(20, 63, 0): "minecraft:stone", (5, 60, 0): "minecraft:stone"})   # 5 60 0 is buried
            await until(lambda: e.mind.fresh_state() is not None)
            assert await hear(e, "Find exposed stone") == "Stone here, 20 blocks east of you."
            await until(lambda: e.db.execute("SELECT count(*) FROM actions WHERE skill='walk_to'").fetchone()[0])
            p = json.loads(e.db.execute("SELECT params FROM actions WHERE skill='walk_to'").fetchone()[0])
            assert p["pos"] == {"x": 20.5, "y": 64.0, "z": 0.5}
            assert not e.db.execute("SELECT count(*) FROM actions WHERE skill LIKE 'mine%'").fetchone()[0]
    run(go())


def test_where_is_the_nearest_water(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.nearby = [HUNTER]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"), what="his entry in a state frame")
            world(e, {(0, 63, -12): "minecraft:water"})
            await until(lambda: e.mind.fresh_state() is not None)
            assert await hear(e, "where is the nearest water") == "Water here, 12 blocks north of you."
    run(go())


def test_nothing_found_offers_to_dig_and_a_refused_plan_is_one_plain_sentence(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            world(e, {})
            await until(lambda: e.mind.fresh_state() is not None)
            assert await hear(e, "find exposed stone") == "No stone within 32 blocks; want me to dig for it?"

            async def bad_plan(text, registry, caps, st, env):
                return {"ok": True, "plan": {"steps": [{"skill": "mine_resource", "params": {"tag": "minecraft:logs",
                        "count": 1}, "depends_on": [], "x": 1}], "question": ""}, "model": "stub",
                        "prompt_version": "stub", "raw": "", "latency_ms": 1}
            e.mind.planner.plan = bad_plan
            r = await e.mind.owner_text("build me a lighthouse", "Hunter")
            assert r.get("error") == "invalid_plan"
            await until(lambda: [m for m in e.body.received if m["type"] == "chat.send"][-1]["payload"]["text"] ==
                        "I don't know how to do that yet.", timeout=6, what="the plain refusal")   # O114 spacing
    run(go())


# ---- O165 (live at night: "fight all of them" replaced the running fight, then the planner refused it)

ZOMBIE = {"id": 21, "type": "minecraft:zombie", "distance": 5, "hostile": True, "protected": False,
          "pos": {"x": 5, "y": 64, "z": 0}}


@pytest.mark.parametrize("line", ["fight all of them", "all of them", "everything", "keep fighting", "get them all"])
def test_o165_all_of_them_mid_fight_keeps_the_fight(tmp_path, line):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.nearby, e.body.walk_s = [ZOMBIE], 5.0
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"))
            await hear(e, "kill that zombie")
            await until(lambda: (e.mind.open_action() or {"skill": None})["skill"] == "fight_melee", what="the fight")
            aid = e.mind.open_action()["action_id"]
            assert await hear(e, line) == "On it, all of them."
            assert e.mind.status_of(aid) in ("pending", "sent", "accepted", "running"), e.mind.status_of(aid)   # open: no cancel
            assert [r[0] for r in e.db.execute("SELECT skill FROM actions")] == ["fight_melee"]
    run(go())


def test_o165_all_of_them_as_a_fresh_order_fights_every_hostile(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.nearby = [ZOMBIE]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"))
            assert await hear(e, "fight all of them") == "On it, all of them - nearest first."
            await until(lambda: e.db.execute("SELECT count(*) FROM actions WHERE skill='fight_melee'").fetchone()[0])
            assert json.loads(e.db.execute("SELECT params FROM actions WHERE skill='fight_melee'").fetchone()[0]) == {}
    run(go())
