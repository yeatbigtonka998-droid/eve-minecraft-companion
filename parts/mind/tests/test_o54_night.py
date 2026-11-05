"""O54 night_survival on fakes: dusk to dawn at the shelter, a cornering melee mob is fought, a death is followed by
respawn -> equip_best -> walk to the drops -> shelter, and a death in lava is not chased."""
import asyncio
import json

from conftest import mind_env, run, until

PICK = {"slot": 9, "item": "minecraft:stone_pickaxe", "count": 1}     # O101: the lane kit carries one
ZOMBIE = {"id": 5, "type": "minecraft:zombie", "name": None, "pos": {"x": 2, "y": 64, "z": 0}, "distance": 2.5,
          "hostile": True, "protected": False, "line_of_sight": True}


def skills(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


async def night_start(e, away=True):
    e.body.walk_s, e.body.time_of_day = 0.05, 14000
    if away:
        e.body.pos = {"x": 20.5, "y": 64.0, "z": 9.5}
    await until(lambda: (e.mind.fresh_state() or {}).get("time_of_day") == 14000)
    r = e.mind.night.start()
    assert r["ok"], r
    return r["objective_id"]


async def dawn(e):
    e.body.time_of_day = 23500
    await until(lambda: e.mind.night.current["status"] != "running", timeout=10, what="night ends")
    return e.mind.night.current


def test_quiet_night_goes_to_the_shelter_and_ends_at_dawn(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            await night_start(e)
            await until(lambda: any(s == "go_home" for s, _ in skills(e)))
            await asyncio.sleep(0.6)
            r = await dawn(e)
            assert r["status"] == "done" and r["deaths"] == [] and r["fights"] == [], r
            assert [p["name"] for p in r["phases"]][:2] == ["dusk", "shelter"]
            assert [s for s, _ in skills(e)] == ["go_home"]          # no wandering, one trip home
    run(go())


def test_a_melee_mob_cornering_her_at_the_shelter_is_fought(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            await night_start(e, away=False)
            e.body.nearby, e.body.threats = [ZOMBIE], [{"entity_id": 5, "kind": "melee", "danger": 0.6}]
            await until(lambda: any(s == "fight_melee" for s, _ in skills(e)), timeout=5)
            e.body.nearby, e.body.threats = [], []
            r = await dawn(e)
            fight = next(p for s, p in skills(e) if s == "fight_melee")
            assert fight["entity_id"] == 5 and fight["health_floor"] == 8, fight
            assert r["status"] == "done" and len(r["fights"]) >= 1 and r["fights"][0]["type"] == "minecraft:zombie"
    run(go())


def test_low_health_does_not_pick_a_fight(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.health = 6
            await night_start(e, away=False)
            e.body.nearby, e.body.threats = [ZOMBIE], [{"entity_id": 5, "kind": "melee", "danger": 0.6}]
            await asyncio.sleep(1.5)
            r = await dawn(e)
            assert not any(s == "fight_melee" for s, _ in skills(e)) and r["fights"] == []
    run(go())


def test_death_then_respawn_recovers_the_drops_and_returns_to_the_shelter(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            kit = [{"slot": 0, "item": "minecraft:iron_sword", "count": 1},
                   {"slot": 1, "item": "minecraft:bread", "count": 6}]
            e.body.inventory = list(kit)
            base = e.body.execute

            async def execute(aid, p, req_id):         # walking over the drops picks them up (vanilla contact)
                if p["skill"] == "walk_to" and p["params"]["pos"] == spot:
                    e.body.inventory = list(kit)
                return await base(aid, p, req_id)
            e.body.execute, spot = execute, {"x": 30.5, "y": 64.0, "z": -4.5}
            await night_start(e, away=False)
            await asyncio.sleep(0.5)
            e.body.inventory = []                       # keep_inventory off: everything dropped at the spot
            await e.body.send("event", {"kind": "death", "detail": {"pos": spot, "dim": e.body.dim, "cause": "mob",
                                                                    "killer_type": "minecraft:zombie"}})
            await e.body.send("event", {"kind": "respawn", "detail": {"pos": {"x": 0.5, "y": 64, "z": 0.5},
                                                                      "dim": e.body.dim}})
            await until(lambda: e.mind.night.current["deaths"] and
                        e.mind.night.current["deaths"][0]["recovered"] is not None, timeout=10)
            r = await dawn(e)
            d = r["deaths"][0]
            assert d["recovered"] is True and d["cause"] == "mob" and d["killer_type"] == "minecraft:zombie", d
            seq = [s for s, _ in skills(e)]
            i = seq.index("walk_to")
            assert seq[i - 1] == "equip_best" and seq[i + 1:i + 3] == ["equip_best", "equip_best"], seq
            assert seq[i + 3] == "go_home" and skills(e)[i][1] == {"pos": spot, "tolerance": 1.0}
            assert r["status"] == "done" and "1 death(s)" in r["report"]
    run(go())


def test_a_death_in_lava_is_not_chased(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            await night_start(e, away=False)
            await e.body.send("event", {"kind": "death", "detail": {"pos": {"x": 9, "y": 11, "z": 9},
                                                                    "dim": e.body.dim, "cause": "lava"}})
            await e.body.send("event", {"kind": "respawn", "detail": {"pos": {"x": 0.5, "y": 64, "z": 0.5}}})
            await until(lambda: e.mind.night.current["deaths"] and
                        e.mind.night.current["deaths"][0]["recovered"] is False, timeout=10)
            r = await dawn(e)
            assert "lava" in r["deaths"][0]["note"] and not any(s == "walk_to" for s, _ in skills(e))
            assert "without recovering" in r["report"], r["report"]
    run(go())


def test_o83_a9_a_skeleton_inside_the_shelter_is_fought_one_outside_is_not(tmp_path):
    """CP-31 a9 night 0, first death: a skeleton walked in through the open doorway and stood 2.8 blocks from her,
    inside the shelter; cornering() took only melee kinds, so she never swung. A ranged mob inside is fought; one
    outside the shelter (shooting through the door) is never chased."""
    outside = {"id": 7, "type": "minecraft:skeleton", "name": None, "pos": {"x": 4.5, "y": 64, "z": 3.5},
               "distance": 4.9, "hostile": True, "protected": False, "line_of_sight": True}
    inside = {"id": 8, "type": "minecraft:skeleton", "name": None, "pos": {"x": 2.5, "y": 64, "z": 2.5},
              "distance": 2.8, "hostile": True, "protected": False, "line_of_sight": True}

    async def go():
        async with mind_env(tmp_path) as e:
            await night_start(e, away=False)
            e.body.nearby, e.body.threats = [outside], [{"entity_id": 7, "kind": "ranged", "danger": 0.5}]
            await asyncio.sleep(1.5)
            assert not any(s == "fight_melee" for s, _ in skills(e))                  # outside: never chased
            e.body.nearby, e.body.threats = [inside], [{"entity_id": 8, "kind": "ranged", "danger": 0.5}]
            await until(lambda: any(s == "fight_melee" for s, _ in skills(e)), timeout=5)
            e.body.nearby, e.body.threats = [], []
            r = await dawn(e)
            fight = next(p for s, p in skills(e) if s == "fight_melee")
            assert fight["entity_id"] == 8 and r["fights"][0]["type"] == "minecraft:skeleton", (fight, r["fights"])
    run(go())


def test_o83e_the_doorway_is_sealed_at_dusk_and_opened_at_dawn(tmp_path):
    """O83e: with a doorway and blocks she seals both cells (lower first) at dusk after reaching the shelter and opens
    exactly those cells at dawn; one block seals the lower cell only; no block leaves it open - all logged."""
    door = [{"x": 3, "y": 65, "z": 0}, {"x": 3, "y": 64, "z": 0}]

    async def night_with(e, inventory):
        e.body.inventory = inventory
        e.body.walk_s, e.body.time_of_day = 0.05, 14000
        await until(lambda: (e.mind.fresh_state() or {}).get("time_of_day") == 14000)
        assert e.mind.night.start(doorway=door)["ok"]
        await until(lambda: any(s == "go_home" for s, _ in skills(e)), timeout=5)
        await asyncio.sleep(1.0)
        return await dawn(e)

    async def go():
        async with mind_env(tmp_path) as e:
            r = await night_with(e, [{"slot": 0, "item": "minecraft:cobblestone", "count": 4}, PICK])
            done = skills(e)
            placed = [p["pos"] for s, p in done if s == "place_block"]
            mined = [p["pos"] for s, p in done if s == "mine_block"]
            assert placed == [door[1], door[0]], placed                                 # lower cell first
            assert sorted(map(str, mined)) == sorted(map(str, placed)) and not r["sealed"], (mined, r["sealed"])
            assert [s for s, _ in done].index("place_block") > [s for s, _ in done].index("go_home")   # from inside
            assert any("sealed 2/2 at dusk with minecraft:cobblestone" in n for n in r["notes"]), r["notes"]
            assert any("opened 2 cell(s) at dawn" in n for n in r["notes"]), r["notes"]
    run(go())

    async def one_or_none():
        async with mind_env(tmp_path / "b") as e:
            r = await night_with(e, [{"slot": 0, "item": "minecraft:dirt", "count": 1}])
            assert [p["pos"] for s, p in skills(e) if s == "place_block"] == [door[1]]
            assert any("sealed 1/2" in n for n in r["notes"]), r["notes"]
        async with mind_env(tmp_path / "c") as e:
            r = await night_with(e, [{"slot": 0, "item": "minecraft:bread", "count": 5}])
            assert not any(s == "place_block" for s, _ in skills(e))
            assert any("UNSEALED" in n for n in r["notes"]) and "UNSEALED" in r["report"], (r["notes"], r["report"])
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    run(one_or_none())


def test_o83b_a11_no_recover_walk_at_night_past_a_hostile_and_none_once_sealed(tmp_path):
    """CP-31 a11 (7 deaths in a night): a recover walk to the drops at night with zombies 1.8 blocks from the path killed
    her again. At night she walks to her drops only when no hostile is within 16 of the path; otherwise she waits for
    dawn while the drops last, else lets them go. Sealed, she never goes out at night and fights only what is inside."""
    spot = {"x": 30.5, "y": 64.0, "z": -4.5}
    zombie = {"id": 9, "type": "minecraft:zombie", "name": None, "pos": {"x": 15.0, "y": 64, "z": 0.8}, "distance": 14.6,
              "hostile": True, "protected": False, "line_of_sight": True}

    async def die(e):
        await e.body.send("event", {"kind": "death", "detail": {"pos": spot, "dim": e.body.dim, "cause": "mob",
                                                                "killer_type": "minecraft:zombie"}})
        await e.body.send("event", {"kind": "respawn", "detail": {"pos": {"x": 0.5, "y": 64, "z": 0.5},
                                                                  "dim": e.body.dim}})
        await until(lambda: e.mind.night.current["deaths"] and "not" in e.mind.night.current["deaths"][0]["note"] or
                    "waiting" in (e.mind.night.current["deaths"] or [{"note": ""}])[0]["note"], timeout=10)

    def walks_to_spot(e):
        return [p for s, p in skills(e) if s == "walk_to" and p["pos"] == spot]

    async def late_night():                            # 1000 ticks to dawn: the drops outlast the night
        async with mind_env(tmp_path / "a") as e:
            await night_start(e, away=False)
            e.body.time_of_day, e.body.nearby = 22000, [zombie]
            await asyncio.sleep(0.5)
            await die(e)
            d = e.mind.night.current["deaths"][0]
            assert d.get("deferred") and "zombie within 16 of the path" in d["note"], d
            await asyncio.sleep(1.0)
            assert not walks_to_spot(e)                                                  # not at night
            e.body.nearby = []
            r = await dawn(e)
            assert walks_to_spot(e) and r["deaths"][0]["recovered"] is not None, r["deaths"]  # in daylight

    async def early_night():                           # 9000 ticks to dawn: the drops would not last - let them go
        async with mind_env(tmp_path / "b") as e:
            await night_start(e, away=False)
            e.body.nearby = [zombie]
            await asyncio.sleep(0.5)
            await die(e)
            d = e.mind.night.current["deaths"][0]
            assert d["recovered"] is False and "despawn before dawn" in d["note"], d
            e.body.nearby = []
            await dawn(e)
            assert not walks_to_spot(e)

    async def sealed():
        async with mind_env(tmp_path / "c") as e:
            e.body.inventory = [{"slot": 0, "item": "minecraft:cobblestone", "count": 4}, PICK]
            e.body.walk_s, e.body.time_of_day = 0.05, 22000
            await until(lambda: (e.mind.fresh_state() or {}).get("time_of_day") == 22000)
            assert e.mind.night.start(doorway=[{"x": 3, "y": 64, "z": 0}, {"x": 3, "y": 65, "z": 0}])["ok"]
            await until(lambda: e.mind.night.current["sealed"], timeout=5)
            outside = {**ZOMBIE, "pos": {"x": 4.5, "y": 64, "z": 2.5}, "distance": 4.5}
            e.body.nearby, e.body.threats = [outside], [{"entity_id": 5, "kind": "melee", "danger": 0.6}]
            await asyncio.sleep(1.5)
            assert not any(s == "fight_melee" for s, _ in skills(e))                    # sealed: no chase outside
            e.body.nearby, e.body.threats = [ZOMBIE], [{"entity_id": 5, "kind": "melee", "danger": 0.6}]
            await until(lambda: any(s == "fight_melee" for s, _ in skills(e)), timeout=5)
            fight = next(p for s, p in skills(e) if s == "fight_melee")
            assert fight["radius"] <= 4, fight                                           # never leaves the room
            e.body.nearby, e.body.threats = [], []
            await die(e)
            d = e.mind.night.current["deaths"][0]
            assert d.get("deferred") and "sealed" in d["note"] and not walks_to_spot(e), d
            r = await dawn(e)
            seq = [s for s, _ in skills(e)]
            assert walks_to_spot(e) and seq.index("mine_block") < max(i for i, (s, p) in enumerate(skills(e))
                                                                       if s == "walk_to" and p["pos"] == spot), seq
    for d in "abc":
        (tmp_path / d).mkdir()
    run(late_night())
    run(early_night())
    run(sealed())


def test_o90_a13_dawn_unseal_walks_within_reach_then_retries_once_from_the_other_side(tmp_path, monkeypatch):
    """CP-31 a13 night 0 (lane D, b011978): sealed 2/2 at dusk, but at dawn both mine_block calls failed
    `out_of_range` - mine_block does not walk and she stood beyond reach. Now she walks within reach of the cell on
    her own side of the doorway first, and if the mine still fails, tries once more from the other side. O90c: the
    dusk seal had the same assumption (place_block does not walk either) - she walks in to the inside stand first,
    never out."""
    door = [{"x": 5, "y": 65, "z": 0}, {"x": 5, "y": 64, "z": 0}]       # 5.1 from her eyes at the shelter (0.5 64 0.5)
    from eve_mind import nightsurvival
    monkeypatch.setattr(nightsurvival, "OPEN_RETRY_S", 0.0)         # one pass per dawn here (O235's retry: test_s28)
    inside, outside = {"x": 4.5, "y": 64, "z": 0.5}, {"x": 6.5, "y": 64, "z": 0.5}

    async def night(e, fails):
        base = e.body.execute

        async def execute(aid, p, req_id):          # the body's reach (4.5 from the eyes) plus a scripted side
            c, b, sk = p["params"].get("pos"), e.body.pos, p["skill"]
            far = sk in ("mine_block", "place_block") and ((b["x"] - c["x"] - 0.5) ** 2 + (b["y"] + 1.62 - c["y"] -
                                                            0.5) ** 2 + (b["z"] - c["z"] - 0.5) ** 2) ** 0.5 > 4.5
            bad = sk == "mine_block" and fails(b)
            e.body.result_override = {sk: ("failed", "out_of_range" if far else "obstructed")} if far or bad else {}
            return await base(aid, p, req_id)
        e.body.execute = execute
        e.body.inventory = [{"slot": 0, "item": "minecraft:cobblestone", "count": 4}, PICK]
        e.body.walk_s, e.body.time_of_day = 0.05, 14000
        await until(lambda: (e.mind.fresh_state() or {}).get("time_of_day") == 14000)
        assert e.mind.night.start(doorway=door)["ok"]
        await until(lambda: len(e.mind.night.current["sealed"]) == 2, timeout=5)       # O90c: the dusk seal walked in
        seq = [(s, p.get("pos")) for s, p in skills(e)]
        first = next(n for n, (s, _) in enumerate(seq) if s == "place_block")
        assert seq[first - 1] == ("walk_to", inside) and seq[first - 2][0] == "go_home", seq
        await until(lambda: e.body.pos["x"] < 1.0, timeout=5)   # 4.0 from the shelter: back home (a13: far at dawn)
        r = await dawn(e)
        i = max(n for n, (s, _) in enumerate(skills(e)) if s == "place_block") + 1
        assert not any(x["skill"] == "place_block" for x in r["refusals"]), r["refusals"]
        return r, [(s, p.get("pos")) for s, p in skills(e)[i:] if s in ("walk_to", "mine_block")]

    async def go():
        async with mind_env(tmp_path / "a") as e:            # a13: walk in, then both cells open from inside
            r, after = await night(e, lambda b: False)
            park = {"x": 0.5, "y": 64.0, "z": 0.5}                         # O236: the night is held off the door
            assert after == [("walk_to", park), ("walk_to", inside), ("mine_block", door[0]), ("mine_block", door[1]),
                             ("walk_to", park)], after                     # O235: top first; O237: back off the door
            assert not r["sealed"] and any("opened 2 cell(s) at dawn" in n for n in r["notes"]), r["notes"]
            assert not any(x["code"] == "out_of_range" for x in r["refusals"]), r["refusals"]
        async with mind_env(tmp_path / "b") as e:            # blocked from inside: once more from outside
            r, after = await night(e, lambda b: b["x"] < 5)
            assert after == [("walk_to", {"x": 0.5, "y": 64.0, "z": 0.5}), ("walk_to", inside), ("mine_block", door[0]),
                             ("walk_to", outside), ("mine_block", door[0]), ("mine_block", door[1]),
                             ("walk_to", {"x": 0.5, "y": 64.0, "z": 0.5})], after
            assert not r["sealed"] and any("opened 2 cell(s) at dawn" in n for n in r["notes"]), r["notes"]
        async with mind_env(tmp_path / "c") as e:            # fails from both sides: two tries per cell, no more
            r, after = await night(e, lambda b: True)
            assert [s for s, _ in after].count("mine_block") == 4 and len(r["sealed"]) == 2, after
            assert any("opened 0 cell(s) at dawn, 2 still sealed" in n for n in r["notes"]), r["notes"]
            # eve-watch N2: obstructed is not a tool problem - its own code, never walled_in or a pickaxe request
            assert r["stop_reason"] == "shut_in" and r["status"] == "partial", r
            assert "still shut at dawn: minecraft:cobblestone (obstructed)" in r["report"], r["report"]
            assert "walled_in" not in r["report"] and not any("walled_in" in n for n in r["notes"]), r
    for d in "abc":
        (tmp_path / d).mkdir()
    run(go())


def test_o90_a14_sealed_she_never_fights_a_mob_she_cannot_see_behind_a_wall(tmp_path):
    """CP-31 a14 night 10 (lane D, 1c6d2963): sealed all night, yet fight_melee went to a skeleton outside the west wall
    - inside() is a 3.0 sphere round shelter_pos and reaches past the walls (the wall's inner face was 1.48 away).
    Sealed, a target she has no line of sight to is behind a wall: never dispatched. One she can see is fought."""
    door = [{"x": 3, "y": 64, "z": 0}, {"x": 3, "y": 65, "z": 0}]
    behind = {"id": 41, "type": "minecraft:skeleton", "name": None, "pos": {"x": -2.0, "y": 64, "z": 1.5},
              "distance": 2.7, "hostile": True, "protected": False, "line_of_sight": False}

    async def go():
        async with mind_env(tmp_path) as e:
            e.body.inventory = [{"slot": 0, "item": "minecraft:cobblestone", "count": 4}, PICK]
            e.body.walk_s, e.body.time_of_day = 0.05, 14000
            await until(lambda: (e.mind.fresh_state() or {}).get("time_of_day") == 14000)
            assert e.mind.night.start(doorway=door)["ok"]
            await until(lambda: len(e.mind.night.current["sealed"]) == 2, timeout=5)
            e.body.nearby, e.body.threats = [behind], [{"entity_id": 41, "kind": "ranged", "danger": 0.5}]
            await asyncio.sleep(1.5)
            assert not any(s == "fight_melee" for s, _ in skills(e))                    # behind the wall: never
            e.body.nearby = [{**behind, "line_of_sight": True}]                         # in the room, in sight
            await until(lambda: any(s == "fight_melee" for s, _ in skills(e)), timeout=5)
            e.body.nearby, e.body.threats = [], []
            await dawn(e)
    run(go())


SKEL = {"type": "minecraft:skeleton", "name": None, "hostile": True, "protected": False}


async def late_night(e, door):
    """O98: a late-seal night (the lane's: no seal at dusk) with a doorway she knows."""
    e.body.inventory = [{"slot": 0, "item": "minecraft:cobblestone", "count": 4}, PICK]
    e.body.walk_s, e.body.time_of_day = 0.05, 14000
    await until(lambda: (e.mind.fresh_state() or {}).get("time_of_day") == 14000)
    assert e.mind.night.start(doorway=door, late_seal=True)["ok"]
    await until(lambda: any(s == "go_home" for s, _ in skills(e)), timeout=5)
    await asyncio.sleep(1.0)
    assert not any(s == "place_block" for s, _ in skills(e))                     # open from dusk


def test_o98_a16_a_late_seal_night_seals_at_once_on_a_threat_and_stays_open_without_one(tmp_path):
    """CP-31 a16 night 1 (lane D, late-seal night): a skeleton stood ~1.3 outside the open doorway, 5.7-6.0 from her,
    and shot her for 45 s (20 -> 4 HP) until she died; night_survival never sealed (the lane seals at midnight) nor
    fought. O98: an open doorway is sealed at once, from inside, when a hostile is in or within 4 of it, or a ranged
    hostile can see her; with no threat it stays open all night."""
    door = [{"x": 3, "y": 64, "z": 0}, {"x": 3, "y": 65, "z": 0}]               # out: east; 1.3 out = x 4.3
    a16 = {**SKEL, "id": 7, "pos": {"x": 4.3, "y": 64.0, "z": 0.8}, "distance": 3.8, "line_of_sight": True}

    async def quiet():
        async with mind_env(tmp_path / "a") as e:
            await late_night(e, door)
            r = await dawn(e)
            assert not any(s in ("place_block", "mine_block") for s, _ in skills(e)) and not r["sealed"], skills(e)

    async def a16_night():
        async with mind_env(tmp_path / "b") as e:
            await late_night(e, door)
            e.body.nearby, e.body.threats = [a16], [{"entity_id": 7, "kind": "ranged", "danger": 0.6}]
            await until(lambda: len(e.mind.night.current["sealed"]) == 2, timeout=5)
            r = e.mind.night.current
            assert any("sealed 2/2 on a threat (a minecraft:skeleton within 4 of the doorway)" in n for n in r["notes"]), \
                r["notes"]
            assert not any(s == "fight_melee" for s, _ in skills(e))                    # outside: never chased
            e.body.nearby, e.body.threats = [], []
            r = await dawn(e)
            assert any("opened 2 cell(s) at dawn" in n for n in r["notes"]), r["notes"]

    async def ranged_far():
        async with mind_env(tmp_path / "c") as e:
            await late_night(e, door)
            far = {**SKEL, "id": 8, "pos": {"x": 12.5, "y": 64.0, "z": 0.5}, "distance": 12.0, "line_of_sight": False}
            e.body.nearby, e.body.threats = [far], [{"entity_id": 8, "kind": "ranged", "danger": 0.4}]
            await asyncio.sleep(1.5)
            assert not any(s == "place_block" for s, _ in skills(e))                   # cannot see her: stays open
            e.body.nearby = [{**far, "line_of_sight": True}]
            await until(lambda: len(e.mind.night.current["sealed"]) == 2, timeout=5)
            assert any("on a threat (a ranged minecraft:skeleton with line of sight)" in n
                       for n in e.mind.night.current["notes"]), e.mind.night.current["notes"]
            e.body.nearby, e.body.threats = [], []
            await dawn(e)

    for d in "abc":
        (tmp_path / d).mkdir()
    run(quiet())
    run(a16_night())
    run(ranged_far())


def test_o98_a_hostile_standing_in_the_doorway_cell_is_fought_with_combats_doorway_param(tmp_path):
    """O98: the fight trigger covers a mob standing in the open doorway cell (a skeleton there is neither melee nor
    inside the 3.0 sphere); combat's fight_melee `doorway` {pos, out} goes with it only when the registry declares it."""
    door = [{"x": 4, "y": 64, "z": 0}, {"x": 4, "y": 65, "z": 0}]               # 4.0 from the shelter
    in_door = {**SKEL, "id": 9, "pos": {"x": 4.5, "y": 64.0, "z": 0.5}, "distance": 4.0, "line_of_sight": True}

    async def go(with_param):
        async with mind_env(tmp_path / str(with_param)) as e:
            if with_param:                                       # combat's registry patch (fight_melee doorway)
                e.mind.registry.skills["fight_melee"]["params"]["properties"]["doorway"] = {"type": "object"}
            await late_night(e, door)
            e.body.nearby, e.body.threats = [in_door], [{"entity_id": 9, "kind": "ranged", "danger": 0.6}]
            await until(lambda: any(s == "fight_melee" for s, _ in skills(e)), timeout=5)
            fight = next(p for s, p in skills(e) if s == "fight_melee")
            assert fight["entity_id"] == 9, fight
            if with_param:
                assert fight["doorway"] == {"pos": {"x": 4, "y": 64, "z": 0}, "out": "east"}, fight
            else:
                assert "doorway" not in fight, fight
            e.body.nearby, e.body.threats = [], []
            await dawn(e)
    for d in (False, True):
        (tmp_path / str(d)).mkdir()
        run(go(d))


def test_o101e_the_door_is_never_left_open_dirt_first_else_any_block_else_reported_unsealed(tmp_path):
    """O101e/O101g (lane D night 0, b51d8970: cobblestone only and no pickaxe, the O101 rule left the door open - 4
    deaths). Seal order: a block she opens by hand (dirt, then planks/logs), else ANY block - cobblestone included; with
    no block at all she stays inside and the night is reported UNSEALED. Dawn, on both registries (gated by the LOADED
    registry's params, never a version): a pickaxe -> equipped, mine_block; no pickaxe and mine_block declares by_hand
    (0.16.0 draft) -> mine_block by_hand; no pickaxe and no by_hand (0.15.0, the live 45L) -> no mine_block, walled_in
    reported and the owner asked, the night partial, and the next night's dawn tries again (the dusk re-check of a
    still-shut cell is `occupied`, kept sealed)."""
    from eve_mind.nightsurvival import WALLED, seal_items
    d, c, pl = "minecraft:dirt", "minecraft:cobblestone", "minecraft:oak_planks"
    assert seal_items({c: 64, d: 1}, 2) == [d, c]                      # the hand-openable one first, then stone
    assert seal_items({c: 64, pl: 3}, 2) == [pl, pl]
    assert seal_items({c: 1, "minecraft:bread": 6}, 2) == [c] and seal_items({"minecraft:bread": 6}, 2) == []
    door = [{"x": 3, "y": 64, "z": 0}, {"x": 3, "y": 65, "z": 0}]
    l45 = [{"slot": 0, "item": "minecraft:stone_sword", "count": 1}, {"slot": 1, "item": "minecraft:bread", "count": 6},
           {"slot": 2, "item": c, "count": 2}]

    def by_hand(e):                                         # body's registry patch (398e8ed5, 0.16.0 draft v3)
        e.mind.registry.skills["mine_block"]["params"]["properties"]["by_hand"] = {"type": "boolean", "default": False}

    async def night(e, inventory, override=None):
        e.body.inventory, e.body.result_override = inventory, override
        e.body.walk_s, e.body.time_of_day = 0.05, 14000
        said, orig, n0 = [], e.mind.say, len(skills(e))

        async def say(t):
            said.append(t)
            return await orig(t)
        e.mind.say = say
        await until(lambda: (e.mind.fresh_state() or {}).get("time_of_day") == 14000)
        assert e.mind.night.start(doorway=door)["ok"]
        await until(lambda: any(s == "go_home" for s, _ in skills(e)[n0:]), timeout=5)
        await asyncio.sleep(1.0)
        return await dawn(e), said, skills(e)[n0:]

    def mined(seq):
        return [p for s, p in seq if s == "mine_block"]

    async def go():
        for reg in ("0.16.0", "0.15.0"):
            async with mind_env(tmp_path / f"p{reg}") as e:        # a pickaxe: stone, equipped, mined (no by_hand)
                if reg == "0.16.0":
                    by_hand(e)
                r, _, seq = await night(e, l45 + [PICK])
                assert [p["item"] for s, p in seq if s == "place_block"] == [c] * 2, seq
                i = next(k for k, (s, _) in enumerate(seq) if s == "mine_block")
                assert seq[i - 1] == ("equip_best", {"for_block": c, "slots": []}), seq
                assert len(mined(seq)) == 2 and not any("by_hand" in p for p in mined(seq)), seq
                assert any("opened 2 cell(s) at dawn" in n for n in r["notes"]) and r["status"] == "done", r
        async with mind_env(tmp_path / "h") as e:                  # no pickaxe, by_hand declared: dug out by hand
            by_hand(e)
            r, _, seq = await night(e, l45)
            assert [p["item"] for s, p in seq if s == "place_block"] == [c] * 2, seq
            assert [p.get("by_hand") for p in mined(seq)] == [True, True] and r["status"] == "done", seq
            assert not r["unsealed"] and not r["sealed"] and e.mind.meta(WALLED) == "[]", r
        async with mind_env(tmp_path / "w") as e:                  # no pickaxe, 0.15.0: walled in, never opened
            r, said, seq = await night(e, l45)
            assert [p["item"] for s, p in seq if s == "place_block"] == [c] * 2 and not mined(seq), seq
            assert r["status"] == "partial" and r["stop_reason"] == "walled_in" and len(r["sealed"]) == 2, r
            assert "walled_in (no_tool)" in r["report"] and any("walled in" in t for t in said), (r["report"], said)
            r, said, seq = await night(e, l45[:2])                # no block to re-check them with (eve-watch N1):
            assert not any(s == "place_block" for s, _ in seq) and len(r["sealed"]) == 2, seq   # kept for the dawn,
            assert "not re-checked" in r["unsealed"] and "UNSEALED" in r["report"], r          # never reported shut
            assert any("2 not re-checked" in n for n in r["notes"]) and r["stop_reason"] == "walled_in", r["notes"]
            r, said, seq = await night(e, l45, {"place_block": ("failed", "occupied")})   # the next night: still shut
            assert any("walled in since the last dawn (2 cell(s))" in n for n in r["notes"]), r["notes"]
            assert len(r["sealed"]) == 2 and not r["unsealed"] and r["stop_reason"] == "walled_in", r   # kept sealed
            by_hand(e)                                             # ... and once the registry has by_hand: out
            r, _, seq = await night(e, l45, {"place_block": ("failed", "occupied")})
            assert [p.get("by_hand") for p in mined(seq)] == [True, True] and r["status"] == "done", seq
            assert not r["sealed"] and e.mind.meta(WALLED) == "[]", r
        async with mind_env(tmp_path / "g") as e:                  # walled in, then the cell is gone (dug out or a
            r, _, _ = await night(e, l45)                          # rebuilt room): wrong_block drops it, not walled
            assert r["stop_reason"] == "walled_in", r
            by_hand(e)
            r, _, seq = await night(e, l45, {"place_block": ("failed", "occupied"),
                                             "mine_block": ("failed", "wrong_block")})
            assert len(mined(seq)) == 2 and not r["sealed"] and r["status"] == "done" and not r["stop_reason"], r
            assert sum("no longer holds her minecraft:cobblestone" in n for n in r["notes"]) == 2, r["notes"]
            assert e.mind.meta(WALLED) == "[]", e.mind.meta(WALLED)
        async with mind_env(tmp_path / "x") as e:                  # by_hand declared but the body says no_tool
            by_hand(e)
            r, said, seq = await night(e, l45, {"mine_block": ("failed", "no_tool")})
            assert len(mined(seq)) == 2 and r["stop_reason"] == "walled_in", seq      # not again from outside
            assert any("pickaxe" in t for t in said), said
        async with mind_env(tmp_path / "b") as e:                  # + dirt: sealed with dirt, opened by hand
            r, _, seq = await night(e, l45 + [{"slot": 3, "item": d, "count": 4}])
            assert [p["item"] for s, p in seq if s == "place_block"] == [d] * 2, seq
            assert not any(s == "equip_best" for s, _ in seq) and not any("by_hand" in p for p in mined(seq)), seq
            assert any("opened 2 cell(s) at dawn" in n for n in r["notes"]), r["notes"]
        async with mind_env(tmp_path / "n") as e:                  # nothing to seal with: inside, reported UNSEALED
            r, said, seq = await night(e, l45[:2])
            assert not any(s in ("place_block", "mine_block") for s, _ in seq), seq
            assert r["unsealed"] and "UNSEALED" in r["report"] and not r["sealed"], r
            assert any("dirt" in t for t in said), said                                 # she asks for some
    for k in ("p0.16.0", "p0.15.0", "h", "w", "g", "x", "b", "n"):
        (tmp_path / k).mkdir()
    run(go())


def test_o101q_merge_a_late_seal_night_rechecks_walled_in_cells_and_threat_seals_say_unsealed_once(tmp_path):
    """O101q (O101e onto O98's late seal): a late-seal dusk seals nothing new but re-checks the cells walled in since
    the last dawn (`occupied` keeps them); a threat seal with no block is reported UNSEALED and said only once a night,
    however many threat seals it tries."""
    door = [{"x": 3, "y": 64, "z": 0}, {"x": 3, "y": 65, "z": 0}]
    c = "minecraft:cobblestone"
    l45 = [{"slot": 0, "item": "minecraft:stone_sword", "count": 1}, {"slot": 1, "item": "minecraft:bread", "count": 6},
           {"slot": 2, "item": c, "count": 2}]
    a16 = {**SKEL, "id": 7, "pos": {"x": 4.3, "y": 64.0, "z": 0.8}, "distance": 3.8, "line_of_sight": True}

    async def start(e, inventory, late, override=None):
        e.body.inventory, e.body.result_override = inventory, override
        e.body.walk_s, e.body.time_of_day = 0.05, 14000
        n0, said, orig = len(skills(e)), [], e.mind.say

        async def say(t):
            said.append(t)
            return await orig(t)
        e.mind.say = say
        await until(lambda: (e.mind.fresh_state() or {}).get("time_of_day") == 14000)
        assert e.mind.night.start(doorway=door, late_seal=late)["ok"]
        await until(lambda: any(s == "go_home" for s, _ in skills(e)[n0:]), timeout=5)
        await asyncio.sleep(1.0)
        return n0, said

    async def walled():
        async with mind_env(tmp_path / "a") as e:
            await start(e, l45, False)                                           # 0.15.0, no pickaxe: walled in
            assert (await dawn(e))["stop_reason"] == "walled_in"
            n0, _ = await start(e, l45, True, {"place_block": ("failed", "occupied")})   # the late-seal night after
            assert [p["pos"] for s, p in skills(e)[n0:] if s == "place_block"] == door, skills(e)[n0:]   # re-check only
            r = e.mind.night.current
            assert len(r["sealed"]) == 2 and not r["unsealed"] and not r["recheck"], r
            assert any("sealed 2/2 at dusk with minecraft:cobblestone" in n for n in r["notes"]), r["notes"]
            r = await dawn(e)
            assert r["stop_reason"] == "walled_in", r

    async def no_block():
        async with mind_env(tmp_path / "b") as e:
            n0, said = await start(e, l45[:2], True)
            assert not any(s == "place_block" for s, _ in skills(e)[n0:]) and not e.mind.night.current["unsealed"]
            e.body.nearby, e.body.threats = [a16], [{"entity_id": 7, "kind": "ranged", "danger": 0.6}]
            await until(lambda: sum(n.startswith("doorway: UNSEALED on a threat") for n in e.mind.night.current["notes"])
                        >= 2, timeout=15, what="two threat seals with no block")
            assert sum("no block to close my door" in t for t in said) == 1, said      # said once a night
            assert "a minecraft:skeleton" in e.mind.night.current["unsealed"], e.mind.night.current["unsealed"]
            e.body.nearby, e.body.threats = [], []
            r = await dawn(e)
            assert "UNSEALED" in r["report"], r["report"]
    for k in "ab":
        (tmp_path / k).mkdir()
    run(walled())
    run(no_block())
