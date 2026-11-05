"""CP-40 (O41/O41c): the deterministic iron-kit runner on fakes, against the proposed registry 0.13.0 (prospect +
equip_best). A fake world turns prospect into raw iron/coal (+ breaks), smelt into ingots, craft into items."""
import asyncio
import contextlib
import io
import json
import runpy
from collections import Counter

import pytest

from conftest import ROOT, envelope, mind_env, run, until

KIT = {"minecraft:stone_pickaxe": 1, "minecraft:stone_axe": 1, "minecraft:stone_sword": 1, "minecraft:bread": 16,
       "minecraft:torch": 16, "minecraft:crafting_table": 1, "minecraft:oak_planks": 8}


@pytest.fixture
def reg13(tmp_path, monkeypatch):
    import fake_body
    from eve_mind import config
    applied = json.loads((ROOT / "contracts" / "skills" / "registry.json").read_text("utf-8"))
    if "equip_best" not in {s["name"] for s in applied["skills"]}:
        with contextlib.redirect_stdout(io.StringIO()):
            applied = runpy.run_path(str(ROOT / "parts" / "body" / "registry" / "body-0.13.0.py"), run_name="x")["reg"]
        f = tmp_path / "registry-0.13.0.json"
        f.write_text(json.dumps(applied), encoding="utf-8")
        monkeypatch.setattr(fake_body, "REGISTRY", f)
        monkeypatch.setattr(config.Config, "registry_path", property(lambda self: f))
    return applied


def inv_of(body):
    c = Counter()
    for s in body.inventory:
        c[s["item"]] += s["count"]
    return c


def set_inv(body, c):
    body.inventory = [{"slot": i, "item": k, "count": v} for i, (k, v) in enumerate(sorted((+c).items()))]


def fake_world(body, per_prospect=10, breaks=20):
    orig = body.execute

    async def execute(aid, p, req_id):
        skill, params = p["skill"], p["params"]
        body.result_override, have = None, inv_of(body)
        if skill == "prospect":
            item = "minecraft:raw_iron" if "iron" in params["tag"] else "minecraft:coal"
            n = min(params["count"], per_prospect)
            have[item] += n
            have["minecraft:cobblestone"] += breaks
            set_inv(body, have)
            body.evidence_extra = {"prospect": [{"mined": {item: n}, "ores": n, "blocks_broken": breaks,
                                                 "stop_reason": "count", "returned": True}]}
        elif skill == "smelt":
            k = min(have[params["item"]], params["count"])
            have[params["item"]] -= k
            have["minecraft:iron_ingot"] += k
            set_inv(body, have)
        elif skill == "craft":
            have[params["item"]] += params["count"]
            set_inv(body, have)
        elif skill == "place_block":
            have[params["item"]] -= 1
            set_inv(body, have)
        elif skill == "equip_best":
            after = {s: f"minecraft:iron_{p}" for s, p in (("head", "helmet"), ("chest", "chestplate"),
                                                         ("legs", "leggings"), ("feet", "boots")) if have[f"minecraft:iron_{p}"]}
            body.evidence_extra = {"equip_best": [{"after": after, "changed": list(after)}]}
        return await orig(aid, p, req_id)
    body.execute = execute


async def ready(e, kit=KIT):
    e.body.walk_s = 0.01
    set_inv(e.body, Counter(kit))
    await e.sense.send_storage([])
    await until(lambda: e.mind.sense_tick is not None and {x["item"] for x in (e.mind.fresh_state() or {}).get(
        "inventory", [])} == set(kit), timeout=3)


ENTRY = {"x": 40.5, "y": 40, "z": -48.5}


async def finish(e, timeout=20, entry=ENTRY, direction=None):
    r = e.mind.ironkit.start(entry=entry, direction=direction)
    assert r["ok"], r
    await until(lambda: e.mind.ironkit.current["status"] != "running", timeout=timeout, what="iron_kit ends")
    return e.mind.ironkit.current


def test_iron_kit_end_to_end_on_fakes_with_a_full_run_record(tmp_path, reg13):
    async def go():
        async with mind_env(tmp_path) as e:
            fake_world(e.body)
            await ready(e)
            r = await finish(e)
            assert r["status"] == "done", (r["report"], r["refusals"], r["shortfalls"])
            names = [p["name"] for p in r["phases"]]
            assert names == ["needs", "kit", "to_mine", "mine_raw_iron", "mine_coal", "home", "stations", "smelt",
                             "craft", "equip"], names
            walks = [json.loads(x[0]) for x in e.mind.db.execute("SELECT params FROM actions WHERE skill='walk_to'")]
            seq = [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            assert all(seq[k - 1] == ("equip_best", {"for_block": "minecraft:stone", "slots": []})
                       for k, a in enumerate(seq) if a[0] == "prospect")               # pickaxe in hand first
            assert walks[0] == {"pos": ENTRY}                            # she walks to the mine herself (O41e)
            assert r["raw_iron"] >= 27 and r["coal"] >= 4 and r["ingots"] >= 27
            assert set(r["equipped"]) == {"head", "chest", "legs", "feet"}
            assert r["breaks_by_phase"]["mine_raw_iron"] == 60 and r["breaks_by_phase"]["mine_coal"] == 20
            assert r["start_gametime"] is not None and r["end_gametime"] >= r["start_gametime"] and r["real_end_ms"]
            assert [p["breaks"] for p in r["phases"] if p["name"].startswith("mine_")] == [60, 20]
            assert all({"skill", "status", "code"} <= set(a) for p in r["phases"] for a in p["actions"])
            hist = e.mind.memory.history(e.mind.world_id)
            assert hist and "full iron armour" in hist[-1]["text"]          # grounded in ok actions
            rows = [json.loads(x) for x in (tmp_path / "candidates.jsonl").read_text("utf-8").splitlines()]
            route = [x for x in rows if x["decision_type"] == "route_choice"]     # O94f: the heading she took
            first = next(a for a, s, p in e.mind.db.execute("SELECT action_id, skill, params FROM actions ORDER BY rowid")
                         if s == "prospect")
            assert route and route[0]["action_id"] == first and route[0]["objective_id"] == r["id"], route
            assert {o["id"] for o in route[0]["options"]} == {"north", "south", "east", "west"}
            assert all(x["runner"] == "iron_kit" and x["safety"] is False for x in rows)
    run(go())


def test_iron_kit_stops_capped_at_the_zone_break_cap(tmp_path, reg13):
    env = envelope(zones=[{"id": "fresh_mine", "limits": {"blocks_broken_per_hour": 50}}])

    async def go():
        async with mind_env(tmp_path, env=env) as e:
            fake_world(e.body, per_prospect=5, breaks=25)
            await ready(e)
            r = await finish(e)
            assert r["status"] == "partial" and r["stop_reason"] == "capped" and "break cap" in r["report"], r
            assert r["phases"][-1]["name"] == "home"                 # she ends in the shelter (the judge checks)
            assert sum(r["breaks_by_phase"].values()) == 50
    run(go())


def test_iron_kit_refuses_without_food_and_skips_when_already_equipped(tmp_path, reg13):
    async def go():
        async with mind_env(tmp_path) as e:
            fake_world(e.body)
            await ready(e, {**KIT, "minecraft:bread": 2})
            r = await finish(e)
            assert r["status"] == "refused" and "food" in r["report"]
            assert not e.mind.db.execute("SELECT count(*) FROM actions").fetchone()[0]
            e.body.equipment = {"head": "minecraft:iron_helmet", "chest": "minecraft:diamond_chestplate",
                                "legs": "minecraft:iron_leggings", "feet": "minecraft:iron_boots"}
            await until(lambda: (e.mind.fresh_state() or {}).get("equipment", {}).get("head"), timeout=3)
            r = await finish(e)
            assert r["status"] == "done" and "already" in r["report"]
    run(go())


def test_deferred_needs_are_recorded_and_journalled(tmp_path, reg13):
    async def go():
        async with mind_env(tmp_path) as e:
            fake_world(e.body)
            e.sense.crops = [{"pos": {"x": 1, "y": 64, "z": 1}, "block": "minecraft:wheat", "state": "age=7"}]
            await ready(e)
            r = await finish(e)
            assert r["deferred"] == ["1 ripe crop(s) left unharvested"], r["deferred"]
            assert "Skipped to go mining: 1 ripe crop" in e.mind.memory.history(e.mind.world_id)[-1]["text"]
    run(go())


def test_iron_kit_refuses_without_a_mine_entry(tmp_path, reg13):
    async def go():
        async with mind_env(tmp_path) as e:
            fake_world(e.body)
            await ready(e)
            r = await finish(e, entry=None)
            assert r["status"] == "refused" and "entry" in r["report"]
            assert not e.mind.db.execute("SELECT count(*) FROM actions").fetchone()[0]
    run(go())


def test_a5_broken_pickaxe_is_recrafted_in_the_tunnel_and_prospect_resumes(tmp_path, reg13):
    """Skills' CP-40 a5: prospect ended no_tool when the stone pickaxe broke 131 blocks down. The runner now makes a
    stone pickaxe from tunnel cobblestone and sticks (from planks), at her table placed on the step behind her, mines
    the table back and prospects again."""
    async def go():
        async with mind_env(tmp_path) as e:
            base = e.body.execute
            fake_world(e.body)
            world, broke = e.body.execute, []

            async def execute(aid, p, req_id):
                have = inv_of(e.body)
                if p["skill"] == "prospect" and not broke:
                    broke.append(aid)
                    have["minecraft:stone_pickaxe"] = 0
                    have["minecraft:cobblestone"] += 30
                    set_inv(e.body, have)
                    e.body.result_override = ("failed", "no_tool")
                    e.body.evidence_extra = {"prospect": [{"mined": {}, "blocks_broken": 30, "stop_reason": "no_tool"}]}
                    return await base(aid, p, req_id)
                if p["skill"] == "mine_block" and p["params"].get("expect_block"):
                    have[p["params"]["expect_block"]] += 1
                    set_inv(e.body, have)
                return await world(aid, p, req_id)
            e.body.execute = execute
            await ready(e)
            r = await finish(e)
            assert r["status"] == "done", (r["report"], r["notes"], r["shortfalls"])
            assert r["retools"] == 1 and any("retool 1" in n for n in r["notes"]), r["notes"]
            acts = [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            i = next(k for k, a in enumerate(acts) if a[0] == "craft" and a[1]["item"] == "minecraft:stone_pickaxe")
            table = acts[i][1]["pos"]
            assert acts[i - 1] == ("place_block", {"pos": table, "item": "minecraft:crafting_table"})
            assert acts[i - 2] == ("craft", {"item": "minecraft:stick", "count": 4})          # sticks from planks
            assert acts[i + 1] == ("mine_block", {"pos": table, "expect_block": "minecraft:crafting_table"})
            assert acts[i + 2:i + 4] == [("equip_best", {"for_block": "minecraft:stone", "slots": []}),
                                         acts[i + 3]] and acts[i + 3][0] == "prospect"         # mining resumes
    run(go())


def test_uses_left_reads_pickaxe_durability(tmp_path, reg13):
    """Proactive re-tool: the best pickaxe's max_damage - damage; none -> 0; no durability reported -> plenty."""
    async def go():
        async with mind_env(tmp_path) as e:
            k = e.mind.ironkit
            e.body.inventory = [{"slot": 0, "item": "minecraft:stone_pickaxe", "count": 1, "damage": 120,
                                 "max_damage": 131}]
            first = lambda: ((e.mind.fresh_state() or {}).get("inventory") or [{}])[0]  # noqa: E731
            await until(lambda: first().get("damage") == 120)
            assert k.uses_left() == 11
            e.body.inventory = [{"slot": 0, "item": "minecraft:bread", "count": 4}]
            await until(lambda: first().get("item") == "minecraft:bread")
            assert k.uses_left() == 0
    run(go())


def test_a6_hazard_with_no_ore_tries_the_next_heading_from_the_entry(tmp_path, reg13):
    """O41l (a), CP-40 a6: prospect east from a sea-level entry met water before any ore. The runner walks back to
    the entry and prospects south (then north, west); here south is clear and the kit completes."""
    async def go():
        async with mind_env(tmp_path) as e:
            base = e.body.execute
            fake_world(e.body)
            world = e.body.execute

            async def execute(aid, p, req_id):
                if p["skill"] == "prospect" and p["params"].get("direction") == "east":
                    e.body.result_override = ("failed", "hazard")
                    e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 6,
                                                           "stop_reason": "hazard:water"}]}
                    return await base(aid, p, req_id)
                return await world(aid, p, req_id)
            e.body.execute = execute
            await ready(e)
            r = await finish(e, direction="east")                  # skills' harness names the heading
            assert r["status"] == "done", (r["report"], r["notes"])
            assert r["headings"] == ["east", "south"], r["headings"]
            assert any("east stopped (hazard:water)" in n and "trying south" in n for n in r["notes"]), r["notes"]
            acts = [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            pros = [a[1]["direction"] for a in acts if a[0] == "prospect"]
            assert pros[0] == "east" and set(pros[1:]) == {"south"}, pros
            i = next(k for k, a in enumerate(acts) if a[0] == "prospect")
            assert acts[i + 1] == ("walk_to", {"pos": ENTRY})                 # back to the entry before turning
    run(go())


def test_all_four_headings_in_hazard_end_the_run_honestly(tmp_path, reg13):
    async def go():
        async with mind_env(tmp_path) as e:
            base = e.body.execute
            fake_world(e.body)

            async def execute(aid, p, req_id):
                if p["skill"] == "prospect":
                    e.body.result_override = ("failed", "hazard")
                    e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 4}]}
                    return await base(aid, p, req_id)
                return await world(aid, p, req_id)
            world = e.body.execute
            e.body.execute = execute
            await ready(e)
            r = await finish(e)
            assert r["status"] == "partial" and sorted(r["headings"]) == ["east", "north", "south", "west"], r
            assert any("no untried heading" in n for n in r["notes"]) and "hazard" in r["report"], r["report"]
    run(go())


HURT = {"kind": "hostile", "health": 16.0, "entity_id": 9, "type": "zombie", "distance": 3.2}
STOP_AT = {"x": 46, "y": 30, "z": -48}


def hurt_once(e, threat=HURT):
    """prospect's first call stops `hurt` in skills' exact shape (eve-skills, O58); later calls are the fake world's."""
    base = e.body.execute
    fake_world(e.body)
    world, hit = e.body.execute, []

    async def execute(aid, p, req_id):
        if p["skill"] == "prospect" and not hit:
            hit.append(aid)
            e.body.result_override = ("failed", "hurt")
            e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 12, "returned": False,
                                                   "threat": dict(threat), "stop_at": dict(STOP_AT),
                                                   "stop_reason": "hurt"}]}
            return await base(aid, p, req_id)
        return await world(aid, p, req_id)
    e.body.execute = execute


def test_o58_hurt_by_a_zombie_fights_eats_and_resumes_prospect(tmp_path, reg13):
    async def go():
        async with mind_env(tmp_path) as e:
            hurt_once(e)
            e.body.food = 14
            e.body.nearby = [{"id": 9, "type": "minecraft:zombie", "name": None, "pos": {"x": 47, "y": 30, "z": -48},
                              "distance": 3.2, "hostile": True, "protected": False, "line_of_sight": True}]
            await ready(e)
            r = await finish(e, direction="east")
            assert r["status"] == "done", (r["report"], r["notes"])
            assert r["fights"] and r["fights"][0]["entity_id"] == 9 and r["fights"][0]["type"] == "zombie", r["fights"]
            acts = [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            i = next(k for k, a in enumerate(acts) if a[0] == "fight_melee")
            assert acts[i][1] == {"entity_id": 9, "radius": 8}
            assert [a[0] for a in acts[i + 1:i + 6]] == ["select_hotbar", "use_item", "walk_to", "equip_best",
                                                         "prospect"], acts[i:i + 6]
            assert acts[i + 3][1] == {"pos": STOP_AT, "tolerance": 1.0}
            assert acts[i + 5][1]["direction"] == "east"                       # same heading
    run(go())


def test_o58_without_a_weapon_or_low_health_she_goes_home(tmp_path, reg13):
    async def go():
        for kit, threat in (({k: v for k, v in KIT.items() if not k.endswith(("_sword", "_axe"))}, HURT),
                            (KIT, {**HURT, "health": 6.0})):
            async with mind_env(tmp_path / str(len(kit)) / str(threat["health"])) as e:
                hurt_once(e, threat)
                await ready(e, kit)
                r = await finish(e, direction="east")
                assert r["status"] == "partial" and r["fights"] == [] and any("going home" in n for n in r["notes"]), r
                assert not any(s == "fight_melee" for (s,) in e.mind.db.execute("SELECT skill FROM actions"))
    run(go())


A10_ZONE = {"id": "fresh_mine", "dim": "minecraft:overworld", "min": {"x": 17, "y": -64, "z": -64},
            "max": {"x": 63, "y": 100, "z": -32}}
A10_ENTRY = {"x": 62.5, "y": 40, "z": -48.5}


def test_o41n_a10_entry_at_the_zone_east_edge_goes_west(tmp_path, reg13):
    """CP-40 a10: entry x 62 in fresh_mine x 17..63; 'away from the shelter' (east) left the zone after one block.
    The heading with room for the 24-step staircase is west - even when the harness names east."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[A10_ZONE])) as e:
            fake_world(e.body)
            await ready(e)
            r = await finish(e, entry=A10_ENTRY, direction="east")
            assert r["status"] == "done", (r["report"], r["notes"])
            pros = [json.loads(p)["direction"] for (p,) in e.mind.db.execute(
                "SELECT params FROM actions WHERE skill='prospect'")]
            assert set(pros) == {"west"} and r["headings"] == ["west"], (pros, r["headings"])
    run(go())


def test_o41n_fallback_headings_are_ordered_by_room_and_never_leave_the_zone(tmp_path, reg13):
    """Hazard west with no ore: the next headings by room (south 16.5, north 15.5 >= 8), each with its own
    descend_to so the staircase ends inside the zone (O67); then none is left and the run ends."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[A10_ZONE])) as e:
            base = e.body.execute
            fake_world(e.body)
            world = e.body.execute

            async def execute(aid, p, req_id):
                if p["skill"] == "prospect":
                    e.body.result_override = ("failed", "hazard")
                    e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 3}]}
                    return await base(aid, p, req_id)
                return await world(aid, p, req_id)
            e.body.execute = execute
            await ready(e)
            r = await finish(e, entry=A10_ENTRY)
            assert r["headings"] == ["west", "south", "north"] and r["status"] == "partial", (r["headings"], r["status"])
            assert any("no untried heading with room" in n for n in r["notes"]), r["notes"]
            pros = [json.loads(p) for (p,) in e.mind.db.execute("SELECT params FROM actions WHERE skill='prospect'")]
            assert [(x["direction"], x["descend_to"]) for x in pros] == [("west", 16), ("south", 32), ("north", 33)]
    run(go())


def test_o41n_no_heading_with_room_refuses_before_walking(tmp_path, reg13):
    async def go():
        small = {**A10_ZONE, "min": {"x": 55, "y": -64, "z": -52}, "max": {"x": 63, "y": 100, "z": -45}}
        async with mind_env(tmp_path, env=envelope(zones=[small])) as e:
            fake_world(e.body)
            await ready(e)
            r = await finish(e, entry=A10_ENTRY)
            assert r["status"] == "refused" and "every heading" in r["report"], r["report"]
            assert not e.mind.db.execute("SELECT count(*) FROM actions").fetchone()[0]
    run(go())


def test_o67_a11_entry_on_the_west_edge_goes_east_and_descends_only_to_29(tmp_path, reg13):
    """CP-40 a11 was refused: entry (17, 67, -50) on the west edge of a 47 x 65 fresh_mine; O41n wanted room >= 51,
    the best heading (east) has 46. O67: east, descend_to = max(16, 67 - 46 + 8) = 29."""
    zone = {"id": "fresh_mine", "dim": "minecraft:overworld", "min": {"x": 17, "y": -64, "z": -95},
            "max": {"x": 63, "y": 100, "z": -31}}
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[zone])) as e:
            fake_world(e.body)
            await ready(e)
            r = await finish(e, entry={"x": 17, "y": 67, "z": -50})
            assert r["status"] == "done", (r["report"], r["notes"])
            pros = [json.loads(p) for (p,) in e.mind.db.execute("SELECT params FROM actions WHERE skill='prospect'")]
            assert {(x["direction"], x["descend_to"]) for x in pros} == {("east", 29)}, pros
    run(go())


def test_o74_a13_every_heading_with_room_is_tried_before_giving_up(tmp_path, reg13):
    """CP-40 a13 (entry 44,71,-17 in fresh_mine x 17..63, z -80..-16): north stopped no_floor at tunnel 0, west
    no_floor after 5, and the run ended with east (room 19) never tried. Every heading with room >= 8 is tried, best
    room first, whatever ended the last one; each stop reason is noted."""
    zone = {"id": "fresh_mine", "dim": "minecraft:overworld", "min": {"x": 17, "y": -64, "z": -80},
            "max": {"x": 63, "y": 100, "z": -16}}

    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[zone])) as e:
            base = e.body.execute
            fake_world(e.body)
            world = e.body.execute

            async def execute(aid, p, req_id):
                if p["skill"] == "prospect" and p["params"].get("direction") in ("north", "west"):
                    e.body.result_override = ("failed", "partial")
                    e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 5,
                                                           "stop_reason": "no_floor"}]}
                    return await base(aid, p, req_id)
                return await world(aid, p, req_id)
            e.body.execute = execute
            await ready(e)
            r = await finish(e, entry={"x": 44, "y": 71, "z": -17})
            assert r["headings"] == ["north", "west", "east"], (r["headings"], r["notes"])
            assert r["status"] == "done", (r["report"], r["notes"])
            assert sum("stopped (no_floor)" in n for n in r["notes"]) == 2, r["notes"]
            pros = [json.loads(p) for (p,) in e.mind.db.execute("SELECT params FROM actions WHERE skill='prospect'")]
            assert ("east", 60) in {(x["direction"], x["descend_to"]) for x in pros}          # 71 - 19 + 8
    run(go())


def test_o74e_a15_spoil_is_no_find_west_hazard_then_east_is_tried(tmp_path, reg13):
    """CP-40 a15 replayed exactly: north hazard:no_floor at once (mined {}), west hazard:no_floor after 9 blocks with
    skills' sub-coded fill string (mined {cobblestone: 9}, ores 0). The 9 cobblestone counted as a find and the run
    ended; spoil is no ore, so east (room 19) must be tried."""
    zone = {"id": "fresh_mine", "dim": "minecraft:overworld", "min": {"x": 17, "y": -64, "z": -80},
            "max": {"x": 63, "y": 100, "z": -16}}
    a15 = {"north": {"mined": {}, "ores": 0, "blocks_broken": 0, "tunnel_length": 0, "stop_reason": "hazard:no_floor"},
           "west": {"mined": {"minecraft:cobblestone": 9}, "ores": 0, "blocks_broken": 9, "tunnel_length": 5,
                    "stop_reason": "hazard:no_floor (fill failed failed/obstructed: crosshair on block "
                                   "minecraft:stone at 39, 65, -17 at 38, 63, -17)"}}

    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[zone])) as e:
            base = e.body.execute
            fake_world(e.body)
            world = e.body.execute

            async def execute(aid, p, req_id):
                d = p["params"].get("direction")
                if p["skill"] == "prospect" and d in a15:
                    e.body.result_override = ("failed", "hazard")
                    e.body.evidence_extra = {"prospect": [dict(a15[d])]}
                    return await base(aid, p, req_id)
                return await world(aid, p, req_id)
            e.body.execute = execute
            await ready(e)
            r = await finish(e, entry={"x": 44, "y": 71, "z": -17})
            assert r["headings"] == ["north", "west", "east"], (r["headings"], r["notes"])
            assert r["status"] == "done", (r["report"], r["notes"])
            assert any("west stopped (hazard:no_floor (fill failed" in n for n in r["notes"]), r["notes"]
    run(go())


A18_ZONE = {"id": "fresh_mine", "dim": "minecraft:overworld", "min": {"x": 17, "y": -64, "z": -80},
            "max": {"x": 63, "y": 149, "z": -16}}
A18_ENTRY = {"x": 44, "y": 71, "z": -17}
STRANDED = {"x": 63.7, "y": 60.0, "z": -30.5}                  # a18's server truth at the end
@pytest.fixture
def reg_no_dig(tmp_path, monkeypatch, reg13):
    """The registry without dig_up (0.15.0 declares it, so reg13 has skills' real entry; this is the body without it)."""
    import fake_body
    from eve_mind import config
    reg = {**reg13, "skills": [s for s in reg13["skills"] if s["name"] != "dig_up"]}
    f = tmp_path / "registry-no-dig_up.json"
    f.write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setattr(fake_body, "REGISTRY", f)
    monkeypatch.setattr(config.Config, "registry_path", property(lambda self: f))
    return reg


def a18_world(e, spoil=40):
    """CP-40 attempt 18 replayed (O86): north hazard:no_floor at once, west hazard:no_floor after 9, east turned north
    at the zone edge and was interrupted (water broke in, drowning reflex) with "dir north" in its stop_reason; the
    resume ends unreachable with her pickaxe gone, at y 60; go_home finds no route through the flooded staircase.
    dig_up as skills' v1: a staircase with a pickaxe, a pillar of filler without one (planks only past 6), else no_tool."""
    base = e.body.execute
    fake_world(e.body)
    world, homes, branched = e.body.execute, [], []
    end = {"north": {"stop_reason": "hazard:no_floor"},
           "west": {"stop_reason": "hazard:no_floor (fill failed failed/obstructed: crosshair on block minecraft:stone "
                                   "at 39, 65, -17 at 38, 63, -17)"}}

    async def execute(aid, p, req_id):
        s, d, have = p["skill"], p["params"].get("direction"), inv_of(e.body)
        if s == "prospect" and d in end:
            e.body.result_override = ("failed", "hazard")
            e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 9, **end.pop(d)}]}
            return await base(aid, p, req_id)
        if s == "prospect" and d == "east":
            have["minecraft:cobblestone"] += spoil
            set_inv(e.body, have)
            e.body.result_override = ("failed", "interrupted")
            e.body.evidence_extra = {"prospect": [{"mined": {"minecraft:cobblestone": 40}, "ores": 0,
                                                   "blocks_broken": 40, "stop_reason": "interrupted (turns 1; dir north)",
                                                   "returned": False}]}
            return await base(aid, p, req_id)
        if s == "prospect" and d == "west" and "west" not in end and branched is not None:
            branched.append(aid)                                       # O86v's branch off the north tunnel: flooded
            e.body.result_override = ("failed", "hazard")
            e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 2,
                                                   "stop_reason": "hazard:water (dir west)", "returned": True}]}
            return await base(aid, p, req_id)
        if s == "prospect" and d == "north" and not end:            # the resume, after the turn
            have["minecraft:stone_pickaxe"] = 0
            have["minecraft:cobblestone"] += spoil
            set_inv(e.body, have)
            e.body.pos = dict(STRANDED)
            await asyncio.sleep(0.15)                                  # the state stream carries her there
            e.body.result_override = ("failed", "unreachable")
            e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 7, "returned": False,
                                                   "stop_reason": "blocked:obstructed (dir north)"}]}
            return await base(aid, p, req_id)
        if s == "go_home" and not homes:
            homes.append(aid)                                          # no route: she stays where she is
            e.body.active = None
            return await e.body.finish(aid, "failed", "home_blocked", {"final_pos": dict(e.body.pos)}, req_id)
        if s == "dig_up":
            filler = have["minecraft:cobblestone"] + have["minecraft:dirt"] + max(0, have["minecraft:oak_planks"] - 6)
            if not have["minecraft:stone_pickaxe"] and not filler:
                e.body.result_override = ("failed", "no_tool")
                return await base(aid, p, req_id)
            e.body.pos = {a: float(v) for a, v in p["params"]["toward"].items()}
            await asyncio.sleep(0.15)
        if s == "mine_block" and p["params"].get("expect_block"):
            have[p["params"]["expect_block"]] += 1
            set_inv(e.body, have)
        return await world(aid, p, req_id)
    e.body.execute = execute


def test_o86_a18_resume_turned_heading_then_dig_up_and_home(tmp_path, reg13):
    """O86 (3): the interrupted east tunnel is resumed north (where prospect turned it), never east again. (4) go_home
    home_blocked below the entry with no pickaxe: retool, equip, dig_up to the entry, go_home again - she ends home."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[A18_ZONE])) as e:
            a18_world(e)
            await ready(e)
            r = await finish(e, entry=A18_ENTRY)
            acts = [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            assert [a[1]["direction"] for a in acts if a[0] == "prospect"] == ["north", "west", "east", "north",
                                                                                "west"], acts   # + O86v's branch
            assert r["headings"] == ["north", "west", "east"] and r["branch"]["off"] == "north", (r["headings"], r["branch"])
            assert [a[1].get("descend_to") for a in acts if a[0] == "prospect"] == [16, 52, 60, 60, None]   # east's
            i = next(k for k, a in enumerate(acts) if a[0] == "go_home")
            tail = [a[0] for a in acts[i:]]
            assert tail == ["go_home", "equip_best", "dig_up", "go_home"], tail    # O86v: re-tooled before the branch
            assert acts[i + 2][1] == {"toward": {"x": 44, "y": 71, "z": -17}}                  # skills' param
            assert r["retools"] == 1 and any("dig_up ok" in n for n in r["notes"]), r["notes"]
            assert r["stop_reason"] == "hazard" and not chats(e)            # the flooded branch's stop; not stranded
            assert r["phases"][-1]["name"] == "home" and r["phases"][-1]["result"] == "ok", r["phases"][-1]
            assert e.body.pos == {"x": 0.5, "y": 64.0, "z": 0.5}                                # home
    run(go())


def chats(e):
    return [m["payload"]["text"] for m in e.body.received if m["type"] == "chat.send"]


def test_o86a_no_way_up_is_stranded_with_an_owner_call_and_no_dig_at_the_surface(tmp_path, reg_no_dig):
    """O86a: home_blocked underground with no dig_up registered ends `stranded`: an emergency event and a chat line to
    the owner; at the entry (not underground) dig_up is never tried."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[A18_ZONE])) as e:
            a18_world(e)
            await ready(e)
            r = await finish(e, entry=A18_ENTRY)                       # no dig_up registered
            skills = [s for (s,) in e.mind.db.execute("SELECT skill FROM actions ORDER BY rowid")]
            assert skills[-1] == "go_home" and skills.count("go_home") == 1 and "dig_up" not in skills, skills
            assert r["stop_reason"] == "stranded" and any("stranded underground at 63 60 -31: no dig_up skill" in n
                                                          for n in r["notes"]), (r["stop_reason"], r["notes"])
            assert e.mind.db.execute("SELECT count(*) FROM events WHERE kind='emergency'").fetchone()[0] == 1
            await until(lambda: chats(e), what="owner call")
            assert "stuck underground at 63 60 -31" in chats(e)[0], chats(e)
            e.body.pos = {**A18_ENTRY}                                  # at the entry: not underground
            await until(lambda: (e.mind.fresh_state() or {}).get("pos", {}).get("y") == 71)
            assert await e.mind.ironkit.dig_up(r, r["phases"][-1], "cli") is False
    run(go())


def test_o86a_no_pickaxe_to_make_dig_up_pillars_with_her_cobblestone(tmp_path, reg13):
    """No table to make a pickaxe: dig_up is still sent (skills pillars with her filler) and she gets home."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[A18_ZONE])) as e:
            a18_world(e)
            await ready(e, {k: v for k, v in KIT.items() if k != "minecraft:crafting_table"})
            r = await finish(e, entry=A18_ENTRY)
            acts = [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            i = next(k for k, a in enumerate(acts) if a[0] == "go_home")
            assert [a[0] for a in acts[i:]] == ["go_home", "equip_best", "dig_up", "go_home"], acts[i:]
            assert not any(a[0] == "craft" and a[1]["item"] == "minecraft:stone_pickaxe" for a in acts)
            assert any("retool: no crafting table" in n for n in r["notes"]) and r["stop_reason"] != "stranded", r
            assert r["phases"][-1]["result"] == "ok" and not chats(e), r["phases"][-1]
    run(go())


def test_o86a_nothing_to_climb_with_is_stranded_and_the_night_runner_calls_the_owner_at_once(tmp_path, reg13):
    """No pickaxe, no table, no filler (no spoil, no planks): dig_up ends no_tool -> stranded with an owner call. A night
    started while she is still in the mine does not wait for dusk in the hole: emergency, owner called, night over."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[A18_ZONE])) as e:
            a18_world(e, spoil=0)
            await ready(e, {k: v for k, v in KIT.items() if k not in ("minecraft:crafting_table", "minecraft:oak_planks")})
            r = await finish(e, entry=A18_ENTRY)
            skills = [s for (s,) in e.mind.db.execute("SELECT skill FROM actions ORDER BY rowid")]
            assert skills[-3:] == ["go_home", "equip_best", "dig_up"] and skills.count("dig_up") == 1, skills
            assert r["stop_reason"] == "stranded" and any("dig_up failed (no_tool; no pickaxe)" in n
                                                          for n in r["notes"]), (r["stop_reason"], r["notes"])
            await until(lambda: len(chats(e)) == 1, what="owner call")
            e.body.time_of_day = 6000                                   # midday: the runner would wait for dusk
            n = e.mind.night.start()
            await until(lambda: e.mind.night.current["status"] != "running", timeout=5, what="night ends")
            night = e.mind.night.current
            assert night["status"] == "partial" and night["stop_reason"] == "stranded", night
            assert "emergency" in night["report"] and n["ok"]
            await until(lambda: len(chats(e)) == 2, what="second owner call")
            assert [s for (s,) in e.mind.db.execute("SELECT skill FROM actions")].count("go_home") == 1
            e.body.pos = {"x": 0.5, "y": 64.0, "z": 0.5}               # fetched home: under the entry's y, out of the zone
            await until(lambda: (e.mind.fresh_state() or {}).get("pos", {}).get("x") == 0.5)
            assert not e.mind.night.stranded_now()
    run(go())


def test_o86v_a20_three_dead_headings_branch_perpendicular_off_the_longest_tunnel(tmp_path, reg13):
    """O86v (CP-40 a20 replayed): north no floor, west no floor, east turned north and hit water - no heading left from
    the entry and no iron. The runner walks to the farthest cell of the longest tunnel (the east one, turned north) and
    prospects once at a right angle, toward the side with zone room (west), with no staircase; south never without
    MIN_ROOM."""
    a20 = {"north": ({"x": 44.5, "y": 66.0, "z": -21.5}, "hazard:no_floor (seals 0, bridged 1; dir north)"),
           "west": ({"x": 39.5, "y": 63.0, "z": -16.5}, "hazard:no_floor (dir west)"),
           "east": ({"x": 63.5, "y": 60.0, "z": -29.5}, "hazard:water (seals 12, bridged 0; turns 1; dir north)")}

    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[A18_ZONE])) as e:
            base = e.body.execute
            fake_world(e.body)
            world, done = e.body.execute, set()

            async def execute(aid, p, req_id):
                d = p["params"].get("direction")
                if p["skill"] == "prospect" and d in a20 and d not in done:
                    done.add(d)
                    start, (far, why) = dict(e.body.pos), a20[d]
                    e.body.pos = dict(far)
                    await asyncio.sleep(0.6)                           # the mind's watcher sees her down the tunnel
                    e.body.pos = start                                 # prospect walks back
                    e.body.result_override = ("failed", "hazard")
                    e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 9,
                                                           "stop_reason": why, "returned": True}]}
                    return await base(aid, p, req_id)
                return await world(aid, p, req_id)
            e.body.execute = execute
            await ready(e)
            r = await finish(e, entry=A18_ENTRY, timeout=30)
            acts = [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            pros = [(i, a[1]) for i, a in enumerate(acts) if a[0] == "prospect"]
            assert [x[1]["direction"] for x in pros[:4]] == ["north", "west", "east", "west"], [x[1] for x in pros]
            i4, fourth = pros[3]
            assert "descend_to" not in fourth, fourth                  # she is already down there
            assert acts[i4 - 2] == ("walk_to", {"pos": a20["east"][0], "tolerance": 1.0}), acts[i4 - 2]
            assert r["branch"]["heading"] == "west" and r["branch"]["off"] == "north", r["branch"]
            assert any("branch: west off the north tunnel" in n for n in r["notes"]), r["notes"]
            assert r["status"] == "done", (r["report"], r["notes"])
            k = e.mind.ironkit
            assert k.branch_heading({"x": 44.5, "y": 60.0, "z": -18.5}, "east") == "north"   # south: 2 < MIN_ROOM
            assert k.branch_heading({"x": 62.5, "y": 60.0, "z": -17.5}, "west") == "north"   # south: 1.5 < MIN_ROOM
    run(go())


def test_o86v_skills_far_evidence_picks_the_branch_point_and_heading(tmp_path, reg13):
    """O86v with skills' jar 3c3a9c39: prospect's stop_reason carries "far <x> <y> <z> <heading>" (the farthest cell she
    stood on, two steps back from a hazard) - the branch starts there, off the tunnel with the longest tunnel_length,
    at a right angle to far's heading; no tracking guess needed."""
    a20 = {"north": "hazard:no_floor (seals 0, bridged 1; dir north; far 44 66 -22 north)",
           "west": "hazard:no_floor (dir west; far 39 63 -17 west)",
           "east": "hazard:water (seals 12, bridged 0; turns 1; dir north; far 63 60 -35 north)"}
    length = {"north": 5, "west": 5, "east": 30}

    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[A18_ZONE])) as e:
            base = e.body.execute
            fake_world(e.body)
            world, done = e.body.execute, set()

            async def execute(aid, p, req_id):
                d = p["params"].get("direction")
                if p["skill"] == "prospect" and d in a20 and d not in done:
                    done.add(d)
                    e.body.result_override = ("failed", "hazard")
                    e.body.evidence_extra = {"prospect": [{"mined": {}, "ores": 0, "blocks_broken": 9, "returned": True,
                                                           "tunnel_length": length[d], "stop_reason": a20[d]}]}
                    return await base(aid, p, req_id)
                return await world(aid, p, req_id)
            e.body.execute = execute
            await ready(e)
            r = await finish(e, entry=A18_ENTRY, timeout=30)
            acts = [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            pros = [(i, a[1]) for i, a in enumerate(acts) if a[0] == "prospect"]
            assert [x[1]["direction"] for x in pros[:4]] == ["north", "west", "east", "west"], [x[1] for x in pros]
            assert acts[pros[3][0] - 2] == ("walk_to", {"pos": {"x": 63.5, "y": 60.0, "z": -34.5}, "tolerance": 1.0})
            assert r["branch"]["heading"] == "west" and r["branch"]["reach"] == 30, r["branch"]
            assert r["status"] == "done", (r["report"], r["notes"])
    run(go())


def test_o93_a21_the_retool_table_goes_inside_the_mine_zone_and_a_refusal_tries_the_next_cell(tmp_path, reg13):
    """CP-40 a21 (skills, mind 1c6d2963, a13's seed): on the west branch off the north tunnel at x 63 - fresh_mine's east
    edge - the stone pickaxe wore out and retool put the table on 'the step behind her' at x 64, twice: zone_denied both
    times, no pickaxe, the run ended no_tool. Now the table goes only to cells inside the zone, and a refusal (sense, or
    a cell that will not take it) tries the next one instead of ending the run."""
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[A18_ZONE])) as e:   # x 17..63
            base = e.body.execute
            fake_world(e.body)
            world, placed = e.body.execute, []

            async def execute(aid, p, req_id):
                if p["skill"] == "place_block":
                    c = p["params"]["pos"]
                    placed.append(dict(c))
                    if not A18_ZONE["min"]["x"] <= c["x"] <= A18_ZONE["max"]["x"]:      # sense: outside the box
                        e.body.result_override = ("failed", "zone_denied")
                        return await base(aid, p, req_id)
                    if len(placed) == 1:                                                # a cell that will not take it
                        e.body.result_override = ("failed", "obstructed")
                        return await base(aid, p, req_id)
                return await world(aid, p, req_id)
            e.body.execute = execute
            await ready(e, kit={"minecraft:cobblestone": 12, "minecraft:crafting_table": 1, "minecraft:stick": 4})
            e.body.pos = {"x": 63.5, "y": 60.0, "z": -36.5}                    # a21: on the branch, at the east edge
            await until(lambda: (e.mind.fresh_state() or {}).get("pos", {}).get("x") == 63.5)
            k = e.mind.ironkit
            r = {"id": "a21", "phases": [], "refusals": [], "injuries": [], "notes": [], "retools": 0, "headings": [],
                 "dir": None, "tunnels": [], "branch": {"heading": "west", "off": "north"}, "breaks_by_phase": {},
                 "start_gametime": None, "entry": A18_ENTRY}
            ok = await k.retool(r, k.phase(r, "retool"), A18_ENTRY, "cli")
            assert ok and r["retools"] == 1, r["notes"]
            assert all(c["x"] <= 63 for c in placed), placed                           # never x 64 again
            assert len(placed) == 2 and "obstructed" in r["notes"][0], (placed, r["notes"])
            acts = [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            craft = next(a for a in acts if a[0] == "craft" and a[1]["item"] == "minecraft:stone_pickaxe")
            assert craft[1]["pos"] == placed[1], (craft, placed)                      # crafted at the table that took
            rows = [json.loads(x) for x in (tmp_path / "candidates.jsonl").read_text("utf-8").splitlines()]
            rows = [x for x in rows if x["decision_type"] == "retool"]               # O94f: one row per table try
            ids = [a for a, in e.mind.db.execute("SELECT action_id FROM actions WHERE skill='place_block' ORDER BY rowid")]
            key = [f"{c['x']} {c['y']} {c['z']}" for c in placed]
            assert [x["pick"] for x in rows] == key and [x["action_id"] for x in rows] == ids, (rows, ids)
            out = [o for o in rows[0]["options"] if o.get("rejected_by") == "outside_zone"]
            assert out and all(o["id"].startswith("64 ") for o in out), rows[0]["options"]  # x 64 weighed, rejected
            assert {"id": key[0], "rejected_by": "obstructed"}.items() <= next(
                o for o in rows[1]["options"] if o["id"] == key[0]).items(), rows[1]["options"]
            assert all(x["safety"] is False and "status" not in x for x in rows)
    run(go())
