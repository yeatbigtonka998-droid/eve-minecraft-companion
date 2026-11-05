"""CP-52 (mind side): `repair <zone> <snapshot>` on fakes, against the proposed registry 0.9.0 (repair_zone,
harvest_replant), plus the link v1.3 validators (zone.repair.result in, grant_region out)."""
import contextlib
import io
import json
import runpy

import pytest
from jsonschema import Draft202012Validator

from conftest import ROOT, mind_env, run, until

STEP = lambda op, x, y, z, b: {"op": op, "pos": {"x": x, "y": y, "z": z}, "block": b}  # noqa: E731


@pytest.fixture
def reg09(tmp_path, monkeypatch):
    """The proposed registry 0.9.0 for both the mind and the fake body (contracts/ is still older)."""
    import fake_body
    from eve_mind import config
    applied = json.loads((ROOT / "contracts" / "skills" / "registry.json").read_text("utf-8"))
    if "repair_zone" in {s["name"] for s in applied["skills"]}:
        return applied                          # contracts/ already carries 0.9.0 (622196e): use it as is
    with contextlib.redirect_stdout(io.StringIO()):
        reg = runpy.run_path(str(ROOT / "parts" / "builder" / "registry" / "builder-0.9.0.py"), run_name="x")["reg"]
    f = tmp_path / "registry-0.9.0.json"
    f.write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setattr(fake_body, "REGISTRY", f)
    monkeypatch.setattr(config.Config, "registry_path", property(lambda self: f))
    return reg


def test_zone_repair_result_schema_and_grant_region_check():
    from eve_mind.hub import INBOUND as SCHEMAS, grant_region_ok
    v = Draft202012Validator(SCHEMAS["sense"]["zone.repair.result"])
    good = {"steps": [STEP("remove", 1, 64, 1, "minecraft:dirt"), STEP("place", 1, 64, 2, "minecraft:oak_stairs[facing=north]")],
            "manual": [{"pos": {"x": 0, "y": 64, "z": 0}, "kind": "minecraft:chest", "note": "O17"}],
            "skipped": [{"pos": {"x": 3, "y": 64, "z": 3}, "reason": "crop_cycle", "block": "minecraft:wheat[age=7]",
                         "actual": "minecraft:air"}], "total": 2, "truncated": False}
    assert not list(v.iter_errors(good))
    assert list(v.iter_errors({**good, "steps": [{"op": "explode", "pos": {"x": 0, "y": 0, "z": 0}, "block": "x"}]}))
    assert list(v.iter_errors({"steps": []}))                       # manual and skipped are required
    g = {"action_id": "a", "effect": "place", "dim": "minecraft:overworld", "min": {"x": 0, "y": 60, "z": 0},
         "max": {"x": 31, "y": 63, "z": 31}, "expires_tick": 100}
    assert grant_region_ok(g) is None
    assert grant_region_ok({**g, "max": {"x": 31, "y": 95, "z": 31}}) == "36864 blocks > 32768"
    assert grant_region_ok({**g, "min": {"x": 40, "y": 60, "z": 0}}) == "min > max"
    assert grant_region_ok({**g, "effect": "explode"})


def test_harvest_and_repair_grant_regions_come_only_from_params_box_and_senses_answer(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            m = e.mind
            assert m.grant_regions("harvest_replant", {"min": {"x": 5, "y": 64, "z": 5}, "max": {"x": 1, "y": 63, "z": 9}}) \
                == [({"x": 1, "y": 63, "z": 5}, {"x": 5, "y": 64, "z": 9})]
            assert m.grant_regions("harvest_crop", {"min": {"x": 5, "y": 64, "z": 5}, "max": {"x": 1, "y": 63, "z": 9},
                                                    "crop": "minecraft:wheat"})                 == [({"x": 1, "y": 63, "z": 5}, {"x": 5, "y": 64, "z": 9})]           # registry 0.16.0, same box
            steps = [STEP("place", 1, 64, 1, "minecraft:farmland"), STEP("remove", 4, 66, 2, "minecraft:dirt")]
            p = {"zone_id": "home", "snapshot_id": "home@v1", "steps": steps}
            assert m.grant_regions("repair_zone", p) == []        # sense has not answered: no grant
            m.repair_answers[("home", "home@v1")] = {(s["op"], s["pos"]["x"], s["pos"]["y"], s["pos"]["z"], s["block"])
                                                     for s in steps}
            assert m.grant_regions("repair_zone", p) == [({"x": 1, "y": 64, "z": 1}, {"x": 4, "y": 67, "z": 2})]
            p2 = {**p, "steps": steps + [STEP("place", 30, 70, 30, "minecraft:tnt")]}
            assert m.grant_regions("repair_zone", p2) == []       # a step sense never gave: nothing granted
    run(go())


def test_repair_loop_restores_reports_manual_and_keeps_owner_edits(tmp_path, reg09):
    async def go():
        async with mind_env(tmp_path) as e:
            asks = {"n": 0}
            steps = [STEP("remove", 2, 65, 2, "minecraft:cobblestone"), STEP("place", 2, 64, 2, "minecraft:farmland")]

            def answer(q):
                asks["n"] += 1
                if asks["n"] == 1:
                    return {"steps": steps, "manual": [{"pos": {"x": 9, "y": 64, "z": 9}, "kind": "minecraft:chest",
                                                        "note": "O17"}],
                            "skipped": [{"pos": {"x": 5, "y": 64, "z": 5}, "reason": "owner_edit",
                                         "block": "minecraft:dirt", "actual": "minecraft:stone"},
                                        {"pos": {"x": 3, "y": 65, "z": 3}, "reason": "crop_cycle",
                                         "block": "minecraft:wheat[age=7]", "actual": "minecraft:air"}],
                            "total": 2, "truncated": False}
                return {"steps": [], "manual": [{"pos": {"x": 9, "y": 64, "z": 9}, "kind": "minecraft:chest",
                                                 "note": "O17"}], "skipped": [], "total": 0, "truncated": False}
            e.sense.repair_answers = answer
            await e.sense.send_storage([])
            await until(lambda: e.mind.sense_tick is not None)
            e.body.walk_s = 0.02
            assert not e.mind.repair.start("home", "home@v1")["ok"]     # never a whole-zone repair
            r = e.mind.repair.start("home", "home@v1", {"min": {"x": 0, "y": 60, "z": 0}, "max": {"x": 10, "y": 70, "z": 10}})
            assert r["ok"], r
            await until(lambda: e.mind.repair.current.status != "running", timeout=10)
            st = e.mind.repair.status(r["repair_id"])
            assert st["status"] == "done" and len(st["manual"]) == 1, st
            assert all(q["box"] == {"min": {"x": 0, "y": 60, "z": 0}, "max": {"x": 10, "y": 70, "z": 10}} for q in e.sense.repair_asks)            # the site box goes to sense
            sent = [json.loads(x["params"]) for x in e.mind.db.execute(
                "SELECT params FROM actions WHERE skill='repair_zone'")]
            assert len(sent) == 1 and sent[0]["steps"] == steps
            assert sent[0]["replant"] == [{"pos": {"x": 3, "y": 65, "z": 3}, "block": "minecraft:wheat[age=7]"}]
            assert all(s["pos"] != {"x": 5, "y": 64, "z": 5} for s in sent[0]["steps"])   # owner edit untouched
            aid = e.mind.db.execute("SELECT action_id FROM actions WHERE skill='repair_zone'").fetchone()[0]
            assert sum(1 for o in e.sense.order if o == ("region", aid)) == 3      # place + break + use (v1.7)
            assert ("revoke", aid) in e.sense.order
    run(go())


def test_repair_no_item_gathers_then_retries_without_a_retry_limit_hit(tmp_path, reg09):
    async def go():
        async with mind_env(tmp_path, retry_limit=1) as e:
            steps = [STEP("place", 2, 64, 2, "minecraft:oak_planks")]
            left = {"n": 2}

            def answer(q):
                return {"steps": steps if left["n"] > 0 else [], "manual": [], "skipped": [], "total": 1,
                        "truncated": False}
            e.sense.repair_answers = answer
            chest = {"x": 10, "y": 64, "z": 10}
            await e.sense.send_storage([{"id": "c1", "dim": "minecraft:overworld", "pos": chest, "kind": "minecraft:chest",
                                         "tag": "shared", "loaded": True, "observed_tick": 1,
                                         "items": [{"item": "minecraft:oak_planks", "count": 5}]}])
            await until(lambda: e.mind.sense_tick is not None and e.mind.db.execute(
                "SELECT count(*) FROM containers").fetchone()[0] == 1)
            e.body.walk_s = 0.02
            orig = e.body.execute

            async def world(aid, msg, req_id):
                e.body.result_override = None
                if msg["skill"] == "repair_zone":
                    if not any(s["item"] == "minecraft:oak_planks" for s in e.body.inventory):
                        e.body.result_override = ("failed", "no_item")
                        e.body.evidence_extra = {"repair_zone": [{"deficit": {"missing": {"minecraft:oak_planks": 1},
                                                                             "want": {"minecraft:oak_planks": 1}}}]}
                    else:
                        left["n"] = 0
                elif msg["skill"] == "withdraw":
                    e.body.inventory = [{"slot": 0, "item": "minecraft:oak_planks", "count": 1}]
                return await orig(aid, msg, req_id)
            e.body.execute = world
            r = e.mind.repair.start("home", "home@v1", {"min": {"x": 0, "y": 60, "z": 0}, "max": {"x": 10, "y": 70, "z": 10}})
            await until(lambda: e.mind.repair.current.status != "running", timeout=10)
            st = e.mind.repair.status(r["repair_id"])
            assert st["status"] == "done", st
            skills = [x["skill"] for x in e.mind.db.execute("SELECT skill FROM actions ORDER BY rowid")]
            assert skills == ["repair_zone", "walk_to", "withdraw", "repair_zone"], skills
    run(go())


def test_repair_refuses_answers_not_limited_to_the_site_box(tmp_path, reg09):
    """eve-sense <= 0.1.13 ignores `box` and answers for the whole zone: no echoed box, a bigger box, or a step
    outside the site box -> the run fails safe and nothing is dispatched."""
    box = {"min": {"x": 0, "y": 60, "z": 0}, "max": {"x": 10, "y": 70, "z": 10}}

    async def go():
        async with mind_env(tmp_path) as e:
            cases = [
                ("no box", False, lambda q: {"steps": [STEP("place", 2, 64, 2, "minecraft:dirt")], "manual": [],
                                             "skipped": []}),
                ("bigger box", True, lambda q: {"steps": [], "manual": [], "skipped": [],
                                                "box": {"min": {"x": -50, "y": 0, "z": -50},
                                                        "max": {"x": 50, "y": 200, "z": 50}}}),
                ("step outside", True, lambda q: {"steps": [STEP("remove", -46, 151, -75, "minecraft:smooth_stone")],
                                                  "manual": [], "skipped": []}),
            ]
            for name, echo, answer in cases:
                e.sense.echo_box, e.sense.repair_answers = echo, answer
                r = e.mind.repair.start("home", "home@v1", box)
                assert r["ok"], r
                await until(lambda: e.mind.repair.current.status != "running", timeout=10)
                st = e.mind.repair.status(r["repair_id"])
                assert st["status"] == "failed" and "refused" in st["reason"], (name, st)
            assert not e.mind.db.execute("SELECT count(*) FROM actions WHERE skill='repair_zone'").fetchone()[0]
    run(go())


def test_grants_use_senses_clock_and_repair_replant_gets_use(tmp_path, reg09):
    """link v1.7 (CP-52 a2: every grant_region refused, expires_tick outside sense's (now, now+72000]): the expiry is
    the last server tick sense reported + a ttl capped below 72000, whatever sense's clock reads; a repair with a
    replant gets place, break AND use regions over the site."""
    box = {"min": {"x": 0, "y": 60, "z": 0}, "max": {"x": 10, "y": 70, "z": 10}}

    async def go():
        async with mind_env(tmp_path) as e:
            e.mind.sense_tick = (-10 ** 6, __import__("time").monotonic())   # S93-ci-1: an expiry never lands below 0
            assert await e.mind.grant_expiry({"timeout_ms": 1000}) == 0
            # far from the mind's clock but a real tick, >= 0: a fake one below 0 has no valid expiry at all (fresh CI box)
            for offset in (1000 - e.sense.tick, 5_000_000):
                e.sense.tick_offset, e.sense.refused, e.sense.order = offset, [], []
                await e.sense.send_storage([])
                await until(lambda: e.mind.sense_tick is not None and abs(e.mind.sense_tick[0] - e.sense.tick) < 100)
                steps = [STEP("place", 2, 64, 2, "minecraft:farmland")]
                e.sense.repair_answers = lambda q, s=steps: {"steps": s if not e.sense.order else [], "manual": [],
                                                             "skipped": [{"pos": {"x": 2, "y": 65, "z": 2},
                                                                          "reason": "crop_cycle",
                                                                          "block": "minecraft:wheat[age=7]",
                                                                          "actual": "minecraft:air"}]
                                                             if not e.sense.order else []}
                e.body.walk_s = 0.02
                r = e.mind.repair.start("home", "home@v1", box)
                await until(lambda: e.mind.repair.current.status != "running", timeout=10)
                assert not e.sense.refused, e.sense.refused[:1]
                aid = e.mind.db.execute("SELECT action_id FROM actions WHERE skill='repair_zone' ORDER BY rowid DESC "
                                        "LIMIT 1").fetchone()[0]
                effects = [g["effect"] for g in e.mind.db.execute(
                    "SELECT payload FROM events WHERE kind='grant_region'") for g in [__import__("json").loads(g[0])]
                    if g["action_id"] == aid]
                assert set(effects) == {"place", "break", "use"}, effects
                ticks = [g["expires_tick"] for g in e.mind.db.execute(
                    "SELECT payload FROM events WHERE kind='grant_region'") for g in [__import__("json").loads(g[0])]
                    if g["action_id"] == aid]
                assert ticks and min(ticks) >= 0, ticks             # S93-ci-1: a grant never expires in the past
    run(go())


def test_a_stale_tick_is_refreshed_before_granting(tmp_path):
    """No sense frame for > 600 ticks: the mind asks sense for a fresh frame before computing any expiry."""
    import time as _time

    async def go():
        async with mind_env(tmp_path) as e:
            await e.sense.send_storage([])
            await until(lambda: e.mind.sense_tick is not None)
            tick, at = e.mind.sense_tick
            e.mind.sense_tick = (tick - 10_000, at - 40)      # 40 s (800 ticks) old and far behind
            sk = {"timeout_ms": 600_000, "name": "x"}
            exp = await e.mind.grant_expiry(sk)
            assert exp is not None and e.sense.tick < exp <= e.sense.tick + 72000, (exp, e.sense.tick)
    run(go())


def test_o49_senses_actor_stays_out_of_repair_zone_params(tmp_path, reg09):
    """Builder's CP-52 a4: sense marks steps on Eve-placed blocks `actor: eve`; repair_zone's step schema is strict, so
    the loop forwards only op/pos/block (and pos/block for replant) and keeps the actor in its own notes."""
    async def go():
        async with mind_env(tmp_path) as e:
            steps = [{**STEP("place", 2, 64, 2, "minecraft:cobblestone"), "actor": "eve"},
                     STEP("remove", 2, 65, 2, "minecraft:dirt")]
            e.sense.repair_answers = lambda q: {
                "steps": steps if not e.sense.order else [], "manual": [], "total": 2, "truncated": False,
                "skipped": [{"pos": {"x": 3, "y": 65, "z": 3}, "reason": "crop_cycle", "block": "minecraft:wheat[age=7]",
                             "actual": "minecraft:air", "actor": "eve"}] if not e.sense.order else []}
            await e.sense.send_storage([])
            await until(lambda: e.mind.sense_tick is not None)
            e.body.walk_s = 0.02
            r = e.mind.repair.start("home", "home@v1", {"min": {"x": 0, "y": 60, "z": 0}, "max": {"x": 10, "y": 70, "z": 10}})
            await until(lambda: e.mind.repair.current.status != "running", timeout=10)
            st = e.mind.repair.status(r["repair_id"])
            assert st["status"] == "done", st
            sent = [json.loads(x["params"]) for x in e.mind.db.execute("SELECT params FROM actions WHERE skill='repair_zone'")]
            assert sent[0]["steps"] == [STEP("place", 2, 64, 2, "minecraft:cobblestone"), STEP("remove", 2, 65, 2, "minecraft:dirt")]
            assert sent[0]["replant"] == [{"pos": {"x": 3, "y": 65, "z": 3}, "block": "minecraft:wheat[age=7]"}]
            assert any("actor" in n and "eve" in n for n in st["notes"]), st["notes"]
    run(go())
