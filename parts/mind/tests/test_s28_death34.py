"""S28-DEATH-3/4 (18:19:56 / 18:20:20): H-recover walked her back into the mob twice; "My things are gone." 2 s after a
death; idle at night in manual mode she stood outside. Recover waits away from a mob at the spot or on the path, says
"gone" only from the spot, stops after a second death within 10 min; the night guard runs O54 when she is idle."""
import asyncio
import json

from conftest import envelope, mind_env, run, until

from eve_mind import mind as mind_mod
from eve_mind import recover

SPOT = {"x": 5.0, "y": 64.0, "z": 5.0}
ZOMBIE = {"id": 77, "type": "minecraft:zombie", "hostile": True, "pos": {"x": 6.0, "y": 64.0, "z": 6.0}, "distance": 8.0}


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


def skills(e):
    return [s for (s,) in e.mind.db.execute("SELECT skill FROM actions ORDER BY rowid")]


async def die(e, cause="zombie"):
    await e.body.send("event", {"kind": "death", "detail": {"pos": dict(SPOT), "dim": "minecraft:overworld",
                                                            "cause": cause}})
    await e.body.send("event", {"kind": "respawn", "detail": {}})


def test_s28_recover_waits_away_from_a_mob_at_the_spot_then_goes(tmp_path, monkeypatch):
    monkeypatch.setattr(recover, "RETRY_S", 0.5)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.1
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.nearby = [dict(ZOMBIE)]
            await die(e)
            await until(lambda: "There are mobs at my things - I'll wait." in said, timeout=5)
            assert SPOT not in walks(e), walks(e)                          # never into the mob
            e.body.nearby = []
            await until(lambda: SPOT in walks(e), timeout=5, what="the walk once the mob is gone")
            await until(lambda: "My things are gone." in said, timeout=5)  # at the spot, no drops there
    run(go())


def test_s28_gone_only_from_the_spot(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 1.0                                             # far away: the frame shows no drops
            await until(lambda: e.mind.fresh_state() is not None)
            await die(e)
            await until(lambda: "Going back for my things." in said, timeout=5)
            await until(lambda: SPOT in walks(e), timeout=5)
            assert "My things are gone." not in said, said                 # not before she stands there
            await until(lambda: "My things are gone." in said, timeout=8)
    run(go())


def test_s28_second_death_within_10_min_stops_and_asks_for_him(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.1
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.nearby = [dict(ZOMBIE)]
            await die(e)
            await until(lambda: "There are mobs at my things - I'll wait." in said, timeout=5)
            await die(e)                                                    # the second death: no recovery
            await until(lambda: "I've died twice; I need you." in said, timeout=8)
            home = e.mind.env["home"]["center"]
            mob_at_home = recover.dist(ZOMBIE["pos"], home) <= recover.DANGER_R
            assert ("go_home" in skills(e)) == (not mob_at_home), (skills(e), home)   # the shelter only if clear
            assert e.mind.recover.needs_owner and not e.mind.recovering
            await e.mind.owner_text("hi Eve", "Hunter")
            assert not e.mind.recover.needs_owner
    run(go())


def test_s28_idle_at_dusk_in_manual_mode_runs_the_night_routine(tmp_path, monkeypatch):
    monkeypatch.setattr(mind_mod, "IDLE_S", 0.3)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.1
            await until(lambda: e.mind.fresh_state() is not None)
            assert e.mind.mode == "manual"
            e.body.time_of_day = 12500                                      # 500 ticks to dusk
            await until(lambda: "It's getting dark - I'm going to the shelter." in said, timeout=8)
            assert e.mind.night.task is not None
            e.mind.night.task.cancel()
    run(go())


def test_o220_recovery_waits_under_the_back_off_and_sends_nothing(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.1
            await until(lambda: e.mind.fresh_state() is not None)
            e.mind.backing_off = True                                       # the back-off owns her
            await die(e)
            await until(lambda: "Going back for my things." in said, timeout=5)
            await asyncio.sleep(1.5)
            assert not walks(e), walks(e)                                   # no walk to race it (refused busy)
            e.mind.backing_off = False
            await until(lambda: SPOT in walks(e), timeout=5, what="the walk once the back-off is over")
    run(go())


def test_o220_the_killer_alive_near_the_pile_keeps_her_away(tmp_path, monkeypatch):
    monkeypatch.setattr(recover, "RETRY_S", 0.5)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.1
            await until(lambda: e.mind.fresh_state() is not None)
            killer = {"id": 88, "type": "minecraft:zombie", "pos": {"x": 18.0, "y": 64.0, "z": 5.0}, "distance": 20.0}
            e.body.nearby = [killer]                                        # 13 from the pile, not flagged hostile
            await e.body.send("event", {"kind": "death", "detail": {"pos": dict(SPOT), "dim": "minecraft:overworld",
                                                                    "cause": "zombie", "killer_id": 88}})
            await e.body.send("event", {"kind": "respawn", "detail": {}})
            await until(lambda: "There are mobs at my things - I'll wait." in said, timeout=5)
            assert SPOT not in walks(e), walks(e)
    run(go())


def test_o220_a_worn_shield_is_said_once_and_a_new_one_crafted(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.05
            ground = {(x, y, z): ("minecraft:stone" if y < 64 else "minecraft:air")
                      for x in range(-6, 7) for y in range(60, 68) for z in range(-6, 7)}

            async def near(st, r, dy):
                return ground
            e.mind.blocks_near = near
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.inventory = [{"slot": 40, "item": "minecraft:shield", "count": 1, "damage": 300, "max_damage": 336},
                                {"slot": 0, "item": "minecraft:iron_ingot", "count": 1},
                                {"slot": 1, "item": "minecraft:oak_planks", "count": 6},
                                {"slot": 2, "item": "minecraft:crafting_table", "count": 1}]   # she carries one live
            await until(lambda: "My shield is nearly broken." in said, timeout=5)
            await until(lambda: "craft" in skills(e), timeout=8, what="the replacement craft")
            assert said.count("My shield is nearly broken.") == 1, said
    run(go())


def test_s28b_the_guarded_night_seals_at_sunset_not_at_13000(tmp_path, monkeypatch):
    """CP-87 19:10:16-19:12:07: the guard started O54 at ~tick 10800, O54 waited for 13000 and sealed 34 s after dark
    (19:11:33). Now the run's night begins at sunset (12000): she is home and the doorway sealed before 13000."""
    monkeypatch.setattr(mind_mod, "IDLE_S", 0.3)
    door = [{"x": 5, "y": 65, "z": 0}, {"x": 5, "y": 64, "z": 0}]       # out of reach from the park spot: sealed
    #                                                                   from the inside stand, as live (0.5,77,2.5)

    env = envelope()
    env["home"]["doorway"] = door                                           # at start: a later edit breaks the binding

    async def go():
        async with mind_env(tmp_path, env=env) as e:
            said = said_by(e)
            e.body.walk_s = 0.05
            e.body.inventory = [{"slot": 0, "item": "minecraft:dirt", "count": 4}]
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.time_of_day = 10803                                      # the live start
            await until(lambda: e.mind.night.task is not None, timeout=5, what="the guarded night run")
            await until(lambda: "I'll seal up at dark." in said, timeout=3)     # the quiet wait reads as meant
            await asyncio.sleep(0.5)
            assert "place_block" not in skills(e)                           # still day: not yet
            e.body.time_of_day = 12100                                      # sunset, before 13000
            await until(lambda: skills(e).count("place_block") == 2, timeout=8, what="the seal at sunset")
            assert skills(e).index("go_home") < skills(e).index("place_block")
            c = e.mind.env["home"]["center"]                                # O236: the night is held off the door
            park = {"x": c["x"] + 0.5, "y": float(c["y"]), "z": c["z"] + 0.5}
            await until(lambda: walks(e) and walks(e)[-1] == park and skills(e)[-1] == "walk_to", timeout=5,
                        what="the walk to the park spot after the seal")
            await asyncio.sleep(0.3)
            me = e.body.pos
            assert all(recover.dist(me, {a: d[a] + 0.5 for a in "xyz"}) >= 1.5 for d in door), (me, door)
            e.mind.night.task.cancel()
    run(go())


def test_cp87_the_dawn_open_waits_for_23500_and_a_clear_doorway(tmp_path, monkeypatch):
    monkeypatch.setattr(mind_mod, "IDLE_S", 0.3)
    door = [{"x": 3, "y": 65, "z": 0}, {"x": 3, "y": 64, "z": 0}]
    env = envelope()
    env["home"]["doorway"] = door
    at_door = {"id": 91, "type": "minecraft:zombie", "hostile": True, "pos": {"x": 6.0, "y": 64.0, "z": 0.5},
               "distance": 6.0}

    async def go():
        async with mind_env(tmp_path, env=env) as e:
            e.body.walk_s = 0.05
            e.body.inventory = [{"slot": 0, "item": "minecraft:dirt", "count": 4}]
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.time_of_day = 14000
            await until(lambda: skills(e).count("place_block") == 2, timeout=8, what="the seal")
            e.body.time_of_day = 23200                                      # old dawn: zombies not burning yet
            await asyncio.sleep(1.5)
            assert "mine_block" not in skills(e), skills(e)
            e.body.nearby = [dict(at_door)]
            e.body.time_of_day = 23600
            await asyncio.sleep(2.0)
            assert "mine_block" not in skills(e), skills(e)                 # a zombie at the door: still shut
            e.body.nearby = []
            await until(lambda: "mine_block" in skills(e), timeout=8, what="the open once the door is clear")
    run(go())


def test_o235_the_dawn_open_steps_back_opens_top_first_and_retries_within_the_dawn(tmp_path, monkeypatch):
    """O235 (21:01 dawn): half a block from the door the bottom cell failed "obstructed" (crosshair on the top cell)
    and she gave up till the next dawn. Now: back to the park spot, top cell first, retry every 20 s for 2 min."""
    from eve_mind import nightsurvival
    monkeypatch.setattr(mind_mod, "IDLE_S", 0.3)
    monkeypatch.setattr(nightsurvival, "OPEN_EVERY_S", 0.5)
    door = [{"x": 3, "y": 65, "z": 0}, {"x": 3, "y": 64, "z": 0}]
    env = envelope()
    env["home"]["doorway"] = door

    async def go():
        async with mind_env(tmp_path, env=env) as e:
            e.body.walk_s = 0.05
            e.body.inventory = [{"slot": 0, "item": "minecraft:dirt", "count": 4}]
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.time_of_day = 14000
            await until(lambda: skills(e).count("place_block") == 2, timeout=8, what="the seal")
            c = e.mind.env["home"]["center"]
            e.body.pos = {"x": 2.48, "y": float(c["y"]), "z": 0.49}           # half a block from the door
            e.body.result_override = {"mine_block": [("failed", "obstructed")] * 4}   # round 1: both cells, both sides
            n0 = len(skills(e))
            e.body.time_of_day = 23600
            await until(lambda: skills(e)[n0:].count("mine_block") >= 6, timeout=10, what="round 2 of the open")
            done = [(s, p) for s, p in [(s, json.loads(p)) for s, p in e.mind.db.execute(
                "SELECT skill, params FROM actions ORDER BY rowid")][n0:]]
            assert done[0][0] == "walk_to" and done[0][1]["pos"] == {"x": c["x"] + 0.5, "y": float(c["y"]),
                                                                      "z": c["z"] + 0.5}, done[:2]   # the park spot
            mines = [p["pos"] for s, p in done if s == "mine_block"]
            assert mines[0] == door[0], mines                                 # the top cell first
            assert mines[-2:] == [door[0], door[1]], mines                   # round 2 opens both, top first
    run(go())


def test_o237_after_the_dawn_open_she_steps_back_off_the_doorway(tmp_path, monkeypatch):
    """O237 (22:01:12-14: the second door mine's drop pickup carried her into the doorway cell; the night ended there
    and a creeper_flee at 22:03 ran her off the step): after the open she walks back to the park spot."""
    monkeypatch.setattr(mind_mod, "IDLE_S", 0.3)
    door = [{"x": 3, "y": 65, "z": 0}, {"x": 3, "y": 64, "z": 0}]
    env = envelope()
    env["home"]["doorway"] = door

    async def go():
        async with mind_env(tmp_path, env=env) as e:
            e.body.walk_s = 0.05
            e.body.inventory = [{"slot": 0, "item": "minecraft:dirt", "count": 4}]
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.time_of_day = 14000
            await until(lambda: skills(e).count("place_block") == 2, timeout=8, what="the seal")
            n0 = len(skills(e))
            e.body.time_of_day = 23600
            await until(lambda: skills(e)[n0:].count("mine_block") >= 1, timeout=8, what="the open")
            e.body.pos = {"x": 3.3, "y": e.body.pos["y"], "z": 0.5}          # the pickup: on the doorway lip
            await until(lambda: e.mind.night.current["status"] != "running", timeout=8, what="the night's end")
            c = e.mind.env["home"]["center"]
            assert walks(e)[-1] == {"x": c["x"] + 0.5, "y": float(c["y"]), "z": c["z"] + 0.5}, walks(e)[-3:]
            assert skills(e)[-1] == "walk_to", skills(e)[-4:]
    run(go())
