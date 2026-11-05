"""H-recover (Hunter's rule after S28-DEATH-2): after a death outside a night run she goes back for her drops at once,
walks the pile, says how many came back; his orders wait (except stop); lava / void / another dimension: gone."""
import json

from conftest import mind_env, run, until

PILE = [{"id": 90 + i, "type": "minecraft:item", "pos": {"x": 5.5 + i * 0.5, "y": 64.0, "z": 5.5}, "distance": 7.0}
        for i in range(2)]


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def walks(e):
    return [json.loads(p)["pos"] for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")
            if s == "walk_to"]


async def die(e, cause="zombie"):
    await e.body.send("event", {"kind": "death", "detail": {"pos": {"x": 5.0, "y": 64.0, "z": 5.0},
                                                            "dim": "minecraft:overworld", "cause": cause}})
    await e.body.send("event", {"kind": "respawn", "detail": {}})


def test_h_recover_goes_back_walks_the_pile_and_queues_his_order(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.3
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.nearby = list(PILE)
            await die(e)
            await until(lambda: "Going back for my things." in said, timeout=5)
            r = await e.mind.owner_text("go to 9 64 9", "Hunter")                 # queued behind the recovery
            assert r == {"ok": True, "deferred": True}, r
            await until(lambda: len(walks(e)) >= 3, timeout=8, what="death spot + the pile")
            assert walks(e)[0] == {"x": 5.0, "y": 64.0, "z": 5.0} and walks(e)[1]["x"] == 5.5, walks(e)
            e.body.inventory = [{"slot": 0, "item": "minecraft:stone_pickaxe", "count": 1},
                                {"slot": 1, "item": "minecraft:bread", "count": 6}]
            e.body.nearby = []                                                       # picked up on contact
            await until(lambda: any(t.startswith("Got my things back: 7 items") for t in said), timeout=10)
            await until(lambda: walks(e)[-1] == {"x": 9.0, "y": 64.0, "z": 9.0}, timeout=5, what="his order after")
    run(go())


def test_h_recover_lava_means_gone(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await until(lambda: e.mind.fresh_state() is not None)
            await die(e, cause="lava")
            await until(lambda: "My things are gone." in said, timeout=5)
            assert not walks(e) and not e.mind.recovering
    run(go())
